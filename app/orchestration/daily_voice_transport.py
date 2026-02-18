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