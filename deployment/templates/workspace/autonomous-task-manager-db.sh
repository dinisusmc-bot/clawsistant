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

MAX_PARALLEL_CODER=${MAX_PARALLEL_CODER:-3}
MAX_PARALLEL_TESTER=${MAX_PARALLEL_TESTER:-1}
AGENT_TIMEOUT=${AGENT_TIMEOUT:-3600}
STALE_SECONDS=${STALE_SECONDS:-7200}
BLOCKED_DIGEST_INTERVAL_SEC=${BLOCKED_DIGEST_INTERVAL_SEC:-21600}
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

OPENCLAW_NODE=${OPENCLAW_NODE:-/usr/bin/node}
OPENCLAW_CLI=${OPENCLAW_CLI:-$HOME/.local/openclaw/node_modules/openclaw/dist/index.js}

mkdir -p "$LOG_DIR"

log() {
  echo "[$TIMESTAMP] $1"
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

normalize_statuses

log "Checking for stale PIDs..."
STALE_TASKS=$(query "SELECT id, name, pid, started_at FROM autonomous_tasks WHERE status = 'IN_PROGRESS' AND pid IS NOT NULL;")

if [ -n "$STALE_TASKS" ]; then
  echo "$STALE_TASKS" | while IFS='|' read -r task_id name pid started_at; do
    task_id=$(echo "$task_id" | xargs)
    name=$(echo "$name" | xargs)
    pid=$(echo "$pid" | xargs)
    started_at=$(echo "$started_at" | xargs)

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

      execute "UPDATE autonomous_tasks
               SET status = 'TODO',
                   blocked_reason = 'Stale PID reset: $pid',
                   pid = NULL,
                   assigned_agent = NULL,
               started_at = NULL,
                   attempt_count = attempt_count + 1
               WHERE id = $task_id;"
      send_notification reset "$task_id" "$name" "Stale PID reset to TODO"
    fi
  done
fi

RUNNING_CODER=$(query "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'IN_PROGRESS' AND assigned_agent = 'coder' AND pid IS NOT NULL;" | xargs)
RUNNING_TESTER=$(query "SELECT COUNT(*) FROM autonomous_tasks WHERE status = 'IN_PROGRESS' AND assigned_agent = 'tester' AND pid IS NOT NULL;" | xargs)

log "Running coder tasks: $RUNNING_CODER / $MAX_PARALLEL_CODER"
log "Running tester tasks: $RUNNING_TESTER / $MAX_PARALLEL_TESTER"

SLOTS_CODER=$((MAX_PARALLEL_CODER - RUNNING_CODER))
SLOTS_TESTER=$((MAX_PARALLEL_TESTER - RUNNING_TESTER))

if [ "$SLOTS_CODER" -gt 0 ]; then
  TODO_TASKS=$(query "SELECT id, name, implementation_plan, phase, COALESCE(notes,'') FROM autonomous_tasks WHERE status = 'TODO' ORDER BY priority DESC, id ASC;")

  if [ -n "$TODO_TASKS" ]; then
    echo "$TODO_TASKS" | while IFS='|' read -r task_id name plan phase notes; do
      task_id=$(echo "$task_id" | xargs)
      name=$(echo "$name" | xargs)
      plan=$(echo "$plan" | xargs)
      phase=$(echo "$phase" | xargs)

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

        execute "UPDATE autonomous_tasks
             SET status = 'IN_PROGRESS',
               assigned_agent = '$agent',
               pid = $$,
               started_at = CURRENT_TIMESTAMP,
               attempt_count = attempt_count + 1
             WHERE id = $task_id;"

        payload=$(cat <<EOF
Task ID: $task_id
Task Name: $name
Phase: $phase
Plan: $plan

Update task notes with:
- Files changed
- Tests run (command + result)

Return one of these markers in your final response:
- TASK_COMPLETE:$task_id
- TASK_BLOCKED:$task_id:<reason>
EOF
)

        response=$(openclaw_cmd agent --agent "$agent" --message "$payload" --timeout "$AGENT_TIMEOUT" 2>&1 || true)
        echo "$response"

        if echo "$response" | grep -q "TASK_COMPLETE:$task_id"; then
          execute "UPDATE autonomous_tasks
                   SET status = 'READY_FOR_TESTING',
                       pid = NULL
                   WHERE id = $task_id;"
          send_notification ready "$task_id" "$name" "ready for testing"
        elif echo "$response" | grep -q "TASK_BLOCKED:$task_id:"; then
          reason=$(echo "$response" | sed -n "s/.*TASK_BLOCKED:$task_id:\(.*\)$/\1/p" | head -n 1)
          reason=${reason//"'"/"''"}
          execute "UPDATE autonomous_tasks
                   SET status = 'BLOCKED',
                       pid = NULL,
                       blocked_reason = 'Task blocked: $reason',
                       error_log = '$reason'
                   WHERE id = $task_id;"
          send_notification blocker "$task_id" "$name" "$reason"
        else
          execute "UPDATE autonomous_tasks
                   SET status = 'BLOCKED',
                       pid = NULL,
                       blocked_reason = 'No completion marker found in agent response',
                       error_log = 'No completion marker found in agent response'
                   WHERE id = $task_id;"
          send_notification blocker "$task_id" "$name" "No completion marker found"
        fi

        echo
        echo "Finished: $(date)"
      ) &

      NEW_PID=$!
      execute "UPDATE autonomous_tasks SET pid = $NEW_PID WHERE id = $task_id;"
      log "Task $task_id started with PID $NEW_PID"

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
  READY_PHASES=$(query "SELECT COALESCE(phase,'__NO_PHASE__') AS phase_key
                        FROM autonomous_tasks
                        GROUP BY phase_key
                        HAVING SUM(CASE WHEN status IN ('TODO','IN_PROGRESS','BLOCKED') THEN 1 ELSE 0 END) = 0
                           AND SUM(CASE WHEN status = 'READY_FOR_TESTING' THEN 1 ELSE 0 END) > 0
                        ORDER BY phase_key;")

  if [ -n "$READY_PHASES" ]; then
    echo "$READY_PHASES" | while IFS='|' read -r phase_key; do
      phase_key=$(echo "$phase_key" | xargs)

      if [ "$phase_key" = "__NO_PHASE__" ]; then
        PHASE_TASKS=$(query "SELECT id, name, implementation_plan FROM autonomous_tasks
                            WHERE status = 'READY_FOR_TESTING' AND phase IS NULL
                            ORDER BY priority DESC, id ASC;")
        phase_label=""
        phase_filter="phase IS NULL"
      else
        phase_label="$phase_key"
        phase_key=${phase_key//"'"/"''"}
        PHASE_TASKS=$(query "SELECT id, name, implementation_plan FROM autonomous_tasks
                            WHERE status = 'READY_FOR_TESTING' AND phase = '$phase_key'
                            ORDER BY priority DESC, id ASC;")
        phase_filter="phase = '$phase_key'"
      fi

      if [ -z "$PHASE_TASKS" ]; then
        continue
      fi

      primary_id=$(echo "$PHASE_TASKS" | head -n 1 | cut -d'|' -f1 | xargs)
      primary_name=$(echo "$PHASE_TASKS" | head -n 1 | cut -d'|' -f2 | xargs)

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

        execute "UPDATE autonomous_tasks
                 SET status = 'IN_PROGRESS',
                     assigned_agent = 'tester',
                     pid = $$,
                     started_at = CURRENT_TIMESTAMP,
                     attempt_count = attempt_count + 1
                 WHERE id = $primary_id;"

        payload=$(cat <<EOF
Primary Task ID: $primary_id
Phase: ${phase_label:-<none>}
Tasks in phase:
$PHASE_TASKS

Run E2E + data validation for this phase.
If failures occur, create coder tasks with repro steps and logs.

Return one of these markers in your final response:
- TASK_COMPLETE:$primary_id
- TASK_BLOCKED:$primary_id:<reason>
EOF
)

        response=$(openclaw_cmd agent --agent "tester" --message "$payload" --timeout "$AGENT_TIMEOUT" 2>&1 || true)
        echo "$response"

        if echo "$response" | grep -q "TASK_COMPLETE:$primary_id"; then
            execute "UPDATE autonomous_tasks
                 SET status = 'COMPLETE',
                   pid = NULL,
                   completed_at = CURRENT_TIMESTAMP
                 WHERE $phase_filter AND status IN ('READY_FOR_TESTING','IN_PROGRESS');"
          send_notification complete "$primary_id" "$primary_name" "phase complete"
        elif echo "$response" | grep -q "TASK_BLOCKED:$primary_id:"; then
          reason=$(echo "$response" | sed -n "s/.*TASK_BLOCKED:$primary_id:\(.*\)$/\1/p" | head -n 1)
          reason=${reason//"'"/"''"}
            execute "UPDATE autonomous_tasks
                 SET status = 'BLOCKED',
                   pid = NULL,
                   blocked_reason = 'Testing blocked: $reason',
                   error_log = '$reason'
                 WHERE $phase_filter AND status IN ('READY_FOR_TESTING','IN_PROGRESS');"
          send_notification blocker "$primary_id" "$primary_name" "$reason"
        else
          execute "UPDATE autonomous_tasks
                   SET status = 'BLOCKED',
                       pid = NULL,
                       blocked_reason = 'No completion marker found in agent response',
                       error_log = 'No completion marker found in agent response'
                   WHERE id = $primary_id;"
          send_notification blocker "$primary_id" "$primary_name" "No completion marker found"
        fi

        echo
        echo "Finished: $(date)"
      ) &

      NEW_PID=$!
      execute "UPDATE autonomous_tasks SET pid = $NEW_PID WHERE id = $primary_id;"
      log "Tester started with PID $NEW_PID"

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
