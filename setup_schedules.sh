#!/bin/bash
# ============================================================
# SETUP AUTOMATED SCHEDULES
# ============================================================
# Run this ONCE on each machine to install the Sunday +
# Wednesday schedules. Plists are generated dynamically using
# the actual project path — no hardcoded paths in the repo.
#
# Usage: bash setup_schedules.sh
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
LAUNCH_DIR="$HOME/Library/LaunchAgents"

echo ""
echo "  ⚙️  Setting up automated signal engine schedules..."
echo "  Project: $PROJECT_DIR"
echo ""

# Create required directories
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/weekly_reports"
mkdir -p "$LAUNCH_DIR"

# Make scripts executable
chmod +x "$PROJECT_DIR/run_weekly.sh"
chmod +x "$PROJECT_DIR/run_midweek.sh"
echo "  ✓ Scripts made executable"

# ── Generate weekly plist ──────────────────────────────────
cat > "$LAUNCH_DIR/com.signalengine.weekly.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.signalengine.weekly</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PROJECT_DIR/run_weekly.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>0</integer>
        <key>Hour</key>
        <integer>19</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/logs/launchd_weekly.log</string>

    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/logs/launchd_weekly_err.log</string>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.pyenv/shims:$HOME/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PYENV_ROOT</key>
        <string>$HOME/.pyenv</string>
    </dict>
</dict>
</plist>
EOF
echo "  ✓ Generated com.signalengine.weekly.plist"

# ── Generate midweek plist ─────────────────────────────────
cat > "$LAUNCH_DIR/com.signalengine.midweek.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.signalengine.midweek</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PROJECT_DIR/run_midweek.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>3</integer>
        <key>Hour</key>
        <integer>20</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/logs/launchd_midweek.log</string>

    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/logs/launchd_midweek_err.log</string>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.pyenv/shims:$HOME/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PYENV_ROOT</key>
        <string>$HOME/.pyenv</string>
    </dict>
</dict>
</plist>
EOF
echo "  ✓ Generated com.signalengine.midweek.plist"

# ── Load into launchd ──────────────────────────────────────
launchctl unload "$LAUNCH_DIR/com.signalengine.weekly.plist" 2>/dev/null || true
launchctl unload "$LAUNCH_DIR/com.signalengine.midweek.plist" 2>/dev/null || true

launchctl load "$LAUNCH_DIR/com.signalengine.weekly.plist"
launchctl load "$LAUNCH_DIR/com.signalengine.midweek.plist"
echo "  ✓ Schedules loaded into launchd"

# ── Verify ─────────────────────────────────────────────────
echo ""
echo "  Verifying schedules..."
echo ""

if launchctl list | grep -q "com.signalengine.weekly"; then
    echo "  ✅ SUNDAY 7:00 PM    — Full pipeline (signals + paper trade + catalyst)"
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
echo "  Logs:    $PROJECT_DIR/logs/"
echo "  Reports: $PROJECT_DIR/weekly_reports/"
echo ""
echo "  Test now:      bash $PROJECT_DIR/run_weekly.sh"
echo "  Check status:  launchctl list | grep signalengine"
echo "  Stop Sunday:   launchctl unload ~/Library/LaunchAgents/com.signalengine.weekly.plist"
echo "  Stop Wednesday:launchctl unload ~/Library/LaunchAgents/com.signalengine.midweek.plist"
echo "  ────────────────────────────────────────"
echo ""
echo "  ⚠️  Your MacBook must be awake when the task runs."
echo "     If it's asleep, macOS will run it when you wake it up."
echo ""
echo "  Done! Run this script again on any new machine to set up schedules."
echo ""
