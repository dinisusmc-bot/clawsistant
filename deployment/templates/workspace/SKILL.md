# Skill: Ask the Owner a Clarifying Question

When you are uncertain, need a preference, or require information that only the owner (Nick) can provide, you can ask a clarifying question.

## How to Ask

Send a POST request to the chat router:

```bash
curl -s -X POST http://127.0.0.1:18801/ask-owner \
  -H "Content-Type: application/json" \
  -d '{"agent": "YOUR_AGENT_NAME", "task_id": TASK_ID, "question": "Your question here"}'
```

- **agent** (required): Your agent name (e.g. "planner", "coder", "tester")
- **task_id** (optional): The numeric task ID you're working on. Include when relevant.
- **question** (required): A clear, specific question.

The question is delivered to Nick via Telegram. His answer will be appended to the task's solution field and you will be dispatched to continue.

## When to Ask

- You need a **preference** or **decision** (e.g. "Should the report include crypto prices?")
- A task is **ambiguous** and could go multiple directions
- You need **access credentials** or external info you can't look up
- You're about to make a **significant assumption** that could be wrong

## When NOT to Ask

- The answer is in USER.md, IDENTITY.md, or project context
- You can make a reasonable default choice and note it
- The question is about implementation details you can decide yourself
- You've already asked the same question recently

## Writing Good Questions

1. **Be specific** — not "What should I do?" but "Should the tech report cover AI, cybersecurity, or both?"
2. **Offer options** — "Option A: daily digest at 9am. Option B: real-time alerts. Which do you prefer?"
3. **Provide context** — briefly explain why you're asking
4. **One question per request** — don't bundle multiple questions

## Important Notes

- Questions expire after 60 minutes if unanswered
- Only ask when you're genuinely blocked — don't ask frivolous questions
- After asking, you may continue working on other tasks while waiting
- The answer arrives asynchronously; you'll be re-dispatched when it's ready

---

# Skill: Gmail

You can read, search, and send emails through the owner's Gmail account (dinisusmc@gmail.com).

## Read Inbox

```bash
curl -s http://127.0.0.1:18801/gmail/inbox
```

Returns: `{"ok": true, "emails": [{"id", "from", "to", "subject", "date", "snippet", "unread"}]}`

## Check Unread Count

```bash
curl -s http://127.0.0.1:18801/gmail/unread
```

Returns: `{"ok": true, "unread": 5}`

## Search Emails

```bash
curl -s -X POST http://127.0.0.1:18801/gmail/search \
  -H "Content-Type: application/json" \
  -d '{"query": "from:someone@example.com subject:invoice", "max_results": 5}'
```

Uses Gmail search syntax: `from:`, `to:`, `subject:`, `is:unread`, `after:2026/02/01`, etc.

## Read a Specific Email

```bash
curl -s -X POST http://127.0.0.1:18801/gmail/read \
  -H "Content-Type: application/json" \
  -d '{"id": "MESSAGE_ID_FROM_INBOX"}'
```

Returns full email body. Automatically marks as read.

## Send an Email

```bash
curl -s -X POST http://127.0.0.1:18801/gmail/send \
  -H "Content-Type: application/json" \
  -d '{"to": "recipient@example.com", "subject": "Hello", "body": "Email body text"}'
```

**Important:** Only send emails when explicitly instructed by the owner. Never send unsolicited emails.

---

# Skill: Google Calendar

You can view, create, and delete calendar events for the owner.

## View Today's Events

```bash
curl -s http://127.0.0.1:18801/calendar/today
```

## View This Week's Events

```bash
curl -s http://127.0.0.1:18801/calendar/week
```

Returns: `{"ok": true, "events": [{"id", "summary", "start", "end", "location", "description", "status"}]}`

## Create an Event

```bash
curl -s -X POST http://127.0.0.1:18801/calendar/create \
  -H "Content-Type: application/json" \
  -d '{"summary": "Meeting", "start_time": "2026-02-23T14:00:00", "end_time": "2026-02-23T15:00:00", "description": "Discuss roadmap", "location": "Zoom"}'
```

