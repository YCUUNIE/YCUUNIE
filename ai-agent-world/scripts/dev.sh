#!/usr/bin/env bash
# Start backend + frontend together (macOS / Linux).
set -e
cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
  echo "Creating Python venv…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r backend/requirements.txt

if [ ! -d "frontend/node_modules" ]; then
  echo "Installing frontend deps…"
  npm --prefix frontend install
fi

# Run backend in the background, frontend in the foreground.
echo "Starting backend on http://127.0.0.1:8000 …"
python -m backend.app.main &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null' EXIT

echo "Starting frontend on http://127.0.0.1:5173 …"
npm --prefix frontend run dev
