#!/bin/bash
# Autonomous Task Manager - PostgreSQL Version
# Dispatches tasks to coder/tester via OpenClaw agent CLI

set -euo pipefail

if [ -f "$HOME/.env" ]; then
  export $(grep -v '^#' "$HOME/.env" | xargs)
fi

POSTGRES_HOST=${OPENCLAW_POSTGRES_HOST:-localhost}
POSTGRES_PORT=${OPENCLAW_POSTGRES_PORT:-5433}
POSTGRES_DB=${OPENCLAW_POSTGRES_DB:-openclaw}
POSTGRES_USER=${OPENCLAW_POSTGRES_USER:-openclaw}
export PGPASSWORD=${OPENCLAW_POSTGRES_PASSWORD:-openclaw_dev_pass}

TASKS_DIR="$HOME/tasks"
LOG_DIR="$TASKS_DIR/logs"
HEARTBEAT_FILE="$TASKS_DIR/HEARTBEAT.md"
BLOCKED_DIGEST_STATE="$TASKS_DIR/.blocked-digest.sent"
DISCORD_NOTIFY="$HOME/.openclaw/workspace/discord-notify.sh"
TELEGRAM_NOTIFY="$HOME/.openclaw/workspace/telegram-notify.sh"
AGENT_CONTEXT_DIR="$HOME/.openclaw/workspace/agent-context"
LESSONS_FILE="$AGENT_CONTEXT_DIR/lessons.log"
PROJECT_CONTEXT_DIR="$AGENT_CONTEXT_DIR/projects"

MAX_PARALLEL_CODER=${MAX_PARALLEL_CODER:-3}
MAX_PARALLEL_TESTER=${MAX_PARALLEL_TESTER:-1}
AGENT_TIMEOUT=${AGENT_TIMEOUT:-3600}
TESTER_TIMEOUT=${TESTER_TIMEOUT:-2400}
TESTER_STEP_TIMEOUT=${TESTER_STEP_TIMEOUT:-600}
TESTER_MAX_ATTEMPTS=${TESTER_MAX_ATTEMPTS:-3}
STALE_SECONDS=${STALE_SECONDS:-7200}
MAX_ATTEMPTS=${MAX_ATTEMPTS:-2}
BLOCKED_DIGEST_INTERVAL_SEC=${BLOCKED_DIGEST_INTERVAL_SEC:-21600}
COMPLETED_RETENTION_DAYS=${COMPLETED_RETENTION_DAYS:-7}
LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS:-7}
VERBOSE_TASK_LOGS=${VERBOSE_TASK_LOGS:-0}
TASK_HEARTBEAT_SEC=${TASK_HEARTBEAT_SEC:-60}
TASK_MANAGER_LOG="$LOG_DIR/task-manager.log"
TEST_CLEANUP_AFTER=${TEST_CLEANUP_AFTER:-0}

OPENCLAW_NODE=${OPENCLAW_NODE:-/usr/bin/node}
OPENCLAW_CLI=${OPENCLAW_CLI:-$HOME/.local/openclaw/node_modules/openclaw/dist/index.js}
PLANNER_TEMP=${PLANNER_TEMP:-0.25}
CODER_TEMP=${CODER_TEMP:-0.18}
TESTER_TEMP=${TESTER_TEMP:-0.10}

mkdir -p "$LOG_DIR"
mkdir -p "$AGENT_CONTEXT_DIR" "$PROJECT_CONTEXT_DIR"

# Mirror service output into the task manager log for easier debugging.
exec > >(tee -a "$TASK_MANAGER_LOG") 2>&1

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

sql_escape() {
  echo "$1" | sed "s/'/''/g"
}

query() {
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -t -c "$1" 2>/dev/null || echo ""
}

execute() {
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "$1" >/dev/null 2>&1
}

openclaw_cmd() {
  if command -v openclaw >/dev/null 2>&1; then
    openclaw "$@"
  else
    "$OPENCLAW_NODE" "$OPENCLAW_CLI" "$@"
  fi
}

openclaw_cmd_stream() {
  if command -v stdbuf >/dev/null 2>&1; then
    if command -v openclaw >/dev/null 2>&1; then
      stdbuf -oL -eL openclaw "$@"
    else
      stdbuf -oL -eL "$OPENCLAW_NODE" "$OPENCLAW_CLI" "$@"
    fi
  else
    openclaw_cmd "$@"
  fi
}

thinking_from_temp() {
  awk -v t="$1" 'BEGIN {
    if (t <= 0.15) { print "minimal"; exit }
    if (t <= 0.35) { print "low"; exit }
    if (t <= 0.60) { print "medium"; exit }
    print "high"
  }'
}

CODER_THINKING=$(thinking_from_temp "$CODER_TEMP")
TESTER_THINKING=$(thinking_from_temp "$TESTER_TEMP")

cleanup_project_test_env() {
  if [ "$TEST_CLEANUP_AFTER" -ne 1 ]; then
    return
  fi

  if [ -z "$1" ]; then
    return
  fi

  if ! command -v docker >/dev/null 2>&1; then
    return
  fi

  local project_dir="$HOME/projects/$1"
  if [ ! -f "$project_dir/docker-compose.yml" ]; then
    return
  fi

  log "Cleaning up test environment for project '$1'"
  (cd "$project_dir" && docker compose down -v) || true
}

