#!/bin/bash
# Daily Report Setup Script
# Run this after starting the OpenClaw Gateway

echo "Setting up Daily Weather + Sports Report for 9:00 AM EST"

# Update the existing cron job using OpenClaw CLI
openclaw cron edit fe8d1224-853a-4a27-9948-2792fa3ea27c \
  --message "Run the Python script at /home/bot/.openclaw/workspace/daily-report-sms.py to generate the daily weather and sports report with real game schedules and start times, then send via SMS to 7323970270@vtext.com. SMS is split into chunks if needed." \
  --no-deliver

echo "Daily report cron job updated successfully!"
echo "The report will be sent at 9:00 AM EST (14:00 UTC) each day via:"
echo "  - SMS to 732-397-0270 (Verizon, split into chunks)"
echo ""
echo "SMS includes:"
echo "  - Freehold, NJ weather (temp, wind chill, wind speed, precipitation)"
echo "  - Today's sports event start times with actual matchups:"
echo "    - NFL (Playoffs/Championship games)"
echo "    - NHL (Today's games with times)"
echo "    - MLB (Spring Training schedule)"
echo "    - UFC (Upcoming events)"
