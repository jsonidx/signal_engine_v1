#!/bin/bash
# Signal Engine v1 — Master Pipeline
# Schedule: Monday 14:45 Berlin time via launchd (com.signalengine.master.monday)
#
# COST ESTIMATE (ai_quant.py --top-n 5):
#   ~5 Claude API calls × ~€0.02–0.04 = ~€0.10–0.20 per run
#   2 runs/week = ~€0.80–1.60/month
#   To process more tickers: change --top-n 5 to --top-n 10
#   Full universe (no cap): python3 ai_quant.py --no-limit  ← WARNING: high cost
#
# USAGE:
#   bash run_master.sh             → full run including Claude API (~€0.10–0.20)
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
# Open positions are read dynamically from trade_journal.db at runtime via
# _get_open_positions() in ai_quant.py. Static fallback: config.AI_QUANT_ALWAYS_INCLUDE

set -e  # exit immediately if any step fails

REPORT_DIR="signal_reports"
DATE=$(date +%Y%m%d)
REPORT_FILE="$REPORT_DIR/signal_report_$DATE.txt"
mkdir -p "$REPORT_DIR"
mkdir -p "data"
mkdir -p "logs"

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
echo "" | tee "$REPORT_FILE"

# ── Step 0: Universe builder ──────────────────────────────
echo "Step 0: Building dynamic universe (Russell 1000/2000, S&P 500, Nasdaq 100)..."
python3 universe_builder.py --build-cache
echo "Step 0 complete." | tee -a "$REPORT_FILE"

# ── Step 1: Dark pool flow ────────────────────────────────
echo "Step 1: Scanning FINRA dark pool / short volume data..."
python3 dark_pool_flow.py --scan --output data/dark_pool_latest.json
echo "Step 1 complete." | tee -a "$REPORT_FILE"

# ── Step 2: Regime filter ─────────────────────────────────
echo "Step 2: Computing market and sector regime..."
python3 regime_filter.py --compute --output data/regime_latest.json
echo "Step 2 complete." | tee -a "$REPORT_FILE"

# ── Step 3: Signal engine ─────────────────────────────────
echo "Step 3: Running multi-factor equity screener (reads regime from Step 2)..."
python3 signal_engine.py --watchlist | tee -a "$REPORT_FILE"
echo "Step 3 complete." | tee -a "$REPORT_FILE"

# ── Step 4: Catalyst screener ─────────────────────────────
echo "Step 4: Running catalyst screener (dynamic universe)..."
python3 catalyst_screener.py --use-dynamic-universe | tee -a "$REPORT_FILE"
echo "Step 4 complete." | tee -a "$REPORT_FILE"

# ── Step 5: Options flow ──────────────────────────────────
echo "Step 5: Running options flow screener..."
python3 options_flow.py | tee -a "$REPORT_FILE"
echo "Step 5 complete." | tee -a "$REPORT_FILE"

# ── Step 6: Squeeze screener ──────────────────────────────
echo "Step 6: Running short squeeze screener..."
python3 squeeze_screener.py | tee -a "$REPORT_FILE"
echo "Step 6 complete." | tee -a "$REPORT_FILE"

# ── Step 7: Fundamental analysis ──────────────────────────
echo "Step 7: Running fundamental analysis scorecard..."
python3 fundamental_analysis.py | tee -a "$REPORT_FILE"
echo "Step 7 complete." | tee -a "$REPORT_FILE"

# ── Step 8: SEC insider signals ───────────────────────────
echo "Step 8: Scanning SEC EDGAR (Form 4, 13D, 8-K)..."
python3 sec_module.py | tee -a "$REPORT_FILE"
echo "Step 8 complete." | tee -a "$REPORT_FILE"

# ── Step 9: Congressional trades ─────────────────────────
echo "Step 9: Fetching congressional trade disclosures..."
python3 congress_trades.py | tee -a "$REPORT_FILE"
echo "Step 9 complete." | tee -a "$REPORT_FILE"

# ── Step 10: Polymarket ───────────────────────────────────
echo "Step 10: Fetching Polymarket prediction market signals..."
python3 polymarket_screener.py | tee -a "$REPORT_FILE"
echo "Step 10 complete." | tee -a "$REPORT_FILE"

