# Bot Deployment Setup

This directory contains everything needed to deploy a new bot on a new server.

## What's Included

| File/Directory | Purpose |
|----------------|---------|
| `setup.sh` | Main deployment script |
| `openclaw-config/` | OpenClaw configuration |
| `projects/` | Project directories (property_management, atlas, animal-rescue) |
| `scripts/` | Helper scripts for setup/backup |
| `secrets.example/` | Template for secrets (DO NOT commit real secrets) |

## Quick Start

### On New Server

```bash
# Clone this repository
git clone https://github.com/YOUR_USERNAME/bot-deploy-setup.git ~/bot-deploy-setup
cd ~/bot-deploy-setup

# Run setup
./setup.sh
```

The setup script will:
1. Install dependencies (Node, Python, Docker)
2. Configure OpenClaw
3. Clone project repositories
4. Set up environment variables
5. Start services

## Environment Variables

Copy `secrets.example/.env` to `.env` and fill in real values:

```bash
cp secrets.example/.env .env
# Edit .env with your real values
```

## Project Repositories

| Project | Repo |
|---------|------|
| Property Management | `git@github.com:dinisusmc-bot/property_management.git` |
| Atlas | `git@github.com:dinisusmc-bot/atlas.git` |
| Animal Rescue | `git@github.com:dinisusmc-bot/animal-rescue.git` |

## Backup & Restore

### Backup current setup:
```bash
./scripts/backup.sh
```

### Restore from backup:
```bash
./scripts/restore.sh backup.tar.gz
```

## Troubleshooting

See `docs/TROUBLESHOOTING.md` for common issues.

## Support

- 📧 Email: support@pm-demo.com
- 💬 Discord: https://discord.gg/pm-demo
