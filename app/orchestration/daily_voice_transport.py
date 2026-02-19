from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner

from pipecat.frames.frames import Frame, TTSSpeakFrame, InputAudioRawFrame, StartFrame, EndFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection



from pipecat.transports.daily.transport import (
    DailyTransport, 
    DailyParams,
    DailyOutputTransportMessageFrame
)

from pipecat.services.openai.tts import OpenAITTSService # or DeepgramTTSService, CartesiaTTSService

@dataclass(frozen=True)
class DailyVoiceTransportConfig:
    room_url: str
    token: Optional[str] = None
    # Daily Transcription (Deepgram) is transport-level
    transcription_enabled: bool = True

    # TTS (pick one provider)
    openai_api_key: Optional[str] = None
    openai_voice: str = "alloy"

class DropInboundAudioFrames(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # IMPORTANT: let base class see StartFrame so this processor becomes "started"
        if isinstance(frame, StartFrame):
            await super().process_frame(frame, direction)
            return

        # Always pass EndFrame through
        if isinstance(frame, EndFrame):
            await self.push_frame(frame, direction)
            return

        # Drop inbound raw audio to prevent echo
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, InputAudioRawFrame):
            return

        await self.push_frame(frame, direction)

class DailyVoiceTransport:
    def __init__(self, *, bot_name: str, cfg: DailyVoiceTransportConfig) -> None:
        self._bot_name = bot_name
        self._cfg = cfg

        params = DailyParams(
            transcription_enabled=cfg.transcription_enabled,
            audio_out_enabled=True,
            audio_out_channels=1,
            microphone_out_enabled=True,
            camera_out_enabled=False,

            # try one of these (only ONE will be valid in your version)
            rtvi_enabled=False,
            # enable_rtvi=False,
            # use_rtvi=False,
        )


        self._transport = DailyTransport(
            room_url=cfg.room_url, 
            token=cfg.token, 
            bot_name=bot_name, 
            params=params,
        )

        # Transport lifecycle
        self._joined = asyncio.Event()
        self._left = asyncio.Event()

        #transcription inbox: (speaker_name, text, is_final)
        self._tx_inbox: asyncio.Queue[tuple[str, str, bool]] = asyncio.Queue()
        self._inbox: asyncio.Queue[tuple[Any, str]] = asyncio.Queue()

        self._runner: Optional[PipelineRunner] = None
        self._task: Optional[PipelineTask] = None
        self._run_task: Optional[asyncio.Task] = None
        
        self._register_handlers()

        # TTS processor (start with OpenAI TTS for simplicity)
        self._tts = OpenAITTSService(
            api_key=cfg.openai_api_key, 
            voice=cfg.openai_voice,
        )

    def _register_handlers(self) -> None:
        @self._transport.event_handler("on_joined")
        def _on_joined(*args, **kwargs) -> None: 
            self._joined.set()

        @self._transport.event_handler("on_left")
        def _on_left(*args, **kwargs) -> None:
            self._left.set()

        @self._transport.event_handler("on_transcription_message")
        def _on_transcription_message(*args, **kwargs) -> None:
            """
            Daily transport can emit transcription events when transcription_enabled = True
            The exact payload shape may vary; normalize defensively
            """
            msg = kwargs.get("message") or kwargs.get("data") or (args[0] if args else None)
            if not isinstance(msg, dict):
                return
            
            # Common fields seen in Daily transcription events:     
            text = (msg.get('text') or msg.get("transcript") or "").strip()
            if not text:
                return
            
            is_final = bool(msg.get("is_final") or msg.get("final") or msg.get("completed"))

            print(f"[tx:event] bot={self._bot_name} msg={msg}")

            #speaker identity fields vary; normalize
            speaker_name = (
                msg.get("participantName")
                or msg.get("participant_name")
                or msg.get("user_name")
                or msg.get("speaker")
                or msg.get("name")
                or msg.get("participantId")
                or msg.get("participant_id")
                or "unknown"
            )

            self._tx_inbox.put_nowait((str(speaker_name), text, is_final))

        @self._transport.event_handler("on_app_message")
        def _on_app_message(*args, **kwargs) -> None:
            print(f"[control:event_raw] bot={self._bot_name} args={args} kwargs={kwargs}")

            msg = None
            sender = "unknown"

            # Common shapes:
            # 1) (payload_dict, sender_id)
            # 2) (transport_obj, payload_dict, sender_id)
            # 3) kwargs: message/data + sender/sender_id
            if len(args) >= 2:
                a0, a1 = args[0], args[1]

                if isinstance(a0, dict):
                    # (payload, sender)
                    msg = a0
                    sender = args[1] if len(args) >= 2 else "unknown"

                elif isinstance(a1, dict):
                    # (transport, payload, sender)
                    msg = a1
                    sender = args[2] if len(args) >= 3 else "unknown"

            if msg is None:
                msg = kwargs.get("message") or kwargs.get("data") or kwargs.get("payload")

            if sender == "unknown":
                sender = kwargs.get("sender") or kwargs.get("sender_id") or "unknown"

            if msg is None:
                return

            print(f"[control:recv] bot={self._bot_name} sender={sender} msg={msg}")
            self._inbox.put_nowait((msg, str(sender)))



    async def start(self) -> None: 
        if self._run_task is not None:
            return
        
        # pipeline = Pipeline([
        #     self._transport.input(),
        #     DropInboundAudioFrames(),
        #     self._tts,
        #     self._transport.output(),
        # ])
        pipeline = Pipeline([
            self._transport.input(),
            self._tts,
            self._transport.output(),
        ])

        self._task = PipelineTask(pipeline, enable_rtvi=False)
        self._runner = PipelineRunner()
        self._run_task = asyncio.create_task(self._runner.run(self._task))
        await self.wait_joined()

        # wait up to ~2s for participant_id to become non-empty
        for _ in range(20):
            pid = self.participant_id()
            if pid:
                break
            await asyncio.sleep(0.1)

        print(f"[daily:joined] bot={self._bot_name} pid={self.participant_id()}")




    async def stop(self) -> None:
        if self._task is None:
            return

        # cancel pipeline
        await self._task.cancel()

        # force transport leave (so Daily room cleans up)
        leave_fn = getattr(self._transport, "leave", None)
        if callable(leave_fn):
            res = leave_fn()
            if asyncio.iscoroutine(res):
                await res

        # now wait for on_left
        try:
            await self.wait_left(timeout_s=10.0)
        except asyncio.TimeoutError:
            pass

        if self._run_task is not None:
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

    def participant_id(self) -> Optional[str]:
        attr = getattr(self._transport, "participant_id", None)
        try:
            pid = attr() if callable(attr) else attr
        except Exception:
            pid = None
        if isinstance(pid, str) and pid.strip():
            return pid
        return None
    
    async def send_control(self, payload: dict[str, Any]) -> None:
        print(f"[control:sent] bot={self._bot_name} pid={self.participant_id()} payload={payload}")

        # If you removed RTVIProcessor (fix #1), you can send the payload raw.
        # If you ever re-enable RTVI later, wrap it with an 'id' like below:
        # payload = {"id": str(uuid4()), "label": "rtvi-ai", "type": "control", "data": payload}

        send_fn = getattr(self._transport, "send_app_message", None)
        if callable(send_fn):
            res = send_fn(payload, "*")  # broadcast
            if asyncio.iscoroutine(res):
                await res
            return

        # Fallback: queue a transport message frame through the pipeline (if you want)
        if self._task is not None:
            frame = DailyOutputTransportMessageFrame(payload)
            await self._task.queue_frame(frame)
            return


        raise RuntimeError("No send_app_message available, and pipeline task not started")


    async def wait_for_control_from(self, expected_name: str, expected_turn_id: int, timeout_s: float = 8.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for turn_done from={expected_name} turn_id={expected_turn_id}")

            payload, sender = await asyncio.wait_for(self._inbox.get(), timeout=remaining)
            print(f"[control:dequeue] bot={self._bot_name} sender={sender} payload={payload}")

            # payload should be the dict you sent via send_app_message
            if not isinstance(payload, dict):
                continue

            if payload.get("type") != "turn_done":
                continue

            if payload.get("name") != expected_name:
                continue

            if payload.get("turn_id") != expected_turn_id:
                continue

            return payload



    async def speak(self, text: str) -> None:
        if self._task is None:
            raise RuntimeError("Transport not started; call start() before speak()")
        try:
            await self._task.queue_frame(TTSSpeakFrame(text))
        except Exception as e:
            print(f"[speak:error] bot={self._bot_name} err={e!r}")
            raise
        print(f"[speak] {self._bot_name}: {text}")


    async def wait_for_final_transcript_from(
            self,
            expected_name: str,
            *,
            contains: Optional[str] = None,
            timeout_s: float = 8.0,
    ) -> str:
        deadline = asyncio.get_running_loop().time() + timeout_s
        needle = (contains or "").lower().strip()

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"{self._bot_name} did not receive FINAL transcript from {expected_name} within {timeout_s}s"
                )
            speaker, text, is_final = await asyncio.wait_for(self._tx_inbox.get(), timeout=remaining)
            if speaker != expected_name:
                continue
            if not is_final:
                continue

            if needle and needle not in text.lower():
                continue
            return text