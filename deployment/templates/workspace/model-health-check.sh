#!/usr/bin/env bash
set -euo pipefail

if [ -f "$HOME/.env" ]; then
  set -a
  source "$HOME/.env"
  set +a
fi

OPENCLAW_CONFIG="$HOME/.openclaw/.openclaw/openclaw.json"
LOG_FILE="$HOME/.openclaw/workspace/model-health-check.log"
STATE_FILE="$HOME/.openclaw/workspace/.model-health.state"
TELEGRAM_NOTIFY="$HOME/.openclaw/workspace/telegram-notify.sh"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S UTC'
}

log() {
  echo "[$(timestamp)] $1"
}

json_get() {
  local py="$1"
  python3 -c "$py"
}

load_from_openclaw_config() {
  local field="$1"
  python3 - "$field" <<'PY'
import json
import sys
from pathlib import Path

field = sys.argv[1]
path = Path.home() / ".openclaw" / ".openclaw" / "openclaw.json"
if not path.exists():
    print("")
    raise SystemExit(0)

try:
    data = json.loads(path.read_text())
except Exception:
    print("")
    raise SystemExit(0)

if field == "base_url":
    val = data.get("env", {}).get("OPENAI_BASE_URL", "")
elif field == "api_key":
    val = data.get("env", {}).get("OPENAI_API_KEY", "")
elif field == "text_model":
    val = data.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
    if isinstance(val, str) and "/" in val:
        val = val.split("/", 1)[1]
elif field == "vision_model":
    val = data.get("agents", {}).get("defaults", {}).get("imageModel", {}).get("primary", "")
    if isinstance(val, str) and "/" in val:
        val = val.split("/", 1)[1]
elif field == "embedding_model":
    val = data.get("agents", {}).get("defaults", {}).get("memorySearch", {}).get("model", "")
else:
    val = ""

print(val or "")
PY
}

OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
OPENAI_API_KEY="${OPENAI_API_KEY:-${OPENAI_TOOLS_API_KEY:-}}"
PRIMARY_TEXT_MODEL="${PRIMARY_TEXT_MODEL:-}"
PRIMARY_VISION_MODEL="${PRIMARY_VISION_MODEL:-}"
EMBEDDINGS_MODEL="${EMBEDDINGS_MODEL:-}"

if [ -z "$OPENAI_BASE_URL" ]; then
  OPENAI_BASE_URL="$(load_from_openclaw_config base_url)"
fi
if [ -z "$OPENAI_API_KEY" ]; then
  OPENAI_API_KEY="$(load_from_openclaw_config api_key)"
fi
if [ -z "$PRIMARY_TEXT_MODEL" ]; then
  PRIMARY_TEXT_MODEL="$(load_from_openclaw_config text_model)"
fi
if [ -z "$PRIMARY_VISION_MODEL" ]; then
  PRIMARY_VISION_MODEL="$(load_from_openclaw_config vision_model)"
fi
if [ -z "$EMBEDDINGS_MODEL" ]; then
  EMBEDDINGS_MODEL="$(load_from_openclaw_config embedding_model)"
fi

OPENAI_BASE_URL="${OPENAI_BASE_URL%/}"
MODELS_URL="$OPENAI_BASE_URL/models"
CHAT_URL="$OPENAI_BASE_URL/chat/completions"
EMBED_URL="$OPENAI_BASE_URL/embeddings"
CURL_CONNECT_TIMEOUT_SEC="${MODEL_HEALTH_CONNECT_TIMEOUT_SEC:-5}"
CURL_TIMEOUT_MODELS_SEC="${MODEL_HEALTH_MODELS_TIMEOUT_SEC:-20}"
CURL_TIMEOUT_CHAT_SEC="${MODEL_HEALTH_CHAT_TIMEOUT_SEC:-90}"
CURL_TIMEOUT_EMBED_SEC="${MODEL_HEALTH_EMBED_TIMEOUT_SEC:-60}"
CURL_RETRIES="${MODEL_HEALTH_RETRIES:-2}"
MODEL_HEALTH_FORCE_FAIL="${MODEL_HEALTH_FORCE_FAIL:-0}"

failures=()

require_non_empty() {
  local name="$1"
  local value="$2"
  if [ -z "$value" ]; then
    failures+=("Missing required config: $name")
  fi
}

notify_telegram() {
  local status="$1"
  local details="$2"
  local type="model-health"
  local task_id="model-check"
  local task_name="OpenClaw Model Health"

  if [ -x "$TELEGRAM_NOTIFY" ]; then
    "$TELEGRAM_NOTIFY" "$type" "$task_id" "$task_name" "$status"$'\n'"$details" >/dev/null 2>&1 || true
    return
  fi

  log "telegram-notify script not found or not executable; skipping telegram send"
}

