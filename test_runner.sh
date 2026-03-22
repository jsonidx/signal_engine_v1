#!/bin/bash
set -e

PROJECT_ROOT="/Users/jason/Documents/GitHub/signal_engine_v1"
cd "$PROJECT_ROOT"
source venv/bin/activate

echo "Python: $(which python3)"
echo "Pytest: $(python3 -m pytest --version)"
echo ""

python3 -m pytest tests/ -v --tb=short "$@"
