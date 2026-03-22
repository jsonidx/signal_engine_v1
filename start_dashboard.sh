#!/bin/bash
# Starts the Signal Engine dashboard (API + frontend)
set -e

echo "Starting Signal Engine API..."
cd /Users/jason/Documents/GitHub/signal_engine_v1/dashboard/api
uvicorn main:app --host 0.0.0.0 --port 8000 &
API_PID=$!

echo "Starting frontend dev server..."
cd /Users/jason/Documents/GitHub/signal_engine_v1/dashboard/frontend
npm run dev &
FRONTEND_PID=$!

echo ""
echo "Signal Engine dashboard running:"
echo "  API:      http://localhost:8000/docs"
echo "  Dashboard: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop both servers."

trap "kill $API_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
