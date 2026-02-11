#!/bin/bash
# Add autonomous tasks to PostgreSQL from JSON input
# Usage: echo '{"project":"Name","tasks":[...]}' | add-tasks-to-db.sh

set -euo pipefail

if [ -f "$HOME/.env" ]; then
  export $(grep -v '^#' "$HOME/.env" | xargs)
fi

POSTGRES_HOST=${OPENCLAW_POSTGRES_HOST:-localhost}
POSTGRES_PORT=${OPENCLAW_POSTGRES_PORT:-5433}
POSTGRES_DB=${OPENCLAW_POSTGRES_DB:-openclaw}
POSTGRES_USER=${OPENCLAW_POSTGRES_USER:-openclaw}
export PGPASSWORD=${OPENCLAW_POSTGRES_PASSWORD:-openclaw_dev_pass}

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required (install jq)" >&2
  exit 1
fi

payload=$(cat)
project=$(echo "$payload" | jq -r '.project // ""')

if [ -z "$project" ]; then
  echo "Missing project name" >&2
  exit 1
fi

count=$(echo "$payload" | jq -r '.tasks | length')
if [ "$count" -eq 0 ]; then
  echo "No tasks provided" >&2
  exit 1
fi

idx=0
while [ "$idx" -lt "$count" ]; do
  name=$(echo "$payload" | jq -r ".tasks[$idx].name // \"\"")
  phase=$(echo "$payload" | jq -r ".tasks[$idx].phase // \"\"")
  priority=$(echo "$payload" | jq -r ".tasks[$idx].priority // 3")
  plan=$(echo "$payload" | jq -r ".tasks[$idx].plan // \"\"")
  notes=$(echo "$payload" | jq -r ".tasks[$idx].notes // \"\"")

  name=${name//"'"/"''"}
  phase=${phase//"'"/"''"}
  plan=${plan//"'"/"''"}
  notes=${notes//"'"/"''"}

  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "INSERT INTO autonomous_tasks (name, status, priority, phase, implementation_plan, notes)
      VALUES ('$name', 'TODO', $priority, NULLIF('$phase',''), NULLIF('$plan',''), NULLIF('$notes',''))" >/dev/null

  idx=$((idx + 1))
done

echo "Inserted $count tasks for project: $project"
