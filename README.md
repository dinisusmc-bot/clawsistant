# Ashley — Personal AI Assistant Bot

A self-hosted autonomous personal assistant running on [OpenClaw](https://github.com/openclaw/openclaw), built around a 3-agent pipeline (Planner → Doer → Reviewer) and delivered via Telegram.

Ashley watches your email, manages your calendar, takes notes, runs web searches, monitors scheduled jobs, and handles multi-step tasks autonomously — all controlled through a simple Telegram chat interface.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Planner    │────▶│  Doer (x3)   │────▶│   Reviewer   │
│  (strategy)  │     │ (execution)  │     │  (quality)   │
└──────────────┘     └──────────────┘     └──────────────┘
        ▲                                        │
        │            ┌──────────────┐             │
        └────────────│ Task Manager │◀────────────┘
                     │  (PostgreSQL) │
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │ Chat Router  │ ← HTTP API (port 18801)
                     │  (Python)    │
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │   Telegram   │ ← User interface
                     │  Bot Daemon  │
                     └──────────────┘
```

### Components

| Component | Description | Port |
|---|---|---|
| **OpenClaw Gateway** | LLM orchestration engine | 18789 |
| **Chat Router** | Central HTTP command router (Python) | 18801 |
| **Telegram Daemon** | Polls Telegram for messages, routes commands | — |
| **Task Manager** | Manages autonomous task lifecycle via PostgreSQL | — |
| **Email Monitor** | Watches Gmail, filters spam, notifies on Telegram | — |
| **SearXNG** | Self-hosted web search engine (Docker) | 8888 |
| **PostgreSQL** | Task DB, question tracking, state | 5433 |
| **LiteLLM Proxy** | Model routing (minimax-m2.5) | 8010 |

### Agent Pipeline

- **Planner** — Receives requests, decomposes into tasks, dispatches to Doers. Never executes directly.
- **Doer** (up to 3 concurrent) — Executes individual tasks: research, drafting, organizing, API calls.
- **Reviewer** — Validates deliverables for accuracy, completeness, and usefulness before delivery.

Agents can ask the owner clarifying questions via `/pending` and `/answer` when blocked.

## Capabilities

### Telegram Commands

#### Task Management
| Command | Description |
|---|---|
| `/plan <request>` | Submit a multi-step request for the agent pipeline |
| `/think <request>` | Quick single-agent task |
| `/adhoc <request>` | Ad-hoc task, skip planning |
| `/tasks` | List all active tasks |
| `/task <id>` | View task details |
| `/todo` | View pending tasks |
| `/inprogress` | View in-progress tasks |
| `/readyfortesting` | View tasks awaiting review |
| `/blockers` | View blocked tasks |
| `/unblock <id>` | Unblock a stuck task |
| `/retry <id>` | Retry a failed task |

#### Gmail Integration
| Command | Description |
|---|---|
| `/emails` | List recent inbox emails |
| `/email <id>` | Read a specific email |
| `/sendemail <to>\|<subject>\|<body>` | Send an email |
| `/unread` | Count unread emails |

#### Google Calendar
| Command | Description |
|---|---|
| `/calendar` | View today's events |
| `/event <title>\|<date>\|<time>\|<duration>` | Create an event |
| `/delevent <id>` | Delete an event |

#### Web Search
| Command | Description |
|---|---|
| `/search <query>` | Search the web via SearXNG |

#### Weather
| Command | Description |
|---|---|
| `/weather` | Current weather (OpenWeatherMap) |
| `/weather <city>` | Weather for a specific city |

#### Notes & Bookmarks
| Command | Description |
|---|---|
| `/note <text>` | Save a quick note (daily file) |
| `/notes` | View today's notes |
| `/notes search <term>` | Search all notes |
| `/save <url> [tags]` | Save a bookmark with optional tags |
| `/links` | View recent bookmarks |

#### Scheduling
| Command | Description |
|---|---|
| `/schedule <cron> <command>` | Schedule a recurring command |
| `/jobs` | List scheduled jobs |
| `/deletejob <id>` | Delete a scheduled job |

#### Intelligence
| Command | Description |
|---|---|
| `/briefing` | Morning briefing (calendar, inbox, tasks, notes, jobs) |
| `/weeklyreview` | Weekly summary of activity |
| `/digest` | Task progress digest |

#### Agent Communication
| Command | Description |
|---|---|
| `/pending` | View agent questions awaiting your answer |
| `/answer <response>` | Reply to the oldest pending agent question |

#### Other
| Command | Description |
|---|---|
| `/prompt` | View current system prompt |
| `/help` | Show all available commands |

### Automated Services

| Service | Schedule | Description |
|---|---|---|
| **Email Monitor** | Every 5 minutes | Checks Gmail for new important emails, aggressively filters spam/noise, sends Telegram notifications for anything that passes filters |
| **Morning Briefing** | Daily 8:00 AM | Aggregates calendar, unread count, blocked tasks, pending questions, scheduled jobs, recent notes |
| **Weekly Review** | Sundays 7:00 PM | Summarizes the week's tasks, email activity, calendar events |
| **Task Manager** | Continuous | Monitors task DB, dispatches agents, handles lifecycle |
| **Model Health Check** | Every hour | Verifies LLM availability |

### Email Monitoring Details

The email monitor uses aggressive multi-layer filtering:

1. **Gmail Label filtering** — Auto-skips `SPAM`, `TRASH`, `CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, `CATEGORY_UPDATES`, `CATEGORY_FORUMS`
2. **Bulk mail detection** — Filters emails with `List-Unsubscribe` headers (unless Gmail marked `IMPORTANT`)
3. **Sender pattern matching** — 80+ regex patterns for noreply addresses, social media, retail, newsletters, marketing platforms
4. **Subject pattern matching** — 50+ patterns for receipts, password resets, order confirmations, promotional language, social notifications, subscription alerts
5. **Importance scoring** (1-5) — Weighs Gmail `IMPORTANT`/`STARRED`/`CATEGORY_PERSONAL` labels, urgency keywords, personal sender format

### File Sharing

Send documents, photos, or audio to the Telegram bot — files are automatically downloaded to the `inbox/` directory for agent access.

### Conversation Memory

Rolling 20-message context buffer maintains conversational continuity across interactions.

## Tech Stack

| Component | Technology |
|---|---|
| Orchestration | OpenClaw v2026.2.9 |
| LLM | minimax-m2.5 via LiteLLM (self-hosted, zero cost) |
| Embeddings | bge-small-en-v1.5 |
| Chat Router | Python 3.12 (stdlib HTTP server) |
| Database | PostgreSQL 16 |
| Web Search | SearXNG (Docker) |
| Weather | OpenWeatherMap API |
| Email/Calendar | Google APIs (OAuth2) |
| Messaging | Telegram Bot API |
| Process Management | systemd user services & timers |
| OS | Ubuntu (headless VM) |

## Directory Structure

```
based_claw/
├── deployment/
│   └── templates/
│       ├── agents/
│       │   ├── planner/agent/SOUL.md    # Planner personality & rules
│       │   ├── coder/agent/SOUL.md      # Doer personality & rules
│       │   └── tester/agent/SOUL.md     # Reviewer personality & rules
│       ├── openclaw.json                # OpenClaw gateway config
│       ├── systemd/                     # Service unit files
│       └── workspace/
│           ├── SOUL.md                  # Core personality & boundaries
│           ├── USER.md                  # Owner profile & preferences
│           ├── IDENTITY.md              # Bot identity
│           ├── SKILL.md                 # Agent skill documentation
│           ├── AGENTS.md                # Agent architecture docs
│           ├── TOOLS.md                 # Available tools
│           ├── chat-router.py           # Central HTTP command router
│           ├── telegram-task-commands.py # Telegram polling daemon
│           ├── google-services.py       # Gmail & Calendar API module
│           ├── email-monitor.py         # Gmail watcher & notifier
│           ├── autonomous-task-manager-db.sh  # Task lifecycle manager
│           ├── autonomous-tasks-schema.sql    # DB schema
│           └── telegram-notify.sh       # Notification dispatcher
└── scripts/
    ├── start-openclaw-services.sh
    └── stop-openclaw-services.sh
```

## Setup

1. Clone this repo to `~/based_claw/`
2. Set up `.env` with required tokens (see below)
3. Run `python3 deployment/setup.py` to deploy templates to `~/.openclaw/`
4. Start services: `scripts/start-openclaw-services.sh`
5. Run Gmail OAuth: `python3 ~/.openclaw/workspace/google-services.py --auth`

### Required Environment Variables (`~/.env`)

```bash
# Telegram
TELEGRAM_BOT_TOKEN=<bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>

# OpenClaw
OPENCLAW_POSTGRES_HOST=localhost
OPENCLAW_POSTGRES_PORT=5433
OPENCLAW_POSTGRES_DB=openclaw
OPENCLAW_POSTGRES_USER=openclaw
OPENCLAW_POSTGRES_PASSWORD=<password>

# LLM
OPENAI_API_KEY=<key>
OPENAI_BASE_URL=http://ai-services:8010/v1
PRIMARY_TEXT_MODEL=minimax-m2.5
EMBEDDINGS_MODEL=bge-small-en-v1.5

# Web Search
SEARXNG_URL=http://localhost:8888

# Weather
OPENWEATHER_API_KEY=<key>
```

## Gaps & Improvement Opportunities

### High Priority

- **Reminder System** — `/remind <time> <message>` for time-based reminders with snooze support. Currently no way to set one-off future alerts.
- **Contact/CRM Database** — A structured contact store the agents can reference. Currently no way to track relationships, birthdays, follow-ups, or communication history.
- **Task Priorities & Due Dates** — Tasks have no urgency or deadline. Adding priority levels and due dates would enable smarter scheduling and overdue alerts.
- **Email Draft Review** — Currently `/sendemail` sends immediately. Should support a draft → review → send flow, especially for agent-composed emails.
- **Calendar Conflict Detection** — No awareness of overlapping events when creating new ones.

### Medium Priority

- **Rate Limiting / Throttling** — No protection against runaway agent loops or API abuse. Need per-agent and per-service rate limits.
- **Heartbeat / Watchdog System** — If a service crashes silently, no alert fires. Need a lightweight watchdog that pings each component and alerts on failure.
- **Audit Logging** — No centralized log of what agents did, what emails were sent, what tasks were modified. Important for accountability and debugging.
- **Smart Email Categorization** — Beyond spam filtering, use LLM to categorize important emails (action required, FYI, personal, financial) and summarize them.
- **Multi-Calendar Support** — Currently hardcoded to primary calendar. Should support multiple calendars (work, personal, shared).

### Lower Priority

- **Voice Message Transcription** — Audio files saved to `inbox/` aren't transcribed. Could use Whisper to convert to text and route to agents.
- **Recurring Task Templates** — Frequently repeated tasks (weekly reports, monthly reviews) should be templateable rather than manually scheduled.
- **Natural Language Scheduling** — `/schedule` requires cron syntax. Should support "every weekday at 9am" or "remind me every Monday."
- **Location-Aware Features** — Weather defaults to a fixed city. Could learn home/work locations and auto-contextualize.
- **Mobile Quick Actions** — Telegram inline keyboards for common actions (approve/reject task, snooze reminder, archive email) instead of typing commands.
- **Inter-Agent Memory** — Agents start fresh each session. A shared persistent memory (beyond SOUL/USER.md files) would enable learning from past interactions.
- **Dashboard / Web UI** — All interaction is via Telegram text. A simple web dashboard showing tasks, agent status, email queue, and calendar would add visibility.
- **Backup & Recovery** — No automated backup of the PostgreSQL task DB, notes, bookmarks, or state files.
- **Multi-User Support** — Currently single-user only. Architecture could extend to support multiple Telegram users with isolated contexts.

## License

Private project.
