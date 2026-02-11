-- Autonomous tasks schema (safe to re-run)

CREATE TABLE IF NOT EXISTS autonomous_tasks (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'TODO',
  priority INTEGER NOT NULL DEFAULT 3,
  phase TEXT,
  implementation_plan TEXT,
  notes TEXT,
  assigned_agent TEXT,
  pid INTEGER,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  blocked_reason TEXT,
  error_log TEXT,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS assigned_agent TEXT;
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS pid INTEGER;
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS attempt_count INTEGER DEFAULT 0;
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS blocked_reason TEXT;
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS error_log TEXT;
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_status ON autonomous_tasks(status);
CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_priority ON autonomous_tasks(priority DESC);
CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_agent ON autonomous_tasks(assigned_agent);
CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_pid ON autonomous_tasks(pid) WHERE pid IS NOT NULL;

CREATE OR REPLACE FUNCTION update_autonomous_tasks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS autonomous_tasks_updated_at_trigger ON autonomous_tasks;
CREATE TRIGGER autonomous_tasks_updated_at_trigger
  BEFORE UPDATE ON autonomous_tasks
  FOR EACH ROW
  EXECUTE FUNCTION update_autonomous_tasks_updated_at();