normalize_statuses() {
  execute "UPDATE autonomous_tasks
           SET status = CASE LOWER(status)
             WHEN 'todo' THEN 'TODO'
             WHEN 'in-progress' THEN 'IN_PROGRESS'
             WHEN 'in_progress' THEN 'IN_PROGRESS'
             WHEN 'ready_for_testing' THEN 'READY_FOR_TESTING'
             WHEN 'complete' THEN 'COMPLETE'
             WHEN 'blocked' THEN 'BLOCKED'
             ELSE status
           END;"
}

send_notification() {
  local status="$1"
  local task_id="$2"
  local task_name="$3"
  local details="$4"

  if [ -x "$TELEGRAM_NOTIFY" ]; then
    bash "$TELEGRAM_NOTIFY" "$status" "$task_id" "$task_name" "$details"
  elif [ -f "$DISCORD_NOTIFY" ]; then
    bash "$DISCORD_NOTIFY" "$status" "$task_id" "$task_name" "$details"
  fi
}

record_blocked_reason() {
  local task_id="$1"
  local reason="$2"
  local reason_sql
  reason_sql=$(sql_escape "$reason")
  execute "INSERT INTO blocked_reasons (task_id, reason) VALUES ($task_id, '$reason_sql');"
}

summarize_blocked_reasons() {
  local task_id="$1"
  local summary
  summary=$(query "SELECT COALESCE(reason,'') FROM blocked_reasons WHERE task_id = $task_id ORDER BY created_at ASC;")
  if [ -z "$summary" ]; then
    echo "(no reasons captured)"
    return
  fi

  local message=""
  while IFS='|' read -r reason; do
    reason=$(echo "$reason" | xargs)
    if [ -n "$reason" ]; then
      message+="- ${reason}\n"
    fi
  done <<< "$summary"

  if [ -z "$message" ]; then
    echo "(no reasons captured)"
    return
  fi

  echo -e "$message"
}

task_context() {
  local task_id="$1"
  local row
  row=$(query "SELECT COALESCE(implementation_plan,''), COALESCE(notes,''), COALESCE(solution,''), COALESCE(project,'') FROM autonomous_tasks WHERE id = $task_id;")
  if [ -z "$row" ]; then
    echo ""
    return
  fi

  local plan notes solution project
  IFS='|' read -r plan notes solution project <<< "$row"

  plan=$(echo "$plan" | xargs)
  notes=$(echo "$notes" | xargs)
  solution=$(echo "$solution" | xargs)
  project=$(echo "$project" | xargs)

  local context=""
  if [ -n "$project" ]; then
    context+="Project: $project\n"
  fi
  if [ -n "$plan" ]; then
    context+="Plan: $plan\n"
  fi
  if [ -n "$notes" ]; then
    context+="Notes: $notes\n"
  fi
  if [ -n "$solution" ]; then
    context+="Solution: $solution\n"
  fi

  echo -e "$context"
}

safe_project_name() {
  echo "$1" | tr -c 'A-Za-z0-9._-' '_'
}

lessons_context_text() {
  if [ ! -f "$LESSONS_FILE" ]; then
    return
  fi
  tail -n 20 "$LESSONS_FILE" | sed 's/^/- /'
}

project_context_text() {
  local project_name="$1"
  if [ -z "$project_name" ]; then
    return
  fi

  local project_key
  project_key=$(safe_project_name "$project_name")
  local project_file="$PROJECT_CONTEXT_DIR/${project_key}.log"
  if [ ! -f "$project_file" ]; then
    return
  fi
  tail -n 20 "$project_file" | sed 's/^/- /'
}

project_repo_override() {
  local project_name="$1"
  if [ -z "$project_name" ]; then
    return
  fi

  local project_key
  project_key=$(safe_project_name "$project_name")
  local project_file="$PROJECT_CONTEXT_DIR/${project_key}.log"
  if [ ! -f "$project_file" ]; then
    return
  fi

  grep -Eo '~/projects/[A-Za-z0-9._/-]+' "$project_file" | tail -n 1 || true
}

cleanup_completed_tasks() {
  local retention_days="$1"
  execute "DELETE FROM blocked_reasons
           WHERE task_id IN (
             SELECT id
             FROM autonomous_tasks
             WHERE status = 'COMPLETE'
               AND completed_at < NOW() - INTERVAL '${retention_days} days'
           );"
  execute "DELETE FROM autonomous_tasks
           WHERE status = 'COMPLETE'
             AND completed_at < NOW() - INTERVAL '${retention_days} days';"
}

cleanup_task_logs() {
  local retention_days="$1"
  if [ -d "$LOG_DIR" ]; then
    find "$LOG_DIR" -type f -name "task-*.log" -mtime +"$retention_days" -delete 2>/dev/null || true
  fi
}

