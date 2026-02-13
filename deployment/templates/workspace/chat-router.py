#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

POSTGRES_HOST = os.environ.get("OPENCLAW_POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("OPENCLAW_POSTGRES_PORT", "5433")
POSTGRES_DB = os.environ.get("OPENCLAW_POSTGRES_DB", "openclaw")
POSTGRES_USER = os.environ.get("OPENCLAW_POSTGRES_USER", "openclaw")
POSTGRES_PASSWORD = os.environ.get("OPENCLAW_POSTGRES_PASSWORD", "openclaw_dev_pass")

OPENCLAW_NODE = os.environ.get("OPENCLAW_NODE", "/usr/bin/node")
OPENCLAW_CLI = os.environ.get(
    "OPENCLAW_CLI",
    str(Path.home() / ".local" / "openclaw" / "node_modules" / "openclaw" / "dist" / "index.js"),
)

CHAT_ROUTER_PORT = int(os.environ.get("CHAT_ROUTER_PORT", "18801"))
ALLOWED_ASK_AGENTS = {"planner", "coder", "tester"}
ASK_TIMEOUT_SEC = int(os.environ.get("CHAT_ROUTER_ASK_TIMEOUT_SEC", "180"))
ASK_DEFAULT_AGENT = "planner"
TELEGRAM_NOTIFY_SCRIPT = str(Path.home() / ".openclaw" / "workspace" / "telegram-notify.sh")


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


def summarize_tasks() -> str:
    rows = run_psql(
        "SELECT status, COUNT(*) FROM autonomous_tasks GROUP BY status ORDER BY status;"
    )
    if not rows:
        return "No tasks found."
    pairs = []
    for line in rows.splitlines():
        status, count = [s.strip() for s in line.split("|", 1)]
        pairs.append(f"{status}={count}")
    return "Tasks: " + ", ".join(pairs)


def summarize_tasks_detailed(limit: int = 10) -> str:
    rows = run_psql(
        "SELECT id, name, status FROM autonomous_tasks ORDER BY id DESC LIMIT %d;" % limit
    )
    if not rows:
        return "No tasks found."
    lines = ["Recent tasks:"]
    for line in rows.splitlines():
        task_id, name, status = [s.strip() for s in line.split("|", 2)]
        lines.append(f"#{task_id} [{status}] {name}")
    return "\n".join(lines)


def summarize_blocked(limit: int = 5) -> str:
    rows = run_psql(
        "SELECT id, name, COALESCE(blocked_reason,'') FROM autonomous_tasks "
        "WHERE status = 'BLOCKED' ORDER BY priority DESC, id ASC LIMIT %d;" % limit
    )
    if not rows:
        return "No blocked tasks."
    lines = ["Blocked tasks:"]
    for line in rows.splitlines():
        task_id, name, reason = [s.strip() for s in line.split("|", 2)]
        lines.append(f"#{task_id} {name}")
        if reason:
            lines.append(reason)
    return "\n".join(lines)


def service_status() -> str:
    units = [
        "openclaw-task-manager-db.timer",
        "openclaw-task-manager-db.service",
        "openclaw-telegram-commands.timer",
        "openclaw-gateway.service",
        "openclaw-chat-router.service",
    ]
    lines = ["Services:"]
    for unit in units:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
        )
        state = result.stdout.strip() or "unknown"
        lines.append(f"{unit}: {state}")
    return "\n".join(lines)


def gpu_status() -> str:
    if not shutil.which("nvidia-smi"):
        return "GPU status unavailable (nvidia-smi not found)."
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,utilization.memory",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "GPU status unavailable."
    lines = []
    for idx, line in enumerate(result.stdout.strip().splitlines(), start=0):
        if not line.strip():
            continue
        util_gpu, util_mem = [s.strip() for s in line.split(",", 1)]
        lines.append(f"GPU{idx}: {util_gpu}% gpu, {util_mem}% mem")
    return "\n".join(lines) if lines else "GPU status unavailable."


def openclaw_cmd(args: list[str]) -> list[str]:
    if shutil.which("openclaw"):
        return ["openclaw"] + args
    return [OPENCLAW_NODE, OPENCLAW_CLI] + args


