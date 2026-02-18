from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from app.orchestration.daily_text_transport import DailyTextTransport, DailyTextTransportConfig

from app.orchestration.daily_voice_transport import (
    DailyVoiceTransport,
    DailyVoiceTransportConfig,
)

@dataclass(frozen=True)
class Step: 
    speaker: str # "teller" or "customer"
    text: str

SCENARIO = [
    Step("customer", "Hi—can you help me check my account balance?"),
    Step("teller", "Absolutely. Can you confirm your full name and last 4 digits of your SSN?"),
    Step("customer", "Sure. Tanner Lyon, last 4 is ****."),
    Step("teller", "Thanks. Your current balance is $2,413.18."),
    Step("customer", "What’s the overdraft fee if I go negative?"),
    Step("teller", "Our overdraft fee is $35 per item, up to 3 per day. You can opt into alerts to help avoid it."),
    Step("customer", "One more thing—what was that $19.99 charge yesterday?"),
    Step("teller", "That looks like a subscription merchant charge. If you don’t recognize it, I can help dispute it."),
    Step("customer", "That’s all—thanks. Goodbye!"),
    Step("teller", "You’re welcome. Have a great day—ending the session now."),
]

async def run_text_mvp() -> None:
    load_dotenv()
    logger.disable("pipecat.processors.frameworks.rtvi")

    room_url = os.getenv("DAILY_ROOM_URL", "").strip().strip('"')
    token = os.getenv("DAILY_TOKEN", "").strip().strip('"') or None
    if not room_url: 
        raise RuntimeError("DAILY_ROOM_URL is required in .env")
    
    teller = DailyTextTransport(
        bot_name="Bank Teller Bot",
        cfg=DailyTextTransportConfig(room_url=room_url, token=token),
    )
    customer = DailyTextTransport(
        bot_name = "Customer Bot",
        cfg=DailyTextTransportConfig(room_url=room_url, token=token),
    )

    #Start/Join FIRST
    await asyncio.gather(teller.start(), customer.start())
    await asyncio.sleep(0.5)
    turns_sent = 0
    turns_acked = 0
    exit_reason = "success"

    try: 
        t0 = time.perf_counter()

        for step in SCENARIO:
            if step.speaker == "customer":
                await customer.send_text(step.text)
                turns_sent += 1
                await teller.wait_for_text_from("Customer Bot", timeout_s=5)
                turns_acked += 1
            else:
                await teller.send_text(step.text)
                turns_sent += 1
                await customer.wait_for_text_from("Bank Teller Bot", timeout_s=5)
                turns_acked += 1


        dur_ms = int((time.perf_counter() - t0) * 1000)
        print(
            "\nPhase 1 Summary\n"
            f"- turns_sent: {turns_sent}\n"
            f"- turns_acked: {turns_acked}\n"
            f"- duration_ms: {dur_ms}\n"
            f"- exit_reason: {exit_reason}\n"
        )
    except TimeoutError as e:
        exit_reason = "timeout"
        print(f"\n[ERROR] timeout: {e}\n")
        raise
    finally:
        # Leave cleanly
        await asyncio.gather(teller.stop(), customer.stop())


async def run_audio_mvp() -> None:
    load_dotenv()
    logger.disable("pipecat.processors.frameworks.rtvi")

    room_url = os.getenv("DAILY_ROOM_URL", "").strip().strip('"')
    token = os.getenv("DAILY_TOKEN", "").strip().strip('"') or None
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip().strip('"') or None

    if not room_url:
        raise RuntimeError("DAILY_ROOM_URL is required in .env")
    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI TTS in Phase 2")

    teller = DailyVoiceTransport(
        bot_name="Bank Teller Bot",
        cfg=DailyVoiceTransportConfig(
            room_url=room_url,
            token=token,
            openai_api_key=openai_api_key,
            transcription_enabled=True,
        ),
    )
    customer = DailyVoiceTransport(
        bot_name="Customer Bot",
        cfg=DailyVoiceTransportConfig(
            room_url=room_url,
            token=token,
            openai_api_key=openai_api_key,
            transcription_enabled=True,
        ),
    )

    # Start/Join FIRST
    await asyncio.gather(teller.start(), customer.start())
    await asyncio.sleep(0.5)

    turns_sent = 0
    turns_acked = 0
    exit_reason = "success"
    turn_latency_ms: list[int] = []

    try:
        t0 = time.perf_counter()

        for step in SCENARIO:
            t_turn = time.perf_counter()

            if step.speaker == "customer":
                await customer.speak(step.text)
                turns_sent += 1

                await teller.wait_for_final_transcript_from(
                    expected_name="Customer Bot",
                    contains=step.text[:10],
                    timeout_s=12,
                )
                turns_acked += 1

            else:
                await teller.speak(step.text)
                turns_sent += 1

                await customer.wait_for_final_transcript_from(
                    expected_name="Bank Teller Bot",
                    contains=step.text[:10],
                    timeout_s=12,
                )
                turns_acked += 1

            turn_latency_ms.append(int((time.perf_counter() - t_turn) * 1000))

        dur_ms = int((time.perf_counter() - t0) * 1000)
        p50 = sorted(turn_latency_ms)[len(turn_latency_ms) // 2] if turn_latency_ms else -1
        p95 = sorted(turn_latency_ms)[max(0, int(len(turn_latency_ms) * 0.95) - 1)] if turn_latency_ms else -1

        print(
            "\nPhase 2 Summary\n"
            f"- turns_sent: {turns_sent}\n"
            f"- turns_acked: {turns_acked}\n"
            f"- duration_ms: {dur_ms}\n"
            f"- turn_latency_ms_p50: {p50}\n"
            f"- turn_latency_ms_p95: {p95}\n"
            f"- exit_reason: {exit_reason}\n"
        )

    except TimeoutError as e:
        exit_reason = "timeout"
        print(f"\n[ERROR] timeout: {e}\n")
        raise
    finally:
        # Leave cleanly
        await asyncio.gather(teller.stop(), customer.stop())

def main() -> None:
    mode = os.getenv("OUTRIVAL_MODE", "text").strip().lower()
    if mode == "audio":
        asyncio.run(run_audio_mvp())
    else:
        asyncio.run(run_text_mvp())

if __name__ == "__main__":
    main()