cleanup_completed_task_logs() {
  if [ ! -d "$LOG_DIR" ]; then
    return
  fi

  local completed_ids
  completed_ids=$(query "SELECT id FROM autonomous_tasks WHERE status = 'COMPLETE';")
  if [ -z "$completed_ids" ]; then
    return
  fi

  while IFS='|' read -r task_id; do
    task_id=$(echo "$task_id" | xargs)
    if [ -n "$task_id" ]; then
      rm -f "$LOG_DIR/task-${task_id}.log" 2>/dev/null || true
    fi
  done <<< "$completed_ids"
}

send_blocked_digest_if_needed() {
  local blocked_count="$1"

  if [ "$blocked_count" -le 0 ]; then
    return 0
  fi

  local now_epoch
  now_epoch=$(date +%s)
  local last_epoch=0
  if [ -f "$BLOCKED_DIGEST_STATE" ]; then
    last_epoch=$(cat "$BLOCKED_DIGEST_STATE" 2>/dev/null || echo 0)
  fi

  if [ $((now_epoch - last_epoch)) -lt "$BLOCKED_DIGEST_INTERVAL_SEC" ]; then
    return 0
  fi

  local summary
  summary=$(query "SELECT id, name, COALESCE(blocked_reason,'') FROM autonomous_tasks WHERE status = 'BLOCKED' ORDER BY priority DESC, id ASC LIMIT 10;")

  if [ -n "$summary" ]; then
    local message="Blocked tasks: $blocked_count\n\n"
    while IFS='|' read -r tid tname treason; do
      tid=$(echo "$tid" | xargs)
      tname=$(echo "$tname" | xargs)
      treason=$(echo "$treason" | xargs)
      message+="#${tid} ${tname}\n${treason}\n\n"
    done <<< "$summary"

    if [ -x "$TELEGRAM_NOTIFY" ]; then
      bash "$TELEGRAM_NOTIFY" "blocked-summary" "blocked" "Blocked Tasks" "$message"
      echo "$now_epoch" > "$BLOCKED_DIGEST_STATE"
    fi
  fi
}

log "=== Autonomous Task Manager Starting (Database Mode) ==="
log "Working directory: $(pwd)"
log "OPENCLAW_NODE=$OPENCLAW_NODE"
log "OPENCLAW_CLI=$OPENCLAW_CLI"

normalize_statuses

log "Checking for stale PIDs..."
STALE_TASKS=$(query "SELECT id, name, pid, started_at, attempt_count, COALESCE(assigned_agent,'') FROM autonomous_tasks WHERE status = 'IN_PROGRESS' AND pid IS NOT NULL;")

if [ -n "$STALE_TASKS" ]; then
  echo "$STALE_TASKS" | while IFS='|' read -r task_id name pid started_at attempt_count assigned_agent; do
    task_id=$(echo "$task_id" | xargs)
    name=$(echo "$name" | xargs)
    pid=$(echo "$pid" | xargs)
    started_at=$(echo "$started_at" | xargs)
    attempt_count=$(echo "$attempt_count" | xargs)
    assigned_agent=$(echo "$assigned_agent" | xargs)

    max_attempts="$MAX_ATTEMPTS"
    if [ "$assigned_agent" = "tester" ]; then
      max_attempts="$TESTER_MAX_ATTEMPTS"
    fi

    is_running=0
    if [ -n "$pid" ] && ps -p "$pid" >/dev/null 2>&1; then
      is_running=1
    fi

    is_stale=0
    if [ -n "$started_at" ]; then
      started_epoch=$(date -d "$started_at" +%s 2>/dev/null || echo "")
      now_epoch=$(date +%s)
      if [ -n "$started_epoch" ]; then
        age=$((now_epoch - started_epoch))
        if [ "$age" -gt "$STALE_SECONDS" ]; then
          is_stale=1
        fi
      fi
    fi

    if [ "$is_stale" -eq 1 ] || [ "$is_running" -eq 0 ]; then
      next_attempt=$((attempt_count + 1))
      record_blocked_reason "$task_id" "Abrupt stop: stale PID $pid"
      if [ "$is_running" -eq 1 ] && [ -n "$pid" ]; then
        log "Stale timeout for task: $name (PID $pid) - terminating"
        kill -15 "$pid" >/dev/null 2>&1 || true
        sleep 1
        if ps -p "$pid" >/dev/null 2>&1; then
          kill -9 "$pid" >/dev/null 2>&1 || true
        fi
      else
        log "Found stale PID $pid for task: $name"
      fi

      if [ "$next_attempt" -ge "$max_attempts" ]; then
        summary=$(summarize_blocked_reasons "$task_id")
        context=$(task_context "$task_id")
        execute "UPDATE autonomous_tasks
                 SET status = 'BLOCKED',
                     blocked_reason = 'Attempt limit reached; requires human unblock',
                     error_log = 'Attempt limit reached; requires human unblock',
                     pid = NULL,
                     assigned_agent = NULL,
                     started_at = NULL,
                     attempt_count = $next_attempt
                 WHERE id = $task_id;"
        send_notification blocker "$task_id" "$name" "Attempt limit reached ($max_attempts).\n${context}\nFailures:\n${summary}\nSuggested: provide a solution with /unblock $task_id <solution>."
      else
        execute "UPDATE autonomous_tasks
                 SET status = 'TODO',
                     blocked_reason = 'Stale PID reset: $pid',
                     pid = NULL,
                     assigned_agent = NULL,
                     started_at = NULL,
                     attempt_count = $next_attempt
                 WHERE id = $task_id;"
        send_notification reset "$task_id" "$name" "Stale PID reset to TODO"
      fi
    fi
  done
