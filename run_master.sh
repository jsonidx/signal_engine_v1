#!/bin/bash
# Signal Engine v1 — Master Pipeline
# Schedule:
#   Daily  06:00 Berlin — com.signalengine.skipai.daily    (--skip-ai, €0.00)
#   Monday 14:45 Berlin — com.signalengine.master.monday   (full run, ~€0.03–0.05)
#
# COST ESTIMATE (ai_quant.py --top-n 5):
#   ~5 Claude API calls × ~€0.005–0.01 = ~€0.03–0.05 per run (sonnet-4-6 + thinking)
#   2 runs/week = ~€0.24–0.40/month
#   To process more tickers: change --top-n 5 to --top-n 10
#   Full universe (no cap): python3 ai_quant.py --no-limit  ← WARNING: high cost
#
# USAGE:
#   bash run_master.sh             → full run including Claude API (~€0.03–0.05)
#   bash run_master.sh --skip-ai   → data refresh only, no API cost (€0.00)
#
# USE --skip-ai WHEN:
#   - You want fresh data without paying for Claude synthesis
#   - Running mid-week outside the Monday schedule
#   - Debugging pipeline steps without burning API credits
#   - The last Claude run was recent and thesis is still valid
#
# OMIT --skip-ai WHEN:
#   - It is your scheduled Monday run
#   - A major market event happened (earnings, macro print, squeeze)
#   - You opened or closed a position since the last run
#
# All outputs go to SQLite databases and JSON files in data/.
# View results at http://localhost:3000 (React dashboard → FastAPI on :8000).
#
# Open positions are read dynamically from trade_journal.db at runtime via
# _get_open_positions() in ai_quant.py. Static fallback: config.AI_QUANT_ALWAYS_INCLUDE

set -e  # exit immediately if any step fails

# ── Activate venv so all python3 calls use installed packages ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
  source "$SCRIPT_DIR/venv/bin/activate"
fi

mkdir -p "data"
mkdir -p "logs"

# ── Timing helpers ────────────────────────────────────────
PIPELINE_START=$(date +%s)
_timings=()

step_start() {
  _STEP_START=$(date +%s)
}

step_end() {
  local label="$1"
  local elapsed=$(( $(date +%s) - _STEP_START ))
  local mins=$(( elapsed / 60 ))
  local secs=$(( elapsed % 60 ))
  if [ $mins -gt 0 ]; then
    printf "  ⏱  %-45s %dm %02ds\n" "$label" "$mins" "$secs"
  else
    printf "  ⏱  %-45s %ds\n" "$label" "$secs"
  fi
  _timings+=("$(printf '%-45s %dm %02ds' "$label" "$mins" "$secs")")
}

# ── Flag parsing ──────────────────────────────────────────
SKIP_AI=false
for arg in "$@"; do
  case $arg in
    --skip-ai) SKIP_AI=true ;;
  esac
done

if [ "$SKIP_AI" = true ]; then
  echo "────────────────────────────────────────────────"
  echo " --skip-ai: Step 13 skipped. Cost this run: €0.00"
  echo " All data modules will run. Claude API will NOT."
  echo "────────────────────────────────────────────────"
  echo ""
fi

echo "================================================"
echo " Signal Engine v1 — $(date '+%Y-%m-%d %H:%M')"
echo "================================================"

# ── Step 0: Universe builder ──────────────────────────────
echo "Step 0: Building dynamic universe (Russell 1000/2000, S&P 500, Nasdaq 100)..."
step_start
python3 universe_builder.py --build-cache --update-watchlist
step_end "Step 0: Universe builder"
echo ""

# ── Step 1: Dark pool flow ────────────────────────────────
echo "Step 1: Scanning FINRA dark pool / short volume data..."
step_start
python3 dark_pool_flow.py --scan --output data/dark_pool_latest.json
step_end "Step 1: Dark pool flow"
echo ""

# ── Step 2: Regime filter ─────────────────────────────────
echo "Step 2: Computing market and sector regime..."
step_start
python3 regime_filter.py --compute --output data/regime_latest.json
step_end "Step 2: Regime filter"
echo ""

# ── Step 3: Signal engine ─────────────────────────────────
echo "Step 3: Running multi-factor equity screener (reads regime from Step 2)..."
step_start
python3 signal_engine.py
step_end "Step 3: Signal engine"
echo ""

