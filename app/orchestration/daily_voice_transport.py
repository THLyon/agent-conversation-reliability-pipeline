from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner

from pipecat.frames.frames import Frame, TTSSpeakFrame, InputAudioRawFrame
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
    """
    Prevent echo loops:
    - Drop inbound InputAudioRawFrame coming from transport.input()
    - Pass everything else through (TTSSpeakFrame, control frames, etc.)
    """
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, InputAudioRawFrame):
            return # drop inbound audio to prevent echo
        await self.push_frame(frame, direction)

class DailyVoiceTransport:
    def __init__(self, *, bot_name: str, cfg: DailyVoiceTransportConfig) -> None:
        self._bot_name = bot_name
        self._cfg = cfg

        params = DailyParams(
            transcription_enabled=cfg.transcription_enabled,
            audio_out_enabled=True,        # REQUIRED      
            #audio_out_sample_rate=24000,   # or None; defaults usually OK
            audio_out_channels=1,
            microphone_out_enabled=True,
            camera_out_enabled=False,
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

            #speaker identity fields vary; normalize
            speaker_name = (
                msg.get("participantName")
                or msg.get("speaker")
                or msg.get("name")
                or "unknown"
            )

            self._tx_inbox.put_nowait((str(speaker_name), text, is_final))

        @self._transport.event_handler("on_app_message")
        def _on_app_message(*args, **kwargs) -> None:
            msg = kwargs.get("message") or kwargs.get("data") or (args[0] if args else None)
            sender = kwargs.get("sender") or kwargs.get("sender_id") or (args[1] if len(args) > 1 else "unknown")
            if msg is None:
                return
            self._inbox.put_nowait((msg, str(sender)))

        @self._transport.event_handler("on_error")
        def _on_error(*args, **kwargs) -> None:
            print(f"[daily:error] bot={self._bot_name} args={args} kwargs={kwargs}")


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

        self._task = PipelineTask(pipeline)
        self._runner = PipelineRunner()
        self._run_task = asyncio.create_task(self._runner.run(self._task))
        await self.wait_joined()

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._task.cancel()
        await self.wait_left()

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
        return getattr(self._transport, "participant_id", None)
    
    async def send_control(self, payload: dict[str, Any]) -> None:
        if self._task is None:
            raise RuntimeError("Transport not started; call start() before send_control()")
        frame = DailyOutputTransportMessageFrame(payload)
        print(f"[control:sent] {self._bot_name} {payload}")
        await self._task.queue_frame(frame)

    async def wait_for_control_from(
        self,
        expected_name: str,
        expected_turn_id: int,
        *,
        timeout_s: float = 8.0,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            remaining = deadline - asyncio.get_running_loop().time()

            if remaining <= 0:
                raise TimeoutError(f"{self._bot_name} did not receive turn_done from {expected_name} turn_id={expected_turn_id}")

            msg, sender = await asyncio.wait_for(self._inbox.get(), timeout=remaining)
            self_id = self.participant_id()
            if self_id and sender == self_id:
                continue
            print(f"[control:recv] {self._bot_name} sender={sender} msg={msg}")

            if not isinstance(msg, dict):
                continue
            if msg.get("type") != "turn_done":
                continue
            if msg.get("name") != expected_name:
                continue
            if msg.get("turn_id") != expected_turn_id:
                continue
            return msg


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