fi

execute "UPDATE autonomous_tasks
         SET status = 'BLOCKED',
             blocked_reason = 'Attempt limit reached; requires human unblock',
             error_log = 'Attempt limit reached; requires human unblock'
         WHERE status = 'TODO' AND attempt_count >= $MAX_ATTEMPTS;"

RUNNING_CODER=$(query "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'IN_PROGRESS' AND assigned_agent = 'coder' AND pid IS NOT NULL;" | xargs)
RUNNING_TESTER=$(query "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'IN_PROGRESS' AND assigned_agent = 'tester' AND pid IS NOT NULL;" | xargs)

log "Running coder tasks: $RUNNING_CODER / $MAX_PARALLEL_CODER"
log "Running tester tasks: $RUNNING_TESTER / $MAX_PARALLEL_TESTER"

SLOTS_CODER=$((MAX_PARALLEL_CODER - RUNNING_CODER))
SLOTS_TESTER=$((MAX_PARALLEL_TESTER - RUNNING_TESTER))

if [ "$SLOTS_CODER" -gt 0 ]; then
  TODO_TASKS=$(query "SELECT id, name, implementation_plan, phase, COALESCE(notes,''), attempt_count, COALESCE(project,''), COALESCE(solution,'')
                      FROM autonomous_tasks
                      WHERE status = 'TODO'
                        AND attempt_count < $MAX_ATTEMPTS
                      ORDER BY priority DESC, id ASC;")

  if [ -n "$TODO_TASKS" ]; then
    echo "$TODO_TASKS" | while IFS='|' read -r task_id name plan phase notes attempt_count project solution; do
      task_id=$(echo "$task_id" | xargs)
      name=$(echo "$name" | xargs)
      plan=$(echo "$plan" | xargs)
      phase=$(echo "$phase" | xargs)
      attempt_count=$(echo "$attempt_count" | xargs)
      project=$(echo "$project" | xargs)
      solution=$(echo "$solution" | xargs)
      lessons_guidance=$(lessons_context_text)
      project_guidance=$(project_context_text "$project")
      project_repo_hint=$(project_repo_override "$project")

      if [ "$attempt_count" -ge "$MAX_ATTEMPTS" ]; then
        execute "UPDATE autonomous_tasks
                 SET status = 'BLOCKED',
                     blocked_reason = 'Attempt limit reached; requires human unblock',
                     error_log = 'Attempt limit reached; requires human unblock'
                 WHERE id = $task_id;"
        send_notification blocker "$task_id" "$name" "Attempt limit reached; requires human unblock"
        continue
      fi

      agent="coder"

      log "Dispatching task $task_id to $agent: $name"
      WORK_LOG="$LOG_DIR/task-${task_id}.log"

      send_notification started "$task_id" "$name" "assigned to $agent"

      (
        exec > "$WORK_LOG" 2>&1
        echo "=== Task: $name ==="
        echo "Phase: $phase"
        echo "Assigned Agent: $agent"
        echo "Started: $(date)"
        echo
        echo "Implementation Plan:"
        echo "$plan"
        echo
        echo "=== Execution Log ==="

        payload=$(cat <<EOF
Task ID: $task_id
Task Name: $name
      Project: ${project:-<unspecified>}
Phase: $phase
Plan: $plan
      Solution (if provided): ${solution:-<none>}

    Global lessons learned:
    ${lessons_guidance:-<none>}

    MANDATORY project directives from /project:
    ${project_guidance:-<none>}

    Project repo override from /project notes (if present):
    ${project_repo_hint:-<none>}

Update task notes with:
- Files changed
- Tests run (command + result)
- Docs updated (README/CHANGELOG/DEPLOYMENT_TASKS as needed)

Git safety (required):
- If project repo override is provided above, it takes precedence for all git operations and remote checks.
- Repo hint directory is: ~/projects/${project:-<unspecified>}.
- If the exact hint directory does not exist, resolve a best-match repository under ~/projects using this order:
  1) exact project name
  2) canonical project name (remove suffixes like _audit, _review, _test)
  3) existing git repo whose origin remote contains the project or canonical token
- Before any git operation, cd into the resolved repo and verify git rev-parse --is-inside-work-tree succeeds.
- Verify origin remote is compatible with either the project name or canonical token.
- If no clear repo can be resolved, do not push and return TASK_BLOCKED:$task_id:REPO_NOT_FOUND_OR_REMOTE_MISMATCH with candidates checked.
- If copying from a base repo, never copy `.git` metadata.
- Use copy commands that exclude .git (for example: rsync -a --exclude .git <src>/ <dst>/).

