#!/usr/bin/env python3
import hashlib
import json
import os
import re
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
ADHOC_TIMEOUT_SEC = int(os.environ.get("CHAT_ROUTER_ADHOC_TIMEOUT_SEC", "1200"))
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

SCHEDULED_JOBS_DIR = Path.home() / ".config" / "systemd" / "user"
SCHEDULED_JOBS_PREFIX = "ashley-job-"

# ---- New feature directories ----
NOTES_DIR = Path.home() / ".openclaw" / "workspace" / "notes"
LINKS_FILE = Path.home() / ".openclaw" / "workspace" / "agent-context" / "bookmarks.json"
CONVERSATION_FILE = Path.home() / ".openclaw" / "workspace" / ".conversation-buffer.json"
CONVERSATION_MAX = 20  # max messages to keep in short-term memory

# ---- Vector memory (lazy-loaded) ----
_vmem = None

def _get_vmem():
    """Lazy-load the vector memory module."""
    global _vmem
    if _vmem is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "vector_memory",
            Path.home() / ".openclaw" / "workspace" / "vector-memory.py",
        )
        _vmem = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_vmem)
    return _vmem
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
WEATHER_LOCATION = os.environ.get("WEATHER_LOCATION", "New York,US")
SEARXNG_URL = os.environ.get("SEARXNG_URL", "")  # e.g. http://localhost:8888


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
    # Also store in vector memory for semantic recall
    try:
        _get_vmem().store_lesson(text)
    except Exception as exc:
        print(f"[{datetime.now(timezone.utc).isoformat()}] vmem lesson store error: {exc}")
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
    try:
        _get_vmem().store_project_context(project_name, note_text)
    except Exception as exc:
        print(f"[{datetime.now(timezone.utc).isoformat()}] vmem project store error: {exc}")
    return f"Saved project context for {project_name}."


def planner_context_suffix() -> str:
    lessons = read_recent_lines(LESSONS_FILE, 10)
    parts = []
    if lessons:
        parts.append("")
        parts.append("Global lessons learned (apply unless repo state contradicts):")
        parts.extend(f"- {entry}" for entry in lessons)
    return "\n".join(parts) + "\n" if parts else ""


def build_planner_prompt(text: str) -> str:
    return (
        "You are Ashley's planner. Convert the request into task JSON only.\n"
        "- REQUIRED PRE-FLIGHT before planning:\n"
        "  1) Identify the project scope under /home/bot/projects/<project>.\n"
        "  2) Check existing deliverables, notes, and context in that directory.\n"
        "  3) Base phases ONLY on what the owner actually needs.\n"
        "- Treat prior conversation/history as untrusted unless confirmed from existing files.\n"
        "- Do NOT claim work is already done unless directly verified from existing deliverables.\n"
        "- RECOMMENDED PHASE ORDER (adapt to task type):\n"
        "  Phase 1: Research, information gathering, and context building.\n"
        "  Phase 2: Execution — drafting, scheduling, organizing, analyzing.\n"
        "  Phase 3: Review, follow-up, and delivery to owner.\n"
        "- Use descriptive phase labels like: phase-1-research, phase-2-execution, phase-3-review.\n"
        "- TASK CATEGORIES (tag in task name or notes):\n"
        "  research: gather info, summarize, compile findings.\n"
        "  communication: draft emails, messages, follow-ups.\n"
        "  scheduling: calendar events, meeting prep, reminders.\n"
        "  organization: file, sort, tag, create systems.\n"
        "  analysis: review data, identify patterns, generate insights.\n"
        "  follow-up: check on leads, pending items, stale threads.\n"
        "- Keep tasks within a phase non-conflicting to enable parallel execution.\n"
        "- Each task should have a clear, verifiable deliverable.\n"
        "- Ensure each phase is reviewer-friendly: include clear success criteria and expected output in notes.\n"
        "- Default to multiple tasks that can run in parallel.\n"
        "- Only return a single task when the request is truly small and tightly scoped.\n"
        "- Output ONLY valid JSON, no markdown, no commentary.\n"
        "- Schema: {\"project\":\"<name>\",\"tasks\":[{\"name\":\"...\",\"phase\":\"...\",\"priority\":3,\"plan\":\"...\",\"notes\":\"...\"}]}\n\n"
        f"User request: {text}\n"
        f"{planner_context_suffix()}"
    )


def build_think_prompt(text: str) -> str:
    return (
        "You are optimizing a request before planning.\n"
        "Rewrite the user request into a clearer, execution-ready planning brief for Ashley's planner.\n"
        "Requirements for the optimized brief:\n"
        "- Keep original intent and scope; do not add extra work.\n"
        "- Include concrete constraints, deadlines, and quality expectations when implied.\n"
        "- Clarify who needs to be contacted, what information is needed, and what the deliverable should look like.\n"
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


def build_async_adhoc_prompt(instruction: str) -> str:
    return (
        "You are executing a one-off adhoc instruction from the owner.\n"
        "Agent role: doer\n"
        f"Owner instruction: {instruction}\n\n"
        "Rules:\n"
        "1) Execute the request directly — research, draft, organize, or whatever is needed.\n"
        "2) Do NOT create or modify task-table entries as part of this request.\n"
        "3) Keep work focused and minimal for the stated request.\n"
        "4) End with a concise summary of what was produced and where deliverables are saved.\n"
        "5) Return only final answer content; no channel routing metadata."
    )


def adhoc_coder_worker(instruction: str) -> None:
    prompt = build_async_adhoc_prompt(instruction)
    cmd = agent_cmd("coder", prompt, ADHOC_TIMEOUT_SEC)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=ADHOC_TIMEOUT_SEC,
        )
        answer_text = (result.stdout or "").strip()
        if not answer_text:
            answer_text = "".join([result.stdout or "", "\n", result.stderr or ""]).strip()
    except subprocess.TimeoutExpired:
        answer_text = f"Adhoc doer run timed out after {ADHOC_TIMEOUT_SEC}s."

    if not answer_text:
        answer_text = "Doer completed adhoc request without output."
    if len(answer_text) > 3500:
        answer_text = answer_text[:3500] + "\n...<truncated>"
    send_owner_message("coder", instruction, answer_text)


def queue_adhoc_coder(instruction: str) -> str:
    request_text = instruction.strip()
    if not request_text:
        return "Usage: /adhoc <one-off instruction>"

    thread = threading.Thread(
        target=adhoc_coder_worker,
        args=(request_text,),
        daemon=True,
    )
    thread.start()
    preview = request_text.replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."
    return f"Queued adhoc doer request: {preview}. You will receive the result via owner-message."