# ── Step 11: Social sentiment ─────────────────────────────
echo "Step 11: Pre-warming social sentiment cache (Google Trends + StockTwits)..."
python3 social_sentiment.py --batch
echo "Step 11 complete." | tee -a "$REPORT_FILE"

# ── Step 12: Conflict resolver ────────────────────────────
echo "Step 12: Running deterministic conflict resolution layer..."
python3 conflict_resolver.py --pre-resolve --output data/resolved_signals.json
echo "Step 12 complete." | tee -a "$REPORT_FILE"

# ── Step 13: AI Quant synthesis — TOP 10 ONLY ─────────────
# Hard cap: Claude API called for top 10 tickers by priority score only.
# Open positions are read dynamically from trade_journal.db at runtime.
# Static fallback: config.AI_QUANT_ALWAYS_INCLUDE = ['GME', 'COIN', 'SAP']
# To override: python3 ai_quant.py --tickers AAPL,MSFT or --no-limit (high cost).
if [ "$SKIP_AI" = true ]; then
  echo "Step 13: SKIPPED — no Anthropic API calls (--skip-ai flag set)" \
    | tee -a "$REPORT_FILE"
  echo "  Last Claude synthesis: check ai_quant_cache.db for most recent run." \
    | tee -a "$REPORT_FILE"
  echo "  To run synthesis now: python3 ai_quant.py --top-n 5" \
    | tee -a "$REPORT_FILE"
  echo "Step 13 skipped." | tee -a "$REPORT_FILE"
else
  echo "Step 13: AI Quant synthesis (top 5 tickers — Claude API capped)..."
  python3 ai_quant.py --top-n 5 | tee -a "$REPORT_FILE"
  echo "Step 13 complete." | tee -a "$REPORT_FILE"
fi

# ── Step 14: Max pain ─────────────────────────────────────
echo "Step 14: Computing options max pain levels..."
python3 max_pain.py | tee -a "$REPORT_FILE"
echo "Step 14 complete." | tee -a "$REPORT_FILE"

# ── Step 15: Volume profile ───────────────────────────────
echo "Step 15: Computing volume profiles and VWAP levels..."
python3 volume_profile.py | tee -a "$REPORT_FILE"
echo "Step 15 complete." | tee -a "$REPORT_FILE"

# ── Step 16: Paper trader ─────────────────────────────────
echo "Step 16: Recording paper trading snapshot..."
python3 paper_trader.py --record | tee -a "$REPORT_FILE"
echo "Step 16 complete." | tee -a "$REPORT_FILE"

# ── Step 17: Trade journal ────────────────────────────────
echo "Step 17: Updating trade journal and action zones..."
python3 trade_journal.py --update | tee -a "$REPORT_FILE"
echo "Step 17 complete." | tee -a "$REPORT_FILE"

# ── Step 18: IV history collection ───────────────────────
echo "Step 18: Collecting and storing IV history for top tickers..."
python3 -c "
from utils.iv_calculator import collect_and_store_iv
from utils.ticker_selector import select_top_tickers
from ai_quant import _get_open_positions
import config

# Use dynamic open positions (live from trade_journal.db, fallback to config)
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
echo "Step 18 complete." | tee -a "$REPORT_FILE"

# ── Post-run: invalidate dashboard cache ─────────────────
echo "Invalidating dashboard cache (if running)..."
curl -s -X POST http://localhost:8000/api/cache/invalidate || true

echo ""
echo "================================================"
echo " Pipeline complete — $(date '+%Y-%m-%d %H:%M')"
echo " Report: $REPORT_FILE"
if [ "$SKIP_AI" = true ]; then
  echo " Cost this run:  €0.00 (--skip-ai — Claude API skipped)"
  echo " AI thesis:      NOT refreshed — using cached results"
else
  echo " Cost this run:  ~€0.10–0.20 (5 Claude API calls)"
  echo " AI thesis:      refreshed for top 5 tickers"
fi
echo "================================================"

# ── IMPORTANT: Step 13 uses --top-n 5. Do not change this line. ──
# Any future edits to run_master.sh must preserve: python3 ai_quant.py --top-n 5
# See config.py AI_QUANT_MAX_TICKERS to change the cap.
