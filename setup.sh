set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${YELLOW}$*${NC}"; }
ok() { echo -e "${GREEN}✓ $*${NC}"; }
fail() { echo -e "${RED}ERROR: $*${NC}"; exit 1; }

if [[ "${USER:-}" != "bot" ]]; then
    fail "This script must run as 'bot' user"
fi

DEPLOY_DIR="$HOME/bot-deploy-setup"
OPENCLAW_HOME="$HOME/.openclaw"
OPENCLAW_LOCAL="$HOME/.local/openclaw"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

log "Step 1/7: Installing base dependencies"
sudo apt-get update -qq
sudo apt-get install -y -qq curl git unzip ca-certificates python3 python3-pip docker.io docker-compose jq postgresql-client > /dev/null
ok "Base dependencies installed"

log "Step 2/7: Installing Node.js 22"
if ! command -v node >/dev/null 2>&1 || ! node -v | grep -qE '^v22\.'; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - >/dev/null
    sudo apt-get install -y -qq nodejs > /dev/null
fi
node --version
npm --version
ok "Node.js ready"

log "Step 3/7: Installing Bun + QMD"
if [[ ! -x "$HOME/.bun/bin/bun" ]]; then
    curl -fsSL https://bun.sh/install | bash
fi
BUN_BIN="$HOME/.bun/bin/bun"
$BUN_BIN --version
$BUN_BIN install -g https://github.com/tobi/qmd >/dev/null
QMD_TARGET=$(find "$HOME/.bun" -type f -name qmd | head -n 1 || true)
if [[ -n "$QMD_TARGET" ]]; then
    ln -sf "$QMD_TARGET" "$HOME/.bun/bin/qmd"
fi
ok "Bun + QMD installed"

log "Step 4/7: Installing agent skills"
yes | npx skills add https://github.com/gohypergiant/agent-skills >/dev/null
if command -v npx >/dev/null 2>&1; then
    yes | npx skills add https://github.com/vercel-labs/skills --skill find-skills >/dev/null
fi
ok "Agent skills installed"

log "Step 5/7: Installing OpenClaw"
mkdir -p "$OPENCLAW_LOCAL"
if [[ ! -f "$OPENCLAW_LOCAL/package.json" ]]; then
    (cd "$OPENCLAW_LOCAL" && npm init -y >/dev/null)
fi
(cd "$OPENCLAW_LOCAL" && npm install openclaw@latest >/dev/null)
ok "OpenClaw installed"

log "Step 6/7: Syncing OpenClaw config"
mkdir -p "$OPENCLAW_HOME"
rsync -a --delete "$DEPLOY_DIR/openclaw-config/" "$OPENCLAW_HOME/"
mkdir -p "$OPENCLAW_HOME/memory/notes" "$OPENCLAW_HOME/memory/sessions"
mkdir -p "$OPENCLAW_HOME/agents/planner/sessions" "$OPENCLAW_HOME/agents/coder/sessions" "$OPENCLAW_HOME/agents/tester/sessions"
mkdir -p "$OPENCLAW_HOME/workspace-coder/memory" "$OPENCLAW_HOME/workspace-tester/memory"
mkdir -p "$HOME/tasks/logs"
if [ -d "$OPENCLAW_HOME/workspace" ]; then
    find "$OPENCLAW_HOME/workspace" -type f -name "*.sh" -exec chmod +x {} \;
fi
if [ -x "$OPENCLAW_HOME/workspace/init-autonomous-tasks-db.sh" ]; then
    "$OPENCLAW_HOME/workspace/init-autonomous-tasks-db.sh" || log "Autonomous task DB init skipped"
fi
ok "OpenClaw config synced"

log "Step 7/7: Writing systemd user service"
mkdir -p "$SYSTEMD_USER_DIR"
cat > "$SYSTEMD_USER_DIR/openclaw-gateway.service" <<EOF
[Unit]
Description=OpenClaw Gateway
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/node $OPENCLAW_LOCAL/node_modules/openclaw/dist/index.js gateway --port 18789
Restart=always
RestartSec=5
KillMode=process
Environment=HOME=$HOME
Environment=OPENCLAW_HOME=$HOME
Environment=OPENCLAW_STATE_DIR=$OPENCLAW_HOME
Environment=OPENCLAW_CONFIG_PATH=$OPENCLAW_HOME/openclaw.json
Environment=OPENCLAW_GATEWAY_PORT=18789
EnvironmentFile=-$HOME/.env
Environment="PATH=$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/bin:$HOME/.nvm/current/bin:$HOME/.fnm/current/bin:$HOME/.volta/bin:$HOME/.asdf/shims:$HOME/.local/share/pnpm:$HOME/.bun/bin:/usr/local/bin:/usr/bin:/bin"

[Install]
WantedBy=default.target
EOF

cat > "$SYSTEMD_USER_DIR/openclaw-task-manager.service" <<EOF
[Unit]
Description=OpenClaw Autonomous Task Manager (DB)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $OPENCLAW_HOME/workspace/autonomous-task-manager-db.sh
Environment=HOME=$HOME
Environment=OPENCLAW_HOME=$HOME
Environment=OPENCLAW_STATE_DIR=$OPENCLAW_HOME
Environment=OPENCLAW_CONFIG_PATH=$OPENCLAW_HOME/openclaw.json
EnvironmentFile=-$HOME/.env
Environment="PATH=$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/bin:$HOME/.nvm/current/bin:$HOME/.fnm/current/bin:$HOME/.volta/bin:$HOME/.asdf/shims:$HOME/.local/share/pnpm:$HOME/.bun/bin:/usr/local/bin:/usr/bin:/bin"
EOF

cat > "$SYSTEMD_USER_DIR/openclaw-task-manager.timer" <<EOF
[Unit]
Description=Run OpenClaw task manager every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now openclaw-gateway.service
systemctl --user enable --now openclaw-task-manager.timer
ok "Gateway service running"

ok "Deployment complete"
