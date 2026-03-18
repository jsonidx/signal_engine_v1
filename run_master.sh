#!/bin/bash
# ============================================================
# SIGNAL ENGINE — MASTER REPORT GENERATOR
# ============================================================
# Runs ALL modules and produces ONE summary file you can
# drag & drop into Claude for analysis.
#
# USAGE:
#   cd ~/Documents/GitHub/signal_engine_v1
#   source venv/bin/activate
#   bash run_master.sh
#
# OUTPUT:
#   signal_reports/signal_report_YYYYMMDD.txt
#
# STEPS:
#    1. Portfolio status
#    2. Signal engine (equity + BTC 200MA)
#    3. Paper trader snapshot
#    4. Options flow screen (high-vol, unusual activity, expected move)
#    5. Catalyst screener (social + polymarket + congress + watchlist update)
#    6. RSI early warning alerts
#    7. Fundamental analysis (watchlist)
#    8. SEC insider + congressional trade scan (watchlist)
#    9. Per-ticker deep dives (TIER 1 + TIER 2) incl. AI quant per ticker
#    9b. AI quant portfolio briefing (Claude Opus 4.6 — requires API key)
#   10. Action zones (open positions)
#
# AI QUANT: Set ANTHROPIC_API_KEY to enable Claude analysis.
#           Cost: ~$0.10-0.40 per ticker + ~$0.50 for portfolio briefing.
#
# TIME: ~15-20 min without AI, ~25-35 min with AI quant enabled
# ============================================================

PROJECT_DIR="$HOME/Documents/GitHub/signal_engine_v1"
VENV="$PROJECT_DIR/venv/bin/activate"
DATE=$(date +%Y%m%d)
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

REPORT_DIR="$PROJECT_DIR/signal_reports"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/signal_report_${DATE}.txt"

cd "$PROJECT_DIR"
source "$VENV"

# Clear previous report
> "$REPORT"

cat >> "$REPORT" << EOF
================================================================
  SIGNAL ENGINE — WEEKLY MASTER REPORT
  Generated: $TIMESTAMP
  System: signal_engine_v1
================================================================

EOF

# ── STEP 1: Portfolio Status ────────────────────────────────
echo "  [1/10] Portfolio status..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 1: PORTFOLIO STATUS" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 trade_journal.py --status >> "$REPORT" 2>&1

# ── STEP 2: Signal Engine (equity + BTC) ────────────────────
echo "  [2/10] Signal engine..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 2: EQUITY SIGNALS + BTC 200MA" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 signal_engine.py >> "$REPORT" 2>&1

# ── STEP 3: Paper Trader Snapshot ───────────────────────────
echo "  [3/10] Paper trader..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 3: PAPER TRADER SNAPSHOT" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 paper_trader.py --record >> "$REPORT" 2>&1

# ── STEP 4: Options Flow Screen ─────────────────────────────
# Screens for high-IV, unusual options volume, expected move
echo "  [4/10] Options flow screen (~2 min)..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 4: OPTIONS FLOW SCREENER (HEAT RANKING)" >> "$REPORT"
echo "  Screens for: unusual options vol, IV rank, expected move," >> "$REPORT"
echo "  put/call ratio — targets high-movement candidates" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 options_flow.py --full --top 20 >> "$REPORT" 2>&1

# ── STEP 5: Catalyst Screener ───────────────────────────────
# Flags: --social (Reddit), --polymarket (prediction markets),
#        --congress (House + Senate STOCK Act trades),
#        --update-watchlist (re-rank watchlist.txt + append history)
echo "  [5/10] Catalyst screener (social + polymarket + congress + watchlist update — ~4 min)..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 5: CATALYST SCREENER (SOCIAL + POLYMARKET + CONGRESS)" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 catalyst_screener.py --social --polymarket --congress --update-watchlist >> "$REPORT" 2>&1

# ── STEP 6: RSI Alerts ──────────────────────────────────────
echo "  [6/10] RSI alerts..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 6: RSI EARLY WARNING ALERTS" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 catalyst_backtest.py --universe full >> "$REPORT" 2>&1

