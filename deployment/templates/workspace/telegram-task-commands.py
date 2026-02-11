#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

POSTGRES_HOST = os.environ.get("OPENCLAW_POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("OPENCLAW_POSTGRES_PORT", "5433")
POSTGRES_DB = os.environ.get("OPENCLAW_POSTGRES_DB", "openclaw")
POSTGRES_USER = os.environ.get("OPENCLAW_POSTGRES_USER", "openclaw")
POSTGRES_PASSWORD = os.environ.get("OPENCLAW_POSTGRES_PASSWORD", "openclaw_dev_pass")

OFFSET_FILE = Path.home() / ".openclaw" / "workspace" / ".telegram-offset"


def api_request(method: str, data: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "result": []}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    api_request("sendMessage", {"chat_id": TELEGRAM_CHAT_ID, "text": text})


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

    if cmd in ("/help", "help"):
        return (
            "Telegram task commands:\n"
            "/help - show this help\n"
            "/blockers - list blocked tasks (top 20)\n"
            "/task <id> - show task status, phase, agent\n"
            "/unblock <id> - requeue a blocked task (set to TODO)\n"
            "/unblock all - requeue all blocked tasks\n"
            "/retry <id> - alias for /unblock <id>\n"
            "/digest now - send blocked tasks summary"
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
            lines.append(f"#{tid} {name}")
            if reason:
                lines.append(reason)
            lines.append("")
        return "\n".join(lines).strip()

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
        count = run_psql(
            "WITH updated AS ("
            "UPDATE autonomous_tasks SET status = 'TODO', blocked_reason = NULL, error_log = NULL, "
            "assigned_agent = NULL, pid = NULL, started_at = NULL "
            "WHERE id = %s AND status = 'BLOCKED' RETURNING id) "
            "SELECT COUNT(*) FROM updated;" % task_id
        )
        if count == "1":
            return f"Task {task_id} set to TODO."
        return f"Task {task_id} not updated (not blocked or not found)."

    if cmd == "/unblock" and len(parts) == 2 and parts[1].lower() == "all":
        count = run_psql(
            "WITH updated AS ("
            "UPDATE autonomous_tasks SET status = 'TODO', blocked_reason = NULL, error_log = NULL, "
            "assigned_agent = NULL, pid = NULL, started_at = NULL "
            "WHERE status = 'BLOCKED' RETURNING id) "
            "SELECT COUNT(*) FROM updated;"
        )
        if count:
            return f"Requeued {count} blocked tasks."
        return "No blocked tasks to requeue."

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
            lines.append(f"#{tid} {name}")
            if reason:
                lines.append(reason)
            lines.append("")
        return "\n".join(lines).strip()

    return "Unknown command. Send /help for options."


def main() -> int:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0

    offset = load_offset()
    response = api_request("getUpdates", {"timeout": 0, "offset": offset})
    if not response.get("ok"):
        return 0

    updates = response.get("result", [])
    for update in updates:
        update_id = update.get("update_id")
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = message.get("text", "")

        if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
            if update_id is not None:
                offset = update_id + 1
                save_offset(offset)
            continue

        if text:
            reply = handle_command(text)
            if reply:
                send_message(reply)

        if update_id is not None:
            offset = update_id + 1
            save_offset(offset)

    return 0


if __name__ == "__main__":
    sys.exit(main())
