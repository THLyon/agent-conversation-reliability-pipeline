# Architecture Overview

## High-Level Flow:

Local Machine
    |
    ├── Bank Teller Agent
    ├── Customer Agent
    |
    └── Conversation Manager
            |
            └── Daily WebRTC Room (Pipecat)

## Lifecycle:

INIT → CONNECTED → ACTIVE → CLOSING → EXITED

## Reliability Layer:

- Structured JSON logs
- Turn latency measurement
- Completion tracking
- Graceful termination validation

## Separation of Concerns:

agents/           → behavior
orchestration/    → lifecycle
reliability/      → observability + metrics
config.py         → environment loading
main.py           → entrypoint



## Architecture planning notes:
app/
  main.py                      # entrypoint
  config.py                    # env + config loading
  agents/
    base_agent.py
    bank_teller.py
    customer.py
  orchestration/
    conversation_manager.py    # lifecycle + termination
  reliability/
    metrics.py                 # SLIs + run summary
    logging.py                 # structured logs
    slis.py                    # definitions + helpers
  utils/