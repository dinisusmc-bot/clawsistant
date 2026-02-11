# OpenClaw Deployment Bundle

This directory bootstraps a new VM with the current OpenClaw setup:
- OpenClaw config + workspace scripts
- Systemd user services/timers
- Postgres (optional via Docker)
- Agent skills from `agent-skills`

## Quick start

1) Clone this repo onto the VM.
2) Copy `.env.example` to `.env` and fill in values.
3) Run the setup script:

```bash
python3 setup.py --start-postgres
```

## What the script does

- Creates `~/projects` and `~/tmp`.
- Clones `agent-skills` to `~/projects/agent-skills` if missing.
- Copies OpenClaw workspace scripts to `~/.openclaw/workspace`.
- Writes OpenClaw config to `~/.openclaw/.openclaw/openclaw.json`.
- Writes Telegram env to `~/.telegram-env`.
- Writes Postgres env to `~/.env`.
- Installs systemd user units and enables timers + gateway service.
- Writes summarize config to `~/.summarize/config.json`.
- Installs required skill binaries (tmux, summarize, clawhub) on best-effort.

## Notes

- The script assumes OpenClaw is already installed in `OPENCLAW_CLI_PATH`.
- Use `openclaw skills check` to confirm readiness after install.
