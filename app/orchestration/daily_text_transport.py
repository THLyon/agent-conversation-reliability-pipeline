from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.daily.transport import DailyTransport, DailyOutputTransportMessageFrame

AppMessageHandler = Callable[[Any, str], Awaitable[None]] # (message, sender_id)

@dataclass(frozen=True)
class DailyTextTransportConfig:
    room_url: str
    token: Optional[str] = None

class DailyTextTransport: 
    """
    Text-only MVP wrapper around Pipecat's DailyTransport.

    - Receives incoming app messages via on_app_message
    - Sends outgoing text as a chat/app message

    Phase 1 goal: deterministic scripted convo over data channels, no STT/TTS.
    """

    #! ---------------- Construction / Wiring ------------------
    def __init__(self, *, bot_name: str, cfg: DailyTextTransportConfig) -> None:
        self._bot_name = bot_name
        self._cfg = cfg

        self._transport = DailyTransport(
            room_url=cfg.room_url,
            token=cfg.token, 
            bot_name=bot_name,
        )

        self._on_app_message: Optional[AppMessageHandler] = None
        self._joined = asyncio.Event()
        self._left = asyncio.Event()

        self._runner: Optional[PipelineRunner] = None
        self._task: Optional[PipelineTask] = None
        self._run_task: Optional[asyncio.Task] = None

        self._inbox: asyncio.Queue[tuple[Any, str]] = asyncio.Queue()
        self._dumped_first_app_message = False

        self._register_event_handlers()
    
    def _register_event_handlers(self) -> None:
        #Event handlers (Pipecat DailyTransport supports these)

        @self._transport.event_handler("on_joined")
        def _on_joined(*args, **kwargs) -> None:
            self._joined.set()

        @self._transport.event_handler("on_left")
        def _on_left(*args, **kwargs) -> None:
            self._left.set()

        @self._transport.event_handler("on_app_message")
        def _on_app_message(*args, **kwargs) -> None:
            """
            Pipecat/Daily have changed arg ordering across versions.

            We want to reliably extract:
              - payload: dict (our {"type":"text",...})
              - sender:  str or id-like (participant id)
            """

            payload, sender = self._extract_app_message(args, kwargs)

            if payload is None:
                return
            
            self._inbox.put_nowait((payload, sender))

            if self._on_app_message is not None:
                asyncio.create_task(self._on_app_message(payload, str(sender)))
    
    #!---------------- Public API ------------------
    def set_app_message_handler(self, handler: AppMessageHandler) -> None:
        self._on_app_message = handler

    async def start(self) -> None:
        """
        Start a minimal Pipecat pipeline so the trasnport actually joins the room.
        """
        if self._run_task is not None: 
            return
        
        # Minimal pipeline: transport only. 
        pipeline = Pipeline([
            self._transport.input(),
            self._transport.output(),
        ])

        self._task = PipelineTask(pipeline, enable_rtvi=False)
        self._runner = PipelineRunner()

        # Run in background task (within this process)
        self._run_task = asyncio.create_task(self._runner.run(self._task))

        # Wait until joined (or raise)
        await self.wait_joined()

    async def stop(self) -> None:
        """
        Stop the pipeline task so the trasnport leaves.
        """
        if self._task is None: 
            return
        
        # Cancel the pipeline task; this triggers Daily transport shutdown/leave.
        await self._task.cancel()
        await self.wait_left()

        if self._run_task is not None:
            # Ensure background runner task is done
            try: 
                await self._run_task
            except asyncio.CancelledError:
                pass
        self._run_task = None
        self._task = None
        self._runner = None
    
    #! ---------------- Public API: Lifecycle Helpers ------------------
    async def wait_joined(self, timeout_s: float = 15.0) -> None:
        try:
            await asyncio.wait_for(self._joined.wait(), timeout=timeout_s)
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"{self._bot_name} did not join within {timeout_s}s") from e

    async def wait_left(self, timeout_s: float = 15.0) -> None: 
        await asyncio.wait_for(self._left.wait(), timeout=timeout_s)

    #! ---------------- Public API: Messaging ------------------    
    async def send_text(self, text: str) -> None:
        """
        send an app/ddata-channel message through the Pipecat pipeline.

        Why: In Pipecat, app messages are sent as OutputTransportMessageFrame(s), 
        not via transport.send_app_message(). The transport output processor
        will deliver it over daily, and peers receive it via on_app_message.
        """

        if self._task is None:
            raise RuntimeError("Transport not started; call start() before send_text()")

        payload = {"type": "text", "text": text, "name": self._bot_name}

        # Daily output transport consumes OutputTransportMessgeFrame; Daily has a typed subclass.
        frame = DailyOutputTransportMessageFrame(payload)

        await self._task.queue_frame(frame)

        if self._bot_name == "Customer Bot":
            spaces = " " * 5
        else: 
            spaces = " " * 2
        print(f"[sent-app] {self._bot_name}:", spaces, text)
        
    
    async def wait_for_text_from(self, expected_name: str, timeout_s: float = 5.0) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"{self._bot_name} did not receive message from {expected_name} within {timeout_s}s"
                )

            msg, sender = await asyncio.wait_for(self._inbox.get(), timeout=remaining)

            payload = self._normalize_app_payload(msg)
            if payload is None:
                continue
            
            self_id = self.participant_id()
            if self_id and sender == self_id:
                continue

            if payload.get("name") == expected_name:
                return payload
    
    async def recv(self, timeout_s: float = 5.0) -> tuple[Any, str]:    
        return await asyncio.wait_for(self._inbox.get(), timeout=timeout_s)
    
    #! ---------------- Public API: Introspection ------------------
    def participant_id(self) -> Optional[str]:
        return getattr(self._transport, "participant_id", None)

    #! ---------------- Private Helpers ------------------
    def _extract_app_message(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, str]:
        # Common patterns we've seen:
        # 1) (payload_dict, sender_id)
        # 2) (transport_obj, payload_dict, sender_id, ...)
        # 3) (transport_obj, sender_id, payload_dict, ...)  (rare)
        if len(args) >= 2:
            a0, a1 = args[0], args[1]

            if isinstance(a0, dict):
                payload = a0
                sender = a1
            elif isinstance(a1, dict):
                payload = a1
                sender = args[2] if len(args) >= 3 else a0
            else:
                # fallback: try kwargs
                payload = kwargs.get("message") or kwargs.get("data") or kwargs.get("payload")
                sender = kwargs.get("sender") or kwargs.get("sender_id")
        else:
            payload = kwargs.get("message") or kwargs.get("data") or kwargs.get("payload")
            sender = kwargs.get("sender") or kwargs.get("sender_id")

        if not self._dumped_first_app_message:
            self._dumped_first_app_message = True
            print(
                f"[debug:first on_app_message] bot={self._bot_name} "
                f"sender={sender!r} payload={payload!r} raw_args={args!r}"
            )
        return payload, str(sender)
            
    def _normalize_app_payload(self, msg: Any) -> Optional[dict[str, Any]]:
        """
        Normalize the various shapes we might see from Daily/Pipecat into the inner dict payload.
        """
        if not isinstance(msg, dict):
            return None

        # direct payload
        if msg.get("type") == "text" and "name" in msg:
            return msg

        # common wrappers
        inner = msg.get("message") or msg.get("data") or msg.get("payload")
        if isinstance(inner, dict) and inner.get("type") == "text" and "name" in inner:
            return inner

        return None