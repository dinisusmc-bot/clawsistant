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
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path not in {"/route", "/owner-message", "/ask-owner", "/reply"}:
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
