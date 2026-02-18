from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner

from pipecat.frames.frames import Frame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from pipecat.transports.daily.transport import (
    DailyTransport, 
    DailyParams,
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
        tname = type(frame).__name__
        if direction == FrameDirection.DOWSTREAM and tname == "InputAudioRawFrame":
            return # drop
        await self.push_frame(frame, direction)

class DailyVoiceTransport:
    def __init__(self, *, bot_name: str, cfg: DailyVoiceTransportConfig) -> None:
        self._bot_name = bot_name
        self._cfg = cfg

        params = DailyParams(
            transcription_enabled=cfg.transcription_enables,
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

        self._runner: Optional[PipelineRunner] = None
        self._task: Optional[PipelineTask] = None
        self._run_task: Optional[asyncio.Task] = None
        
        self._register_handlers()

        # TTS processor (start with OpenAI TTS for simplicity)
        self._tts = OpenAITTSService(
            api_key=cfg.openai_api_key, 
            voice=cfg.openai_voice,
        )

