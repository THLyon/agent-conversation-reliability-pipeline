from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.daily.transport import DailyTransport

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

        #Event handlers (Pipecat DailyTransport supports these)
        @self._transport.event_handler("on_joined")
        def _on_joined(*args, **kwargs) -> None:
            self._joined.set()

        @self._transport.event_handler("on_left")
        def _on_left(*args, **kwargs) -> None:
            self._left.set()

        @self._transport.event_handler("on_app_message")
        def _on_app_message(message: Any, sender: str, *args, **kwargs) -> None:
            if self._on_app_message is None: 
                return
            # fan-in to async handler
            asyncio.create_task(self._on_app_message(message, sender))
    
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

        self._task = PipelineTask(pipeline)
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

    async def wait_joined(self, timeout_s: float = 15.0) -> None:
        try:
            await asyncio.wait_for(self._joined.wait(), timeout=timeout_s)
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"{self._bot_name} did not join within {timeout_s}s") from e

    async def wait_left(self, timeout_s: float = 15.0) -> None: 
        await asyncio.wait_for(self._left.wait(), timeout=timeout_s)
    
    async def send_text(self, text: str) -> None:
        # This sends to Daily "prebuild chat"; works as a text-channel MVP.
        # Pipecat exposes this helper on transport.
        err = await self._transport.send_prebuilt_chat_message(message=text, user_name=self._bot_name)
        if err: 
            raise RuntimeError(f"Daily send_prebuilt_chat_message error: {err}")
        print(f"[sent] {self._bot_name}")
        
    async def leave(self) -> None:
        # The actual leave is driven by stopping the transport via pipeline/task.
        # Here we just provide a hook for the orchestrator to call. 
        # We'll stop the pipeline/task which triggers on_left.
        return