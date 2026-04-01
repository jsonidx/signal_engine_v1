#!/bin/bash
# Starts the Signal Engine dashboard (API + frontend).
# Works on any machine — no hardcoded paths.

set -e

# Resolve the project root relative to this script's location,
# regardless of where the script is called from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

API_DIR="$PROJECT_ROOT/dashboard/api"
FRONTEND_DIR="$PROJECT_ROOT/dashboard/frontend"

# Activate venv if present
VENV="$PROJECT_ROOT/venv/bin/activate"
if [ -f "$VENV" ]; then
    source "$VENV"
fi

echo ""
echo "Project root: $PROJECT_ROOT"
echo ""

echo "Starting Signal Engine API..."
cd "$API_DIR"
uvicorn main:app --host 0.0.0.0 --port 8000 &
API_PID=$!

echo "Starting frontend dev server..."
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "Signal Engine dashboard running:"
echo "  API:       http://localhost:8000/docs"
echo "  Dashboard: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop both servers."

trap "kill $API_PID $FRONTEND_PID 2>/dev/null; echo 'Servers stopped.'" EXIT
wait
