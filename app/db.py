"""SQLite layer: one connection + one RLock. No ORM by design — the concurrency
bottleneck of an annotation system is task distribution, not object mapping."""
import json
import sqlite3
import threading

DDL = """
CREATE TABLE IF NOT EXISTS batches(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  show_suggestions INTEGER NOT NULL DEFAULT 0,
  overlap INTEGER NOT NULL DEFAULT 1,
  guideline_version TEXT NOT NULL DEFAULT 'v1.0',
  lang_profile TEXT NOT NULL DEFAULT 'en-es',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY,
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  source TEXT NOT NULL,
  hypothesis TEXT NOT NULL,
  reference TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  lf_flags TEXT NOT NULL DEFAULT '[]',
  is_golden INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS golden_answers(
  task_id TEXT PRIMARY KEY REFERENCES tasks(id),
  answer TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  annotator TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'assigned' CHECK(status IN ('assigned','submitted','skipped')),
  lease_expires_at TEXT NOT NULL,
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(task_id, annotator)
);
CREATE TABLE IF NOT EXISTS annotations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  annotator TEXT NOT NULL,
  error_types TEXT NOT NULL,
  worst_severity TEXT NOT NULL,
  adequacy INTEGER NOT NULL,
  fluency INTEGER NOT NULL,
  correction TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  elapsed_ms INTEGER NOT NULL DEFAULT 0,
  guideline_version TEXT NOT NULL DEFAULT 'v1.0',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS judge_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  verdict TEXT NOT NULL,
  confidence REAL NOT NULL,
  model TEXT NOT NULL,
  is_mock INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  reviewed_annotation_id INTEGER NOT NULL REFERENCES annotations(id),
  reviewer TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN ('approved','overturned')),
  replacement_annotation_id INTEGER REFERENCES annotations(id),
  case_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS exports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version TEXT NOT NULL,
  filters TEXT NOT NULL DEFAULT '{}',
  sha256 TEXT NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database:
    def __init__(self, path=":memory:"):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.lock, self.conn:
            self.conn.executescript(DDL)
            self._migrate()
            self._migrate_speech_review_context()

    def _migrate_speech_review_context(self):
        """Record hint exposure without rewriting any existing annotation row."""
        if self.conn.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
            return
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(speech_annotations)")}
        if "review_context" not in columns:
            self.conn.execute("ALTER TABLE speech_annotations ADD COLUMN review_context "
                              "TEXT NOT NULL DEFAULT '{}'")
        self.conn.execute("INSERT INTO schema_migrations(version,description) "
                          "VALUES(2,'speech QC exposure and playback context on new revisions')")

    def _migrate(self):
        """Additive migrations preserve existing translation databases."""
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations("
                          "version INTEGER PRIMARY KEY, description TEXT NOT NULL, "
                          "applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
        if self.conn.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone():
            return
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        if "task_type" not in columns:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL "
                              "DEFAULT 'translation' CHECK(task_type IN ('translation','speech'))")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS audio_assets(
              id TEXT PRIMARY KEY,
              path TEXT NOT NULL,
              sha256 TEXT NOT NULL,
              duration_s REAL NOT NULL,
              sample_rate INTEGER NOT NULL,
              channels INTEGER NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS speech_tasks(
              task_id TEXT PRIMARY KEY REFERENCES tasks(id),
              audio_id TEXT NOT NULL REFERENCES audio_assets(id),
              provenance TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS speech_annotations(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id TEXT NOT NULL REFERENCES tasks(id),
              assignment_id INTEGER NOT NULL,
              annotator TEXT NOT NULL,
              corrected_transcript TEXT NOT NULL,
              uncertain_spans TEXT NOT NULL DEFAULT '[]',
              note TEXT NOT NULL DEFAULT '',
              elapsed_ms INTEGER NOT NULL DEFAULT 0,
              active_ms INTEGER NOT NULL DEFAULT 0,
              guideline_version TEXT NOT NULL DEFAULT 'speech-v1',
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS speech_annotations_task ON speech_annotations(task_id,id);
        """)
        self.conn.execute("INSERT INTO schema_migrations(version,description) "
                          "VALUES(1,'speech tasks, audio registry and immutable revisions')")

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        with self.lock, self.conn:
            return self.conn.execute(sql, params).lastrowid

    def audit(self, actor, action, entity_type, entity_id, payload=None):
        self.execute(
            "INSERT INTO audit_log(actor,action,entity_type,entity_id,payload) VALUES(?,?,?,?,?)",
            (actor, action, entity_type, str(entity_id),
             json.dumps(payload or {}, ensure_ascii=False)),
        )