If behavior, setup, or usage changes, update repo documentation accordingly.
Do not push changes; phase tester will push after successful testing.

Return one of these markers in your final response:
- TASK_COMPLETE:$task_id
- TASK_BLOCKED:$task_id:<reason>
EOF
)

        response_file=$(mktemp)
        : > "$response_file"
        log "Launching agent for task $task_id (agent=$agent, timeout=${AGENT_TIMEOUT}s)"
        openclaw_cmd_stream agent --agent "$agent" --message "$payload" --timeout "$AGENT_TIMEOUT" --thinking "$CODER_THINKING" > "$response_file" 2>&1 &
        cmd_pid=$!
        tail --pid="$cmd_pid" -n +1 -f "$response_file" &
        tail_pid=$!
        agent_pid="$cmd_pid"
        sleep 0.2
        child_pid=$(pgrep -P "$cmd_pid" -n openclaw-agent 2>/dev/null || true)
        if [ -z "$child_pid" ]; then
          child_pid=$(pgrep -P "$cmd_pid" -n openclaw 2>/dev/null || true)
        fi
        if [ -n "$child_pid" ]; then
          agent_pid="$child_pid"
        fi

        execute "UPDATE autonomous_tasks
                 SET status = 'IN_PROGRESS',
                     assigned_agent = '$agent',
                     pid = $agent_pid,
                     started_at = CURRENT_TIMESTAMP,
                     attempt_count = attempt_count + 1
                 WHERE id = $task_id;"
        log "Task $task_id recorded PID $agent_pid (cmd pid $cmd_pid)"

        heartbeat_pid=""
        if [ "$VERBOSE_TASK_LOGS" -eq 1 ]; then
          (
            while kill -0 "$cmd_pid" 2>/dev/null; do
              sleep "$TASK_HEARTBEAT_SEC"
              if kill -0 "$cmd_pid" 2>/dev/null; then
                log "Task $task_id heartbeat: agent still running (pid $agent_pid)"
              fi
            done
          ) &
          heartbeat_pid=$!
        fi

        set +e
        wait "$cmd_pid"
        rc=$?
        set -e
        if [ -n "$heartbeat_pid" ]; then
          kill "$heartbeat_pid" 2>/dev/null || true
        fi
        if [ -n "${tail_pid:-}" ]; then
          kill "$tail_pid" 2>/dev/null || true
        fi
        response=$(cat "$response_file")
        rm -f "$response_file"
        echo "$response"
        if [ "$rc" -ne 0 ]; then
          log "Agent command failed for task $task_id (rc=$rc)"
        elif [ -z "$response" ]; then
          log "Agent command returned empty response for task $task_id"
        fi

        if echo "$response" | grep -qi -E "TASK_COMPLETE:$task_id|TASK[ _-]?COMPLETE"; then
          execute "UPDATE autonomous_tasks
                   SET status = 'READY_FOR_TESTING',
                       pid = NULL,
                       attempt_count = 0
                   WHERE id = $task_id;"
          send_notification ready "$task_id" "$name" "ready for testing"
        elif echo "$response" | grep -q "TASK_BLOCKED:$task_id:"; then
          reason=$(echo "$response" | sed -n "s/.*TASK_BLOCKED:$task_id:\(.*\)$/\1/p" | head -n 1)
          reason=${reason//"'"/"''"}
          context=$(task_context "$task_id")
          record_blocked_reason "$task_id" "Agent blocked: ${reason}"
          execute "UPDATE autonomous_tasks
                   SET status = 'BLOCKED',
                       pid = NULL,
                       blocked_reason = 'Task blocked: $reason',
                       error_log = '$reason'
                   WHERE id = $task_id;"
          send_notification blocker "$task_id" "$name" "${context}\nReason: $reason"
        else
          context=$(task_context "$task_id")
          record_blocked_reason "$task_id" "Abrupt stop: no completion marker"
          execute "UPDATE autonomous_tasks
                   SET status = 'BLOCKED',
                       pid = NULL,
                       blocked_reason = 'No completion marker found in agent response',
                       error_log = 'No completion marker found in agent response'
                   WHERE id = $task_id;"
          send_notification blocker "$task_id" "$name" "${context}\nReason: No completion marker found"
        fi

        echo
        echo "Finished: $(date)"
      ) &

      NEW_PID=$!
      log "Task $task_id worker PID $NEW_PID"

      SLOTS_CODER=$((SLOTS_CODER - 1))

      if [ "$SLOTS_CODER" -le 0 ]; then
        break
      fi
    done
  else
    log "No todo tasks available"
  fi
fi

