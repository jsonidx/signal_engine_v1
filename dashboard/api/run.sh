#!/bin/bash
# ─── Signal Engine API — Development Server ────────────────────────────────
# Usage:  bash dashboard/api/run.sh
# Starts uvicorn with hot-reload on http://0.0.0.0:8000

set -e
cd "$(dirname "$0")"

# Activate virtualenv if present at project root
VENV="../../.venv/bin/activate"
if [ -f "$VENV" ]; then
    # shellcheck disable=SC1090
    source "$VENV"
fi

echo "Starting Signal Engine API on http://0.0.0.0:8000 ..."
uvicorn main:app --reload --host 0.0.0.0 --port 8000