# ── Step 4: Catalyst screener ─────────────────────────────
echo "Step 4: Running catalyst screener (dynamic universe)..."
step_start
python3 catalyst_screener.py --use-dynamic-universe --update-watchlist
step_end "Step 4: Catalyst screener"
echo ""

# ── Step 5: Options flow ──────────────────────────────────
echo "Step 5: Running options flow screener..."
step_start
python3 options_flow.py
step_end "Step 5: Options flow"
echo ""

# ── Step 6: Squeeze screener ──────────────────────────────
echo "Step 6: Running short squeeze screener..."
step_start
python3 squeeze_screener.py
step_end "Step 6: Squeeze screener"
echo ""

# ── Step 7: Fundamental analysis (extended: DCF + peer + quality) ─────────
echo "Step 7: Running fundamental analysis scorecard (extended mode)..."
step_start
python3 fundamental_analysis.py --watchlist --extended
step_end "Step 7: Fundamental analysis"
echo ""

# ── Step 8: SEC insider signals ───────────────────────────
echo "Step 8: Scanning SEC EDGAR (Form 4, 13D, 8-K)..."
step_start
python3 sec_module.py --scan
step_end "Step 8: SEC insider signals"
echo ""

# ── Step 8b: Red flag screener ────────────────────────────
echo "Step 8b: Running accounting red flag screener..."
step_start
python3 red_flag_screener.py --watchlist --skip-edgar
step_end "Step 8b: Red flag screener"
echo ""

# ── Step 11: Social sentiment ─────────────────────────────
echo "Step 11: Pre-warming social sentiment cache (Google Trends + StockTwits)..."
step_start
python3 social_sentiment.py --batch
step_end "Step 11: Social sentiment"
echo ""

# ── Step 12: Conflict resolver ────────────────────────────
echo "Step 12: Running deterministic conflict resolution layer..."
step_start
python3 conflict_resolver.py --pre-resolve --output data/resolved_signals.json
step_end "Step 12: Conflict resolver"
echo ""

# ── Step 13: AI Quant synthesis ───────────────────────────
# Results saved to ai_quant_cache.db — dashboard reads via /api/signals/thesis.
# Open positions are read dynamically from trade_journal.db at runtime.
# To override: python3 ai_quant.py --tickers AAPL,MSFT or --no-limit (high cost).
step_start
if [ "$SKIP_AI" = true ]; then
  echo "Step 13: SKIPPED — no Anthropic API calls (--skip-ai flag set)"
  python3 ai_quant.py --backfill-agreement
  step_end "Step 13: AI Quant (skipped, backfill only)"
  echo "Step 13 skipped — theses unchanged in ai_quant_cache.db"
else
  echo "Step 13: AI Quant synthesis (top 5 tickers — sonnet-4-6 + thinking)..."
  python3 ai_quant.py --top-n 5
  step_end "Step 13: AI Quant synthesis"
  echo "Step 13 complete — theses saved to ai_quant_cache.db"
fi
echo ""

# ── Step 13a: Archive candidates snapshot (no API cost) ───────────────────────
# Saves full priority-scored candidate list to Supabase candidate_snapshots.
# Accumulates historical data for future backtesting with real priority scores.
echo "Step 13a: Archiving candidate snapshot to Supabase..."
step_start
python3 -c "
from utils.ticker_selector import select_top_tickers
from utils.candidate_archive import archive_candidates
from ai_quant import _get_open_positions
import sys, traceback
try:
    open_pos = _get_open_positions()
    candidates = select_top_tickers(
        resolved_signals_path='data/resolved_signals.json',
        equity_signals_path=None,
        max_tickers=50,
        min_agreement=0.0,
        always_include=open_pos,
    )
    n = archive_candidates(candidates, open_positions=open_pos)
    print(f'  Archived {n} candidates to candidate_snapshots')
except Exception as e:
    print(f'  Archive skipped: {e}', file=sys.stderr)
    traceback.print_exc()
"
step_end "Step 13a: Candidate snapshot archive"
echo ""