if [ "$SLOTS_TESTER" -gt 0 ]; then
        READY_PHASES=$(query "SELECT COALESCE(project,''), COALESCE(phase,'__NO_PHASE__') AS phase_key
                    FROM autonomous_tasks
                    GROUP BY project, phase_key
                    HAVING SUM(CASE WHEN status IN ('TODO','IN_PROGRESS','BLOCKED') THEN 1 ELSE 0 END) = 0
                      AND SUM(CASE WHEN status = 'READY_FOR_TESTING' THEN 1 ELSE 0 END) > 0
                    ORDER BY project, phase_key;")

  if [ -n "$READY_PHASES" ]; then
    echo "$READY_PHASES" | while IFS='|' read -r phase_project phase_key; do
      phase_project=$(echo "$phase_project" | xargs)
      phase_key=$(echo "$phase_key" | xargs)
      lessons_guidance=$(lessons_context_text)
      project_guidance=$(project_context_text "$phase_project")
      project_repo_hint=$(project_repo_override "$phase_project")

      if [ "$phase_key" = "__NO_PHASE__" ]; then
        PHASE_TASKS=$(query "SELECT id, name, implementation_plan, COALESCE(project,'') FROM autonomous_tasks
                            WHERE status = 'READY_FOR_TESTING' AND phase IS NULL
                              AND COALESCE(project,'') = '$phase_project'
                            ORDER BY priority DESC, id ASC;")
        phase_label=""
        phase_filter="phase IS NULL AND COALESCE(project,'') = '$phase_project'"
      else
        phase_label="$phase_key"
        phase_key=${phase_key//"'"/"''"}
        PHASE_TASKS=$(query "SELECT id, name, implementation_plan, COALESCE(project,'') FROM autonomous_tasks
                            WHERE status = 'READY_FOR_TESTING' AND phase = '$phase_key'
                              AND COALESCE(project,'') = '$phase_project'
                            ORDER BY priority DESC, id ASC;")
        phase_filter="phase = '$phase_key' AND COALESCE(project,'') = '$phase_project'"
      fi

      if [ -z "$PHASE_TASKS" ]; then
        continue
      fi

      primary_id=$(echo "$PHASE_TASKS" | head -n 1 | cut -d'|' -f1 | xargs)
      primary_name=$(echo "$PHASE_TASKS" | head -n 1 | cut -d'|' -f2 | xargs)
      primary_attempt_count=$(query "SELECT COALESCE(attempt_count,0) FROM autonomous_tasks WHERE id = $primary_id;" | xargs)
      if [ -z "$primary_attempt_count" ]; then
        primary_attempt_count=0
      fi

      if [ "$primary_attempt_count" -ge "$TESTER_MAX_ATTEMPTS" ]; then
        context=$(task_context "$primary_id")
        record_blocked_reason "$primary_id" "Tester attempt limit reached ($TESTER_MAX_ATTEMPTS)"
        execute "UPDATE autonomous_tasks
                 SET status = 'BLOCKED',
                     pid = NULL,
                     assigned_agent = NULL,
                     blocked_reason = 'Tester attempt limit reached; requires human unblock',
                     error_log = 'Tester attempt limit reached; requires human unblock'
                 WHERE $phase_filter AND status IN ('READY_FOR_TESTING','IN_PROGRESS');"
        send_notification blocker "$primary_id" "$primary_name" "${context}\nTester retries reached limit ($TESTER_MAX_ATTEMPTS). Use /unblock $primary_id <solution> to continue."
        continue
      fi

      log "Dispatching tester for phase '$phase_label' (task $primary_id)"
      WORK_LOG="$LOG_DIR/task-${primary_id}.log"

      send_notification started "$primary_id" "$primary_name" "assigned to tester"

      (
        exec > "$WORK_LOG" 2>&1
        echo "=== Phase Test ==="
        echo "Phase: ${phase_label:-<none>}"
        echo "Primary Task: $primary_id"
        echo "Assigned Agent: tester"
        echo "Started: $(date)"
        echo
        echo "Tasks in phase:"
        echo "$PHASE_TASKS"
        echo
        echo "=== Execution Log ==="

        payload=$(cat <<EOF
Primary Task ID: $primary_id
      Project: ${phase_project:-<unspecified>}
Phase: ${phase_label:-<none>}
Tasks in phase:
$PHASE_TASKS

Global lessons learned:
${lessons_guidance:-<none>}

MANDATORY project directives from /project:
${project_guidance:-<none>}

Project repo override from /project notes (if present):
${project_repo_hint:-<none>}

Run E2E + data validation for this phase.
First output must be: "STEP 1: Boot check".
Use this step order and timebox each step to ${TESTER_STEP_TIMEOUT}s:
1) Boot check (docker compose up -d + health endpoints)
2) Schema + seed validation
3) API smoke checks
4) UI build/lint
5) UI flow check (minimal)
Before each step, print "STEP <n>: <name>".
After each step, print "STEP <n> RESULT: PASS/FAIL - <short reason>".
If failures occur, create coder tasks with repro steps and logs.

Tester code changes:
- You may make small, targeted fixes to address issues found during testing.
- If the fix is more than a small change or requires refactoring, do not implement it; create a coder task with repro steps.

Non-blocking guardrails:
- Preflight once before STEP 1: verify DB reachable, required containers running, and required init files exist (e.g., /app/init_db.py).
- If preflight fails, report the exact error and continue to summarize findings (do not block).
- If any step repeats the same error twice, stop further steps, summarize the issue, and continue (do not block).
- If a container is restarting, wait up to 60s for healthy; if still unhealthy, report and continue (do not block).

Documentation is mandatory before pushing:
- Review docs impact every run.
- Update README.md, CHANGELOG.md, and deployment docs when behavior/setup/API changes.
- If no docs change is needed, include "DOCS_CHECK: no changes required" in your final response.

Git safety before commit/push (required):
- If project repo override is provided above, it takes precedence for all git operations and remote checks.
- Repo hint directory is: ~/projects/${phase_project:-<unspecified>}.
- If the exact hint directory does not exist, resolve a best-match repository under ~/projects using this order:
  1) exact project name
  2) canonical project name (remove suffixes like _audit, _review, _test)
  3) existing git repo whose origin remote contains the project or canonical token
