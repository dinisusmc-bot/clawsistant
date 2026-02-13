#!/usr/bin/env bash
set -euo pipefail

systemctl --user daemon-reload

services=$(systemctl --user list-unit-files --type=service --no-legend | awk '{print $1}' | grep -E '^openclaw.*\.service$' || true)
timers=$(systemctl --user list-unit-files --type=timer --no-legend | awk '{print $1}' | grep -E '^openclaw.*\.timer$' || true)

if [[ -z "$services" && -z "$timers" ]]; then
  echo "No openclaw services or timers found."
  exit 0
fi

if [[ -n "$services" ]]; then
  echo "Starting OpenClaw services:"
  echo "$services"
  systemctl --user start $services
fi

if [[ -n "$timers" ]]; then
  echo "Starting OpenClaw timers:"
  echo "$timers"
  systemctl --user enable --now $timers
fi

echo "Ensuring gateway command path is started (best effort)"
openclaw gateway start >/dev/null 2>&1 || true

echo "Started."
