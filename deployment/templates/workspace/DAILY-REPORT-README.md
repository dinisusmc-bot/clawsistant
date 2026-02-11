# Daily Weather & Sports Report - Discord

## Overview
Automated daily report sent to Discord at **9:00 AM EST (14:00 UTC)** every morning with weather and sports information.

## What It Includes

### ☀️ Weather (Freehold, NJ)
- Current conditions
- Temperature (with wind chill if applicable)
- Wind speed
- Precipitation probability
- Umbrella alert if needed

### 🏈 NFL
- Game schedules with start times
- Playoff games
- Championship and Super Bowl dates

### 🏒 NHL
- Today's games with matchups and times
- Live schedule from NHL API

### ⚾ MLB
- Spring Training schedule (late Feb - March)
- Regular season start date
- Offseason information

### 🥊 UFC
- Upcoming events
- Fight cards

## Setup

The report is automatically configured and runs via:
1. **Systemd Timer** (primary) - Runs at 14:00 UTC daily
2. **Cron Job** (backup) - Runs at 14:00 UTC daily

### Files
- **Script**: `/home/bot/.openclaw/workspace/daily-report-discord.py`
- **Setup**: `/home/bot/.openclaw/workspace/setup-daily-report-discord.sh`
- **Service**: `~/.config/systemd/user/daily-report.service`
- **Timer**: `~/.config/systemd/user/daily-report.timer`
- **Log**: `/home/bot/.openclaw/workspace/daily-report.log`

## Management

### Check Status
```bash
systemctl --user status daily-report.timer
```

### View Next Run Time
```bash
systemctl --user list-timers | grep daily-report
```

### View Logs
```bash
tail -f ~/.openclaw/workspace/daily-report.log
```

### Test Immediately
```bash
DISCORD_BOT_TOKEN="your-token" DISCORD_CHANNEL_ID="your-channel" \
  python3 ~/.openclaw/workspace/daily-report-discord.py
```

### Manually Trigger
```bash
systemctl --user start daily-report.service
```

### Change Schedule
Edit the timer:
```bash
systemctl --user edit daily-report.timer
```

Change the `OnCalendar` line:
```ini
OnCalendar=*-*-* 14:00:00  # 9:00 AM EST
```

Then reload:
```bash
systemctl --user daemon-reload
systemctl --user restart daily-report.timer
```

### Disable
```bash
systemctl --user stop daily-report.timer
systemctl --user disable daily-report.timer
```

### Re-enable
```bash
systemctl --user enable daily-report.timer
systemctl --user start daily-report.timer
```

## Configuration

Environment variables in systemd service:
- `DISCORD_BOT_TOKEN`: Your Discord bot token
- `DISCORD_CHANNEL_ID`: Target channel ID (default: 1469940490773332079)

## Troubleshooting

### Report Not Sending
1. Check timer status: `systemctl --user status daily-report.timer`
2. Check service status: `systemctl --user status daily-report.service`
3. View logs: `tail -50 ~/.openclaw/workspace/daily-report.log`
4. Test manually with the test command above

### Wrong Time
- Remember EST = UTC-5 (or UTC-4 during DST)
- Timer uses UTC: 14:00 UTC = 9:00 AM EST

### Missing Sports Data
- NHL API may be slow or unavailable - script has fallbacks
- Other sports use hardcoded recent schedules for reliability

### Discord Errors
- Check bot token is valid
- Verify bot has permissions in the channel
- Confirm channel ID is correct

## Sample Output

```markdown
# 📅 Daily Report • Monday, February 9, 2026

## ☀️ Weather - Freehold, NJ
**Current**: ☀️ +33°F (wind chill: 18°F)
**Temperature**: 26°F
**Wind**: 8 mph
**Precipitation**: 0% chance

## 🏟️ Sports Schedule

🏈 **NFL Playoffs**
• 49ers @ Lions - 6:30 PM ET (NFC Championship)

🏒 **NHL (Today)**
• Devils @ Flyers - 7:00 PM ET
• Rangers @ Islanders - 7:30 PM ET

⚾ **MLB**
• Offseason - Spring training starts Feb 28

🥊 **UFC (Upcoming)**
• Feb 15: UFC 300 - Main Event TBA
• Feb 22: UFC Fight Night
```

## Migration from SMS

**Old system issues (FIXED):**
- ❌ Split into 6+ SMS messages
- ❌ Messages didn't always arrive
- ❌ SMS length limitations
- ❌ Email-to-SMS gateway unreliable

**New Discord system:**
- ✅ Single formatted message
- ✅ Always delivered reliably
- ✅ Markdown formatting for readability
- ✅ No character limits
- ✅ Can include emojis and formatting
- ✅ Message history preserved in Discord

## Benefits

1. **Reliable Delivery**: No more missing messages
2. **Single Message**: Everything in one place
3. **Better Formatting**: Discord markdown makes it readable
4. **Persistent**: Message history in Discord
5. **Easy to Read**: Emojis and formatting enhance clarity
6. **No Cost**: Free (vs SMS carrier charges)

## Next Steps

The report is now set up and will run automatically every morning at 9:00 AM EST. You'll receive it in your Discord channel!

Check Discord tomorrow morning at 9 AM to see your first automated report! 🎉
