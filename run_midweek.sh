#!/bin/bash
# ============================================================
# MIDWEEK SCAN — Automated Runner
# ============================================================
# Runs every Wednesday at 20:00 via macOS launchd
# Lighter scan: catalyst screener + social only
# ============================================================

PROJECT_DIR="$HOME/projects/signal_engine_v1"
VENV="$PROJECT_DIR/venv/bin/activate"
LOG_DIR="$PROJECT_DIR/logs"
DATE=$(date +%Y%m%d)
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/midweek_${DATE}.log"

echo "========================================" >> "$LOG_FILE"
echo "  Midweek Scan: $TIMESTAMP" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

source "$VENV"
cd "$PROJECT_DIR"

echo "[STEP 1] Running midweek_scan.py..." >> "$LOG_FILE"
python3 midweek_scan.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
echo "[STEP 1] Exit code: $EXIT_CODE" >> "$LOG_FILE"

echo "" >> "$LOG_FILE"
echo "  COMPLETE: $(date "+%Y-%m-%d %H:%M:%S")" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

osascript -e "display notification \"Midweek scan complete. Check weekly_reports/\" with title \"Midweek Scan\" sound name \"Glass\""
