#!/bin/bash
# ============================================================
# SIGNAL ENGINE — MASTER REPORT GENERATOR
# ============================================================
# Runs ALL modules and produces ONE summary file you can
# drag & drop into Claude for analysis.
#
# USAGE:
#   cd ~/projects/signal_engine_v1
#   source venv/bin/activate
#   bash run_master.sh
#
# OUTPUT:
#   ~/projects/signal_engine_v1/signal_reports/signal_report_YYYYMMDD.txt
#
# TIME: ~5-8 minutes (SEC parsing is the slow part)
# ============================================================

PROJECT_DIR="$HOME/projects/signal_engine_v1"
VENV="$PROJECT_DIR/venv/bin/activate"
DATE=$(date +%Y%m%d)
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

# Output folder — inside the project directory
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

# ── STEP 1: Portfolio Status ──
echo "  [1/7] Portfolio status..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 1: PORTFOLIO STATUS" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 trade_journal.py --status >> "$REPORT" 2>&1

# ── STEP 2: Signal Engine (equity + BTC) ──
echo "  [2/7] Signal engine..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 2: EQUITY SIGNALS + BTC 200MA" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 signal_engine.py >> "$REPORT" 2>&1

# ── STEP 3: Paper Trader Snapshot ──
echo "  [3/7] Paper trader..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 3: PAPER TRADER SNAPSHOT" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 paper_trader.py --record >> "$REPORT" 2>&1

# ── STEP 4: Catalyst Screener (with SEC + Social) ──
echo "  [4/7] Catalyst screener (SEC + Social — this takes ~3 min)..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 4: CATALYST SCREENER (SEC + SOCIAL)" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 catalyst_screener.py --sec --social >> "$REPORT" 2>&1

# ── STEP 5: RSI Alerts ──
echo "  [5/7] RSI alerts..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 5: RSI EARLY WARNING ALERTS" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 catalyst_backtest.py --rsi-alert >> "$REPORT" 2>&1

# ── STEP 6: SEC Insider Scan (watchlist) ──
echo "  [6/7] SEC insider scan..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 6: SEC INSIDER SCAN (WATCHLIST)" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 sec_module.py --scan >> "$REPORT" 2>&1

# ── STEP 7: Trade Journal Zones ──
echo "  [7/7] Action zones..."
echo "" >> "$REPORT"
echo "================================================================" >> "$REPORT"
echo "  STEP 7: ACTION ZONES (OPEN POSITIONS)" >> "$REPORT"
echo "================================================================" >> "$REPORT"
python3 trade_journal.py --zones >> "$REPORT" 2>&1

# ── Footer ──
cat >> "$REPORT" << EOF

================================================================
  END OF REPORT
  Generated: $(date "+%Y-%m-%d %H:%M:%S")
  
  INSTRUCTIONS:
  Drag this file into Claude Desktop and ask:
  "Analyze my weekly signal report and tell me what to do"
================================================================
EOF

# ── Done ──
echo ""
echo "  ✅ MASTER REPORT COMPLETE"
echo "  📄 File: $REPORT"
echo "  📋 Size: $(wc -l < "$REPORT") lines"
echo ""
echo "  → Drag ~/projects/signal_engine_v1/signal_reports/signal_report_${DATE}.txt into Claude"
echo ""

# macOS notification
osascript -e 'display notification "Master report ready in signal_reports" with title "Signal Engine" sound name "Glass"' 2>/dev/null
