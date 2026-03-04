#!/usr/bin/env python3
"""Twilio Call Recording Monitor with AI Analysis.

Polls Twilio for new call recordings, downloads and transcribes them
via local Whisper, then uses LLM to extract:
  - Action items and deliverables
  - Follow-up meetings/deadlines -> auto-creates calendar events
  - Project/technical requirements -> emails a structured build prompt
  - Key decisions and commitments

Sends a concise Telegram report with actionable intelligence.

Usage:
    python3 twilio-call-monitor.py           # Single check
    python3 twilio-call-monitor.py --dry      # Dry run
    python3 twilio-call-monitor.py --daemon   # Continuous polling
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from twilio.rest import Client

# ── Import local services ──────────────────────────────────────────────────

WORKSPACE_DIR = Path.home() / ".openclaw" / "workspace"
sys.path.insert(0, str(WORKSPACE_DIR))

import microsoft_services as ms  # noqa: E402

# ── Vector Memory ─────────────────────────────────────────────────────────

def _get_vmem():
    """Lazy-load the vector memory module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "vector_memory",
        str(WORKSPACE_DIR / "vector-memory.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ── Config ─────────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".openclaw"
CREDENTIALS_FILE = CONFIG_DIR / "twilio-credentials.json"
STATE_FILE = CONFIG_DIR / "twilio-call-monitor-state.json"
RECORDINGS_DIR = CONFIG_DIR / "call-recordings"
TRANSCRIPTS_DIR = CONFIG_DIR / "call-transcripts"
ANALYSIS_DIR = CONFIG_DIR / "call-analysis"

POLL_INTERVAL_SEC = 5 * 60  # 5 minutes
MAX_SEEN_SIDS = 200

# LiteLLM endpoints
LITELLM_BASE = "http://ai-services:8010/v1"
LITELLM_KEY = "sk-1234"
WHISPER_MODEL = "whisper-stt"
LLM_MODEL = "minimax-m2.5"

# Telegram (direct — not through OpenClaw plugin)
TELEGRAM_BOT_TOKEN = "7202673884:AAGjqUoBRfNNN8sda4Fn9twdJmdUbdee9fU"
TELEGRAM_CHAT_ID = "8302484666"

# Pending handoff approvals
PENDING_HANDOFFS_FILE = CONFIG_DIR / "pending-bot-handoffs.json"

# Business context
BUSINESS_NUMBER = "+17323859499"
PERSONAL_NUMBER = "+17323970270"
OWNER_EMAIL = "nick@sempersolved.com"
OWNER_NAME = "Nick"
CALENDAR_TZ = "America/New_York"

# Today's date for LLM context
TODAY = datetime.now().strftime("%A, %B %d, %Y")


# ── Twilio Client ──────────────────────────────────────────────────────────

def _get_client() -> Client:
    creds = json.loads(CREDENTIALS_FILE.read_text())
    return Client(creds["account_sid"], creds["auth_token"])


# ── State Management ───────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"seen_recording_sids": [], "last_check": None}


def save_state(state: dict) -> None:
    state["seen_recording_sids"] = state["seen_recording_sids"][-MAX_SEEN_SIDS:]
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Recording Download ─────────────────────────────────────────────────────

def fetch_new_recordings(client: Client, seen_sids: set) -> list:
    recordings = client.recordings.list(limit=20)
    new = []
    for rec in recordings:
        if rec.sid not in seen_sids:
            new.append({
                "sid": rec.sid,
                "call_sid": rec.call_sid,
                "date_created": str(rec.date_created),
                "duration": rec.duration,
                "status": rec.status,
            })
    return new


def download_recording(recording_sid: str) -> Path | None:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RECORDINGS_DIR / f"{recording_sid}.wav"
    if output_path.exists():
        return output_path

    try:
        creds = json.loads(CREDENTIALS_FILE.read_text())
        url = f"https://api.twilio.com/2010-04-01/Accounts/{creds['account_sid']}/Recordings/{recording_sid}.wav"
        import requests
        from requests.auth import HTTPBasicAuth
        resp = requests.get(url, auth=HTTPBasicAuth(creds["account_sid"], creds["auth_token"]), stream=True)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        size_kb = output_path.stat().st_size / 1024
        print(f"[call-monitor] Downloaded {recording_sid}.wav ({size_kb:.0f} KB)")
        return output_path
    except Exception as e:
        print(f"[call-monitor] Download error: {e}", file=sys.stderr)
        return None


# ── Transcription via Whisper ──────────────────────────────────────────────

def transcribe_recording(audio_path: Path) -> str | None:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    transcript_path = TRANSCRIPTS_DIR / f"{audio_path.stem}.txt"
    if transcript_path.exists():
        return transcript_path.read_text()

    try:
        import requests
        with open(audio_path, "rb") as f:
            resp = requests.post(
                f"{LITELLM_BASE}/audio/transcriptions",
                headers={"Authorization": f"Bearer {LITELLM_KEY}"},
                files={"file": (audio_path.name, f, "audio/wav")},
                data={"model": WHISPER_MODEL},
                timeout=180,
            )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
        if text:
            transcript_path.write_text(text)
            print(f"[call-monitor] Transcribed {audio_path.stem}: {len(text)} chars")
        else:
            text = "(empty — possibly a very short or silent call)"
            transcript_path.write_text(text)
        return text
    except Exception as e:
        print(f"[call-monitor] Transcription error: {e}", file=sys.stderr)
        return None


# ── Call Info ──────────────────────────────────────────────────────────────

def get_call_info(client: Client, call_sid: str) -> dict:
    try:
        call = client.calls(call_sid).fetch()
        return {
            "from": call.from_formatted,
            "to": call.to_formatted,
            "direction": call.direction,
            "status": call.status,
            "start_time": str(call.start_time) if call.start_time else "",
            "duration": call.duration,
        }
    except Exception as e:
        print(f"[call-monitor] Call info error: {e}", file=sys.stderr)
        return {}


# ── LLM Analysis ──────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """You are an executive assistant AI analyzing a phone call transcript for {owner_name}.

Today's date: {today}
Caller: {caller}
Direction: {direction}
Duration: {duration}

TRANSCRIPT:
{transcript}

---

Analyze this call and respond with a JSON object (no markdown fencing, just raw JSON):

{{
  "summary": "2-3 sentence summary of the call",
  "caller_name": "Best guess at the caller's name, or 'Unknown'",
  "caller_company": "Their company if mentioned, or null",
  "topic": "Brief topic label (e.g., 'Project Demo Scheduling', 'Sales Call')",

  "action_items": [
    {{
      "description": "What needs to be done",
      "owner": "nick or other_party",
      "deadline": "YYYY-MM-DD or null if not specified",
      "priority": "high|medium|low"
    }}
  ],

  "calendar_events": [
    {{
      "title": "Event title (be specific, include project/client name)",
      "date": "YYYY-MM-DD",
      "start_time": "HH:MM (24h, Eastern Time)",
      "end_time": "HH:MM (24h, Eastern Time)",
      "type": "meeting|deadline|work_block",
      "description": "Brief context for the calendar event"
    }}
  ],

  "project_requirements": {{
    "is_technical": true,
    "project_name": "Name of the project if discussed, or null",
    "requirements_summary": "Summary of technical/project requirements discussed, or null",
    "build_prompt": "If technical requirements were discussed, write a detailed prompt that a coding agent could use to build what was discussed. Include all specific requirements, tech stack preferences, features, and constraints mentioned. Make it actionable and comprehensive. null if no technical work discussed."
  }},

  "coding_bot_relevance": {{
    "should_send": "high|medium|low",
    "reasoning": "Brief explanation of why this should/shouldn't go to the coding bot"
  }},

  "key_memories": [
    {{
      "content": "A specific, standalone fact worth remembering long-term (e.g., 'Client John Smith from Acme Corp prefers React over Vue', 'Project Alpha deadline is March 15 2026', 'Jane's email is jane@example.com')",
      "category": "fact|project|note",
      "tags": "client_name, project_name, or other relevant tags"
    }}
  ],

  "follow_up_needed": true,
  "sentiment": "positive|neutral|negative",
  "key_decisions": ["List of any decisions made during the call"],
  "commitments_made": ["List of promises/commitments {owner_name} made"]
}}

IMPORTANT RULES:
- For calendar events with vague times like "Friday", use a reasonable business hour (10:00 AM).
- If someone said "I'll have X ready by Friday", create BOTH a deadline event AND a work block before it.
- For work blocks, schedule 1-2 hours before the deadline.
- All times are Eastern Time.
- Be specific in event titles — include client/project names.
- For the build_prompt, be extremely detailed and specific. Include every requirement mentioned.
- If the call is just a brief/personal call with no action items, keep the response minimal with empty arrays.
- is_technical should be false and build_prompt should be null if no project/technical work was discussed.

CODING BOT RULES (coding_bot_relevance):
- "high": Clear, concrete technical/build requirements were discussed with enough detail to act on. New project plans, feature requests with specs, bug reports with reproduction steps, or explicit updates to an existing project.
- "medium": Some technical content but vague, or it's an update/status check rather than new work. Could be useful but might not warrant sending to the coder.
- "low": Personal call, scheduling only, general business discussion, sales call, or no actionable technical content.
- Be STRICT: only mark "high" when there are real, actionable build/code requirements. When in doubt, use "medium".

MEMORY RULES (key_memories):
- Extract EVERY important fact about people, companies, projects, preferences, contact info, deadlines, budgets, and relationships.
- Each memory should be a complete, standalone sentence that makes sense without context.
- Include the person's full name and company when known.
- Categories: "fact" for client/person info and preferences, "project" for project details and requirements, "note" for general observations and decisions.
- Be thorough — capture anything {owner_name} might want to recall later when asking about this client or project.
- Do NOT store trivial pleasantries or scheduling logistics as memories.
"""


def analyze_transcript(transcript: str, call_info: dict, recording: dict) -> dict | None:
    """Send transcript to LLM for intelligent analysis."""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    analysis_path = ANALYSIS_DIR / f"{recording['sid']}.json"
    if analysis_path.exists():
        try:
            return json.loads(analysis_path.read_text())
        except Exception:
            pass

    direction = call_info.get("direction", "unknown")
    if direction == "inbound":
        caller = call_info.get("from", "Unknown number")
    else:
        caller = call_info.get("to", "Unknown number")

    duration_sec = int(recording.get("duration", 0) or 0)
    duration_str = f"{duration_sec // 60}m {duration_sec % 60}s" if duration_sec >= 60 else f"{duration_sec}s"

    prompt = ANALYSIS_PROMPT.format(
        owner_name=OWNER_NAME,
        today=TODAY,
        caller=caller,
        direction=direction,
        duration=duration_str,
        transcript=transcript,
    )

    content = ""
    try:
        import requests
        resp = requests.post(
            f"{LITELLM_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {LITELLM_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 4000,
            },
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip <think>...</think> reasoning blocks (some models emit these)
        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL)

        # Parse JSON from response (handle markdown fencing if present)
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*\n?", "", content)
            content = re.sub(r"\n?```\s*$", "", content)

        analysis = json.loads(content)
        analysis_path.write_text(json.dumps(analysis, indent=2))
        print(f"[call-monitor] Analysis complete for {recording['sid']}")
        return analysis

    except json.JSONDecodeError as e:
        print(f"[call-monitor] LLM returned invalid JSON: {e}", file=sys.stderr)
        print(f"[call-monitor] Raw content: {content[:500]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[call-monitor] LLM analysis error: {e}", file=sys.stderr)
        return None


# ── Calendar Integration ───────────────────────────────────────────────────

def create_calendar_events(analysis: dict, recording_sid: str) -> list:
    """Create calendar events from LLM analysis."""
    created = []
    events = analysis.get("calendar_events", [])
    if not events:
        return created

    for ev in events:
        try:
            date = ev.get("date")
            start_time = ev.get("start_time", "10:00")
            end_time = ev.get("end_time", "11:00")
            title = ev.get("title", "Follow-up")
            description = ev.get("description", "")
            ev_type = ev.get("type", "meeting")

            if not date:
                continue

            start_dt = f"{date}T{start_time}:00"
            end_dt = f"{date}T{end_time}:00"

            body = f"<p>{description}</p>"
            body += f"<p><em>Auto-created from call recording {recording_sid}</em></p>"
            if ev_type == "work_block":
                body += "<p><strong>Work Block</strong> — dedicated time to complete this task</p>"

            if ev_type == "deadline":
                title = f"DEADLINE: {title}"
            elif ev_type == "work_block":
                title = f"[Work] {title}"

            result = ms.create_event(
                subject=title,
                start=start_dt,
                end=end_dt,
                body=body,
                timezone_str=CALENDAR_TZ,
            )
            created.append({
                "title": title,
                "date": date,
                "time": f"{start_time}-{end_time}",
                "id": result.get("id"),
            })
            print(f"[call-monitor] Created calendar event: {title} on {date}")

        except Exception as e:
            print(f"[call-monitor] Calendar create error: {e}", file=sys.stderr)

    return created


# ── Email Project Specs ────────────────────────────────────────────────────

def email_project_specs(analysis: dict, recording_sid: str) -> bool:
    """Email project requirements and build prompt if technical work was discussed."""
    proj = analysis.get("project_requirements", {})
    if not proj or not proj.get("is_technical") or not proj.get("build_prompt"):
        return False

    project_name = proj.get("project_name") or "Project from Call"
    requirements = proj.get("requirements_summary", "")
    build_prompt = proj.get("build_prompt", "")
    caller_name = analysis.get("caller_name", "Unknown")
    topic = analysis.get("topic", "Call")
    now_str = datetime.now().strftime("%b %d, %Y %I:%M %p ET")

    subject = f"Build Spec: {project_name} -- from call with {caller_name}"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:700px;margin:0 auto;padding:20px;color:#1a1a2e;">

<div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:24px;border-radius:12px 12px 0 0;text-align:center;">
    <h1 style="color:#fff;margin:0;font-size:22px;">Build Specification</h1>
    <p style="color:rgba(255,255,255,0.85);margin:8px 0 0;font-size:14px;">{project_name}</p>
</div>

<div style="background:#fff;padding:24px;border:1px solid #e2e8f0;border-top:none;">
    <h2 style="color:#2d3748;font-size:16px;margin-top:0;">Source</h2>
    <p style="color:#718096;font-size:14px;">
        Call with <strong>{caller_name}</strong>
        {f' ({analysis.get("caller_company")})' if analysis.get("caller_company") else ''}<br>
        Topic: {topic}<br>
        Recording: {recording_sid}
    </p>

    <h2 style="color:#2d3748;font-size:16px;">Requirements Summary</h2>
    <p style="color:#4a5568;font-size:14px;line-height:1.6;">{requirements}</p>

    <h2 style="color:#2d3748;font-size:16px;">Build Prompt (copy into coding agent)</h2>
    <div style="background:#f7fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin:12px 0;">
        <p style="color:#2d3748;font-size:13px;font-family:'Courier New',monospace;line-height:1.5;white-space:pre-wrap;">{build_prompt}</p>
    </div>
</div>

<div style="padding:16px;text-align:center;color:#a0aec0;font-size:11px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;">
    Auto-generated by Call Monitor | {now_str}
</div>

</body>
</html>"""

    try:
        result = ms.send_html_email(
            to=OWNER_EMAIL,
            subject=subject,
            html_content=html,
        )
        print(f"[call-monitor] Emailed project spec: {project_name}")
        return result
    except Exception as e:
        print(f"[call-monitor] Email spec error: {e}", file=sys.stderr)
        return False


# ── Bot-to-Bot Handoff ──────────────────────────────────────────────────────

CODING_BOT_URL = "https://mcp-bot-1.tail0d0958.ts.net/route"


def _send_build_prompt(analysis: dict, recording_sid: str) -> dict | None:
    """Actually POST the build prompt to the coding bot. Internal helper."""
    proj = analysis.get("project_requirements", {})
    project_name = proj.get("project_name") or "Project from Call"
    build_prompt = proj.get("build_prompt", "")
    caller_name = analysis.get("caller_name", "Unknown")
    topic = analysis.get("topic", "Call")

    message = (
        f"/think BUILD REQUEST from phone call with {caller_name} "
        f"(topic: {topic}, recording: {recording_sid})\n\n"
        f"Project: {project_name}\n\n"
        f"{build_prompt}"
    )

    try:
        import requests
        resp = requests.post(
            CODING_BOT_URL,
            json={"text": message},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        print(f"[call-monitor] Sent build prompt to coding bot: {project_name}")
        return result
    except Exception as e:
        print(f"[call-monitor] Coding bot handoff error: {e}", file=sys.stderr)
        return None


def send_to_coding_bot(analysis: dict, recording_sid: str) -> dict | None:
    """Decide whether to send to coding bot based on confidence level.

    Returns:
      - Response dict if sent (high confidence auto-send)
      - {"status": "pending_approval"} if queued for owner confirmation (medium)
      - None if skipped (low confidence or no technical content)
    """
    proj = analysis.get("project_requirements", {})
    if not proj or not proj.get("is_technical") or not proj.get("build_prompt"):
        return None

    relevance = analysis.get("coding_bot_relevance", {})
    confidence = relevance.get("should_send", "low").lower()
    reasoning = relevance.get("reasoning", "No reasoning provided")

    if confidence == "high":
        # Auto-send — clear actionable requirements
        return _send_build_prompt(analysis, recording_sid)

    elif confidence == "medium":
        # Queue for owner approval via Telegram inline keyboard
        _queue_pending_handoff(analysis, recording_sid, reasoning)
        return {"status": "pending_approval", "reasoning": reasoning}

    else:
        # Low confidence — skip
        print(f"[call-monitor] Skipping coding bot (low relevance): {reasoning}")
        return None


def _queue_pending_handoff(analysis: dict, recording_sid: str, reasoning: str) -> None:
    """Save a pending handoff and send Telegram confirmation buttons."""
    pending = _load_pending_handoffs()

    proj = analysis.get("project_requirements", {})
    project_name = proj.get("project_name") or "Project from Call"

    entry = {
        "recording_sid": recording_sid,
        "project_name": project_name,
        "reasoning": reasoning,
        "analysis": analysis,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    pending[recording_sid] = entry
    _save_pending_handoffs(pending)

    # Send Telegram message with inline keyboard
    text = (
        f"CODING BOT APPROVAL NEEDED\n\n"
        f"Project: {project_name}\n"
        f"Reasoning: {reasoning}\n\n"
        f"Build prompt preview:\n{proj.get('build_prompt', '')[:400]}...\n\n"
        f"Send this to the coding bot?"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Approve - Send to Coder", "callback_data": f"approve:{recording_sid}"},
                {"text": "Skip", "callback_data": f"reject:{recording_sid}"},
            ]
        ]
    }
    _send_telegram_with_keyboard(text, keyboard)
    print(f"[call-monitor] Queued pending handoff for approval: {project_name}")


def _send_telegram_with_keyboard(text: str, reply_markup: dict) -> bool:
    """Send a Telegram message with inline keyboard buttons."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:3997] + "..."
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "reply_markup": reply_markup,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[call-monitor] Telegram keyboard send error: {e}", file=sys.stderr)
        return False


def _load_pending_handoffs() -> dict:
    if PENDING_HANDOFFS_FILE.exists():
        try:
            return json.loads(PENDING_HANDOFFS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_pending_handoffs(pending: dict) -> None:
    PENDING_HANDOFFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_HANDOFFS_FILE.write_text(json.dumps(pending, indent=2))


def process_pending_approvals() -> dict:
    """No-op — approvals are now handled by telegram-task-commands.py.

    The telegram-task-commands service is the sole consumer of Telegram
    getUpdates and routes approve:/reject: callbacks directly.  Keeping
    this stub so callers don't break.
    """
    return {"approved": 0, "rejected": 0, "errors": 0}


# _get_telegram_callbacks and _answer_callback removed —
# approval processing is now handled by telegram-task-commands.py
# which is the sole consumer of Telegram getUpdates.


# ── Memory Storage ─────────────────────────────────────────────────────────

def store_call_memories(analysis: dict, call_info: dict, recording: dict) -> int:
    """Extract and store important facts from the call analysis into vector memory.

    Returns the number of memories stored.
    """
    stored = 0
    try:
        vm = _get_vmem()
    except Exception as e:
        print(f"[call-monitor] Vector memory load error: {e}", file=sys.stderr)
        return 0

    rec_sid = recording.get("sid", "unknown")
    caller_name = analysis.get("caller_name", "Unknown")
    company = analysis.get("caller_company")
    topic = analysis.get("topic", "Call")
    direction = call_info.get("direction", "unknown")
    call_date = datetime.now().strftime("%Y-%m-%d")

    source = f"call:{rec_sid}"
    base_meta = {
        "recording_sid": rec_sid,
        "caller_name": caller_name,
        "call_date": call_date,
        "direction": direction,
    }
    if company:
        base_meta["caller_company"] = company

    # 1. Store LLM-extracted key memories
    key_memories = analysis.get("key_memories", [])
    if key_memories:
        batch_items = []
        for mem in key_memories:
            content = mem.get("content", "").strip()
            if not content:
                continue
            category = mem.get("category", "fact")
            if category not in ("fact", "project", "note"):
                category = "fact"
            tags = mem.get("tags", "")
            meta = {**base_meta, "tags": tags}
            batch_items.append({
                "content": content,
                "category": category,
                "source": source,
                "metadata": meta,
            })
        if batch_items:
            ids = vm.store_batch(batch_items)
            stored += len(ids)
            print(f"[call-monitor] Stored {len(ids)} key memories from call")

    # 2. Store call summary as a note (always — useful for future recall)
    summary = analysis.get("summary", "")
    if summary:
        caller_label = caller_name
        if company:
            caller_label += f" ({company})"
        summary_content = f"Call with {caller_label} on {call_date}: {summary}"
        mid = vm.store(
            content=summary_content,
            category="note",
            source=source,
            metadata={**base_meta, "type": "call_summary"},
        )
        if mid:
            stored += 1

    # 3. Store commitments made (important for accountability)
    commitments = analysis.get("commitments_made", [])
    for commitment in commitments:
        if commitment and commitment.strip():
            content = f"Commitment to {caller_name}: {commitment} (call {call_date})"
            mid = vm.store(
                content=content,
                category="fact",
                source=source,
                metadata={**base_meta, "type": "commitment"},
            )
            if mid:
                stored += 1

    # 4. Store key decisions
    decisions = analysis.get("key_decisions", [])
    for decision in decisions:
        if decision and decision.strip():
            content = f"Decision with {caller_name}: {decision} (call {call_date})"
            mid = vm.store(
                content=content,
                category="note",
                source=source,
                metadata={**base_meta, "type": "decision"},
            )
            if mid:
                stored += 1

    # 5. Store project requirements if technical
    proj = analysis.get("project_requirements", {})
    if proj and proj.get("is_technical") and proj.get("requirements_summary"):
        project_name = proj.get("project_name") or "Project"
        content = f"Project '{project_name}' requirements (from call with {caller_name} on {call_date}): {proj['requirements_summary']}"
        mid = vm.store(
            content=content,
            category="project",
            source=source,
            metadata={**base_meta, "type": "project_requirements", "project_name": project_name},
        )
        if mid:
            stored += 1

    print(f"[call-monitor] Total memories stored for {rec_sid}: {stored}")
    return stored


# ── Telegram Report ────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:3997] + "..."
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except Exception as e:
        print(f"[call-monitor] Telegram error: {e}", file=sys.stderr)
        return False


def build_telegram_report(analysis: dict, call_info: dict, recording: dict,
                          created_events: list, spec_emailed: bool,
                          coding_bot_result: dict | None = None,
                          memories_stored: int = 0) -> str:
    """Build an actionable Telegram report from LLM analysis."""
    direction = call_info.get("direction", "unknown")
    caller = call_info.get("from", "Unknown") if direction == "inbound" else call_info.get("to", "Unknown")
    duration_sec = int(recording.get("duration", 0) or 0)
    duration_str = f"{duration_sec // 60}m {duration_sec % 60}s" if duration_sec >= 60 else f"{duration_sec}s"

    caller_name = analysis.get("caller_name", "Unknown")
    company = analysis.get("caller_company")
    sentiment = analysis.get("sentiment", "neutral")

    sentiment_icon = {"positive": "+", "neutral": "=", "negative": "-"}.get(sentiment, "?")

    lines = []

    # Header
    icon = "INCOMING" if direction == "inbound" else "OUTGOING"
    header = f"[{icon}] Call {'from' if direction == 'inbound' else 'to'} {caller_name}"
    if company:
        header += f" ({company})"
    lines.append(header)
    lines.append(f"{caller} | {duration_str} | sentiment: {sentiment_icon}")
    lines.append("")

    # Summary
    lines.append(analysis.get("summary", "No summary available"))
    lines.append("")

    # Action Items
    action_items = analysis.get("action_items", [])
    if action_items:
        lines.append(f"ACTION ITEMS ({len(action_items)}):")
        for i, item in enumerate(action_items, 1):
            priority = item.get("priority", "medium")
            p_tag = f"[{priority.upper()}]"
            owner = item.get("owner", "")
            deadline = item.get("deadline", "")
            owner_tag = f" ({owner})" if owner else ""
            deadline_tag = f" by {deadline}" if deadline else ""
            lines.append(f"  {p_tag} {i}. {item['description']}{owner_tag}{deadline_tag}")
        lines.append("")

    # Calendar Events Created
    if created_events:
        lines.append(f"CALENDAR UPDATED ({len(created_events)} events):")
        for ev in created_events:
            lines.append(f"  > {ev['title']} -- {ev['date']} {ev['time']}")
        lines.append("")

    # Key Decisions
    decisions = analysis.get("key_decisions", [])
    if decisions:
        lines.append("KEY DECISIONS:")
        for d in decisions:
            lines.append(f"  - {d}")
        lines.append("")

    # Commitments
    commitments = analysis.get("commitments_made", [])
    if commitments:
        lines.append("YOUR COMMITMENTS:")
        for c in commitments:
            lines.append(f"  - {c}")
        lines.append("")

    # Project Spec
    if spec_emailed:
        proj_name = analysis.get("project_requirements", {}).get("project_name", "Project")
        lines.append(f"BUILD SPEC EMAILED: {proj_name}")
        lines.append("  Check inbox for detailed prompt")
        lines.append("")

    # Coding Bot Handoff
    if coding_bot_result:
        proj_name = analysis.get("project_requirements", {}).get("project_name", "Project")
        status = coding_bot_result.get("status", "")
        if status == "pending_approval":
            reasoning = coding_bot_result.get("reasoning", "")
            lines.append(f"CODING BOT (AWAITING APPROVAL): {proj_name}")
            lines.append(f"  Reason: {reasoning}")
            lines.append("  Check Telegram for approval buttons")
        else:
            bot_reply = coding_bot_result.get("reply", "(sent)")
            lines.append(f"CODING BOT: {proj_name}")
            lines.append(f"  Auto-sent to mcp-bot-1 -> {bot_reply}")
        lines.append("")

    # Memories Stored
    if memories_stored > 0:
        lines.append(f"MEMORIES SAVED: {memories_stored} new facts/notes stored")
        lines.append("")

    return "\n".join(lines).strip()


# ── Main Check ─────────────────────────────────────────────────────────────

def run_check(dry_run: bool = False) -> dict:
    state = load_state()
    seen_sids = set(state.get("seen_recording_sids", []))

    client = _get_client()
    new_recordings = fetch_new_recordings(client, seen_sids)

    stats = {
        "total_new": len(new_recordings),
        "processed": 0,
        "calendar_events": 0,
        "specs_emailed": 0,
        "bot_handoffs": 0,
        "memories_stored": 0,
        "notified": 0,
        "skipped": 0,
        "errors": 0,
    }

    # Process any pending coding bot approvals from Telegram
    if not dry_run:
        approval_stats = process_pending_approvals()
        if approval_stats["approved"] > 0 or approval_stats["rejected"] > 0:
            print(f"[call-monitor] Pending approvals: {approval_stats['approved']} approved, "
                  f"{approval_stats['rejected']} rejected")

    for rec in new_recordings:
        rec_sid = rec["sid"]

        duration = int(rec.get("duration", 0) or 0)
        if duration < 5:
            seen_sids.add(rec_sid)
            stats["skipped"] += 1
            if dry_run:
                print(f"  [SKIP] {rec_sid} -- too short ({duration}s)")
            continue

        call_info = get_call_info(client, rec["call_sid"])

        if dry_run:
            seen_sids.add(rec_sid)
            print(f"  [NEW] {rec_sid} | {call_info.get('from', '?')} -> {call_info.get('to', '?')} | {duration}s")
            continue

        # Step 1: Download
        audio_path = download_recording(rec_sid)
        if not audio_path:
            stats["errors"] += 1
            # Don't mark as seen — retry on next run
            continue

        # Step 2: Transcribe
        transcript = transcribe_recording(audio_path)
        if not transcript or transcript.startswith("(empty"):
            seen_sids.add(rec_sid)  # Mark seen — no useful content to retry
            stats["skipped"] += 1
            continue

        # Step 3: LLM Analysis
        analysis = analyze_transcript(transcript, call_info, rec)
        if not analysis:
            send_telegram(f"New call ({duration}s) -- analysis failed\n\nTranscript preview:\n{transcript[:500]}")
            stats["errors"] += 1
            # Don't mark as seen — retry on next run
            continue

        # Mark as seen now that we have successful analysis
        seen_sids.add(rec_sid)
        stats["processed"] += 1

        # Step 4: Create calendar events
        created_events = create_calendar_events(analysis, rec_sid)
        stats["calendar_events"] += len(created_events)

        # Step 5: Email project specs if technical
        spec_emailed = email_project_specs(analysis, rec_sid)
        if spec_emailed:
            stats["specs_emailed"] += 1

        # Step 6: Send build prompt to coding bot if technical
        coding_bot_result = send_to_coding_bot(analysis, rec_sid)
        if coding_bot_result:
            stats["bot_handoffs"] += 1

        # Step 7: Store important memories from the call
        memories_stored = store_call_memories(analysis, call_info, rec)
        stats["memories_stored"] += memories_stored

        # Step 8: Send Telegram report
        report = build_telegram_report(analysis, call_info, rec, created_events, spec_emailed, coding_bot_result, memories_stored)
        if send_telegram(report):
            stats["notified"] += 1
        else:
            stats["errors"] += 1

    # Save state
    state["seen_recording_sids"] = list(seen_sids)
    if not dry_run:
        save_state(state)

    return stats


def run_daemon():
    print(f"[call-monitor] Starting daemon (poll every {POLL_INTERVAL_SEC}s)")
    while True:
        try:
            stats = run_check()
            ts = datetime.now().strftime("%H:%M:%S")
            if stats["total_new"] > 0:
                print(f"[call-monitor {ts}] new={stats['total_new']} "
                      f"processed={stats['processed']} "
                      f"calendar={stats['calendar_events']} "
                      f"specs={stats['specs_emailed']} "
                      f"bot_handoffs={stats['bot_handoffs']} "
                      f"memories={stats['memories_stored']} "
                      f"notified={stats['notified']}")
            else:
                print(f"[call-monitor {ts}] No new recordings")
        except Exception as e:
            print(f"[call-monitor] Error: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    dry_run = "--dry" in sys.argv
    daemon = "--daemon" in sys.argv

    if dry_run:
        print("[call-monitor] DRY RUN\n")
        stats = run_check(dry_run=True)
    elif daemon:
        run_daemon()
    else:
        stats = run_check()

    if not daemon:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[call-monitor {ts}] new={stats.get('total_new', 0)} "
              f"processed={stats.get('processed', 0)} "
              f"calendar={stats.get('calendar_events', 0)} "
              f"specs={stats.get('specs_emailed', 0)} "
              f"bot_handoffs={stats.get('bot_handoffs', 0)} "
              f"memories={stats.get('memories_stored', 0)} "
              f"notified={stats.get('notified', 0)} "
              f"errors={stats.get('errors', 0)}")