def build_planner_prompt(text: str) -> str:
    return (
        "You are the planner. Convert the request into task JSON only.\n"
        "- Default to multiple tasks that can run in parallel.\n"
        "- Split tasks into non-conflicting repo areas/components to avoid file-write races.\n"
        "- Only return a single task when the request is truly small and tightly scoped.\n"
        "- Each task should own a clear file/module boundary.\n"
        "- Output ONLY valid JSON, no markdown, no commentary.\n"
        "- Schema: {\"project\":\"<name>\",\"tasks\":[{\"name\":\"...\",\"phase\":\"...\",\"priority\":3,\"plan\":\"...\",\"notes\":\"...\"}]}\n\n"
        f"User request: {text}\n"
    )


def extract_json_payload(output: str) -> str | None:
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return output[start : end + 1]


def planner_worker(text: str) -> None:
    prompt = build_planner_prompt(text)
    cmd = openclaw_cmd(["agent", "--agent", "planner", "--message", prompt, "--timeout", "1200"])
    log_path = Path.home() / ".openclaw" / "workspace" / "chat-router-planner.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log_file:
        log_file.write("\n=== Planner dispatch ===\n")
        log_file.write(f"Request: {text}\n")
        log_file.write("---\n")
        result = subprocess.run(cmd, capture_output=True, text=True)
        combined = "".join([result.stdout, "\n", result.stderr]).strip()
        if combined:
            log_file.write(combined)
            log_file.write("\n")
        payload = extract_json_payload(combined)
        if not payload:
            log_file.write("Planner output did not include JSON payload.\n")
            return
        add_cmd = [str(Path.home() / ".openclaw" / "workspace" / "add-tasks-to-db.sh")]
        add_result = subprocess.run(add_cmd, input=payload, text=True, capture_output=True)
        if add_result.stdout:
            log_file.write(add_result.stdout)
        if add_result.stderr:
            log_file.write(add_result.stderr)
        if add_result.returncode != 0:
            log_file.write("add-tasks-to-db.sh failed.\n")
            return
        tm_cmd = [str(Path.home() / ".openclaw" / "workspace" / "autonomous-task-manager-db.sh")]
        subprocess.Popen(tm_cmd)


def spawn_planner(text: str) -> None:
    thread = threading.Thread(target=planner_worker, args=(text,), daemon=True)
    thread.start()


def ask_agent(agent: str, question: str) -> str:
    normalized_agent = agent.strip().lower()
    if normalized_agent not in ALLOWED_ASK_AGENTS:
        allowed = ", ".join(sorted(ALLOWED_ASK_AGENTS))
        return f"Unknown agent '{agent}'. Use one of: {allowed}."
    if not question.strip():
        return "Usage: /ask <agent> <question>"

    cmd = openclaw_cmd(
        [
            "agent",
            "--agent",
            normalized_agent,
            "--message",
            question,
            "--timeout",
            "1200",
        ]
    )
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=ASK_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return f"Agent {normalized_agent} timed out after {ASK_TIMEOUT_SEC}s."
    combined = "".join([result.stdout or "", "\n", result.stderr or ""]).strip()
    if result.returncode != 0 and not combined:
        return f"Agent {normalized_agent} failed with exit code {result.returncode}."
    return combined or f"No response from {normalized_agent}."


def build_async_ask_prompt(agent: str, question: str) -> str:
    return (
        "You are responding to an owner question asynchronously.\n"
        f"Agent role: {agent}\n"
        f"Owner question: {question}\n\n"
        "Required behavior:\n"
        "1) Determine the best answer to the question.\n"
        "2) Return only the final answer text in your output.\n"
        "3) Do NOT call message tools or any external channel APIs.\n"
        "4) Do NOT include routing metadata, only the answer content."
    )


