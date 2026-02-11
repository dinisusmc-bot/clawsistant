#!/bin/bash
# Query autonomous tasks database
# Usage: query-tasks.sh [status]

set -euo pipefail

if [ -f "$HOME/.env" ]; then
  export $(grep -v '^#' "$HOME/.env" | xargs)
fi

POSTGRES_HOST=${OPENCLAW_POSTGRES_HOST:-localhost}
POSTGRES_PORT=${OPENCLAW_POSTGRES_PORT:-5433}
POSTGRES_DB=${OPENCLAW_POSTGRES_DB:-openclaw}
POSTGRES_USER=${OPENCLAW_POSTGRES_USER:-openclaw}
export PGPASSWORD=${OPENCLAW_POSTGRES_PASSWORD:-openclaw_dev_pass}

STATUS_FILTER="${1:-}"

if [ -z "$STATUS_FILTER" ]; then
  echo "=== Task Summary ==="
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --no-psqlrc -t -A -F'|' -c "
    SELECT status, COUNT(*) as count, COALESCE(assigned_agent, 'unassigned') as agent
    FROM autonomous_tasks 
    GROUP BY status, assigned_agent 
    ORDER BY status, agent;" | while IFS='|' read -r status count agent; do
    echo "$status: $count ($agent)"
  done
  
  echo ""
  echo "=== Recent Tasks (last 10) ==="
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --no-psqlrc -t -A -F'|' -c "
    SELECT id, LEFT(name, 60), status, COALESCE(assigned_agent, '-') 
    FROM autonomous_tasks 
    ORDER BY id DESC 
    LIMIT 10;" | while IFS='|' read -r id name status agent; do
    printf "%3d | %-60s | %-10s | %s\n" "$id" "$name" "$status" "$agent"
  done
else
  echo "=== Tasks with status: $STATUS_FILTER ==="
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --no-psqlrc -t -A -F'|' -c "
    SELECT id, name, COALESCE(phase, '-'), COALESCE(assigned_agent, '-'), priority
    FROM autonomous_tasks 
    WHERE status = '$STATUS_FILTER'
    ORDER BY priority DESC, id ASC;" | while IFS='|' read -r id name phase agent priority; do
    echo "[$id] $name"
    echo "    Phase: $phase | Agent: $agent | Priority: $priority"
    echo ""
  done
fi
