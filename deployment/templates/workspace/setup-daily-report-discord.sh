#!/bin/bash
# Daily Weather & Sports Report - Discord Setup
# Sends formatted report to Discord at 9:00 AM EST (14:00 UTC) daily

OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
SCRIPT_PATH="$OPENCLAW_HOME/workspace/daily-report-discord.py"

echo "🤖 Setting up Daily Weather + Sports Report for Discord"
echo "========================================================"
echo

# Check if script exists
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "❌ Error: $SCRIPT_PATH not found"
    exit 1
fi

# Make script executable
chmod +x "$SCRIPT_PATH"

# Create systemd timer for 9:00 AM EST (14:00 UTC)
TIMER_DIR="$HOME/.config/systemd/user"
mkdir -p "$TIMER_DIR"

cat > "$TIMER_DIR/daily-report.service" << EOF
[Unit]
Description=OpenClaw Daily Weather & Sports Report
After=network.target

[Service]
Type=oneshot
Environment="DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN"
Environment="DISCORD_CHANNEL_ID=${DISCORD_CHANNEL_ID:-1469940490773332079}"
WorkingDirectory=$OPENCLAW_HOME/workspace
ExecStart=/usr/bin/python3 $SCRIPT_PATH
StandardOutput=append:$OPENCLAW_HOME/workspace/daily-report.log
StandardError=append:$OPENCLAW_HOME/workspace/daily-report.log

[Install]
WantedBy=default.target
EOF

cat > "$TIMER_DIR/daily-report.timer" << 'EOF'
[Unit]
Description=Daily Report Timer (9:00 AM EST / 14:00 UTC)
Requires=daily-report.service

[Timer]
# Run at 9:00 AM EST (14:00 UTC) daily
OnCalendar=*-*-* 14:00:00
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
EOF

# Reload systemd and enable timer
systemctl --user daemon-reload
systemctl --user enable daily-report.timer
systemctl --user start daily-report.timer

echo "✅ Systemd timer configured"
echo

# Also add cron job as backup
CRON_ENTRY="0 14 * * * cd $OPENCLAW_HOME/workspace && /usr/bin/python3 $SCRIPT_PATH >> $OPENCLAW_HOME/workspace/daily-report.log 2>&1"

if ! crontab -l 2>/dev/null | grep -qF "daily-report-discord.py"; then
    (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
    echo "✅ Cron backup configured"
else
    echo "✅ Cron already exists"
fi

echo
echo "✨ Setup Complete!"
echo
echo "📋 Configuration:"
echo "   Time: 9:00 AM EST (14:00 UTC) daily"
echo "   Channel: $DISCORD_CHANNEL_ID"
echo "   Script: $SCRIPT_PATH"
echo "   Log: $OPENCLAW_HOME/workspace/daily-report.log"
echo
echo "🔍 Check status:"
echo "   systemctl --user status daily-report.timer"
echo
echo "⏰ Next scheduled run:"
systemctl --user list-timers | grep daily-report
echo
echo "🧪 Test now:"
echo "   python3 $SCRIPT_PATH"
echo
echo "📊 Report includes:"
echo "   ☀️ Freehold, NJ weather (temp, wind, precipitation)"
echo "   🏈 NFL games and playoffs"
echo "   🏒 NHL games with start times"
echo "   ⚾ MLB spring training schedule"
echo "   🥊 UFC upcoming events"
echo
echo "🎯 All reports sent directly to Discord - no more SMS issues!"
