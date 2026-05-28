#!/usr/bin/env bash
# Local verification gate for AI-assisted changes.
# Default mode runs deterministic offline checks and avoids paid LLM calls.
# Use --full to run the complete pytest suite, including live DB/integration tests.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/dashboard/frontend"
MODE="${1:-offline}"

PYTEST_OFFLINE_TARGETS=(
  "tests/test_ai_quant_schema.py"
  "tests/test_backtest.py"
  "tests/test_conflict_resolver.py"
  "tests/test_dark_pool_flow.py"
  "tests/test_environment.py"
  "tests/test_options_iv_integration.py"
  "tests/test_prob_engine.py"
  "tests/test_regime_filter.py"
  "tests/test_sec_module.py"
  "tests/test_signal_upgrades.py"
  "tests/test_social_sentiment.py"
  "tests/test_squeeze_alerts.py"
  "tests/test_squeeze_risk_analyzer.py"
  "tests/test_squeeze_state_machine.py"
  "tests/test_ticker_selector.py"
)

cd "$ROOT_DIR"

if [ -f "$ROOT_DIR/venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/venv/bin/activate"
fi

echo "==> Python tests"
if [ "$MODE" = "--full" ] || [ "$MODE" = "full" ]; then
  pytest
else
  pytest "${PYTEST_OFFLINE_TARGETS[@]}"
fi

if [ -f "$FRONTEND_DIR/package.json" ]; then
  echo "==> Frontend tests"
  cd "$FRONTEND_DIR"
  npm test

  echo "==> Frontend build"
  npm run build
fi

echo "==> Import smoke checks"
cd "$ROOT_DIR"
python3 - <<'PY'
import importlib

modules = [
    "config",
    "conflict_resolver",
    "regime_filter",
    "utils.ticker_selector",
    "dashboard.api.main",
]

for name in modules:
    importlib.import_module(name)
    print(f"ok {name}")
PY

echo "Verification complete."