- Before any git add/commit/push, cd into resolved repo and verify git rev-parse --is-inside-work-tree succeeds.
- Verify origin remote is compatible with either the phase project name or canonical token.
- If unresolved, do not push and include REPO_NOT_FOUND_OR_REMOTE_MISMATCH details in final response.

Always complete docs review/update before commit and push all changes.
Commit message should be a short summary of what the phase was for.

Return these markers in your final response:
- TASK_COMPLETE:$primary_id
- GIT_PUSHED:$primary_id:<branch>:<short_sha>

If push cannot be completed, return:
- TASK_BLOCKED:$primary_id:PUSH_FAILED:<reason>
EOF
)

        response_file=$(mktemp)
        : > "$response_file"
        log "Launching agent for phase $primary_id (agent=tester, timeout=${TESTER_TIMEOUT}s)"
        openclaw_cmd_stream agent --agent "tester" --message "$payload" --timeout "$TESTER_TIMEOUT" --thinking "$TESTER_THINKING" > "$response_file" 2>&1 &
        cmd_pid=$!
        tail --pid="$cmd_pid" -n +1 -f "$response_file" &
        tail_pid=$!
        agent_pid="$cmd_pid"
        sleep 0.2
        child_pid=$(pgrep -P "$cmd_pid" -n openclaw-agent 2>/dev/null || true)
        if [ -z "$child_pid" ]; then
          child_pid=$(pgrep -P "$cmd_pid" -n openclaw 2>/dev/null || true)
        fi
        if [ -n "$child_pid" ]; then
          agent_pid="$child_pid"
        fi

        execute "UPDATE autonomous_tasks
                 SET status = 'IN_PROGRESS',
                     assigned_agent = 'tester',
                     pid = $agent_pid,
                     started_at = CURRENT_TIMESTAMP,
                     attempt_count = attempt_count + 1
                 WHERE id = $primary_id;"
        log "Tester $primary_id recorded PID $agent_pid (cmd pid $cmd_pid)"

        heartbeat_pid=""
        if [ "$VERBOSE_TASK_LOGS" -eq 1 ]; then
          (
            while kill -0 "$cmd_pid" 2>/dev/null; do
              sleep "$TASK_HEARTBEAT_SEC"
              if kill -0 "$cmd_pid" 2>/dev/null; then
                log "Tester $primary_id heartbeat: agent still running (pid $agent_pid)"
              fi
            done
          ) &
          heartbeat_pid=$!
        fi

        set +e
        wait "$cmd_pid"
        rc=$?
        set -e
        if [ -n "$heartbeat_pid" ]; then
          kill "$heartbeat_pid" 2>/dev/null || true
        fi
        if [ -n "${tail_pid:-}" ]; then
          kill "$tail_pid" 2>/dev/null || true
        fi
        response=$(cat "$response_file")
        rm -f "$response_file"
        echo "$response"

        if echo "$response" | grep -qi -E "TASK_COMPLETE:$primary_id|TASK[ _-]?COMPLETE" \
          && echo "$response" | grep -qi "GIT_PUSHED:$primary_id:"; then
            execute "UPDATE autonomous_tasks
                 SET status = 'COMPLETE',
                   pid = NULL,
                   completed_at = CURRENT_TIMESTAMP
                 WHERE $phase_filter AND status IN ('READY_FOR_TESTING','IN_PROGRESS');"
          send_notification complete "$primary_id" "$primary_name" "phase complete"
          cleanup_project_test_env "$phase_project"
        elif echo "$response" | grep -qi -E "TASK_COMPLETE:$primary_id|TASK[ _-]?COMPLETE"; then
          context=$(task_context "$primary_id")
          record_blocked_reason "$primary_id" "Tester missing git push confirmation marker"
            execute "UPDATE autonomous_tasks
                 SET status = 'READY_FOR_TESTING',
                   pid = NULL,
                   assigned_agent = NULL,
                   blocked_reason = NULL,
                   error_log = 'Tester marked complete without GIT_PUSHED marker'
                 WHERE $phase_filter AND status IN ('READY_FOR_TESTING','IN_PROGRESS');"
          send_notification ready "$primary_id" "$primary_name" "${context}\nTester marked complete without push confirmation (non-blocking)"
        elif echo "$response" | grep -q "TASK_BLOCKED:$primary_id:"; then
          reason=$(echo "$response" | sed -n "s/.*TASK_BLOCKED:$primary_id:\(.*\)$/\1/p" | head -n 1)
          reason=${reason//"'"/"''"}
          context=$(task_context "$primary_id")
          updated_attempt_count=$(query "SELECT COALESCE(attempt_count,0) FROM autonomous_tasks WHERE id = $primary_id;" | xargs)
          if [ -z "$updated_attempt_count" ]; then
            updated_attempt_count=0
          fi
          record_blocked_reason "$primary_id" "Tester reported blocked: ${reason}"
          if [ "$updated_attempt_count" -ge "$TESTER_MAX_ATTEMPTS" ]; then
            execute "UPDATE autonomous_tasks
                 SET status = 'BLOCKED',
                   pid = NULL,
                   assigned_agent = NULL,
                   blocked_reason = 'Tester attempt limit reached; requires human unblock',
                   error_log = 'Tester attempt limit reached; last error: $reason'
                 WHERE $phase_filter AND status IN ('READY_FOR_TESTING','IN_PROGRESS');"
            send_notification blocker "$primary_id" "$primary_name" "${context}\nTester reported blocked and reached max retries ($TESTER_MAX_ATTEMPTS): $reason\nUse /unblock $primary_id <solution>."
          else
            execute "UPDATE autonomous_tasks
                 SET status = 'READY_FOR_TESTING',
                   pid = NULL,
                   assigned_agent = NULL,
                   blocked_reason = NULL,
                   error_log = 'Tester reported blocked: $reason'
                 WHERE $phase_filter AND status IN ('READY_FOR_TESTING','IN_PROGRESS');"
            send_notification ready "$primary_id" "$primary_name" "${context}\nTester reported blocked (attempt $updated_attempt_count/$TESTER_MAX_ATTEMPTS, non-blocking): $reason"
          fi
        else
          context=$(task_context "$primary_id")
          updated_attempt_count=$(query "SELECT COALESCE(attempt_count,0) FROM autonomous_tasks WHERE id = $primary_id;" | xargs)
          if [ -z "$updated_attempt_count" ]; then
            updated_attempt_count=0
          fi
          record_blocked_reason "$primary_id" "Tester missing completion marker"
          if [ "$updated_attempt_count" -ge "$TESTER_MAX_ATTEMPTS" ]; then
            execute "UPDATE autonomous_tasks
                 SET status = 'BLOCKED',
                   pid = NULL,
                   assigned_agent = NULL,
                   blocked_reason = 'Tester attempt limit reached; requires human unblock',
                   error_log = 'Tester attempt limit reached; last error: missing completion marker'
                 WHERE $phase_filter AND status IN ('READY_FOR_TESTING','IN_PROGRESS');"
            send_notification blocker "$primary_id" "$primary_name" "${context}\nTester missing completion marker and reached max retries ($TESTER_MAX_ATTEMPTS). Use /unblock $primary_id <solution>."
          else
            execute "UPDATE autonomous_tasks
                 SET status = 'READY_FOR_TESTING',
                   pid = NULL,
                   assigned_agent = NULL,
                   blocked_reason = NULL,
                   error_log = 'Tester missing completion marker'
                 WHERE $phase_filter AND status IN ('READY_FOR_TESTING','IN_PROGRESS');"
            send_notification ready "$primary_id" "$primary_name" "${context}\nTester returned no completion marker (attempt $updated_attempt_count/$TESTER_MAX_ATTEMPTS, non-blocking)"
          fi
        fi

        echo
        echo "Finished: $(date)"
      ) &

      NEW_PID=$!
      log "Tester worker PID $NEW_PID"

      SLOTS_TESTER=$((SLOTS_TESTER - 1))
      if [ "$SLOTS_TESTER" -le 0 ]; then
        break
      fi
    done
  else
    log "No phases ready for testing"
  fi
