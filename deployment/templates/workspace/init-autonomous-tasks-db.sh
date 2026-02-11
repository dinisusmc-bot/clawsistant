#!/bin/bash
set -euo pipefail

if [ -f "$HOME/.env" ]; then
  export $(grep -v '^#' "$HOME/.env" | xargs)
fi

POSTGRES_HOST=${OPENCLAW_POSTGRES_HOST:-localhost}
POSTGRES_PORT=${OPENCLAW_POSTGRES_PORT:-5433}
POSTGRES_DB=${OPENCLAW_POSTGRES_DB:-openclaw}
POSTGRES_USER=${OPENCLAW_POSTGRES_USER:-openclaw}
export PGPASSWORD=${OPENCLAW_POSTGRES_PASSWORD:-openclaw_dev_pass}

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCHEMA_FILE="$SCRIPT_DIR/autonomous-tasks-schema.sql"

if ! command -v psql >/dev/null 2>&1; then
  echo "psql not found; install postgresql-client to initialize autonomous_tasks" >&2
  exit 0
fi

if ! psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1" >/dev/null 2>&1; then
  echo "Postgres not reachable; skipping autonomous_tasks init" >&2
  exit 0
fi

psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$SCHEMA_FILE" >/dev/null

psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
ALTER TABLE autonomous_tasks DROP CONSTRAINT IF EXISTS autonomous_tasks_status_check;" >/dev/null

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
END;" >/dev/null

psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DO \$\$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'autonomous_tasks_status_check'
  ) THEN
    ALTER TABLE autonomous_tasks
      ADD CONSTRAINT autonomous_tasks_status_check
      CHECK (status IN ('TODO','IN_PROGRESS','READY_FOR_TESTING','COMPLETE','BLOCKED'));
  END IF;
END \$\$;" >/dev/null

echo "autonomous_tasks schema ensured"
