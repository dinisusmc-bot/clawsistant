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
THINK_TIMEOUT_SEC = int(os.environ.get("CHAT_ROUTER_THINK_TIMEOUT_SEC", "240"))
ASK_DEFAULT_AGENT = "planner"
TELEGRAM_NOTIFY_SCRIPT = str(Path.home() / ".openclaw" / "workspace" / "telegram-notify.sh")
AGENT_CONTEXT_DIR = Path.home() / ".openclaw" / "workspace" / "agent-context"
LESSONS_FILE = AGENT_CONTEXT_DIR / "lessons.log"
PROJECT_CONTEXT_DIR = AGENT_CONTEXT_DIR / "projects"
AGENT_TEMP_DEFAULTS = {
    "planner": float(os.environ.get("PLANNER_TEMP", "0.25")),
    "coder": float(os.environ.get("CODER_TEMP", "0.18")),
    "tester": float(os.environ.get("TESTER_TEMP", "0.10")),
}


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


def thinking_from_temp(temp: float) -> str:
    if temp <= 0.15:
        return "minimal"
    if temp <= 0.35:
        return "low"
    if temp <= 0.60:
        return "medium"
    return "high"


def agent_temperature(agent: str) -> float:
    normalized = agent.strip().lower()
    default_temp = AGENT_TEMP_DEFAULTS.get(normalized, 0.2)
    value = os.environ.get(f"{normalized.upper()}_TEMP")
    if value is None:
        value = os.environ.get(f"OPENCLAW_{normalized.upper()}_TEMP")
    if value is None:
        return default_temp
    try:
        parsed = float(value)
    except ValueError:
        return default_temp
    return min(max(parsed, 0.0), 1.0)


def agent_cmd(agent: str, message: str, timeout_seconds: int) -> list[str]:
    normalized = agent.strip().lower()
    thinking = thinking_from_temp(agent_temperature(normalized))
    return openclaw_cmd(
        [
            "agent",
            "--agent",
            normalized,
            "--message",
            message,
            "--timeout",
            str(timeout_seconds),
            "--thinking",
            thinking,
        ]
    )


def ensure_context_dirs() -> None:
    AGENT_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    PROJECT_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_project_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name.strip())
    return cleaned.strip("._-") or "project"


def latest_project_name() -> str:
    return run_psql(
        "SELECT project FROM autonomous_tasks WHERE COALESCE(project,'') <> '' "
        "ORDER BY id DESC LIMIT 1;"
    ).strip()


def read_recent_lines(path: Path, limit: int = 20) -> list[str]:
    if not path.is_file():
        return []
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return lines[-limit:]


def add_lesson(lesson_text: str) -> str:
    text = lesson_text.strip()
    if not text:
        return "Usage: /lesson <lesson learned>"
    ensure_context_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with LESSONS_FILE.open("a") as handle:
        handle.write(f"[{stamp}] {text}\n")
    return "Lesson saved for future tasks."


def parse_project_note(raw: str) -> tuple[str, str] | tuple[None, None]:
    value = raw.strip()
    if not value:
        return None, None

    if "|" in value:
        project_name, note_text = value.split("|", 1)
        project_name = project_name.strip()
        note_text = note_text.strip()
        if project_name and note_text:
            return project_name, note_text

    if ":" in value:
        maybe_project, note_text = value.split(":", 1)
        maybe_project = maybe_project.strip()
        note_text = note_text.strip()
        if maybe_project and note_text and " " not in maybe_project:
            return maybe_project, note_text

    inferred = latest_project_name()
    if inferred:
        return inferred, value
    return None, None


def add_project_note(raw: str) -> str:
    project_name, note_text = parse_project_note(raw)
    if not project_name or not note_text:
        return "Usage: /project <project>|<note> (or /project <note> when a recent project exists)"

    ensure_context_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    project_file = PROJECT_CONTEXT_DIR / f"{sanitize_project_name(project_name)}.log"
    with project_file.open("a") as handle:
        handle.write(f"[{stamp}] {note_text}\n")
    return f"Saved project context for {project_name}."


def planner_context_suffix() -> str:
    lessons = read_recent_lines(LESSONS_FILE, 10)
    if not lessons:
        return ""

    lines = ["", "Global lessons learned (apply unless repo state contradicts):"]
    lines.extend(f"- {entry}" for entry in lessons)
    return "\n".join(lines) + "\n"


