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
              ┌─────────────┼─────────────┐
              │             │             │
       ┌──────▼───────┐ ┌───▼──────┐ ┌────▼─────────┐
       │   Telegram   │ │ Vector   │ │   Email      │
       │  Bot Daemon  │ │ Memory   │ │   Monitor    │
       │  (keyboards) │ │ (pgvec)  │ │   (Gmail)    │
       └──────────────┘ └──────────┘ └──────────────┘
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
| **Vector Memory** | Semantic long-term memory (pgvector + fastembed) | — |
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

#### Memory
| Command | Description |
|---|---|
| `/remember <text>` | Store a memory (fact, preference, lesson) |
| `/recall <query>` | Search memories by semantic similarity |
| `/forget <id>` | Delete a specific memory |
| `/memories` | Show memory stats by category |

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

### Vector Memory (Long-Term)

Persistent semantic memory powered by **pgvector** and **fastembed**:

- **Storage** — Memories are embedded as 384-dimensional vectors (bge-small-en-v1.5) and stored in PostgreSQL with an HNSW index for fast cosine similarity search.
- **Auto-capture** — Conversations, lessons learned, notes, bookmarks, and project context are automatically stored as memories during normal use.
- **Contextual recall** — Before every Planner invocation, the most relevant memories are retrieved and injected as context, giving agents awareness of past interactions and preferences.
- **Categories** — `conversations`, `lessons`, `notes`, `bookmarks`, `projects`, `facts`
- **Commands** — `/remember`, `/recall`, `/forget`, `/memories` (see Memory commands above)

### Inline Keyboards

Telegram responses include contextual inline action buttons where applicable. After commands like `/tasks`, `/emails`, `/jobs`, and `/pending`, quick-action keyboards appear allowing one-tap follow-up actions (e.g., view details, retry, delete) without typing commands manually.

## Tech Stack

| Component | Technology |
|---|---|
| Orchestration | OpenClaw v2026.2.9 |
| LLM | minimax-m2.5 via LiteLLM (self-hosted, zero cost) |
| Embeddings | bge-small-en-v1.5 (fastembed, 384-dim) |
| Vector Store | pgvector 0.8.1 (HNSW index) |
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
│           ├── vector-memory.py         # Semantic long-term memory (pgvector)
│           ├── autonomous-task-manager-db.sh  # Task lifecycle manager
│           ├── autonomous-tasks-schema.sql    # DB schema
│           └── telegram-notify.sh       # Notification dispatcher
└── scripts/
    ├── start-openclaw-services.sh
    └── stop-openclaw-services.sh
```

## New Bot Deployment (From Scratch)

Complete guide to deploying Ashley on a fresh Ubuntu VM.

### Prerequisites

- **Ubuntu 22.04+** (headless VM, VPS, or bare metal)
- **4GB+ RAM**, 20GB+ disk
- **Docker** installed and running
- **Node.js 20+** and **npm**
- **Python 3.12+**
- **Git** with SSH key configured for GitHub
- An LLM inference server (e.g. [LiteLLM](https://github.com/BerriAI/litellm)) accessible from the VM

### Step 1: System Dependencies

```bash
sudo apt update && sudo apt install -y \
    python3 python3-pip postgresql-client curl jq tmux

# Enable lingering so systemd user services survive logout
sudo loginctl enable-linger $USER
```

### Step 2: Install OpenClaw

```bash
mkdir -p ~/.local/openclaw && cd ~/.local/openclaw
npm init -y && npm install openclaw@2026.2.9
```

Verify: `node ~/.local/openclaw/node_modules/openclaw/dist/index.js --version`

### Step 3: Set Up PostgreSQL

```bash
# Option A: Docker (recommended)
docker run -d --name openclaw-postgres \
    -e POSTGRES_USER=openclaw \
    -e POSTGRES_PASSWORD=openclaw_dev_pass \
    -e POSTGRES_DB=openclaw \
    -p 5433:5432 \
    --restart unless-stopped \
    postgres:16

