================================
        Common Commands
================================

uv venv
uv sync --extra dev

uv run outrival-demo          # run the two-bot demo
uv run python -m app.main     # run via module path (if you prefer)
uv run ruff check .           # lint
uv run ruff format .          # format
uv run pytest -q              # tests

================================
     Terminal setup commands
================================
# 1) Confirm python version
python3 --version

# 2) Install uv if you haven't
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3) Create a venv (creates .venv)
uv venv

# 4) Install deps based on pyproject + lock (if present)
uv sync

# 5) Run your entrypoint via script
uv run outrival-demo
