#!/usr/bin/env bash
set -euo pipefail

services=$(systemctl --user list-unit-files --type=service --no-legend | awk '{print $1}' | grep -E '^openclaw.*\.service$' || true)
timers=$(systemctl --user list-unit-files --type=timer --no-legend | awk '{print $1}' | grep -E '^openclaw.*\.timer$' || true)

if [[ -z "$services" && -z "$timers" ]]; then
  echo "No openclaw services or timers found."
  exit 0
fi

if [[ -n "$timers" ]]; then
  echo "Stopping OpenClaw timers:"
  echo "$timers"
  systemctl --user stop $timers
  systemctl --user disable $timers
fi

if [[ -n "$services" ]]; then
  echo "Stopping OpenClaw services:"
  echo "$services"
  systemctl --user stop $services
fi

echo "Stopping gateway command process (if running)"
openclaw gateway stop >/dev/null 2>&1 || true

echo "Killing any remaining OpenClaw processes"
pkill -f 'openclaw-agent|openclaw( |$)|autonomous-task-manager-db\.sh|chat-router\.py|telegram-task-commands\.py|model-health-check\.sh' >/dev/null 2>&1 || true

echo "Stopped."