# Option B: Use the included docker-compose
cd ~/based_claw/deployment
docker compose -f docker-compose.openclaw.yml up -d
```

### Step 4: Create a Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, choose a name and username
3. Copy the bot token (e.g. `7202673884:AAGjqUoBRf...`)
4. Message your new bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat_id`

### Step 5: Clone and Configure

```bash
cd ~ && git clone git@github.com:dinisusmc-bot/clawsistant.git based_claw
cd ~/based_claw/deployment
```

Edit the `.env` file with your values:

```bash
cp .env.example .env   # If .env doesn't exist, create from the template below
nano .env
```

#### Required `.env` Values

```bash
# === Core OpenClaw Gateway ===
OPENAI_API_KEY=sk-1234                          # API key for your LLM proxy
OPENAI_TOOLS_API_KEY=sk-1234                    # Can be same as above
OPENAI_BASE_URL=http://ai-services:8010/v1      # Your LiteLLM / LLM proxy URL
OPENCLAW_GATEWAY_TOKEN=<random-hex-string>       # Generate: openssl rand -hex 24
OPENCLAW_GATEWAY_PORT=18789
OPENCLAW_VERSION=2026.2.9
OPENCLAW_CLI_PATH=$HOME/.local/openclaw/node_modules/openclaw/dist/index.js

# === Model Selection ===
PRIMARY_TEXT_MODEL=minimax-m2.5                  # Your primary LLM
PRIMARY_VISION_MODEL=internvl                    # Vision model (optional)
EMBEDDINGS_MODEL=bge-small-en-v1.5              # Embedding model for search
PLANNER_TEMP=0.25
CODER_TEMP=0.18
TESTER_TEMP=0.10

# === Telegram ===
TELEGRAM_BOT_TOKEN=<your-bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>
TELEGRAM_ALLOW_FROM=<your-chat-id>              # Comma-separated allowed user IDs
TELEGRAM_ACK_REACTION=✅

# === PostgreSQL ===
OPENCLAW_POSTGRES_HOST=localhost
OPENCLAW_POSTGRES_PORT=5433
OPENCLAW_POSTGRES_DB=openclaw
OPENCLAW_POSTGRES_USER=openclaw
OPENCLAW_POSTGRES_PASSWORD=openclaw_dev_pass

# === Task Manager ===
MAX_ATTEMPTS=2
TESTER_MAX_ATTEMPTS=3
VERBOSE_TASK_LOGS=1
TASK_HEARTBEAT_SEC=120
TEST_CLEANUP_AFTER=1
TESTER_TIMEOUT=2400
TESTER_STEP_TIMEOUT=600

# === Chat Router ===
CHAT_ROUTER_PORT=18801
CHAT_ROUTER_URL=http://127.0.0.1:18801/route
CHAT_ROUTER_ASK_TIMEOUT_SEC=180

# === Model Health ===
MODEL_HEALTH_RETRIES=2
MODEL_HEALTH_CONNECT_TIMEOUT_SEC=5
MODEL_HEALTH_MODELS_TIMEOUT_SEC=20
MODEL_HEALTH_CHAT_TIMEOUT_SEC=90
MODEL_HEALTH_EMBED_TIMEOUT_SEC=60
```

### Step 6: Deploy

```bash
cd ~/based_claw/deployment
python3 setup.py
```

This will:
- Copy all templates to `~/.openclaw/`
- Install and configure all agent SOUL files
- Set up systemd services and timers
- Generate `~/.env` with runtime config
- Enable and start all services

Verify everything is running:

```bash
systemctl --user list-timers --no-pager
systemctl --user status openclaw-gateway.service
systemctl --user status openclaw-chat-router.service
```

### Step 7: Initialize the Task Database

```bash
cd ~/.openclaw/workspace
psql -h localhost -p 5433 -U openclaw -d openclaw -f autonomous-tasks-schema.sql
```

