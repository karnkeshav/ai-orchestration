#!/usr/bin/env bash
# Start the AI Orchestration Studio backend on port 8000
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ -f "$DIR/.venv/bin/python" ]; then
  PYTHON="$DIR/.venv/bin/python"
else
  PYTHON="python3"
fi

echo "🚀 Starting AI Orchestration Backend with Antigravity Engine on port 8000..."
exec "$PYTHON" -m uvicorn server:app --host 0.0.0.0 --port 8000