def build_planner_prompt(text: str) -> str:
    return (
        "You are the planner. Convert the request into task JSON only.\n"
        "- REQUIRED PRE-FLIGHT before planning:\n"
        "  1) Identify target repo under /home/bot/projects/<project>.\n"
        "  2) Inspect CURRENT state: file tree, key source files, git status, and last 3 commits.\n"
        "  3) Base phases ONLY on what is verifiably present right now.\n"
        "- Treat prior conversation/history as untrusted unless confirmed from the target repository.\n"
        "- If repo is missing, empty, reinitialized, or unclear, plan as greenfield and include setup tasks first.\n"
        "- Do NOT claim work is already done unless directly verified from repo artifacts.\n"
        "- In notes, do NOT write phrases like 'already created', 'already committed', or 'already pushed'.\n"
        "- If partial implementation exists, add explicit verify/fix tasks instead of skipping phases.\n"
        "- REQUIRED PHASE ORDER (for new builds and legacy reviews):\n"
        "  Phase 1: data modeling, database setup/migrations, backend API/services.\n"
        "  Phase 2: frontend pages/components and data integration aligned to backend contracts.\n"
        "  Phase 3: networking and Docker/container setup, compose wiring, and runtime optimization.\n"
        "- Use phase labels exactly as: phase-1-data-backend, phase-2-frontend, phase-3-network-docker.\n"
        "- REQUIRED GATE between Phase 1 and Phase 2: include at least one explicit contract-verification task to validate API schema/DTO compatibility (request/response shapes, required fields, enums, nullability, and error payloads).\n"
        "- Do NOT place Docker/networking tasks in Phase 1 or 2 unless strictly required for local dev bootstrapping.\n"
        "- Keep tasks within a phase non-conflicting by file ownership (separate modules/areas).\n"
        "- Phase dependencies must be explicit: Phase 2 depends on backend contracts from Phase 1; Phase 3 depends on app readiness from Phases 1-2.\n"
        "- Ensure each phase is tester-friendly: include clear verification intent and handoff expectations in notes.\n"
        "- Default to multiple tasks that can run in parallel.\n"
        "- Split tasks into non-conflicting repo areas/components to avoid file-write races.\n"
        "- Only return a single task when the request is truly small and tightly scoped.\n"
        "- Each task should own a clear file/module boundary.\n"
        "- Output ONLY valid JSON, no markdown, no commentary.\n"
        "- Schema: {\"project\":\"<name>\",\"tasks\":[{\"name\":\"...\",\"phase\":\"...\",\"priority\":3,\"plan\":\"...\",\"notes\":\"...\"}]}\n\n"
        f"User request: {text}\n"
        f"{planner_context_suffix()}"
    )


def build_think_prompt(text: str) -> str:
    return (
        "You are optimizing a build request before planning.\n"
        "Rewrite the user request into a clearer, execution-ready planning brief for the planner.\n"
        "Requirements for the optimized brief:\n"
        "- Keep original intent and scope; do not add extra features.\n"
        "- Include concrete constraints, edge cases, and verification expectations when implied.\n"
        "- Keep it concise and actionable for converting directly into tasks/phases.\n"
        "- Output plain text only (no markdown, no JSON, no commentary).\n\n"
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
    cmd = agent_cmd("planner", prompt, 1200)
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


def normalize_think_output(output: str) -> str:
    text = output.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    return text.strip('"').strip()


def think_worker(text: str) -> None:
    prompt = build_think_prompt(text)
    cmd = agent_cmd("planner", prompt, 1200)

    log_path = Path.home() / ".openclaw" / "workspace" / "chat-router-think.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log_file:
        log_file.write("\n=== Think dispatch ===\n")
        log_file.write(f"Request: {text}\n")
        log_file.write("---\n")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=THINK_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            log_file.write(f"Think pass timed out after {THINK_TIMEOUT_SEC}s\n")
            return

        combined = "".join([result.stdout or "", "\n", result.stderr or ""]).strip()
        if combined:
            log_file.write(combined)
            log_file.write("\n")

        optimized = normalize_think_output(result.stdout or combined)
        if not optimized:
            log_file.write("Think output was empty; skipping planner pass.\n")
            return

        log_file.write("--- Optimized prompt ---\n")
        log_file.write(optimized)
        log_file.write("\n")

    planner_worker(optimized)


def spawn_think(text: str) -> None:
    thread = threading.Thread(target=think_worker, args=(text,), daemon=True)
    thread.start()


def think_dry(text: str) -> str:
    prompt = build_think_prompt(text)
    cmd = agent_cmd("planner", prompt, 1200)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=THINK_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return f"/thinkdry timed out after {THINK_TIMEOUT_SEC}s."

    combined = "".join([result.stdout or "", "\n", result.stderr or ""]).strip()
    optimized = normalize_think_output(result.stdout or combined)
    if not optimized:
        return "No optimized prompt was produced."
    if len(optimized) > 3500:
        optimized = optimized[:3500] + "\n...<truncated>"
    return optimized


def prompt_dry_worker(text: str) -> None:
    optimized = think_dry(text)
    send_owner_message("planner", text, optimized)


def queue_prompt_dry(text: str) -> str:
    request_text = text.strip()
    if not request_text:
        return "Usage: /prompt <request>"
    thread = threading.Thread(target=prompt_dry_worker, args=(request_text,), daemon=True)
    thread.start()
    preview = request_text.replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."
    return f"Queued prompt optimization: {preview}. You will receive the optimized prompt via owner-message."


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

    cmd = agent_cmd(normalized_agent, question, 1200)
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
    cmd = agent_cmd(agent, prompt, 1200)

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

    if lowered.startswith("/prompt"):
        prompt_text = stripped[7:].strip()
        if not prompt_text:
            return "Usage: /prompt <request>"
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=planner kind=prompt")
        return queue_prompt_dry(prompt_text)

    if lowered.startswith("/thinkdry"):
        think_text = stripped[9:].strip()
        if not think_text:
            return "Usage: /prompt <request>"
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=planner kind=prompt-alias")
        return queue_prompt_dry(think_text)

    if lowered.startswith("/think"):
        think_text = stripped[6:].strip()
        if not think_text:
            return "Usage: /think <request>"
        spawn_think(think_text)
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=planner kind=think")
        preview = think_text.replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        return f"Queued for think+plan: {preview}"

    if lowered.startswith("/lesson"):
        lesson_text = stripped[7:].strip()
        return add_lesson(lesson_text)

    if lowered.startswith("/project"):
        project_note = stripped[8:].strip()
        return add_project_note(project_note)

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