### Step 7b: Enable Vector Memory (pgvector)

```bash
# Install the pgvector extension in PostgreSQL
psql -h localhost -p 5433 -U openclaw -d openclaw -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Install fastembed for local embeddings
pip3 install --break-system-packages fastembed

# Initialize the memories table and HNSW index
cd ~/.openclaw/workspace
python3 vector-memory.py migrate
```

> **Note:** The first run of `vector-memory.py` will download the bge-small-en-v1.5 model (~130MB) to `~/.cache/`. Subsequent runs load instantly.

### Step 8: Create Working Directories

```bash
mkdir -p ~/.openclaw/workspace/{notes,inbox,memory}
```

### Step 9: Test the Bot

Send `/help` to your Telegram bot. You should see the full command list.

Test the chat router directly:

```bash
curl -s -X POST http://127.0.0.1:18801/route \
    -H "Content-Type: application/json" \
    -d '{"text":"/briefing"}'
```

### Step 10: Set Up Gmail & Calendar (Optional)

#### 10a. Google Cloud Console

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable the **Gmail API** and **Google Calendar API**:
   - APIs & Services → Library → search "Gmail API" → Enable
   - APIs & Services → Library → search "Google Calendar API" → Enable
4. Configure OAuth consent screen:
   - APIs & Services → OAuth consent screen
   - User type: **External** (or Internal if using Workspace)
   - Add scopes: `gmail.readonly`, `gmail.send`, `gmail.modify`, `calendar`, `calendar.events`
   - Add your email as a test user
5. Create OAuth credentials:
   - APIs & Services → Credentials → Create Credentials → OAuth client ID
   - Application type: **Desktop app**
   - Download the JSON file

#### 10b. Install Python Dependencies

```bash
pip3 install --user google-auth google-auth-oauthlib google-api-python-client

# If on Ubuntu 24.04+ with externally-managed Python:
pip3 install --break-system-packages google-auth google-auth-oauthlib google-api-python-client
```

#### 10c. Run OAuth Flow

```bash
# Save the downloaded JSON
cp ~/Downloads/client_secret_*.json ~/.openclaw/google-credentials.json

# Run auth flow
cd ~/.openclaw/workspace
python3 google-services.py --auth
```

