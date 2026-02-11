#!/bin/bash
# Telegram Notification Script
# Usage: telegram-notify.sh <type> <task_id> <task_name> [reason] [log_excerpt]

set -e

# Load Telegram config
if [ -f "$HOME/.telegram-env" ]; then
    source "$HOME/.telegram-env"
fi

# Fallback to environment variables
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# Task types
TYPE=$1
TASK_ID=$2
TASK_NAME=$3
REASON=${4:-}
LOG_EXCERPT=${5:-}

# Colors for formatting
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

send_telegram() {
    local message="$1"
    
    if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
        echo -e "${YELLOW}Warning: Telegram config missing. Skipping notification.${NC}"
        return 1
    fi
    
    # URL encode the message
    local encoded_msg=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$message'''))" 2>/dev/null || echo "")
    
    if [ -z "$encoded_msg" ]; then
        # Fallback for simple messages
        encoded_msg=$(echo "$message" | sed 's/ /%20/g' | sed 's/\n/%0A/g')
    fi
    
    local url="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
    
    curl -s -X POST "$url" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=${encoded_msg}" \
        -d "parse_mode=Markdown" > /dev/null 2>&1
}

case "$TYPE" in
    "started")
        message="🚀 *Task Started*\n\nTask: \`$TASK_NAME\`\nID: \`$TASK_ID\`\n\nStarted at: $(date '+%Y-%m-%d %H:%M:%S UTC')"
        ;;
    "complete")
        message="✅ *Task Complete*\n\nTask: \`$TASK_NAME\`\nID: \`$TASK_ID\`\n\nCompleted at: $(date '+%Y-%m-%d %H:%M:%S UTC')"
        ;;
    "ready")
        message="🧪 *Ready for Testing*\n\nTask: \`$TASK_NAME\`\nID: \`$TASK_ID\`\n\nReady at: $(date '+%Y-%m-%d %H:%M:%S UTC')"
        ;;
    "blocker")
        message="⚠️ *Task Blocked*\n\nTask: \`$TASK_NAME\`\nID: \`$TASK_ID\`\n\nReason: $REASON\n\nLast error:\n\`\`\`\n${LOG_EXCERPT:0:500}\n\`\`\`"
        ;;
    "blocked-summary")
        message="🚧 *Blocked Tasks Summary*\n\n$REASON"
        ;;
    "reset")
        message="🔁 *Task Reset*\n\nTask: \`$TASK_NAME\`\nID: \`$TASK_ID\`\n\n$REASON"
        ;;
    *)
        message="🔔 *Notification*\n\nType: $TYPE\nTask: \`$TASK_NAME\`\nID: \`$TASK_ID\`\n\n$REASON"
        ;;
esac

send_telegram "$message"
exit 0