http_post() {
  local url="$1"
  local body="$2"
  local timeout_sec="$3"
  local attempt=1

  while [ "$attempt" -le "$CURL_RETRIES" ]; do
    if response=$(curl -sS --connect-timeout "$CURL_CONNECT_TIMEOUT_SEC" --max-time "$timeout_sec" -X POST "$url" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $OPENAI_API_KEY" \
      -d "$body"); then
      echo "$response"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done

  return 1
}

http_get() {
  local url="$1"
  local timeout_sec="$2"
  local attempt=1

  while [ "$attempt" -le "$CURL_RETRIES" ]; do
    if response=$(curl -sS --connect-timeout "$CURL_CONNECT_TIMEOUT_SEC" --max-time "$timeout_sec" -X GET "$url" \
      -H "Authorization: Bearer $OPENAI_API_KEY"); then
      echo "$response"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done

  return 1
}

json_has_model() {
  local payload="$1"
  local model="$2"
  python3 - "$model" "$payload" <<'PY'
import json
import sys

model = sys.argv[1]
payload = sys.argv[2]
try:
    data = json.loads(payload)
except Exception:
    print("0")
    raise SystemExit(0)

for item in data.get("data", []):
    if str(item.get("id", "")) == model:
        print("1")
        raise SystemExit(0)
print("0")
PY
}

check_chat_response() {
  local payload="$1"
  python3 - "$payload" <<'PY'
import json
import sys

payload = sys.argv[1]
try:
    data = json.loads(payload)
except Exception:
    print("0")
    raise SystemExit(0)

choices = data.get("choices")
if isinstance(choices, list) and choices:
    print("1")
else:
    print("0")
PY
}

check_embedding_response() {
  local payload="$1"
  python3 - "$payload" <<'PY'
import json
import sys

payload = sys.argv[1]
try:
    data = json.loads(payload)
except Exception:
    print("0")
    raise SystemExit(0)

items = data.get("data")
if isinstance(items, list) and items:
    emb = items[0].get("embedding") if isinstance(items[0], dict) else None
    if isinstance(emb, list) and len(emb) > 0:
        print("1")
        raise SystemExit(0)
print("0")
PY
}

require_non_empty "OPENAI_BASE_URL" "$OPENAI_BASE_URL"
require_non_empty "OPENAI_API_KEY" "$OPENAI_API_KEY"
require_non_empty "PRIMARY_TEXT_MODEL" "$PRIMARY_TEXT_MODEL"
require_non_empty "EMBEDDINGS_MODEL" "$EMBEDDINGS_MODEL"

if [ "$MODEL_HEALTH_FORCE_FAIL" = "1" ]; then
  failures+=("Forced failure requested: MODEL_HEALTH_FORCE_FAIL=1")
fi

if [ ${#failures[@]} -eq 0 ]; then
  models_json="$(http_get "$MODELS_URL" "$CURL_TIMEOUT_MODELS_SEC" || true)"
  if [ -z "$models_json" ]; then
    failures+=("Failed to fetch model list from $MODELS_URL")
  else
    if [ "$(json_has_model "$models_json" "$PRIMARY_TEXT_MODEL")" != "1" ]; then
      failures+=("Primary text model missing: $PRIMARY_TEXT_MODEL")
    fi
    if [ -n "$PRIMARY_VISION_MODEL" ] && [ "$(json_has_model "$models_json" "$PRIMARY_VISION_MODEL")" != "1" ]; then
      failures+=("Primary vision model missing: $PRIMARY_VISION_MODEL")
    fi
    if [ "$(json_has_model "$models_json" "$EMBEDDINGS_MODEL")" != "1" ]; then
      failures+=("Embeddings model missing: $EMBEDDINGS_MODEL")
    fi
  fi
fi

if [ ${#failures[@]} -eq 0 ]; then
  chat_payload='{"model":"'"$PRIMARY_TEXT_MODEL"'","messages":[{"role":"user","content":"health check"}],"max_tokens":8,"temperature":0}'
  chat_json="$(http_post "$CHAT_URL" "$chat_payload" "$CURL_TIMEOUT_CHAT_SEC" || true)"
  if [ -z "$chat_json" ] || [ "$(check_chat_response "$chat_json")" != "1" ]; then
    failures+=("Chat completion check failed for model: $PRIMARY_TEXT_MODEL")
  fi
fi

if [ ${#failures[@]} -eq 0 ] && [ -n "$PRIMARY_VISION_MODEL" ]; then
  vision_payload='{"model":"'"$PRIMARY_VISION_MODEL"'","messages":[{"role":"user","content":"health check"}],"max_tokens":8,"temperature":0}'
  vision_json="$(http_post "$CHAT_URL" "$vision_payload" "$CURL_TIMEOUT_CHAT_SEC" || true)"
  if [ -z "$vision_json" ] || [ "$(check_chat_response "$vision_json")" != "1" ]; then
    failures+=("Vision model text check failed for model: $PRIMARY_VISION_MODEL")
  fi
fi

if [ ${#failures[@]} -eq 0 ]; then
  emb_payload='{"model":"'"$EMBEDDINGS_MODEL"'","input":"model health check","encoding_format":"float"}'
  emb_json="$(http_post "$EMBED_URL" "$emb_payload" "$CURL_TIMEOUT_EMBED_SEC" || true)"
  if [ -z "$emb_json" ] || [ "$(check_embedding_response "$emb_json")" != "1" ]; then
    failures+=("Embeddings check failed for model: $EMBEDDINGS_MODEL")
  fi
fi

last_status="UNKNOWN"
if [ -f "$STATE_FILE" ]; then
  last_status="$(cat "$STATE_FILE" 2>/dev/null || echo "UNKNOWN")"
fi

if [ ${#failures[@]} -gt 0 ]; then
  current_status="DOWN"
  details="$(printf '%s\n' "${failures[@]}")"
  log "Model health FAILED"
  log "$details"
  if [ "$last_status" != "$current_status" ]; then
    notify_telegram "Model health check FAILED" "$details"
    log "Sent Telegram alert for model health failure"
  fi
  echo "$current_status" > "$STATE_FILE"
  exit 1
fi

current_status="OK"
log "Model health OK (text=$PRIMARY_TEXT_MODEL vision=${PRIMARY_VISION_MODEL:-n/a} embed=$EMBEDDINGS_MODEL)"
if [ "$last_status" = "DOWN" ]; then
  notify_telegram "Model health recovered" "All configured model checks are passing again."
  log "Sent Telegram recovery notice"
fi

echo "$current_status" > "$STATE_FILE"
exit 0
