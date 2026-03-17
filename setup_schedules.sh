#!/bin/bash
# ============================================================
# SETUP AUTOMATED SCHEDULES
# ============================================================
# Run this ONCE to install the Sunday + Wednesday schedules.
# After this, your signal engine runs automatically.
#
# Usage: bash setup_schedules.sh
# ============================================================

PROJECT_DIR="$HOME/projects/signal_engine_v1"
LAUNCH_DIR="$HOME/Library/LaunchAgents"

echo ""
echo "  ⚙️  Setting up automated signal engine schedules..."
echo ""

# Create directories
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/weekly_reports"

# Make scripts executable
chmod +x "$PROJECT_DIR/run_weekly.sh"
chmod +x "$PROJECT_DIR/run_midweek.sh"
echo "  ✓ Scripts made executable"

# Copy plist files to LaunchAgents
mkdir -p "$LAUNCH_DIR"
cp "$PROJECT_DIR/com.signalengine.weekly.plist" "$LAUNCH_DIR/"
cp "$PROJECT_DIR/com.signalengine.midweek.plist" "$LAUNCH_DIR/"
echo "  ✓ Schedule files copied to $LAUNCH_DIR"

# Unload if already loaded (ignore errors)
launchctl unload "$LAUNCH_DIR/com.signalengine.weekly.plist" 2>/dev/null
launchctl unload "$LAUNCH_DIR/com.signalengine.midweek.plist" 2>/dev/null

# Load schedules
launchctl load "$LAUNCH_DIR/com.signalengine.weekly.plist"
launchctl load "$LAUNCH_DIR/com.signalengine.midweek.plist"
echo "  ✓ Schedules loaded"

# Verify
echo ""
echo "  Verifying schedules..."
echo ""

if launchctl list | grep -q "com.signalengine.weekly"; then
    echo "  ✅ SUNDAY 7:00 PM  — Full pipeline (signals + paper trade + catalyst)"
else
    echo "  ❌ Weekly schedule failed to load. Check the plist file."
fi

if launchctl list | grep -q "com.signalengine.midweek"; then
    echo "  ✅ WEDNESDAY 8:00 PM — Midweek scan (catalyst + social)"
else
    echo "  ❌ Midweek schedule failed to load. Check the plist file."
fi

echo ""
echo "  ────────────────────────────────────────"
echo "  Logs will be saved to: $PROJECT_DIR/logs/"
echo "  Reports saved to:      $PROJECT_DIR/weekly_reports/"
echo ""
echo "  To test right now:     bash $PROJECT_DIR/run_weekly.sh"
echo "  To check status:       launchctl list | grep signalengine"
echo "  To stop Sunday:        launchctl unload ~/Library/LaunchAgents/com.signalengine.weekly.plist"
echo "  To stop Wednesday:     launchctl unload ~/Library/LaunchAgents/com.signalengine.midweek.plist"
echo "  ────────────────────────────────────────"
echo ""
echo "  ⚠️  Your MacBook must be awake when the task runs."
echo "     If it's asleep, macOS will run it when you wake it up."
echo ""
echo "  Done! You'll get a macOS notification when each run completes."
echo ""