fi

BLOCKED_COUNT=$(query "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'BLOCKED';" | xargs)
if [ "$BLOCKED_COUNT" -gt 0 ]; then
  log "Warning: $BLOCKED_COUNT tasks are blocked and need attention"
fi

send_blocked_digest_if_needed "$BLOCKED_COUNT"

cleanup_completed_tasks "$COMPLETED_RETENTION_DAYS"
cleanup_completed_task_logs
cleanup_task_logs "$LOG_RETENTION_DAYS"

TODO_COUNT=$(query "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'TODO';" | xargs)
READY_COUNT=$(query "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'READY_FOR_TESTING';" | xargs)
INPROGRESS_COUNT=$(query "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'IN_PROGRESS';" | xargs)
COMPLETE_COUNT=$(query "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'COMPLETE';" | xargs)

cat > "$HEARTBEAT_FILE" << EOF
# Task Manager Heartbeat

Last run: $(date -u '+%Y-%m-%d %H:%M:%S') UTC
Status: Running
Tasks in progress: ${INPROGRESS_COUNT}
Tasks todo: ${TODO_COUNT}
Tasks ready for testing: ${READY_COUNT}
Tasks complete: ${COMPLETE_COUNT}
Tasks blocked: ${BLOCKED_COUNT}

Using PostgreSQL database: $POSTGRES_DB@$POSTGRES_HOST
EOF

log "=== Task Manager Complete ==="