# ── STEP 7: Fundamental Analysis (watchlist) ────────────────
echo "  [7/10] Fundamental analysis (watchlist)..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 7: FUNDAMENTAL ANALYSIS (WATCHLIST)" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 fundamental_analysis.py --watchlist >> "$REPORT" 2>&1

# ── STEP 8: SEC + Congressional Scan ───────────────────────
echo "  [8/10] SEC + congressional scan..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 8: SEC INSIDER + CONGRESSIONAL TRADES (WATCHLIST)" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 sec_module.py --scan >> "$REPORT" 2>&1
echo "" >> "$REPORT"
echo "  Congressional Trade Scan:" >> "$REPORT"
python3 congress_trades.py --scan >> "$REPORT" 2>&1
python3 congress_trades.py --top-traders >> "$REPORT" 2>&1

# ── STEP 9: Per-Ticker Deep Dives ───────────────────────────
# Reads TIER 1 (≥50%) and TIER 2 (30–49%) tickers from watchlist.txt
# and runs full deep dive + AI quant analysis per ticker.
echo "  [9/10] Per-ticker deep dives (TIER 1 + TIER 2)..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 9: PER-TICKER DEEP DIVES" >> "$REPORT"
echo "================================================================" >> "$REPORT"

# Extract tickers from TIER 1 and TIER 2 sections of watchlist.txt
# Lines in those sections look like: "GME        # ↑3  65% | ..."
# We stop collecting when we hit TIER 3 or MANUALLY ADDED.
DEEP_DIVE_TICKERS=""
IN_DEEP_TIER=0

while IFS= read -r line; do
    # Detect section headers
    if echo "$line" | grep -qE "TIER 1|TIER 2"; then
        IN_DEEP_TIER=1
        continue
    fi
    if echo "$line" | grep -qE "TIER 3|MANUALLY ADDED"; then
        IN_DEEP_TIER=0
        continue
    fi
    # Skip comment-only lines and blank lines
    if echo "$line" | grep -qE '^\s*#|^\s*$'; then
        continue
    fi
    # Extract ticker (first word before any whitespace or #)
    if [ "$IN_DEEP_TIER" -eq 1 ]; then
        TICKER=$(echo "$line" | awk '{print $1}' | tr -d '[:space:]')
        if [ -n "$TICKER" ]; then
            DEEP_DIVE_TICKERS="$DEEP_DIVE_TICKERS $TICKER"
        fi
    fi
done < "$PROJECT_DIR/watchlist.txt"

# Fallback: if watchlist uses old format (no tiers), take first 5 tickers
if [ -z "$DEEP_DIVE_TICKERS" ]; then
    DEEP_DIVE_TICKERS=$(grep -v '^\s*#' "$PROJECT_DIR/watchlist.txt" \
        | grep -v '^\s*$' \
        | awk '{print $1}' \
        | head -5 | tr '\n' ' ')
fi

if [ -z "$DEEP_DIVE_TICKERS" ]; then
    echo "  No tickers found for deep dive." >> "$REPORT"
else
    echo "  Deep dive tickers:$DEEP_DIVE_TICKERS" >> "$REPORT"
    echo "" >> "$REPORT"

    for TICKER in $DEEP_DIVE_TICKERS; do
        echo "  → Deep dive: $TICKER"

        echo "────────────────────────────────────────────────────────" >> "$REPORT"
        echo "  DEEP DIVE: $TICKER" >> "$REPORT"
        echo "────────────────────────────────────────────────────────" >> "$REPORT"

        echo "  [$TICKER] Options flow..." >> "$REPORT"
        python3 options_flow.py --ticker "$TICKER" >> "$REPORT" 2>&1

        echo "" >> "$REPORT"
        echo "  [$TICKER] Catalyst signals..." >> "$REPORT"
        python3 catalyst_screener.py --ticker "$TICKER" --social --polymarket --congress >> "$REPORT" 2>&1

        echo "" >> "$REPORT"
        echo "  [$TICKER] Pattern study (3yr history)..." >> "$REPORT"
        python3 catalyst_backtest.py --ticker "$TICKER" --verbose >> "$REPORT" 2>&1

        echo "" >> "$REPORT"
        echo "  [$TICKER] Fundamentals..." >> "$REPORT"
        python3 fundamental_analysis.py --ticker "$TICKER" >> "$REPORT" 2>&1

        echo "" >> "$REPORT"
        echo "  [$TICKER] SEC filings..." >> "$REPORT"
        python3 sec_module.py --ticker "$TICKER" >> "$REPORT" 2>&1

        echo "" >> "$REPORT"

        # AI Quant analysis (requires ANTHROPIC_API_KEY)
        if [ -n "$ANTHROPIC_API_KEY" ]; then
            echo "  [$TICKER] AI quant analysis..." >> "$REPORT"
            python3 ai_quant.py --ticker "$TICKER" >> "$REPORT" 2>&1
            echo "" >> "$REPORT"
        fi
    done