# ── Step 13c: Daily Top-20 ranking ───────────────────────
# Loads yesterday's top-20 from Supabase, generates today's ranking,
# and upserts 20 rows into daily_rankings. No API cost.
echo "Step 13c: Generating daily Top-20 ranking and saving to Supabase..."
step_start
python3 -c "
from utils.ticker_selector import select_top_tickers
from utils.trade_selector_4w import run_daily_top20_pipeline
from ai_quant import _get_open_positions
import sys, traceback
try:
    open_pos = _get_open_positions()
    candidates = select_top_tickers(
        resolved_signals_path='data/resolved_signals.json',
        equity_signals_path=None,
        max_tickers=50,
        min_agreement=0.0,
        always_include=open_pos,
    )
    top20 = run_daily_top20_pipeline(candidates)
    print(f'  Top-20 ranking complete ({len(top20)} names)')
except Exception as e:
    print(f'  Top-20 ranking skipped: {e}', file=sys.stderr)
    traceback.print_exc()
"
step_end "Step 13c: Daily Top-20 ranking"
echo ""

# ── Step 13b: Thesis outcome checker ─────────────────────
# Checks if prior Claude predictions (targets / stops) were hit.
# Updates thesis_outcomes in ai_quant_cache.db — no API cost.
echo "Step 13b: Checking prior Claude thesis outcomes..."
step_start
python3 thesis_checker.py --verbose
step_end "Step 13b: Thesis outcome checker"
echo ""

# ── Step 14: Volume profile ───────────────────────────────
echo "Step 15: Computing volume profiles and VWAP levels..."
step_start
python3 volume_profile.py --watchlist
step_end "Step 15: Volume profile"
echo ""

# ── Step 16: Paper trader ─────────────────────────────────
echo "Step 16: Recording paper trading snapshot..."
step_start
python3 paper_trader.py --record
step_end "Step 16: Paper trader"
echo ""

# ── Step 17: Trade journal ────────────────────────────────
echo "Step 17: Updating trade journal and action zones..."
step_start
python3 trade_journal.py --update
step_end "Step 17: Trade journal"
echo ""

# ── Step 18: IV history collection ───────────────────────
echo "Step 18: Collecting and storing IV history for top tickers..."
step_start
python3 -c "
from utils.iv_calculator import collect_and_store_iv
from utils.ticker_selector import select_top_tickers
from ai_quant import _get_open_positions
import config

open_positions = _get_open_positions()

selected = select_top_tickers(
    resolved_signals_path='data/resolved_signals.json',
    equity_signals_path=None,
    max_tickers=config.AI_QUANT_MAX_TICKERS,
    always_include=open_positions
)
tickers = list({s['ticker'] for s in selected} | set(open_positions))
results = collect_and_store_iv(tickers)
print(f'IV stored for {len(results)} tickers (open positions: {open_positions})')
"
step_end "Step 18: IV history"
echo ""

# ── Step 19: Backtest (latest window + factor IC) ────────
echo "Step 19: Running backtest (latest window, factor IC, weight suggestions)..."
step_start
python3 backtest.py --run-latest --factor-ic --suggest-weights
step_end "Step 19: Backtest"
echo ""

# ── Post-run: thesis accuracy snapshot ───────────────────
echo ""
python3 thesis_checker.py --report --days 60

# ── Post-run: invalidate dashboard cache ─────────────────
echo "Invalidating dashboard cache (if running)..."
curl -s -X POST http://localhost:8000/api/cache/invalidate || true

# ── Timing summary ────────────────────────────────────────
PIPELINE_END=$(date +%s)
TOTAL=$(( PIPELINE_END - PIPELINE_START ))
TOTAL_MINS=$(( TOTAL / 60 ))
TOTAL_SECS=$(( TOTAL % 60 ))

echo ""
echo "================================================"
echo " Timing Summary"
echo "================================================"
for t in "${_timings[@]}"; do
  echo "  $t"
done
echo "------------------------------------------------"
printf "  %-45s %dm %02ds\n" "TOTAL" "$TOTAL_MINS" "$TOTAL_SECS"
echo "================================================"

echo ""
echo "================================================"
echo " Pipeline complete — $(date '+%Y-%m-%d %H:%M')"
if [ "$SKIP_AI" = true ]; then
  echo " Cost this run:  €0.00 (--skip-ai — Claude API skipped)"
else
  echo " Cost this run:  ~€0.03–0.05 (5 × sonnet-4-6 + thinking)"
fi
echo " Dashboard:      http://localhost:3000"
echo "================================================"