- **summary** (required): Event title
- **start_time** (required): ISO datetime or YYYY-MM-DD for all-day
- **end_time** (optional): defaults to 1 hour after start
- **description** (optional): Event details
- **location** (optional): Where
- **all_day** (optional): boolean, set true for all-day events

Times default to EST (America/New_York).

## Delete an Event

```bash
curl -s -X POST http://127.0.0.1:18801/calendar/delete \
  -H "Content-Type: application/json" \
  -d '{"id": "EVENT_ID"}'
```

## Guidelines

- When creating events, always use the owner's timezone (EST / America/New_York)
- Include relevant details in the description
- If a task mentions scheduling something, create a calendar event for it
- When reporting the owner's schedule, check `/calendar/today` or `/calendar/week`

---

# Skill: Weather

Get current weather and forecast data.

## Current Weather

```bash
curl -s -X POST http://127.0.0.1:18801/route \
  -H "Content-Type: application/json" \
  -d '{"text": "/weather"}'
```

Returns current conditions for the owner's default location (New York).

## Weather for a Specific Location

```bash
curl -s -X POST http://127.0.0.1:18801/route \
  -H "Content-Type: application/json" \
  -d '{"text": "/weather London,UK"}'
```

## Guidelines

- Use weather data in morning briefings and daily planning
- Include weather context when the owner's tasks involve outdoor activities

---

# Skill: Web Search

Search the web for current information using SearXNG or DuckDuckGo fallback.

## Search

```bash
curl -s -X POST http://127.0.0.1:18801/route \
  -H "Content-Type: application/json" \
  -d '{"text": "/search your query here"}'
```

Returns: title, URL, and snippet for each result.

## When to Search

- The owner asks about current events, prices, news, or live data
- A task requires up-to-date information you don't have
- You need to verify a fact or find a reference

## Guidelines

- Summarize search results; don't dump raw output
- Cite sources with URLs when reporting findings
- If search returns no results, say so rather than making things up

---

# Skill: Notes & Quick Capture

Save and retrieve notes for the owner.

## Save a Note

```bash
curl -s -X POST http://127.0.0.1:18801/route \
  -H "Content-Type: application/json" \
  -d '{"text": "/note Buy protein powder"}'
```

Notes are saved as timestamped files in `~/.openclaw/workspace/notes/`.

## List Recent Notes

```bash
curl -s -X POST http://127.0.0.1:18801/route \
  -H "Content-Type: application/json" \
  -d '{"text": "/notes"}'
```

Shows the last 10 notes.

## Guidelines

- When the owner says "remember this" or "note that," use /note
- Notes are simple text captures — for structured data, use files in the workspace

---

# Skill: Links & Bookmarks

Save and retrieve URLs/bookmarks for the owner.

## Save a Link

```bash
curl -s -X POST http://127.0.0.1:18801/route \
  -H "Content-Type: application/json" \
  -d '{"text": "/save https://example.com Optional description"}'
```

## List Saved Links

```bash
curl -s -X POST http://127.0.0.1:18801/route \
  -H "Content-Type: application/json" \
  -d '{"text": "/links"}'
```

## Guidelines

- When the owner shares a URL and says "save this" or "bookmark," use /save
- Links are stored in `~/.openclaw/workspace/agent-context/bookmarks.json`

---

# Skill: Morning Briefing

Generate a comprehensive morning briefing.

```bash
curl -s -X POST http://127.0.0.1:18801/route \
  -H "Content-Type: application/json" \
  -d '{"text": "/briefing"}'
```

The briefing includes:
- Current date and time
- Weather conditions
- Today's calendar events
- Unread email count
- Pending agent questions
- Active/recent tasks
- Recent notes

## Guidelines

- Use this in scheduled morning jobs to give the owner a daily overview
- Can also be triggered on demand

---

# Skill: Weekly Review

Generate a weekly performance and activity summary.

```bash
curl -s -X POST http://127.0.0.1:18801/route \
  -H "Content-Type: application/json" \
  -d '{"text": "/weeklyreview"}'
```

Includes:
- Tasks completed this week
- Tasks still in progress or failed
- Notes and bookmarks saved this week

## Guidelines

- Good for scheduling as a Sunday evening or Monday morning job
- Helps the owner track productivity trends
