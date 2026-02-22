#!/bin/bash
# Telegram Notification Script
# Usage: telegram-notify.sh <type> <task_id> <task_name> [reason] [log_excerpt]

set -e

# Load Telegram config
if [ -f "$HOME/.telegram-env" ]; then
    source "$HOME/.telegram-env"
fi

if [ -f "$HOME/.env" ]; then
    source "$HOME/.env"
fi

# Fallback to environment variables
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# Ignore placeholder scaffold values from ~/.telegram-env
if [ "$TELEGRAM_BOT_TOKEN" = "your-telegram-bot-token-here" ]; then
    TELEGRAM_BOT_TOKEN=""
fi
if [ "$TELEGRAM_CHAT_ID" = "your-chat-id-here" ]; then
    TELEGRAM_CHAT_ID=""
fi

OPENCLAW_CONFIG="$HOME/.openclaw/.openclaw/openclaw.json"
if [ -z "$TELEGRAM_BOT_TOKEN" ] && [ -f "$OPENCLAW_CONFIG" ]; then
    TELEGRAM_BOT_TOKEN=$(python3 - <<'PY'
import json
from pathlib import Path
path = Path.home() / ".openclaw" / ".openclaw" / "openclaw.json"
try:
    data = json.loads(path.read_text())
    print(data.get("channels", {}).get("telegram", {}).get("botToken", ""))
except Exception:
    print("")
PY
)
fi

if [ -z "$TELEGRAM_CHAT_ID" ] && [ -f "$OPENCLAW_CONFIG" ]; then
    TELEGRAM_CHAT_ID=$(python3 - <<'PY'
import json
from pathlib import Path
path = Path.home() / ".openclaw" / ".openclaw" / "openclaw.json"
try:
    data = json.loads(path.read_text())
    allow = data.get("channels", {}).get("telegram", {}).get("allowFrom", [])
    if isinstance(allow, list) and allow:
        print(str(allow[0]))
    else:
        print("")
except Exception:
    print("")
PY
)
fi

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
    message="${message//\\n/$'\n'}"
    
    if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
        echo -e "${YELLOW}Warning: Telegram config missing. Skipping notification.${NC}"
        return 1
    fi
    
    local url="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"

    # First try Markdown formatting, then fall back to plain text if Telegram rejects formatting.
    local response
    response=$(curl -sS --max-time 20 -X POST "$url" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${message}" \
        --data-urlencode "parse_mode=Markdown") || return 1

    if echo "$response" | grep -q '"ok":true'; then
        return 0
    fi

    response=$(curl -sS --max-time 20 -X POST "$url" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${message}") || return 1

    if echo "$response" | grep -q '"ok":true'; then
        return 0
    fi

    echo "Telegram API error: $response" >&2
    return 1
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
    "agent-question")
        message="❓ *Agent Question*\n\nFrom: \`$TASK_NAME\`\n\n$REASON\n\n_Reply with your answer or send /pending to see all._"
        ;;
    "reset")
        message="🔁 *Task Reset*\n\nTask: \`$TASK_NAME\`\nID: \`$TASK_ID\`\n\n$REASON"
        ;;
    *)
        message="🔔 *Notification*\n\nType: $TYPE\nTask: \`$TASK_NAME\`\nID: \`$TASK_ID\`\n\n$REASON"
        ;;
esac

send_telegram "$message"
exit $?
