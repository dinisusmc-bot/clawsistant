#!/bin/bash
# Install systemd user service + timer for autonomous-task-manager-db

set -euo pipefail

OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
SYSTEMD_DIR="$HOME/.config/systemd/user"

mkdir -p "$SYSTEMD_DIR"

cat > "$SYSTEMD_DIR/openclaw-task-manager-db.service" << EOF
[Unit]
Description=OpenClaw Autonomous Task Manager (DB)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=%h/.openclaw/workspace
ExecStartPre=/bin/bash %h/.openclaw/workspace/init-autonomous-tasks-db.sh
ExecStartPre=/bin/bash %h/.openclaw/workspace/migrate-task-statuses.sh
ExecStart=/bin/bash %h/.openclaw/workspace/autonomous-task-manager-db.sh
StandardOutput=append:%h/.openclaw/workspace/autonomous-task-manager-db.log
StandardError=append:%h/.openclaw/workspace/autonomous-task-manager-db.log

[Install]
WantedBy=default.target
EOF

cat > "$SYSTEMD_DIR/openclaw-task-manager-db.timer" << 'EOF'
[Unit]
Description=OpenClaw Autonomous Task Manager Timer (DB)
Requires=openclaw-task-manager-db.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable openclaw-task-manager-db.timer
systemctl --user start openclaw-task-manager-db.timer

echo "✅ openclaw-task-manager-db timer enabled"
