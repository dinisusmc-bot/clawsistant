#!/bin/bash
# Normalize task status strings to canonical values

set -euo pipefail

if [ -f "$HOME/.env" ]; then
  export $(grep -v '^#' "$HOME/.env" | xargs)
fi

POSTGRES_HOST=${OPENCLAW_POSTGRES_HOST:-localhost}
POSTGRES_PORT=${OPENCLAW_POSTGRES_PORT:-5433}
POSTGRES_DB=${OPENCLAW_POSTGRES_DB:-openclaw}
POSTGRES_USER=${OPENCLAW_POSTGRES_USER:-openclaw}
export PGPASSWORD=${OPENCLAW_POSTGRES_PASSWORD:-openclaw_dev_pass}

psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
UPDATE autonomous_tasks
SET status = CASE LOWER(status)
  WHEN 'todo' THEN 'TODO'
  WHEN 'in-progress' THEN 'IN_PROGRESS'
  WHEN 'in_progress' THEN 'IN_PROGRESS'
  WHEN 'ready_for_testing' THEN 'READY_FOR_TESTING'
  WHEN 'complete' THEN 'COMPLETE'
  WHEN 'blocked' THEN 'BLOCKED'
  ELSE status
END;"

echo "Task statuses normalized"
