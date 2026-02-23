#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

POSTGRES_HOST = os.environ.get("OPENCLAW_POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("OPENCLAW_POSTGRES_PORT", "5433")
POSTGRES_DB = os.environ.get("OPENCLAW_POSTGRES_DB", "openclaw")
POSTGRES_USER = os.environ.get("OPENCLAW_POSTGRES_USER", "openclaw")
POSTGRES_PASSWORD = os.environ.get("OPENCLAW_POSTGRES_PASSWORD", "openclaw_dev_pass")

OFFSET_FILE = Path.home() / ".openclaw" / "workspace" / ".telegram-offset"


def load_simple_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        if key in {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"} and not os.environ.get(key):
            os.environ[key] = value


def is_placeholder(value: str) -> bool:
    lowered = value.lower().strip()
    if not lowered:
        return True
    return any(token in lowered for token in ["your-telegram-bot-token-here", "your-chat-id-here"])


def load_telegram_from_openclaw() -> None:
    path = Path.home() / ".openclaw" / ".openclaw" / "openclaw.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return
    telegram = data.get("channels", {}).get("telegram", {})
    current_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not current_token or is_placeholder(current_token):
        token = telegram.get("botToken", "")
        if token:
            os.environ["TELEGRAM_BOT_TOKEN"] = token
    allow_from = telegram.get("allowFrom", [])
    if isinstance(allow_from, list) and allow_from:
        if not os.environ.get("TELEGRAM_ALLOW_FROM"):
            os.environ["TELEGRAM_ALLOW_FROM"] = ",".join(str(value) for value in allow_from)
        current_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not current_chat or is_placeholder(current_chat):
            os.environ["TELEGRAM_CHAT_ID"] = str(allow_from[0])


load_simple_env(Path.home() / ".env")
load_telegram_from_openclaw()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_ALLOW_FROM = [
    value.strip()
    for value in os.environ.get("TELEGRAM_ALLOW_FROM", "").split(",")
    if value.strip()
]
TELEGRAM_ACK_REACTION = os.environ.get("TELEGRAM_ACK_REACTION", "✅")
CHAT_ROUTER_BASE = os.environ.get("CHAT_ROUTER_BASE", "http://127.0.0.1:18801")
CHAT_ROUTER_URL = os.environ.get("CHAT_ROUTER_URL", f"{CHAT_ROUTER_BASE}/route")


def api_request(method: str, data: dict) -> tuple[bool, dict]:
    if not TELEGRAM_BOT_TOKEN:
        return False, {"ok": False, "result": []}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[{datetime.utcnow().isoformat()}] telegram api error={type(exc).__name__}")
        return False, {"ok": False, "result": []}


def send_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    api_request("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "text": text})


def send_reaction(chat_id: str, message_id: int, emoji: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not chat_id or not message_id:
        return False
    payload = json.dumps([{"type": "emoji", "emoji": emoji}])
    ok, response = api_request(
        "setMessageReaction",
        {"chat_id": chat_id, "message_id": message_id, "reaction": payload},
    )
    return ok and response.get("ok") is True


INBOX_DIR = Path.home() / ".openclaw" / "workspace" / "inbox"


def download_telegram_file(file_id: str, filename: str) -> str | None:
    """Download a file from Telegram and save to inbox directory."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    # Get file path from Telegram
    ok, resp = api_request("getFile", {"file_id": file_id})
    if not ok or not resp.get("ok"):
        return None
    file_path = resp.get("result", {}).get("file_path", "")
    if not file_path:
        return None
    # Download
    url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read()
        # Save with timestamp prefix to avoid collisions
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        safe_name = filename.replace("/", "_").replace("\\", "_")
        dest = INBOX_DIR / f"{ts}_{safe_name}"
        dest.write_bytes(data)
        return str(dest)
    except Exception as exc:
        print(f"[{datetime.utcnow().isoformat()}] telegram download error={type(exc).__name__}")
        return None


def has_pending_questions() -> bool:
    """Check if there are any pending agent questions."""
    url = f"{CHAT_ROUTER_BASE}/pending"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("count", 0) > 0
    except Exception:
        return False


def get_pending_questions() -> list:
    """Get list of pending agent questions."""
    url = f"{CHAT_ROUTER_BASE}/pending"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("questions", [])
    except Exception:
        return []


def route_reply_to_agent(answer: str) -> str:
    """Send an answer to the oldest pending agent question."""
    url = f"{CHAT_ROUTER_BASE}/reply"
    payload = json.dumps({"answer": answer}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("result", "Answer sent.")
    except Exception as exc:
        return f"Failed to send answer: {exc}"


def route_via_chat_router(text: str) -> str:
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        CHAT_ROUTER_URL, data=payload, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("reply", "")
    except Exception:
        return ""


def run_psql(query: str) -> str:
    env = os.environ.copy()
    env["PGPASSWORD"] = POSTGRES_PASSWORD
    cmd = [
        "psql",
        "-h",
        POSTGRES_HOST,
        "-p",
        str(POSTGRES_PORT),
        "-U",
        POSTGRES_USER,
        "-d",
        POSTGRES_DB,
        "-t",
        "-A",
        "-F",
        "|",
        "-c",
        query,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def task_context(task_id: int) -> str:
    row = run_psql(
        "SELECT COALESCE(project,''), COALESCE(implementation_plan,''), "
        "COALESCE(notes,''), COALESCE(solution,'') FROM autonomous_tasks WHERE id = %s;" % task_id
    )
    if not row:
        return ""
    project, plan, notes, solution = [s.strip() for s in row.split("|", 3)]
    lines = []
    if project:
        lines.append(f"Project: {project}")
    if plan:
        lines.append(f"Plan: {plan}")
    if notes:
        lines.append(f"Notes: {notes}")
    if solution:
        lines.append(f"Solution: {solution}")
    return "\n".join(lines)


def load_offset() -> int:
    if OFFSET_FILE.exists():
        try:
            return int(OFFSET_FILE.read_text().strip())
        except ValueError:
            return 0
    return 0


def save_offset(value: int) -> None:
    OFFSET_FILE.write_text(str(value))


def handle_command(text: str) -> str:
    parts = text.strip().split()
    if not parts:
        return ""
    cmd = parts[0].lower()

    status_aliases = {
        "todo": "TODO",
        "ready": "READY_FOR_TESTING",
        "ready_for_testing": "READY_FOR_TESTING",
        "ready-for-testing": "READY_FOR_TESTING",
        "in_progress": "IN_PROGRESS",
        "in-progress": "IN_PROGRESS",
        "inprogress": "IN_PROGRESS",
    }

    def parse_unblock_args(tokens: list[str]) -> tuple[str, str]:
        if not tokens:
            return "TODO", ""

        if len(tokens) >= 3:
            phrase = " ".join(tokens[:3]).lower()
            if phrase == "ready for testing":
                return "READY_FOR_TESTING", " ".join(tokens[3:]).strip()

        token = tokens[0].lower()
        if token in status_aliases:
            return status_aliases[token], " ".join(tokens[1:]).strip()

        return "TODO", " ".join(tokens).strip()

    def list_tasks_by_status(status: str, label: str) -> str:
        rows = run_psql(
            "SELECT id, name, COALESCE(phase,''), COALESCE(assigned_agent,'') "
            "FROM autonomous_tasks "
            "WHERE status = '%s' ORDER BY priority DESC, id ASC LIMIT 20;" % status
        )
        if not rows:
            return f"No {label} tasks."
        lines = [f"{label.title()} tasks:"]
        for line in rows.splitlines():
            tid, name, phase, agent = [s.strip() for s in line.split("|", 3)]
            extra = []
            if phase:
                extra.append(f"Phase: {phase}")
            if agent:
                extra.append(f"Agent: {agent}")
            suffix = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"#{tid} {name}{suffix}")
        return "\n".join(lines).strip()

    if cmd in ("/help", "help"):
        return (
            "Telegram task commands:\n"
            "/help - show this help\n"
            "/blockers - list blocked tasks (top 20)\n"
            "/todo - list todo tasks (top 20)\n"
            "/readyfortesting - list ready-for-testing tasks (top 20)\n"
            "/inprogress - list in-progress tasks (top 20)\n"
            "/tasks - summary counts for TODO/IN_PROGRESS/READY_FOR_TESTING\n"
            "/task <id> - show task status, phase, agent\n"
            "/unblock <id> [status] [solution] - requeue blocked task (default status TODO)\n"
            "  status options: TODO, READY_FOR_TESTING, IN_PROGRESS\n"
            "/unblock all [status] [note] - requeue all blocked tasks (default status TODO)\n"
            "/retry <id> - alias for /unblock <id>\n"
            "/digest now - send blocked tasks summary\n"
            "\n"
            "Planner/router commands:\n"
            "/plan <request> - queue planning request\n"
            "/think <request> - optimize request, then queue planning\n"
            "/prompt <request> - return optimized planning prompt only\n"
            "/ask <question> - async planner answer via owner-message\n"
            "/adhoc <instruction> - one-off async coder run (no task DB entry)\n"
            "/lesson <lesson learned> - save global lesson for future tasks\n"
            "/project <project>|<note> - save project-specific context\n"
            "\n"
            "Scheduling commands:\n"
            "/schedule <min> <hr> <dom> <mon> <dow> <task> - create recurring job\n"
            "/jobs - list all scheduled jobs\n"
            "/deletejob <id> - delete a scheduled job (or 'all')\n"
            "\n"
            "Agent questions:\n"
            "/pending - list pending agent questions\n"
            "/answer <text> - answer the oldest pending question\n"
            "(or just reply with plain text when questions are pending)\n"
            "\n"
            "Gmail & Calendar:\n"
            "/emails [search] - list inbox (optional Gmail search query)\n"
            "/email <id> - read a specific email\n"
            "/sendemail to | subject | body - send an email\n"
            "/unread - count of unread emails\n"
            "/calendar [days] - show upcoming events (default 7 days)\n"
            "/event <time> | <title> [| desc] [| location] - create event\n"
            "/delevent <id> - delete a calendar event\n"
            "\n"
            "Info & Tools:\n"
            "/weather [city] - current weather\n"
            "/search <query> - web search\n"
            "/briefing - morning briefing summary\n"
            "/weeklyreview - weekly activity summary\n"
            "\n"
            "Notes & Links:\n"
            "/note <text> - save a quick note\n"
            "/notes [search <query>] - view today's notes or search\n"
            "/save <url> [tags] - bookmark a link\n"
            "/links [tag] - list saved bookmarks\n"
            "\n"
            "Send a file/photo and it'll be saved to inbox."
        )

    if cmd == "/blockers":
        rows = run_psql(
            "SELECT id, name, COALESCE(blocked_reason,'') FROM autonomous_tasks "
            "WHERE status = 'BLOCKED' ORDER BY priority DESC, id ASC LIMIT 20;"
        )
        if not rows:
            return "No blocked tasks."
        lines = ["Blocked tasks:"]
        for line in rows.splitlines():
            tid, name, reason = [s.strip() for s in line.split("|", 2)]
            context = task_context(int(tid))
            lines.append(f"#{tid} {name}")
            if context:
                lines.append(context)
            if reason:
                lines.append(reason)
            lines.append("")
        return "\n".join(lines).strip()

    if cmd == "/todo":
        return list_tasks_by_status("TODO", "todo")

    if cmd == "/readyfortesting":
        return list_tasks_by_status("READY_FOR_TESTING", "ready-for-testing")

    if cmd == "/inprogress":
        return list_tasks_by_status("IN_PROGRESS", "in-progress")

    if cmd == "/tasks":
        row = run_psql(
            "SELECT "
            "SUM(CASE WHEN status = 'TODO' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'IN_PROGRESS' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'READY_FOR_TESTING' THEN 1 ELSE 0 END) "
            "FROM autonomous_tasks;"
        )
        if not row:
            return "No tasks found."
        todo, in_progress, ready = [value.strip() for value in row.split("|", 2)]
        return (
            "Task counts:\n"
            f"TODO: {todo}\n"
            f"IN_PROGRESS: {in_progress}\n"
            f"READY_FOR_TESTING: {ready}"
        )

    if cmd == "/task" and len(parts) >= 2 and parts[1].isdigit():
        task_id = int(parts[1])
        row = run_psql(
            "SELECT id, name, status, COALESCE(phase,''), COALESCE(assigned_agent,''), "
            "COALESCE(blocked_reason,'') FROM autonomous_tasks WHERE id = %s;"
            % task_id
        )
        if not row:
            return f"Task {task_id} not found."
        tid, name, status, phase, agent, reason = [s.strip() for s in row.split("|", 5)]
        msg = f"#{tid} {name}\nStatus: {status}"
        if phase:
            msg += f"\nPhase: {phase}"
        if agent:
            msg += f"\nAgent: {agent}"
        if reason:
            msg += f"\nBlocked: {reason}"
        return msg

    if cmd in ("/unblock", "/retry") and len(parts) >= 2 and parts[1].isdigit():
        task_id = int(parts[1])
        target_status, solution = parse_unblock_args(parts[2:])
        solution_sql = solution.replace("'", "''")
        count = run_psql(
            "WITH updated AS ("
            "UPDATE autonomous_tasks SET status = '%s', blocked_reason = NULL, error_log = NULL, "
            "assigned_agent = NULL, pid = NULL, started_at = NULL, attempt_count = 0, "
            "solution = CASE WHEN '%s' = '' THEN solution ELSE '%s' END "
            "WHERE id = %s AND status = 'BLOCKED' RETURNING id), "
            "deleted AS ("
            "DELETE FROM blocked_reasons WHERE task_id IN (SELECT id FROM updated) RETURNING task_id) "
            "SELECT COUNT(*) FROM updated;" % (target_status, solution_sql, solution_sql, task_id)
        )
        if count == "1":
            if solution:
                return f"Task {task_id} set to {target_status} with solution."
            return f"Task {task_id} set to {target_status}."
        return f"Task {task_id} not updated (not blocked or not found)."

    if cmd == "/unblock" and len(parts) >= 2 and parts[1].lower() == "all":
        target_status, note = parse_unblock_args(parts[2:])
        note_sql = note.replace("'", "''")
        count = run_psql(
            "WITH updated AS ("
            "UPDATE autonomous_tasks SET status = '%s', blocked_reason = NULL, error_log = NULL, "
            "assigned_agent = NULL, pid = NULL, started_at = NULL, attempt_count = 0, "
            "solution = CASE WHEN '%s' = '' THEN solution ELSE '%s' END "
            "WHERE status = 'BLOCKED' RETURNING id), "
            "deleted AS ("
            "DELETE FROM blocked_reasons WHERE task_id IN (SELECT id FROM updated) RETURNING task_id) "
            "SELECT COUNT(*) FROM updated;" % (target_status, note_sql, note_sql)
        )
        if count:
            if note:
                return f"Requeued {count} blocked tasks to {target_status} with note."
            return f"Requeued {count} blocked tasks to {target_status}."
        return "No blocked tasks to requeue."

    if cmd == "/pending":
        questions = get_pending_questions()
        if not questions:
            return "No pending agent questions."
        lines = [f"Pending questions ({len(questions)}):"]
        for q in questions:
            agent = q.get("agent", "unknown")
            question = q.get("question", "")
            task_id = q.get("task_id", "")
            qid = q.get("id", "")
            task_str = f" (task #{task_id})" if task_id else ""
            lines.append(f"❓ #{qid} [{agent}]{task_str}: {question}")
        lines.append("\nReply with /answer <text> or just send plain text.")
        return "\n".join(lines)

    if cmd == "/answer":
        answer = " ".join(parts[1:]).strip()
        if not answer:
            return "Usage: /answer <your answer text>"
        return route_reply_to_agent(answer)

    if cmd == "/digest" and len(parts) >= 2 and parts[1].lower() == "now":
        rows = run_psql(
            "SELECT id, name, COALESCE(blocked_reason,'') FROM autonomous_tasks "
            "WHERE status = 'BLOCKED' ORDER BY priority DESC, id ASC LIMIT 20;"
        )
        if not rows:
            return "No blocked tasks."
        lines = ["Blocked tasks:"]
        for line in rows.splitlines():
            tid, name, reason = [s.strip() for s in line.split("|", 2)]
            context = task_context(int(tid))
            lines.append(f"#{tid} {name}")
            if context:
                lines.append(context)
            if reason:
                lines.append(reason)
            lines.append("")
        return "\n".join(lines).strip()

    return "Unknown command. Send /help for options."


def is_local_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    parts = stripped.split()
    cmd = parts[0].lower()
    local_commands = {
        "/help",
        "help",
        "/blockers",
        "/todo",
        "/readyfortesting",
        "/inprogress",
        "/tasks",
        "/task",
        "/unblock",
        "/retry",
        "/digest",
        "/pending",
        "/answer",
    }
    return cmd in local_commands


def main() -> int:
    print(
        f"[{datetime.utcnow().isoformat()}] telegram poll start token_len={len(TELEGRAM_BOT_TOKEN)} allow_from={len(TELEGRAM_ALLOW_FROM)}"
    )
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0

    offset = load_offset()
    ok, response = api_request("getUpdates", {"timeout": 0, "offset": offset})
    if not ok or not response.get("ok"):
        return 0

    updates = response.get("result", [])
    if updates:
        print(f"[{datetime.utcnow().isoformat()}] telegram updates={len(updates)}")
    for update in updates:
        update_id = update.get("update_id")
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = str(chat.get("id", ""))
        sender_id = str(sender.get("id", ""))
        text = message.get("text", "")
        message_id = message.get("message_id")

        if TELEGRAM_ALLOW_FROM:
            if sender_id not in TELEGRAM_ALLOW_FROM:
                print(
                    f"[{datetime.utcnow().isoformat()}] telegram skip sender={sender_id or 'unknown'}"
                )
                if update_id is not None:
                    offset = update_id + 1
                    save_offset(offset)
                continue
        elif TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
            print(f"[{datetime.utcnow().isoformat()}] telegram skip chat={chat_id}")
            if update_id is not None:
                offset = update_id + 1
                save_offset(offset)
            continue

        if text:
            stripped = text.strip()
            if is_local_command(stripped):
                print(f"[{datetime.utcnow().isoformat()}] telegram command from {sender_id or chat_id}")
                reply = handle_command(text)
            elif not stripped.startswith("/") and has_pending_questions():
                print(f"[{datetime.utcnow().isoformat()}] telegram auto-answer from {sender_id or chat_id}")
                reply = route_reply_to_agent(stripped)
            else:
                print(f"[{datetime.utcnow().isoformat()}] telegram routed from {sender_id or chat_id}")
                reply = route_via_chat_router(text)
            if TELEGRAM_ACK_REACTION and message_id:
                reacted = send_reaction(chat_id, int(message_id), TELEGRAM_ACK_REACTION)
                if not reacted:
                    send_message("✅ received")
            if reply:
                send_message(reply)

        # Handle file uploads (documents, photos, audio, video)
        elif message.get("document"):
            doc = message["document"]
            file_id = doc.get("file_id", "")
            file_name = doc.get("file_name", "unknown_file")
            file_size = doc.get("file_size", 0)
            print(f"[{datetime.utcnow().isoformat()}] telegram document from {sender_id or chat_id}: {file_name}")
            saved = download_telegram_file(file_id, file_name)
            if saved:
                caption = message.get("caption", "")
                cap_note = f" — '{caption}'" if caption else ""
                send_message(f"📁 Saved: {file_name} ({file_size} bytes){cap_note}\n📂 Location: inbox/")
            else:
                send_message(f"⚠️ Failed to download: {file_name}")

        elif message.get("photo"):
            photos = message["photo"]
            # Take the largest resolution (last in the array)
            best = photos[-1] if photos else None
            if best:
                file_id = best.get("file_id", "")
                ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                file_name = f"photo_{ts}.jpg"
                print(f"[{datetime.utcnow().isoformat()}] telegram photo from {sender_id or chat_id}")
                saved = download_telegram_file(file_id, file_name)
                if saved:
                    caption = message.get("caption", "")
                    cap_note = f" — '{caption}'" if caption else ""
                    send_message(f"📸 Photo saved: {file_name}{cap_note}\n📂 Location: inbox/")
                else:
                    send_message("⚠️ Failed to download photo")

        elif message.get("voice") or message.get("audio"):
            audio = message.get("voice") or message.get("audio", {})
            file_id = audio.get("file_id", "")
            mime = audio.get("mime_type", "audio/ogg")
            ext = mime.split("/")[-1] if "/" in mime else "ogg"
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            file_name = f"audio_{ts}.{ext}"
            print(f"[{datetime.utcnow().isoformat()}] telegram audio from {sender_id or chat_id}")
            saved = download_telegram_file(file_id, file_name)
            if saved:
                send_message(f"🎵 Audio saved: {file_name}\n📂 Location: inbox/")
            else:
                send_message("⚠️ Failed to download audio")

        elif message.get("video"):
            vid = message["video"]
            file_id = vid.get("file_id", "")
            file_name = vid.get("file_name", f"video_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.mp4")
            print(f"[{datetime.utcnow().isoformat()}] telegram video from {sender_id or chat_id}")
            saved = download_telegram_file(file_id, file_name)
            if saved:
                send_message(f"🎬 Video saved: {file_name}\n📂 Location: inbox/")
            else:
                send_message(f"⚠️ Failed to download: {file_name}")

        if update_id is not None:
            offset = update_id + 1
            save_offset(offset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