A URL will be printed. Open it in your browser, sign in, approve permissions, then copy the redirect URL (it will fail to load — that's expected) and paste it back into the terminal.

#### 10d. Verify

```bash
python3 google-services.py --test
```

You should see: `✅ Google authentication is working`

### Step 11: Set Up Web Search (Optional)

Deploy SearXNG for self-hosted web search:

```bash
docker run -d --name searxng \
    -p 8888:8080 \
    -e SEARXNG_SECRET=$(openssl rand -hex 32) \
    --restart unless-stopped \
    searxng/searxng:latest

# Enable JSON output format
docker exec searxng sed -i 's/formats: \[\]/formats: ["json"]/' /etc/searxng/settings.yml 2>/dev/null || \
docker exec searxng sh -c "sed -i '/^search:/a\\    formats:\\n      - json' /etc/searxng/settings.yml"
docker restart searxng
```

Add to `~/.env`:

```bash
echo "SEARXNG_URL=http://localhost:8888" >> ~/.env
systemctl --user restart openclaw-chat-router.service
```

### Step 12: Set Up Weather (Optional)

1. Get a free API key from [OpenWeatherMap](https://openweathermap.org/api) (sign up → API keys)
2. Add to `~/.env`:

```bash
echo "OPENWEATHER_API_KEY=<your-key>" >> ~/.env
systemctl --user restart openclaw-chat-router.service
```

> **Note:** New OpenWeatherMap keys can take up to 2 hours to activate.

### Step 13: Set Up Email Monitor (Optional)

Requires Gmail to be configured (Step 10).

```bash
# Create systemd service
cat > ~/.config/systemd/user/email-monitor.service << 'EOF'
[Unit]
Description=Ashley Email Monitor

[Service]
Type=oneshot
WorkingDirectory=/home/$USER/.openclaw/workspace
ExecStart=/usr/bin/python3 /home/$USER/.openclaw/workspace/email-monitor.py
EOF

# Create timer (every 5 minutes)
cat > ~/.config/systemd/user/email-monitor.timer << 'EOF'
[Unit]
Description=Check Gmail every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now email-monitor.timer
```

### Step 14: Schedule Recurring Jobs (Optional)

```bash
# Morning briefing at 8 AM daily
curl -s -X POST http://127.0.0.1:18801/route \
    -H "Content-Type: application/json" \
    -d '{"text":"/schedule 0 8 * * * /briefing"}'

# Weekly review on Sundays at 7 PM
curl -s -X POST http://127.0.0.1:18801/route \
    -H "Content-Type: application/json" \
    -d '{"text":"/schedule 0 19 * * 0 /weeklyreview"}'
```

### Customization

#### Personality

Edit these files in `deployment/templates/workspace/` then re-run `python3 deployment/setup.py`:

| File | Purpose |
|---|---|
| `SOUL.md` | Core personality, boundaries, vibe, autonomy preferences |
| `USER.md` | Owner profile (name, timezone, interests, preferences) |
| `IDENTITY.md` | Bot name and identity |

#### Agent Behavior

Edit files in `deployment/templates/agents/`:

| File | Purpose |
|---|---|
| `planner/agent/SOUL.md` | How the Planner decomposes tasks |
| `coder/agent/SOUL.md` | How Doer agents execute work |
| `tester/agent/SOUL.md` | How the Reviewer validates quality |

### Managing Services

```bash
# View all services
systemctl --user list-timers --no-pager
systemctl --user list-units --type=service --no-pager | grep openclaw

# Restart everything
bash ~/based_claw/scripts/start-openclaw-services.sh

# Stop everything
bash ~/based_claw/scripts/stop-openclaw-services.sh

# Restart a single service
systemctl --user restart openclaw-chat-router.service

# View logs
journalctl --user -u openclaw-chat-router.service -f
journalctl --user -u openclaw-gateway.service --since "1 hour ago"
```

### Redeploying After Config Changes

```bash
cd ~/based_claw/deployment
python3 setup.py
systemctl --user restart openclaw-chat-router.service
```

### Troubleshooting

| Problem | Solution |
|---|---|
| Bot not responding | Check `systemctl --user status openclaw-telegram-commands.timer` |
| Tasks not processing | Check `systemctl --user status openclaw-task-manager-db.timer` |
| Gateway MODULE_NOT_FOUND | Reinstall: `cd ~/.local/openclaw && npm install openclaw@2026.2.9` |
| Gmail "Not authenticated" | Re-run `python3 google-services.py --auth` |
| Weather 401 error | New API keys take up to 2 hours to activate |
| SearXNG no results | Ensure JSON format is enabled in SearXNG settings |
| Vector memory empty results | Run `python3 vector-memory.py migrate` to create table & HNSW index |
| fastembed model slow first load | Normal — first call downloads/loads model (~10-15s). Cached after. |
| Services die on logout | Run `sudo loginctl enable-linger $USER` |

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
- ~~**Mobile Quick Actions**~~ — ✅ Done. Inline keyboards added for `/tasks`, `/emails`, `/jobs`, `/pending`, and more.
- ~~**Inter-Agent Memory**~~ — ✅ Done. Vector memory system (pgvector + fastembed) provides persistent semantic recall across sessions.
- **Dashboard / Web UI** — All interaction is via Telegram text. A simple web dashboard showing tasks, agent status, email queue, and calendar would add visibility.
- **Backup & Recovery** — No automated backup of the PostgreSQL task DB, notes, bookmarks, or state files.
- **Multi-User Support** — Currently single-user only. Architecture could extend to support multiple Telegram users with isolated contexts.

## License

Private project.
