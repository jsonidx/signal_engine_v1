#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
cd "$PROJECT_ROOT"

source venv/bin/activate

echo "Python: $(which python3)"
echo "Pytest: $(python3 -m pytest --version)"
echo ""

python3 -m pytest tests/ -v --tb=short "$@"
