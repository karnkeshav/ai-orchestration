#!/usr/bin/env bash
# Start the AI Orchestration Studio backend on port 8000
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if command -v uv >/dev/null 2>&1; then
  echo "🚀 Starting AI Orchestration Backend with uv on port 8000..."
  exec uv run --with-requirements requirements.txt uvicorn server:app --host 0.0.0.0 --port 8000
elif [ -f "$HOME/.local/bin/uv" ]; then
  echo "🚀 Starting AI Orchestration Backend with uv on port 8000..."
  exec "$HOME/.local/bin/uv" run --with-requirements requirements.txt uvicorn server:app --host 0.0.0.0 --port 8000
elif [ -f "$DIR/.venv/bin/python" ]; then
  PYTHON="$DIR/.venv/bin/python"
  echo "🚀 Starting AI Orchestration Backend with .venv on port 8000..."
  exec "$PYTHON" -m uvicorn server:app --host 0.0.0.0 --port 8000
else
  PYTHON="python3"
  echo "🚀 Starting AI Orchestration Backend with system python on port 8000..."
  exec "$PYTHON" -m uvicorn server:app --host 0.0.0.0 --port 8000
fi
