#!/bin/bash
# ============================================================
# WEEKLY SIGNAL ENGINE — Automated Runner
# ============================================================
# Runs every Sunday at 19:00 via macOS launchd
#
# What it does:
#   1. Activates the Python virtual environment
#   2. Runs signal_engine.py (equity + BTC signals)
#   3. Runs paper_trader.py --record (logs snapshot)
#   4. Runs catalyst_screener.py --social (catalyst + Reddit)
#   5. Saves a combined log to weekly_reports/
#
# Logs: ~/projects/signal_engine_v1/logs/
# ============================================================

PROJECT_DIR="$HOME/projects/signal_engine_v1"
VENV="$PROJECT_DIR/venv/bin/activate"
LOG_DIR="$PROJECT_DIR/logs"
REPORT_DIR="$PROJECT_DIR/weekly_reports"
DATE=$(date +%Y%m%d)
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

# Create directories
mkdir -p "$LOG_DIR"
mkdir -p "$REPORT_DIR"

LOG_FILE="$LOG_DIR/run_${DATE}.log"

echo "========================================" >> "$LOG_FILE"
echo "  Signal Engine Run: $TIMESTAMP" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# Activate venv
source "$VENV"

# Step 1: Signal Engine
echo "" >> "$LOG_FILE"
echo "[STEP 1] Running signal_engine.py..." >> "$LOG_FILE"
cd "$PROJECT_DIR"
python3 signal_engine.py >> "$LOG_FILE" 2>&1
SIGNAL_EXIT=$?
echo "[STEP 1] Exit code: $SIGNAL_EXIT" >> "$LOG_FILE"

# Step 2: Paper Trader
echo "" >> "$LOG_FILE"
echo "[STEP 2] Running paper_trader.py --record..." >> "$LOG_FILE"
python3 paper_trader.py --record >> "$LOG_FILE" 2>&1
PAPER_EXIT=$?
echo "[STEP 2] Exit code: $PAPER_EXIT" >> "$LOG_FILE"

# Step 3: Catalyst Screener with Social
echo "" >> "$LOG_FILE"
echo "[STEP 3] Running catalyst_screener.py --social..." >> "$LOG_FILE"
python3 catalyst_screener.py --social >> "$LOG_FILE" 2>&1
CATALYST_EXIT=$?
echo "[STEP 3] Exit code: $CATALYST_EXIT" >> "$LOG_FILE"

# Step 4: Trade Journal — zones + open position P&L
echo "" >> "$LOG_FILE"
echo "[STEP 4] Running trade_journal.py --report..." >> "$LOG_FILE"
python3 trade_journal.py --report >> "$LOG_FILE" 2>&1
JOURNAL_EXIT=$?
echo "[STEP 4] Exit code: $JOURNAL_EXIT" >> "$LOG_FILE"

# Summary
echo "" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"
echo "  COMPLETE: $(date "+%Y-%m-%d %H:%M:%S")" >> "$LOG_FILE"
echo "  signal_engine: $SIGNAL_EXIT" >> "$LOG_FILE"
echo "  paper_trader:  $PAPER_EXIT" >> "$LOG_FILE"
echo "  catalyst:      $CATALYST_EXIT" >> "$LOG_FILE"
echo "  trade_journal: $JOURNAL_EXIT" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# macOS notification when done
osascript -e "display notification \"Signal engine run complete. Check weekly_reports/\" with title \"Signal Engine\" sound name \"Glass\""

echo "Done. Log: $LOG_FILE"