fi

# ── STEP 9b: AI Quant — Portfolio-Level Analysis ────────────
# Only runs if ANTHROPIC_API_KEY is set
if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "  [9b/10] AI quant — portfolio briefing (Claude Opus 4.6)..."
    echo "" >> "$REPORT"
    echo "================================================================" >> "$REPORT"
    echo "  STEP 9b: AI QUANT — PORTFOLIO BRIEFING (CLAUDE OPUS 4.6)" >> "$REPORT"
    echo "  Powered by adaptive thinking — synthesizes ALL signals above" >> "$REPORT"
    echo "================================================================" >> "$REPORT"
    python3 ai_quant.py --report "$REPORT" >> "$REPORT" 2>&1
else
    echo "  [9b/10] AI quant skipped (ANTHROPIC_API_KEY not set)"
    echo "" >> "$REPORT"
    echo "  STEP 9b: AI QUANT — SKIPPED (set ANTHROPIC_API_KEY to enable)" >> "$REPORT"
fi

# ── STEP 10: Trade Journal Zones ────────────────────────────
echo "  [10/10] Action zones..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 10: ACTION ZONES (OPEN POSITIONS)" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 trade_journal.py --zones >> "$REPORT" 2>&1

# ── Footer ──────────────────────────────────────────────────
cat >> "$REPORT" << EOF

================================================================
  END OF REPORT
  Generated: $(date "+%Y-%m-%d %H:%M:%S")

  WHAT THIS REPORT CONTAINS:
    - Portfolio & paper trader status
    - Broad equity + BTC signal scan (z-score factors)
    - Options flow heat ranking (IV rank, unusual vol, expected move)
    - Catalyst screener composite scores (social + polymarket + congress)
    - Watchlist re-ranked by composite score (watchlist.txt updated)
    - Fundamental scorecard: valuation, growth, quality, balance
    - SEC insider activity scan (Form 4, 13D, 8-K, 13F)
    - Congressional trade scan (House + Senate, incl. spouse trades)
    - Most active politicians leaderboard
    - Per-ticker deep dives (TIER 1 + TIER 2 only):
        • Options flow heat (IV rank, expected move, put/call)
        • Catalyst signals + social + polymarket + congress
        • 3-year pattern study (hit rates, forward returns)
        • Full fundamental breakdown
        • SEC Form 4 / 13D / 8-K filings
        • AI quant thesis (if ANTHROPIC_API_KEY set)
    - AI quant portfolio briefing (Claude Opus 4.6, if API key set)
    - Action zones for open positions

  HOW TO USE:
    The AI quant (Step 9b) already analyzed this report in-line above.
    For additional context, drag this file into Claude Desktop and ask:
    "Act as a quant PM. What are your top 3 trade ideas from this report
     with specific entry, stop, and target levels?"
================================================================
EOF

# ── Done ────────────────────────────────────────────────────
echo ""
echo "  ✅ MASTER REPORT COMPLETE"
echo "  📄 File: $REPORT"
echo "  📋 Lines: $(wc -l < "$REPORT")"
echo ""
echo "  Deep dive tickers covered: $DEEP_DIVE_TICKERS"
if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "  AI quant: enabled (Claude Opus 4.6)"
else
    echo "  AI quant: disabled (set ANTHROPIC_API_KEY to enable)"
fi
echo ""
echo "  → Drag $REPORT into Claude"
echo ""

osascript -e 'display notification "Master report ready — deep dives complete" with title "Signal Engine" sound name "Glass"' 2>/dev/null