def queue_ask_agent(question: str) -> tuple[str, str]:
    question_text = question.strip()
    if not question_text:
        return "", "Usage: /ask <question>"

    target_agent = ASK_DEFAULT_AGENT

    parts = question_text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].lower() in ALLOWED_ASK_AGENTS:
        target_agent = parts[0].lower()
        question_text = parts[1].strip()
    if not question_text:
        return "", "Usage: /ask <agent> <question>"

    thread = threading.Thread(
        target=ask_agent_async_worker,
        args=(target_agent, question_text),
        daemon=True,
    )
    thread.start()
    return target_agent, ""


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
    # Record bot response in conversation memory
    record_conversation("ashley", response_text[:300])
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


# ---------------------------------------------------------------------------
# Scheduled Jobs (systemd timers)
# ---------------------------------------------------------------------------

def cron_to_oncalendar(cron_expr: str) -> str | None:
    """Convert a 5-field cron expression to a systemd OnCalendar spec.

    Supports: minute hour day-of-month month day-of-week
    Uses systemd calendar syntax: DayOfWeek Year-Month-Day Hour:Minute:Second
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return None

    minute, hour, dom, month, dow = parts

    # Validate basic structure (digits, *, /, -, ,)
    cron_field_re = re.compile(r'^[\d\*,/\-]+$')
    for field in parts:
        if not cron_field_re.match(field):
            return None

    # Map cron fields to systemd OnCalendar
    def convert_field(field: str, wildcard: str = "*") -> str:
        if field == "*":
            return wildcard
        # Handle /step: */5 -> *:00/5 for minutes, or 00/5, etc.
        if "/" in field:
            base, step = field.split("/", 1)
            if base == "*":
                base = "0"
            return f"{base}/{step}"
        return field

    # Build OnCalendar string
    cal_dow = "*" if dow == "*" else dow
    cal_month = convert_field(month)
    cal_dom = convert_field(dom)
    cal_hour = convert_field(hour)
    cal_minute = convert_field(minute)

    # systemd format: DayOfWeek Year-Month-Day Hour:Minute:Second
    date_part = f"*-{cal_month}-{cal_dom}"
    time_part = f"{cal_hour}:{cal_minute}:00"

    if cal_dow != "*":
        return f"{cal_dow} {date_part} {time_part}"
    return f"{date_part} {time_part}"


def make_job_id(description: str) -> str:
    """Generate a short unique job ID from the description."""
    slug = re.sub(r'[^a-z0-9]+', '-', description.lower().strip())[:40].strip('-')
    short_hash = hashlib.md5(f"{slug}-{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:6]
    return f"{slug}-{short_hash}"


def schedule_job(cron_expr: str, task_description: str) -> str:
    """Create a systemd timer+service pair for a scheduled task."""
    on_calendar = cron_to_oncalendar(cron_expr)
    if not on_calendar:
        return (
            "Invalid cron expression. Use 5 fields: minute hour day-of-month month day-of-week\n"
            "Examples:\n"
            "  0 7 * * *     = daily at 7am\n"
            "  */30 * * * *  = every 30 minutes\n"
            "  0 9 * * 1     = every Monday at 9am\n"
            "  0 8,17 * * *  = 8am and 5pm daily"
        )

    job_id = make_job_id(task_description)
    unit_name = f"{SCHEDULED_JOBS_PREFIX}{job_id}"
    service_path = SCHEDULED_JOBS_DIR / f"{unit_name}.service"
    timer_path = SCHEDULED_JOBS_DIR / f"{unit_name}.timer"
    meta_path = SCHEDULED_JOBS_DIR / f"{unit_name}.meta.json"
    payload_path = SCHEDULED_JOBS_DIR / f"{unit_name}.payload.json"

    # Write the JSON payload to a file so curl reads it cleanly (no quoting issues)
    payload = json.dumps({"text": f"/think {task_description}"})
    SCHEDULED_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(payload)

    service_content = (
        f"[Unit]\n"
        f"Description=Ashley Scheduled: {task_description[:80]}\n"
        f"\n"
        f"[Service]\n"
        f"Type=oneshot\n"
        f"ExecStart=/usr/bin/curl -sS -X POST http://127.0.0.1:{CHAT_ROUTER_PORT}/route "
        f'-H "Content-Type: application/json" '
        f"-d @{payload_path}\n"
        f"Environment=HOME={Path.home()}\n"
    )

    timer_content = (
        f"[Unit]\n"
        f"Description=Ashley Schedule: {task_description[:80]}\n"
        f"\n"
        f"[Timer]\n"
        f"OnCalendar={on_calendar}\n"
        f"Persistent=true\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=timers.target\n"
    )

    meta = {
        "job_id": job_id,
        "cron": cron_expr,
        "on_calendar": on_calendar,
        "description": task_description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "unit_name": unit_name,
    }

    SCHEDULED_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    service_path.write_text(service_content)
    timer_path.write_text(timer_content)
    meta_path.write_text(json.dumps(meta, indent=2))

    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    result = subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{unit_name}.timer"],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        return f"Failed to enable timer: {result.stderr.strip()}"

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"Scheduled job created.\n"
        f"ID: {job_id}\n"
        f"Cron: {cron_expr}\n"
        f"SystemD: {on_calendar}\n"
        f"Task: {task_description}\n"
        f"Created: {stamp}"
    )


def list_jobs() -> str:
    """List all Ashley scheduled jobs."""
    SCHEDULED_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    meta_files = sorted(SCHEDULED_JOBS_DIR.glob(f"{SCHEDULED_JOBS_PREFIX}*.meta.json"))

    if not meta_files:
        return "No scheduled jobs found."

    lines = ["Scheduled jobs:\n"]
    for meta_path in meta_files:
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        job_id = meta.get("job_id", "?")
        cron = meta.get("cron", "?")
        desc = meta.get("description", "?")
        created = meta.get("created_at", "?")[:16]
        unit_name = meta.get("unit_name", "")

        # Check if timer is active
        status_result = subprocess.run(
            ["systemctl", "--user", "is-active", f"{unit_name}.timer"],
            capture_output=True, text=True,
        )
        status = status_result.stdout.strip() if status_result.returncode == 0 else "inactive"

        # Get next trigger time
        next_result = subprocess.run(
            ["systemctl", "--user", "show", f"{unit_name}.timer", "--property=NextElapseUSecRealtime", "--value"],
            capture_output=True, text=True,
        )
        next_run = next_result.stdout.strip()[:19] if next_result.stdout.strip() else "—"

        lines.append(f"ID: {job_id}")
        lines.append(f"  Cron: {cron}  |  Status: {status}")
        lines.append(f"  Next: {next_run}")
        lines.append(f"  Task: {desc[:100]}")
        lines.append("")

    return "\n".join(lines).strip()


def delete_job(job_id: str) -> str:
    """Delete a scheduled job by ID or 'all'."""
    SCHEDULED_JOBS_DIR.mkdir(parents=True, exist_ok=True)

    if job_id.lower() == "all":
        meta_files = list(SCHEDULED_JOBS_DIR.glob(f"{SCHEDULED_JOBS_PREFIX}*.meta.json"))
        if not meta_files:
            return "No scheduled jobs to delete."
        count = 0
        for meta_path in meta_files:
            try:
                meta = json.loads(meta_path.read_text())
                unit_name = meta.get("unit_name", "")
                if unit_name:
                    subprocess.run(
                        ["systemctl", "--user", "disable", "--now", f"{unit_name}.timer"],
                        capture_output=True,
                    )
                    (SCHEDULED_JOBS_DIR / f"{unit_name}.service").unlink(missing_ok=True)
                    (SCHEDULED_JOBS_DIR / f"{unit_name}.timer").unlink(missing_ok=True)
                    (SCHEDULED_JOBS_DIR / f"{unit_name}.payload.json").unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                count += 1
            except (json.JSONDecodeError, OSError):
                meta_path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        return f"Deleted {count} scheduled job(s)."

    # Find by job_id (partial match)
    meta_files = list(SCHEDULED_JOBS_DIR.glob(f"{SCHEDULED_JOBS_PREFIX}*.meta.json"))
    matched = None
    for meta_path in meta_files:
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get("job_id", "") == job_id or job_id in meta.get("job_id", ""):
                matched = (meta_path, meta)
                break
        except (json.JSONDecodeError, OSError):
            continue

    if not matched:
        return f"Job '{job_id}' not found. Use /jobs to see all scheduled jobs."

    meta_path, meta = matched
    unit_name = meta.get("unit_name", "")
    desc = meta.get("description", "?")

    if unit_name:
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", f"{unit_name}.timer"],
            capture_output=True,
        )
        (SCHEDULED_JOBS_DIR / f"{unit_name}.service").unlink(missing_ok=True)
        (SCHEDULED_JOBS_DIR / f"{unit_name}.timer").unlink(missing_ok=True)
        (SCHEDULED_JOBS_DIR / f"{unit_name}.payload.json").unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)

    return f"Deleted job: {meta.get('job_id', job_id)}\nTask was: {desc[:100]}"


# ---------------------------------------------------------------------------
# Agent Clarifying Questions
# ---------------------------------------------------------------------------

QUESTION_EXPIRY_MINUTES = 60


def _sql_escape(text: str) -> str:
    """Escape single quotes for SQL."""
    return text.replace("'", "''")


def create_pending_question(agent: str, task_id: int | None, question: str) -> int | None:
    """Insert a pending question and return its ID."""
    task_ref = str(task_id) if task_id else "NULL"
    q_escaped = _sql_escape(question)
    a_escaped = _sql_escape(agent)
    result = run_psql(
        f"INSERT INTO pending_questions (agent, task_id, question) "
        f"VALUES ('{a_escaped}', {task_ref}, '{q_escaped}') RETURNING id;"
    )
    try:
        # run_psql may return "5\nINSERT 0 1" — take first line only
        return int(result.strip().splitlines()[0])
    except (ValueError, TypeError, IndexError):
        return None


def get_oldest_pending_question() -> dict | None:
    """Get the oldest pending (unanswered) question."""
    row = run_psql(
        "SELECT id, agent, task_id, question, created_at "
        "FROM pending_questions WHERE status = 'pending' "
        "ORDER BY created_at ASC LIMIT 1;"
    )
    if not row:
        return None
    parts = row.split("|", 4)
    if len(parts) < 5:
        return None
    return {
        "id": int(parts[0]),
        "agent": parts[1],
        "task_id": int(parts[2]) if parts[2] else None,
        "question": parts[3],
        "created_at": parts[4],
    }


def answer_pending_question(question_id: int, answer: str) -> bool:
    """Mark a question as answered and store the answer."""
    a_escaped = _sql_escape(answer)
    run_psql(
        f"UPDATE pending_questions SET answer = '{a_escaped}', "
        f"status = 'answered', answered_at = CURRENT_TIMESTAMP "
        f"WHERE id = {question_id} AND status = 'pending';"
    )
    return True


def expire_old_questions() -> int:
    """Expire questions older than QUESTION_EXPIRY_MINUTES."""
    result = run_psql(
        f"UPDATE pending_questions SET status = 'expired' "
        f"WHERE status = 'pending' "
        f"AND created_at < NOW() - INTERVAL '{QUESTION_EXPIRY_MINUTES} minutes';"
    )
    # psql returns "UPDATE N"
    return 0


def list_pending_questions() -> str:
    """List all pending questions."""
    expire_old_questions()
    rows = run_psql(
        "SELECT id, agent, task_id, question, created_at "
        "FROM pending_questions WHERE status = 'pending' "
        "ORDER BY created_at ASC LIMIT 10;"
    )
    if not rows:
        return "No pending questions."
    lines = ["Pending questions:\n"]
    for row in rows.strip().splitlines():
        parts = row.split("|", 4)
        if len(parts) < 5:
            continue
        qid, agent, task_id, question, created = parts
        task_ref = f" (task #{task_id})" if task_id else ""
        age = created.strip()[:16]
        lines.append(f"#{qid} [{agent}{task_ref}] {age}")
        lines.append(f"  {question[:150]}")
        lines.append("")
    return "\n".join(lines).strip()


def list_pending_questions_structured() -> list[dict]:
    """Return pending questions as a list of dicts for the API."""
    expire_old_questions()
    rows = run_psql(
        "SELECT id, agent, task_id, question, created_at "
        "FROM pending_questions WHERE status = 'pending' "
        "ORDER BY created_at ASC LIMIT 10;"
    )
    if not rows:
        return []
    result = []
    for row in rows.strip().splitlines():
        parts = row.split("|", 4)
        if len(parts) < 5:
            continue
        qid, agent, task_id, question, created = parts
        result.append({
            "id": int(qid.strip()),
            "agent": agent.strip(),
            "task_id": int(task_id.strip()) if task_id.strip() else None,
            "question": question.strip(),
            "created_at": created.strip(),
        })
    return result


def count_pending_questions() -> int:
    """Return count of pending questions."""
    result = run_psql(
        "SELECT COUNT(*) FROM pending_questions WHERE status = 'pending';"
    )
    try:
        return int(result.strip())
    except (ValueError, TypeError):
        return 0


def ask_owner_question(agent: str, task_id: int | None, question: str) -> tuple[bool, str]:
    """Agent asks the owner a clarifying question. Stores in DB and sends to Telegram."""
    agent = agent.strip().lower()
    if agent not in ALLOWED_ASK_AGENTS:
        return False, f"Unknown agent '{agent}'. Allowed: {', '.join(sorted(ALLOWED_ASK_AGENTS))}"

    if not question.strip():
        return False, "Question text is required."

    expire_old_questions()
    qid = create_pending_question(agent, task_id, question.strip())
    if not qid:
        return False, "Failed to store question in database."

    # Send to owner via Telegram
    task_ref = f" (task #{task_id})" if task_id else ""
    message = (
        f"❓ *Agent Question*\n\n"
        f"From: `{agent}`{task_ref}\n"
        f"Question ID: `{qid}`\n\n"
        f"{question.strip()}\n\n"
        f"_Reply with /answer <your response> or just type your answer._"
    )

    if os.path.isfile(TELEGRAM_NOTIFY_SCRIPT) and os.access(TELEGRAM_NOTIFY_SCRIPT, os.X_OK):
        cmd = [
            TELEGRAM_NOTIFY_SCRIPT,
            "agent-question",
            str(qid),
            f"Agent {agent}",
            message,
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            pass

    print(f"[{datetime.now(timezone.utc).isoformat()}] question_created id={qid} agent={agent} task_id={task_id}")
    return True, f"Question #{qid} sent to owner. Waiting for reply."


def handle_owner_reply(answer_text: str) -> str:
    """Process an owner's reply to the oldest pending question."""
    expire_old_questions()
    question = get_oldest_pending_question()
    if not question:
        return "No pending questions to answer."

    qid = question["id"]
    agent = question["agent"]
    task_id = question["task_id"]
    original_q = question["question"]

    answer_pending_question(qid, answer_text)

    # If linked to a task, append the answer to the task's solution field
    if task_id:
        solution_escaped = _sql_escape(
            f"\n\n--- Owner Answer (Q#{qid}) ---\n"
            f"Q: {original_q}\nA: {answer_text}"
        )
        run_psql(
            f"UPDATE autonomous_tasks SET solution = COALESCE(solution, '') || '{solution_escaped}' "
            f"WHERE id = {task_id};"
        )

    # Dispatch a follow-up prompt to the agent so it can continue
    follow_up = (
        f"The owner answered your question.\n"
        f"Question: {original_q}\n"
        f"Answer: {answer_text}\n\n"
        f"Continue your current task with this information."
    )

    def _dispatch():
        try:
            if agent == "planner":
                spawn_planner(follow_up)
            else:
                queue_adhoc_coder(follow_up)
        except Exception as e:
            print(f"[{datetime.now(timezone.utc).isoformat()}] follow-up dispatch failed: {e}")

    threading.Thread(target=_dispatch, daemon=True).start()

    print(f"[{datetime.now(timezone.utc).isoformat()}] question_answered id={qid} agent={agent}")
    return (
        f"Answer recorded for question #{qid}.\n"
        f"Agent: {agent}\n"
        f"Q: {original_q[:100]}\n"
        f"A: {answer_text[:100]}\n"
        f"Follow-up dispatched to {agent}."
    )


