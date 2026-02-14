from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from app.orchestration.daily_text_transport import DailyTextTransport, DailyTextTransportConfig

@dataclass(frozen=True)
class Step: 
    speaker: str # "teller" or "customer"
    text: str

SCENARIO = [
    Step("customer", "Hi—can you help me check my account balance?"),
    Step("teller", "Absolutely. Can you confirm your full name and last 4 digits of your SSN?"),
    Step("customer", "Sure. Tanner Lyon, last 4 is 1234."),
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

    # Minimal "bus": when one bot receives a message, we just print it.
    async def on_teller_msg(message: Any, sender: str) -> None: 
        print(f"[teller recv] from={sender} msg={message}")

    async def on_customer_msg(message: Any, sender: str) -> None:
        print(f"[customer recv] from={sender} msg={message}")

    teller.set_app_message_handler(on_teller_msg)
    customer.set_app_message_handler(on_customer_msg)

    # NOTE: In Phase 1, full Pipecat pipeline is not yet being build
    # I am validating "text message + scenerio timing" first.
    # Next commit will run these transports insise a PipelineTask so join/leave are managed cleanly. 

    # For now I just drive the deterministic script though the transports.  
    t0 = time.perf_counter()

    # Send alternating messages (this proves data channel / chat works).
    for idx, step in enumerate(SCENARIO, start=1):
        if step.speaker == "customer":
            await customer.send_text(step.text)
        else: 
            await teller.send_text(step.text)

        await asyncio.sleep(0.4) # tiny pacing so messages are intelligible

    dur_ms = int((time.perf_counter() - t0) * 1000)
    print(f"\n Phase 1 text MCP complete in {dur_ms}ms. (Next: manage join/leave via PipelineTask)\n")

def main() -> None:
    asyncio.run(run_text_mvp())