def ask_agent_async_worker(agent: str, question: str) -> None:
    prompt = build_async_ask_prompt(agent, question)
    cmd = openclaw_cmd(
        [
            "agent",
            "--agent",
            agent,
            "--message",
            prompt,
            "--timeout",
            "1200",
        ]
    )

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    answer_text = (result.stdout or "").strip()
    if not answer_text:
        answer_text = "".join([result.stdout or "", "\n", result.stderr or ""]).strip()
    if not answer_text:
        answer_text = f"Agent {agent} completed without output."
    if len(answer_text) > 3500:
        answer_text = answer_text[:3500] + "\n...<truncated>"
    send_owner_message(agent, question, answer_text)


def queue_ask_agent(question: str) -> str:
    question_text = question.strip()
    if not question_text:
        return "Usage: /ask <question>"

    parts = question_text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].lower() in ALLOWED_ASK_AGENTS:
        question_text = parts[1].strip()
    if not question_text:
        return "Usage: /ask <question>"

    thread = threading.Thread(
        target=ask_agent_async_worker,
        args=(ASK_DEFAULT_AGENT, question_text),
        daemon=True,
    )
    thread.start()
    return ""


def send_owner_message(agent: str, question: str, response: str) -> tuple[bool, str]:
    agent_name = (agent or "").strip()
    question_text = (question or "").strip()
    response_text = (response or "").strip()

    if not agent_name or not question_text or not response_text:
        return False, "Missing required fields: agent, question, response"

    if not os.path.isfile(TELEGRAM_NOTIFY_SCRIPT) or not os.access(TELEGRAM_NOTIFY_SCRIPT, os.X_OK):
        return False, "Telegram notify script is unavailable"

    message = (
        f"Agent: {agent_name}\n"
        f"Question: {question_text}\n"
        f"Response: {response_text}"
    )

    cmd = [
        TELEGRAM_NOTIFY_SCRIPT,
        "owner-message",
        "owner-message",
        f"Agent {agent_name}",
        message,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "Timed out while sending owner message"

    if result.returncode != 0:
        return False, "Failed to send owner message"
    return True, "Owner message sent"


def handle_status_query(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["blocked", "blockers"]):
        return summarize_blocked()
    if any(word in lowered for word in ["timer", "service", "systemd"]):
        return service_status()
    if any(word in lowered for word in ["gpu", "nvidia"]):
        return gpu_status()
    if any(word in lowered for word in ["detail", "details", "list", "ids", "names"]):
        return summarize_tasks_detailed()
    return summarize_tasks()


def should_handle_status(text: str) -> bool:
    lowered = text.lower()
    keywords = [
        "status",
        "tasks",
        "task",
        "queue",
        "blocked",
        "blockers",
        "timer",
        "service",
        "systemd",
        "gpu",
        "nvidia",
    ]
    return any(keyword in lowered for keyword in keywords)


def route_text(text: str) -> str:
    if not text.strip():
        return ""
    stripped = text.strip()
    lowered = stripped.lower()

    if lowered.startswith("/plan"):
        plan_text = stripped[5:].strip()
        if not plan_text:
            return "Usage: /plan <request>"
        spawn_planner(plan_text)
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=planner kind=explicit")
        preview = plan_text.replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        return f"Queued for planner: {preview}"

    if lowered.startswith("/ask"):
        question = stripped[4:].strip()
        if not question:
            return "Usage: /ask <question>"
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=agent kind=ask agent={ASK_DEFAULT_AGENT}")
        return queue_ask_agent(question)

    if "weather" in lowered:
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=weather")
        return "Weather lookup is not configured on this host."
    if should_handle_status(lowered):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=status")
        return handle_status_query(lowered)
    spawn_planner(stripped)
    print(f"[{datetime.now(timezone.utc).isoformat()}] routed=planner")
    preview = stripped.replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."
    return f"Queued for planner: {preview}"


class RouterHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path not in {"/route", "/owner-message"}:
            self.send_response(404)
            self.end_headers()
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        if self.path == "/owner-message":
            ok, reply = send_owner_message(
                str(payload.get("agent", "")),
                str(payload.get("question", "")),
                str(payload.get("response", "")),
            )
            response = json.dumps({"ok": ok, "reply": reply})
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.encode("utf-8"))
            return

        text = payload.get("text", "")
        reply = route_text(text)
        response = json.dumps({"reply": reply})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response.encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", CHAT_ROUTER_PORT), RouterHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