# ===================== Gmail / Calendar Handlers =====================

def _load_google_services():
    """Lazy-import google-services module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "google_services",
        str(Path.home() / ".openclaw" / "workspace" / "google-services.py"),
    )
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return None


def _handle_emails(text: str) -> str:
    gs = _load_google_services()
    if not gs:
        return "Google services module not found."
    parts = text.strip().split(maxsplit=1)
    query = parts[1] if len(parts) > 1 else ""
    emails = gs.list_emails(query=query, max_results=10)
    if not emails:
        return "No emails found." if query else "Inbox is empty (or not authenticated)."
    return gs._format_email_list(emails)


def _handle_read_email(text: str) -> str:
    gs = _load_google_services()
    if not gs:
        return "Google services module not found."
    parts = text.strip().split()
    if len(parts) < 2:
        return "Usage: /email <message_id>"
    msg = gs.read_email(parts[1])
    if "error" in msg:
        return f"Error: {msg['error']}"
    lines = [
        f"From: {msg['from']}",
        f"To: {msg['to']}",
        f"Subject: {msg['subject']}",
        f"Date: {msg['date']}",
        "",
        msg.get("body", "(no body)"),
    ]
    return "\n".join(lines)


def _handle_send_email(text: str) -> str:
    gs = _load_google_services()
    if not gs:
        return "Google services module not found."
    # Format: /sendemail to@email.com | subject | body
    content = text.strip()
    if content.lower().startswith("/sendemail"):
        content = content[10:].strip()
    if not content:
        return "Usage: /sendemail to@email.com | subject | body text"
    parts = content.split("|", 2)
    if len(parts) < 3:
        return "Usage: /sendemail to@email.com | subject | body text\nSeparate fields with |"
    to = parts[0].strip()
    subject = parts[1].strip()
    body = parts[2].strip()
    if not to or not subject:
        return "Both recipient and subject are required."
    result = gs.send_email(to, subject, body)
    if "error" in result:
        return f"Send failed: {result['error']}"
    return f"✅ Email sent to {to}\nSubject: {subject}"


def _handle_calendar(text: str) -> str:
    gs = _load_google_services()
    if not gs:
        return "Google services module not found."
    parts = text.strip().split()
    days = 7
    if len(parts) >= 2 and parts[1].isdigit():
        days = int(parts[1])
    events = gs.list_events(days=days)
    if not events:
        return f"No events in the next {days} day(s)."
    return gs._format_event_list(events)


def _handle_create_event(text: str) -> str:
    gs = _load_google_services()
    if not gs:
        return "Google services module not found."
    # Format: /event 2026-02-23T14:00 | Meeting title | optional description | optional location
    content = text.strip()
    if content.lower().startswith("/event"):
        content = content[6:].strip()
    if not content:
        return (
            "Usage: /event <start_time> | <title> [| description] [| location]\n"
            "Example: /event 2026-02-23T14:00 | Team meeting | Discuss roadmap | Zoom\n"
            "All-day: /event 2026-02-23 | Day off"
        )
    parts = content.split("|")
    if len(parts) < 2:
        return "At minimum: /event <start_time> | <title>"
    start_time = parts[0].strip()
    summary = parts[1].strip()
    description = parts[2].strip() if len(parts) > 2 else ""
    location = parts[3].strip() if len(parts) > 3 else ""
    all_day = "T" not in start_time and len(start_time) == 10

    result = gs.create_event(
        summary=summary,
        start_time=start_time,
        description=description,
        location=location,
        all_day=all_day,
    )
    if "error" in result:
        return f"Failed to create event: {result['error']}"
    return f"✅ Event created: {result.get('summary', summary)}\nStart: {start_time}"


def _handle_delete_event(text: str) -> str:
    gs = _load_google_services()
    if not gs:
        return "Google services module not found."
    parts = text.strip().split()
    if len(parts) < 2:
        return "Usage: /delevent <event_id>"
    result = gs.delete_event(parts[1])
    if "error" in result:
        return f"Failed: {result['error']}"
    return f"✅ Event deleted."


def _handle_unread() -> str:
    gs = _load_google_services()
    if not gs:
        return "Google services module not found."
    count = gs.count_unread()
    if count < 0:
        return "Not authenticated with Gmail."
    if count == 0:
        return "📧 No unread emails."
    return f"📧 {count} unread email(s)."


# ===================== Weather =====================

def _handle_weather(text: str) -> str:
    """Get current weather using OpenWeatherMap API."""
    if not OPENWEATHER_API_KEY:
        return "Weather not configured. Set OPENWEATHER_API_KEY environment variable."
    parts = text.strip().split(maxsplit=1)
    location = parts[1] if len(parts) > 1 else WEATHER_LOCATION
    try:
        import urllib.request
        import urllib.parse
        url = (
            f"https://api.openweathermap.org/data/2.5/weather?"
            f"q={urllib.parse.quote(location)}&appid={OPENWEATHER_API_KEY}&units=imperial"
        )
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        desc = data["weather"][0]["description"].capitalize()
        wind = data["wind"]["speed"]
        city = data["name"]
        return (
            f"🌤 Weather for {city}:\n"
            f"  {desc}\n"
            f"  🌡 {temp:.0f}°F (feels like {feels:.0f}°F)\n"
            f"  💧 Humidity: {humidity}%\n"
            f"  💨 Wind: {wind:.0f} mph"
        )
    except Exception as e:
        return f"Weather lookup failed: {e}"


# ===================== Web Search =====================

def _handle_search(text: str) -> str:
    """Search the web using SearXNG or fallback."""
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return "Usage: /search <query>"
    query = parts[1]

    # Try SearXNG first
    if SEARXNG_URL:
        return _searxng_search(query)

    # Fallback: use DuckDuckGo instant answers (no API key needed)
    return _ddg_search(query)


def _searxng_search(query: str) -> str:
    """Search via self-hosted SearXNG instance."""
    import urllib.request
    import urllib.parse
    try:
        url = f"{SEARXNG_URL}/search?q={urllib.parse.quote(query)}&format=json&engines=google,duckduckgo,brave&categories=general"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])[:5]
        if not results:
            return f"No results found for: {query}"
        lines = [f"🔍 Search: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = r.get("content", "")[:150]
            lines.append(f"{i}. {title}")
            if content:
                lines.append(f"   {content}")
            lines.append(f"   {url}")
            lines.append("")
        return "\n".join(lines).strip()
    except Exception as e:
        return f"Search failed: {e}"


def _ddg_search(query: str) -> str:
    """Search via DuckDuckGo instant answers API (no key needed)."""
    import urllib.request
    import urllib.parse
    try:
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "AshleyBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        lines = [f"🔍 Search: {query}\n"]

        # Abstract/answer
        abstract = data.get("AbstractText", "")
        answer = data.get("Answer", "")
        if answer:
            lines.append(f"Answer: {answer}")
        if abstract:
            lines.append(abstract[:500])
            src = data.get("AbstractSource", "")
            src_url = data.get("AbstractURL", "")
            if src:
                lines.append(f"Source: {src} — {src_url}")

        # Related topics
        topics = data.get("RelatedTopics", [])[:5]
        if topics:
            lines.append("\nRelated:")
            for t in topics:
                text_val = t.get("Text", "")
                url_val = t.get("FirstURL", "")
                if text_val:
                    lines.append(f"• {text_val[:150]}")
                    if url_val:
                        lines.append(f"  {url_val}")

        if len(lines) <= 1:
            return f"No instant answer found for: {query}\nTip: Try asking Ashley to research this topic with /plan"

        return "\n".join(lines).strip()
    except Exception as e:
        return f"Search failed: {e}"


# ===================== Notes =====================

def _handle_note(text: str) -> str:
    """Save a quick note to today's file."""
    content = text.strip()
    if content.lower().startswith("/note"):
        content = content[5:].strip()
    if not content:
        return "Usage: /note <your note text>"

    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note_file = NOTES_DIR / f"{today}.md"

    timestamp = datetime.now(timezone.utc).strftime("%H:%M")
    entry = f"- [{timestamp}] {content}\n"

    if note_file.exists():
        note_file.write_text(note_file.read_text() + entry)
    else:
        note_file.write_text(f"# Notes — {today}\n\n{entry}")

    try:
        _get_vmem().store_note(content, date=today)
    except Exception as exc:
        print(f"[{datetime.now(timezone.utc).isoformat()}] vmem note store error: {exc}")
    return f"📝 Note saved."


