# OpenClaw Deployment Bundle

This directory bootstraps a new VM with the current OpenClaw setup:
- OpenClaw config + workspace scripts
- Systemd user services/timers
- Postgres (optional via Docker)
- Agent skills from `agent-skills`
- Microsoft 365 email/calendar integration (optional)
- Twilio phone + AI call analysis pipeline (optional)
- Bot-to-bot build prompt handoff via Tailscale (optional)

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
- Creates Python import symlinks (`google_services.py` → `google-services.py`, etc.)
- Writes OpenClaw config to `~/.openclaw/.openclaw/openclaw.json`.
- Writes Telegram env to `~/.telegram-env`.
- Writes Postgres env to `~/.env`.
- Installs systemd user units and enables timers + gateway service.
- Conditionally enables email-monitor and twilio-call-monitor timers (if credentials exist).
- Writes summarize config to `~/.summarize/config.json`.
- Installs required skill binaries (tmux, summarize, clawhub) on best-effort.

## Optional integrations

### Microsoft 365 (Email + Calendar)

1. Create an Azure App Registration with `Mail.Read`, `Mail.Send`, `Mail.ReadWrite`, `Calendars.Read`, `Calendars.ReadWrite` permissions.
2. Save credentials to `~/.openclaw/microsoft-credentials.json`:
   ```json
   {"client_id": "...", "client_secret": "...", "tenant_id": "..."}
   ```
3. Run `python3 ~/.openclaw/workspace/microsoft-services.py --auth` to authenticate.
4. Set `MS365_USER_EMAIL` in `.env`.

The setup script will auto-enable the `email-monitor.timer` if credentials are present.

### Twilio Phone (Call Recording + AI Analysis)

1. Create a Twilio account and buy a phone number.
2. Save credentials to `~/.openclaw/twilio-credentials.json`:
   ```json
   {"account_sid": "...", "auth_token": "...", "phone_number": "+1..."}
   ```
3. Deploy a call forwarding TwiML function on Twilio Serverless (see `twilio-call-monitor.py` header).
4. Configure the phone number's voice URL to point to the serverless function.

The setup script will auto-enable the `twilio-call-monitor.timer` if credentials are present.

The call monitor pipeline: records → transcribes (Whisper) → LLM analysis → auto-creates calendar events → emails build specs → stores memories in vector DB → sends Telegram report.

### Bot-to-Bot Handoff (Tailscale)

Set `CODING_BOT_URL` in `.env` to the Tailscale HTTPS URL of a coding bot (e.g., `https://mcp-bot-1.tail0d0958.ts.net/route`). Technical build prompts from calls can be automatically or approval-gated forwarded to the coding bot.

On the coding bot, run `sudo tailscale serve --bg 18801` to expose its chat router over HTTPS.

## Notes

- The script assumes OpenClaw is already installed in `OPENCLAW_CLI_PATH`.
- Use `openclaw skills check` to confirm readiness after install.
- Python packages required by integrations: `twilio`, `requests`, `fastembed`
