# agent-conversation-reliability-pipeline

Two Pipecat-based agents join a Daily.co WebRTC room, role-play a short scenario, and autonomously exit.

The system is built with a reliability-first architecture emphasizing:

- Deterministic conversation flow  
- Structured logging  
- Measurable SLIs (latency, completion rate, termination state)  
- Clean lifecycle transitions  

---

## What This Demo Does

- Joins a Daily WebRTC room  
- Runs 2 local agents (Service Provider + Customer)  
- Exchanges a short, intelligible conversation  
- Exits the room automatically  
- Prints a reliability run summary  

---

## Requirements

- Python 3.11+
- macOS or Linux recommended
- A Daily.co room URL
- Provider API keys depending on your LLM/STT/TTS pipeline

---

## Quickstart (Fresh Clone)

Install `uv` (one-time):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your terminal.

Clone and install:

```bash
git clone <YOUR_REPO_URL>
cd agent-conversation-reliability-pipeline

uv venv
uv sync --extra dev
```

Create `.env` in project root (see `.env.example`).

Run the demo:

```bash
uv run outrival-demo
```

---

## Common Commands

```bash
uv run outrival-demo
uv run python -m app.main
uv run ruff check .
uv run ruff format .
uv run pytest -q
```

Quick reference available in `info/common_commands.md`.

---

## Documentation

Architecture overview:
`info/architecture.md`

Quick command reference:
`info/common_commands.md`

---

## Commit Convention

This repository follows Conventional Commits:

https://www.conventionalcommits.org/

Examples:

- feat: add deterministic conversation state machine  
- fix: correct agent exit condition  
- perf: reduce turn latency instrumentation overhead  
- test: add conversation completion validation  
- chore: configure ruff + pytest tooling  