def _handle_notes(text: str) -> str:
    """List notes. /notes = today, /notes search <query>, /notes <date>."""
    parts = text.strip().split(maxsplit=2)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)

    if len(parts) >= 3 and parts[1].lower() == "search":
        query = parts[2].lower()
        results = []
        for f in sorted(NOTES_DIR.glob("*.md"), reverse=True)[:30]:
            content = f.read_text()
            if query in content.lower():
                # Find matching lines
                for line in content.splitlines():
                    if query in line.lower():
                        results.append(f"[{f.stem}] {line.strip()}")
        if not results:
            return f"No notes matching '{query}'."
        return f"🔍 Notes matching '{query}':\n" + "\n".join(results[:15])

    if len(parts) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}", parts[1]):
        date = parts[1]
        note_file = NOTES_DIR / f"{date}.md"
        if note_file.exists():
            return note_file.read_text()[:3000]
        return f"No notes for {date}."

    # Default: today's notes
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note_file = NOTES_DIR / f"{today}.md"
    if note_file.exists():
        return note_file.read_text()[:3000]
    return "No notes yet today. Use /note <text> to add one."


# ===================== Links / Bookmarks =====================

def _handle_save_link(text: str) -> str:
    """Save a URL with optional tags. /save <url> [tag1 tag2 ...]"""
    parts = text.strip().split()
    if len(parts) < 2:
        return "Usage: /save <url> [tag1 tag2 ...]"

    url = parts[1]
    tags = parts[2:] if len(parts) > 2 else []

    LINKS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Load existing
    links = []
    if LINKS_FILE.exists():
        try:
            links = json.loads(LINKS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            links = []

    # Try to fetch title
    title = url
    try:
        import urllib.request
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "AshleyBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read(8192).decode("utf-8", errors="replace")
            m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
            if m:
                title = m.group(1).strip()[:100]
    except Exception:
        pass

    links.append({
        "url": url,
        "title": title,
        "tags": tags,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    })
    LINKS_FILE.write_text(json.dumps(links, indent=2))

    try:
        _get_vmem().store_bookmark(url=url, title=title, tags=",".join(tags))
    except Exception as exc:
        print(f"[{datetime.now(timezone.utc).isoformat()}] vmem bookmark store error: {exc}")

    tag_str = f" [{', '.join(tags)}]" if tags else ""
    return f"🔖 Saved: {title}{tag_str}"


def _handle_links(text: str) -> str:
    """List saved links. /links [tag] to filter."""
    parts = text.strip().split(maxsplit=1)
    tag_filter = parts[1].strip().lower() if len(parts) > 1 else ""

    if not LINKS_FILE.exists():
        return "No saved links yet. Use /save <url> [tags] to add one."

    try:
        links = json.loads(LINKS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return "No saved links."

    if tag_filter:
        links = [l for l in links if tag_filter in [t.lower() for t in l.get("tags", [])]]
        if not links:
            return f"No links tagged '{tag_filter}'."

    if not links:
        return "No saved links."

    lines = [f"🔖 Saved links ({len(links)}):"]
    for link in links[-15:]:  # Show last 15
        title = link.get("title", link.get("url", ""))
        url = link.get("url", "")
        tags = link.get("tags", [])
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        lines.append(f"• {title}{tag_str}")
        lines.append(f"  {url}")
    return "\n".join(lines)


# ===================== Conversation Memory =====================

def _load_conversation_buffer() -> list[dict]:
    """Load recent conversation history."""
    if not CONVERSATION_FILE.exists():
        return []
    try:
        return json.loads(CONVERSATION_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_conversation_buffer(messages: list[dict]) -> None:
    """Save conversation buffer, keeping only last N messages."""
    CONVERSATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    trimmed = messages[-CONVERSATION_MAX:]
    CONVERSATION_FILE.write_text(json.dumps(trimmed, indent=2))


def record_conversation(role: str, text: str) -> None:
    """Record a message to the conversation buffer and vector memory."""
    buf = _load_conversation_buffer()
    buf.append({
        "role": role,
        "text": text[:500],
        "time": datetime.now(timezone.utc).isoformat(),
    })
    _save_conversation_buffer(buf)
    # Store in vector memory for long-term recall
    try:
        _get_vmem().store(
            content=f"[{role}] {text[:500]}",
            category="conversation",
            source="telegram",
            metadata={"role": role, "timestamp": datetime.now(timezone.utc).isoformat()},
        )
    except Exception as exc:
        print(f"[{datetime.now(timezone.utc).isoformat()}] vmem conversation store error: {exc}")


def get_conversation_context() -> str:
    """Get recent conversation as context string for the planner."""
    buf = _load_conversation_buffer()
    if not buf:
        return ""
    lines = ["Recent conversation:"]
    for msg in buf[-10:]:
        role = msg.get("role", "?")
        text = msg.get("text", "")
        lines.append(f"[{role}] {text}")
    return "\n".join(lines)


def get_memory_context(query: str) -> str:
    """Get relevant long-term memories for a query."""
    try:
        return _get_vmem().recall(query, limit=5)
    except Exception as exc:
        print(f"[{datetime.now(timezone.utc).isoformat()}] vmem recall error: {exc}")
        return ""


# ===================== Morning Briefing =====================

def _handle_briefing() -> str:
    """Generate a morning briefing with available info."""
    sections = ["☀️ Morning Briefing\n"]

    # Date/time
    from datetime import timezone as tz
    import zoneinfo
    try:
        est = zoneinfo.ZoneInfo("America/New_York")
        now_est = datetime.now(est)
        sections.append(f"📅 {now_est.strftime('%A, %B %-d, %Y — %-I:%M %p %Z')}\n")
    except Exception:
        sections.append(f"📅 {datetime.now(timezone.utc).strftime('%A, %B %-d, %Y')} (UTC)\n")

    # Weather
    if OPENWEATHER_API_KEY:
        try:
            weather = _handle_weather("/weather")
            sections.append(weather + "\n")
        except Exception:
            pass

    # Calendar (today)
    try:
        gs = _load_google_services()
        if gs:
            events = gs.list_events(days=1)
            if events:
                sections.append("📆 Today's Schedule:")
                for ev in events:
                    start = ev["start"]
                    if "T" in start:
                        try:
                            dt = datetime.fromisoformat(start)
                            time_str = dt.strftime("%-I:%M %p")
                        except Exception:
                            time_str = start
                    else:
                        time_str = "All day"
                    loc = f" @ {ev['location']}" if ev.get("location") else ""
                    sections.append(f"  {time_str} — {ev['summary']}{loc}")
                sections.append("")
            else:
                sections.append("📆 No events today.\n")
    except Exception:
        pass

    # Unread emails
    try:
        gs = _load_google_services()
        if gs:
            count = gs.count_unread()
            if count > 0:
                sections.append(f"📧 {count} unread email(s)")
                # Show top 3 unread
                try:
                    emails = gs.list_emails(query="is:unread", max_results=3)
                    for e in emails:
                        sender = e.get("from", "")
                        if "<" in sender:
                            sender = sender.split("<")[0].strip().strip('"')
                        subj = e.get("subject", "(no subject)")[:50]
                        sections.append(f"  • {sender}: {subj}")
                except Exception:
                    pass
                sections.append("")
            else:
                sections.append("📧 Inbox clear — no unread emails.\n")
    except Exception:
        pass

    # Pending questions
    try:
        count = count_pending_questions()
        if count > 0:
            sections.append(f"❓ {count} pending agent question(s) — send /pending to view\n")
    except Exception:
        pass

    # Task summary
    try:
        task_summary = run_psql(
            "SELECT "
            "SUM(CASE WHEN status = 'TODO' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'IN_PROGRESS' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'BLOCKED' THEN 1 ELSE 0 END) "
            "FROM autonomous_tasks;"
        )
        if task_summary:
            todo, in_prog, blocked = [v.strip() for v in task_summary.split("|", 2)]
            if int(todo or 0) + int(in_prog or 0) + int(blocked or 0) > 0:
                sections.append(
                    f"📋 Tasks: {todo} todo, {in_prog} in progress, {blocked} blocked\n"
                )
    except Exception:
        pass

    # Scheduled jobs running today
    try:
        import glob
        job_count = len(list(SCHEDULED_JOBS_DIR.glob(f"{SCHEDULED_JOBS_PREFIX}*.timer")))
        if job_count > 0:
            sections.append(f"⏰ {job_count} active scheduled job(s)\n")
    except Exception:
        pass

    # Today's notes
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        note_file = NOTES_DIR / f"{today}.md"
        if note_file.exists():
            content = note_file.read_text()
            note_count = content.count("\n- [")
            if note_count > 0:
                sections.append(f"📝 {note_count} note(s) from today\n")
    except Exception:
        pass

    return "\n".join(sections).strip()


# ===================== Weekly Review =====================

def _handle_weekly_review() -> str:
    """Generate a weekly review summary."""
    sections = ["📊 Weekly Review\n"]

    from datetime import timedelta
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    week_ago_str = week_ago.strftime("%Y-%m-%d %H:%M:%S")

    # Tasks completed this week
    try:
        completed = run_psql(
            f"SELECT COUNT(*) FROM autonomous_tasks "
            f"WHERE status = 'COMPLETE' AND completed_at >= '{week_ago_str}';"
        )
        completed_count = int(completed.strip()) if completed.strip() else 0

        created = run_psql(
            f"SELECT COUNT(*) FROM autonomous_tasks "
            f"WHERE created_at >= '{week_ago_str}';"
        )
        created_count = int(created.strip()) if created.strip() else 0

        blocked = run_psql(
            "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'BLOCKED';"
        )
        blocked_count = int(blocked.strip()) if blocked.strip() else 0

        sections.append(
            f"📋 Tasks: {created_count} created, {completed_count} completed, {blocked_count} still blocked\n"
        )

        # List completed task names
        if completed_count > 0:
            names = run_psql(
                f"SELECT name FROM autonomous_tasks "
                f"WHERE status = 'COMPLETE' AND completed_at >= '{week_ago_str}' "
                f"ORDER BY completed_at DESC LIMIT 10;"
            )
            if names:
                sections.append("Completed:")
                for name in names.strip().splitlines():
                    sections.append(f"  ✅ {name.strip()}")
                sections.append("")
    except Exception:
        pass

    # Notes count this week
    try:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        note_count = 0
        for f in NOTES_DIR.glob("*.md"):
            try:
                file_date = datetime.strptime(f.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date >= week_ago:
                    note_count += f.read_text().count("\n- [")
            except ValueError:
                pass
        if note_count > 0:
            sections.append(f"📝 {note_count} note(s) captured this week\n")
    except Exception:
        pass

    # Links saved this week
    try:
        if LINKS_FILE.exists():
            links = json.loads(LINKS_FILE.read_text())
            week_links = [
                l for l in links
                if l.get("saved_at", "") >= week_ago.isoformat()
            ]
            if week_links:
                sections.append(f"🔖 {len(week_links)} link(s) saved this week\n")
    except Exception:
        pass

    # Calendar events this week
    try:
        gs = _load_google_services()
        if gs:
            events = gs.list_events(days=7)
            if events:
                sections.append(f"📆 {len(events)} upcoming event(s) this week\n")
    except Exception:
        pass

    # Scheduled jobs
    try:
        job_count = len(list(SCHEDULED_JOBS_DIR.glob(f"{SCHEDULED_JOBS_PREFIX}*.timer")))
        sections.append(f"⏰ {job_count} active scheduled job(s)\n")
    except Exception:
        pass

    if len(sections) <= 1:
        return "📊 Weekly review: No activity data found for this week."

    return "\n".join(sections).strip()


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

    if lowered.startswith("/adhoc"):
        adhoc_text = stripped[6:].strip()
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=agent kind=adhoc agent=coder")
        return queue_adhoc_coder(adhoc_text)

    if lowered.startswith("/ask"):
        question = stripped[4:].strip()
        if not question:
            return "Usage: /ask <question> or /ask <agent> <question>"
        ask_agent_name, ask_result = queue_ask_agent(question)
        if ask_result:
            return ask_result
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=agent kind=ask agent={ask_agent_name or ASK_DEFAULT_AGENT}")
        return ""

    # ---------- Scheduled jobs ----------
    if lowered.startswith("/schedule"):
        sched_text = stripped[9:].strip()
        if not sched_text:
            return (
                "Usage: /schedule <cron> <task description>\n"
                "Examples:\n"
                "  /schedule 0 7 * * * Send me a morning briefing\n"
                "  /schedule */30 * * * * Check server health\n"
                "  /schedule 0 9 * * 1 Weekly project status report"
            )
        # Parse: first 5 tokens are cron fields, rest is description
        parts = sched_text.split()
        if len(parts) < 6:
            return (
                "Need 5 cron fields + a task description.\n"
                "Format: /schedule <min> <hour> <dom> <month> <dow> <task>"
            )
        cron_expr = " ".join(parts[:5])
        task_desc = " ".join(parts[5:])
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=schedule cron={cron_expr}")
        return schedule_job(cron_expr, task_desc)

    if lowered.startswith("/jobs"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=jobs")
        return list_jobs()

    if lowered.startswith("/deletejob"):
        del_id = stripped[10:].strip()
        if not del_id:
            return "Usage: /deletejob <job_id>  or  /deletejob all"
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=deletejob id={del_id}")
        return delete_job(del_id)

    # ---------- Agent questions ----------
    if lowered.startswith("/pending"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=pending")
        return list_pending_questions()

    if lowered.startswith("/answer"):
        answer_text = stripped[7:].strip()
        if not answer_text:
            return "Usage: /answer <your response to the agent's question>"
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=answer")
        return handle_owner_reply(answer_text)

    # ---------- Gmail / Calendar ----------
    if lowered.startswith("/emails"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=emails")
        return _handle_emails(stripped)

    if lowered.startswith("/email "):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=email-read")
        return _handle_read_email(stripped)

    if lowered.startswith("/sendemail"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=sendemail")
        return _handle_send_email(stripped)

    if lowered.startswith("/calendar"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=calendar")
        return _handle_calendar(stripped)

    if lowered.startswith("/event"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=event")
        return _handle_create_event(stripped)

    if lowered.startswith("/delevent"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=delevent")
        return _handle_delete_event(stripped)

    if lowered.startswith("/unread"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=unread")
        return _handle_unread()

    # ---------- Weather ----------
    if lowered.startswith("/weather"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=weather")
        return _handle_weather(stripped)

    # ---------- Web Search ----------
    if lowered.startswith("/search"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=search")
        return _handle_search(stripped)

    # ---------- Notes ----------
    if lowered.startswith("/notes"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=notes")
        return _handle_notes(stripped)

    if lowered.startswith("/note"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=note")
        return _handle_note(stripped)

    # ---------- Links / Bookmarks ----------
    if lowered.startswith("/save"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=save-link")
        return _handle_save_link(stripped)

    if lowered.startswith("/links"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=links")
        return _handle_links(stripped)

    # ---------- Briefing / Review ----------
    if lowered.startswith("/briefing"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=briefing")
        return _handle_briefing()

    if lowered.startswith("/weeklyreview"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=weeklyreview")
        return _handle_weekly_review()

    # ---------- Memory ----------
    if lowered.startswith("/remember"):
        fact = stripped[9:].strip()
        if not fact:
            return "Usage: /remember <fact or preference to store>"
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=remember")
        try:
            mid = _get_vmem().store_fact(fact)
            return f"🧠 Remembered (#{mid})."
        except Exception as exc:
            return f"Failed to store memory: {exc}"

    if lowered.startswith("/recall"):
        query = stripped[7:].strip()
        if not query:
            return "Usage: /recall <what to search for>"
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=recall")
        try:
            results = _get_vmem().search(query, limit=8)
            if not results:
                return "No matching memories found."
            lines = [f"🧠 Memories ({len(results)} matches):"]
            for m in results:
                sim = int(m['similarity'] * 100)
                cat = m['category']
                content = m['content'][:200]
                lines.append(f"#{m['id']} [{cat}] ({sim}%) {content}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Memory recall failed: {exc}"

    if lowered.startswith("/forget"):
        forget_id = stripped[7:].strip()
        if not forget_id or not forget_id.isdigit():
            return "Usage: /forget <memory_id>"
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=forget")
        try:
            if _get_vmem().delete(int(forget_id)):
                return f"🗑️ Memory #{forget_id} deleted."
            return f"Memory #{forget_id} not found."
        except Exception as exc:
            return f"Failed to delete memory: {exc}"

    if lowered.startswith("/memories"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=memories")
        try:
            vmem = _get_vmem()
            total = vmem.count()
            cats = vmem.categories()
            lines = [f"🧠 Memory store: {total} total memories"]
            for c in cats:
                lines.append(f"  {c['category']}: {c['count']}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed to get memory stats: {exc}"

    if "weather" in lowered and not lowered.startswith("/"):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=weather")
        return _handle_weather(f"/weather {WEATHER_LOCATION}")
    if should_handle_status(lowered):
        print(f"[{datetime.now(timezone.utc).isoformat()}] routed=local kind=status")
        return handle_status_query(lowered)

    # Record conversation and include context for planner
    record_conversation("user", stripped)
    context = get_conversation_context()
    memory_context = get_memory_context(stripped)
    planner_input = stripped
    if memory_context:
        planner_input = f"{memory_context}\n\n{planner_input}"
    if context and len(context) > 50:
        planner_input = f"{context}\n\nNew message: {planner_input}"
    spawn_planner(planner_input)
    print(f"[{datetime.now(timezone.utc).isoformat()}] routed=planner")
    preview = stripped.replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."
    return f"Queued for planner: {preview}"


class RouterHandler(BaseHTTPRequestHandler):
    def _json_response(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/pending":
            questions = list_pending_questions_structured()
            self._json_response(200, {"ok": True, "count": len(questions), "questions": questions})
            return

        if self.path == "/gmail/unread":
            gs = _load_google_services()
            if not gs:
                self._json_response(500, {"ok": False, "error": "Google services not available"})
                return
            count = gs.count_unread()
            self._json_response(200, {"ok": True, "unread": count})
            return

        if self.path == "/gmail/inbox":
            gs = _load_google_services()
            if not gs:
                self._json_response(500, {"ok": False, "error": "Google services not available"})
                return
            emails = gs.list_emails(max_results=10)
            self._json_response(200, {"ok": True, "emails": emails})
            return

        if self.path == "/calendar/today":
            gs = _load_google_services()
            if not gs:
                self._json_response(500, {"ok": False, "error": "Google services not available"})
                return
            events = gs.list_events(days=1)
            self._json_response(200, {"ok": True, "events": events})
            return

        if self.path == "/calendar/week":
            gs = _load_google_services()
            if not gs:
                self._json_response(500, {"ok": False, "error": "Google services not available"})
                return
            events = gs.list_events(days=7)
            self._json_response(200, {"ok": True, "events": events})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path not in {"/route", "/owner-message", "/ask-owner", "/reply", "/gmail/send", "/gmail/read", "/gmail/search", "/calendar/create", "/calendar/delete"}:
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

        if self.path == "/ask-owner":
            agent = str(payload.get("agent", "")).strip()
            task_id = payload.get("task_id")
            question = str(payload.get("question", "")).strip()
            if not agent or not question:
                self._json_response(400, {"ok": False, "error": "agent and question required"})
                return
            tid = int(task_id) if task_id is not None else None
            result = ask_owner_question(agent, tid, question)
            self._json_response(200, {"ok": True, "result": result})
            return

        if self.path == "/reply":
            answer = str(payload.get("answer", "")).strip()
            if not answer:
                self._json_response(400, {"ok": False, "error": "answer required"})
                return
            result = handle_owner_reply(answer)
            self._json_response(200, {"ok": True, "result": result})
            return

        if self.path == "/gmail/send":
            gs = _load_google_services()
            if not gs:
                self._json_response(500, {"ok": False, "error": "Google services not available"})
                return
            to = str(payload.get("to", "")).strip()
            subject = str(payload.get("subject", "")).strip()
            body = str(payload.get("body", "")).strip()
            if not to or not subject:
                self._json_response(400, {"ok": False, "error": "to and subject required"})
                return
            result = gs.send_email(to, subject, body)
            self._json_response(200 if result.get("ok") else 400, result)
            return

        if self.path == "/gmail/read":
            gs = _load_google_services()
            if not gs:
                self._json_response(500, {"ok": False, "error": "Google services not available"})
                return
            msg_id = str(payload.get("id", "")).strip()
            if not msg_id:
                self._json_response(400, {"ok": False, "error": "id required"})
                return
            result = gs.read_email(msg_id)
            self._json_response(200 if "error" not in result else 400, result)
            return

        if self.path == "/gmail/search":
            gs = _load_google_services()
            if not gs:
                self._json_response(500, {"ok": False, "error": "Google services not available"})
                return
            query = str(payload.get("query", "")).strip()
            max_results = int(payload.get("max_results", 10))
            emails = gs.list_emails(query=query, max_results=max_results)
            self._json_response(200, {"ok": True, "emails": emails})
            return

        if self.path == "/calendar/create":
            gs = _load_google_services()
            if not gs:
                self._json_response(500, {"ok": False, "error": "Google services not available"})
                return
            summary = str(payload.get("summary", "")).strip()
            start_time = str(payload.get("start_time", "")).strip()
            if not summary or not start_time:
                self._json_response(400, {"ok": False, "error": "summary and start_time required"})
                return
            result = gs.create_event(
                summary=summary,
                start_time=start_time,
                end_time=payload.get("end_time"),
                description=str(payload.get("description", "")),
                location=str(payload.get("location", "")),
                all_day=bool(payload.get("all_day", False)),
            )
            self._json_response(200 if result.get("ok") else 400, result)
            return

        if self.path == "/calendar/delete":
            gs = _load_google_services()
            if not gs:
                self._json_response(500, {"ok": False, "error": "Google services not available"})
                return
            event_id = str(payload.get("id", "")).strip()
            if not event_id:
                self._json_response(400, {"ok": False, "error": "id required"})
                return
            result = gs.delete_event(event_id)
            self._json_response(200 if result.get("ok") else 400, result)
            return

        if self.path == "/owner-message":
            ok, reply = send_owner_message(
                str(payload.get("agent", "")),
                str(payload.get("question", "")),
                str(payload.get("response", "")),
            )
            self._json_response(200 if ok else 400, {"ok": ok, "reply": reply})
            return

        text = payload.get("text", "")
        reply = route_text(text)
        self._json_response(200, {"reply": reply})

    def log_message(self, format: str, *args) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", CHAT_ROUTER_PORT), RouterHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
