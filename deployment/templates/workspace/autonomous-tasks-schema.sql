-- Autonomous tasks schema (safe to re-run)

CREATE TABLE IF NOT EXISTS autonomous_tasks (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  project TEXT,
  status TEXT NOT NULL DEFAULT 'TODO',
  priority INTEGER NOT NULL DEFAULT 3,
  phase TEXT,
  implementation_plan TEXT,
  notes TEXT,
  solution TEXT,
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
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS project TEXT;
ALTER TABLE autonomous_tasks ADD COLUMN IF NOT EXISTS solution TEXT;

CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_status ON autonomous_tasks(status);
CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_priority ON autonomous_tasks(priority DESC);
CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_agent ON autonomous_tasks(assigned_agent);
CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_pid ON autonomous_tasks(pid) WHERE pid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_project ON autonomous_tasks(project);

CREATE TABLE IF NOT EXISTS autonomous_task_history (
  id SERIAL PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES autonomous_tasks(id) ON DELETE CASCADE,
  project TEXT,
  status TEXT NOT NULL,
  notes TEXT,
  error_log TEXT,
  changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_task_history_task_id ON autonomous_task_history(task_id);
CREATE INDEX IF NOT EXISTS idx_task_history_changed_at ON autonomous_task_history(changed_at DESC);

CREATE OR REPLACE FUNCTION log_task_status_change()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.status IS DISTINCT FROM NEW.status OR OLD.notes IS DISTINCT FROM NEW.notes OR OLD.error_log IS DISTINCT FROM NEW.error_log THEN
    INSERT INTO autonomous_task_history (task_id, project, status, notes, error_log)
    VALUES (NEW.id, NEW.project, NEW.status, NEW.notes, NEW.error_log);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS autonomous_tasks_history_trigger ON autonomous_tasks;
CREATE TRIGGER autonomous_tasks_history_trigger
  AFTER UPDATE ON autonomous_tasks
  FOR EACH ROW
  EXECUTE FUNCTION log_task_status_change();

CREATE TABLE IF NOT EXISTS blocked_reasons (
  id SERIAL PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES autonomous_tasks(id) ON DELETE CASCADE,
  reason TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_blocked_reasons_task ON blocked_reasons(task_id);

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

-- Pending questions: agents ask the owner for clarification
CREATE TABLE IF NOT EXISTS pending_questions (
  id SERIAL PRIMARY KEY,
  agent TEXT NOT NULL,
  task_id INTEGER REFERENCES autonomous_tasks(id) ON DELETE SET NULL,
  question TEXT NOT NULL,
  answer TEXT,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending, answered, expired
  telegram_message_id BIGINT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  answered_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pending_questions_status ON pending_questions(status);
CREATE INDEX IF NOT EXISTS idx_pending_questions_agent_created ON pending_questions(agent, created_at DESC);
