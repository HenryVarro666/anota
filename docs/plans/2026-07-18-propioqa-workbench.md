# PropioQA Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build PropioQA Workbench — a self-hosted, keyboard-first translation-QA annotation workbench (FastAPI + SQLite + vanilla JS) that embodies the best mechanism of each major annotation platform, per the approved spec.

**Architecture:** Single FastAPI app serving a REST API plus a static single-page frontend with three tabs (Annotate / Review / Ops Dashboard). SQLite via stdlib `sqlite3` (no ORM, deliberate). Batch policy (`show_suggestions`) is enforced server-side: golden-collection batches never emit judge/LF signals in API responses. Annotations and audit log are append-only.

**Tech Stack:** Python ≥3.10, FastAPI, uvicorn, httpx (judge client + tests), pytest. Frontend: hand-written HTML/CSS/JS, zero build chain, zero JS dependencies.

**Spec:** `/Users/caochao/Documents/Obsidian Vault/OnePointThreeAcres/Propio/future_direction/PropioQA_Workbench/00_设计文档_PropioQA_Workbench.md` (approved 2026-07-18).

**Spec deltas locked in this plan** (implementation refinements, all noted for the vault doc):
1. `batches.overlap` column added (Labelbox-consensus lesson; demo batch uses overlap=2 so the κ panel has data).
2. Ninth table `reviews` added — adjudication verdicts must be queryable; `audit_log` is not a query source.
3. LF results stored on `tasks.lf_flags` (JSON column), not a separate table.

## Global Constraints

- Repo root for all code: `~/Documents/Propio_Prep_Materials/propioqa/` (inside existing private git repo `Propio_Prep_Materials`).
- Python ≥ 3.10. Runtime deps EXACTLY: `fastapi`, `uvicorn`, `httpx`. Dev dep: `pytest`.
- Default port **8420**. Default DB file `propioqa.db`; `--demo` uses a temp DB, clean on every start.
- Error types (9, exact strings): `no_error, mistranslation, omission, addition, terminology, number_unit, negation_polarity, grammar, punctuation`. Severities (4): `neutral, minor, major, critical` (weights 0/1/5/25).
- Server-side invariants: `no_error` is exclusive and forces severity `neutral`; a non-`no_error` annotation must not have severity `neutral`; `critical` requires non-empty `note`; suggestions (judge + LF) appear in `/api/claim` responses **only** when the batch has `show_suggestions=1`; `is_golden` and golden answers never leave the server except via export with `include_golden=true`.
- UI copy in **English** (demo audience = Propio interviewers). All demo data is synthetic, zero PHI.
- Annotations & audit_log append-only: corrections/undo create new rows, never UPDATE/DELETE annotation rows.
- Commits: prefix `propioqa:`; end body with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` (private repo keeps trailer). Run all git commands as `git -C ~/Documents/Propio_Prep_Materials …`; never `cd`.
- All pytest runs from `~/Documents/Propio_Prep_Materials/propioqa/` with the venv at `.venv/` active: `.venv/bin/python -m pytest`.

## File Structure (final)

```
propioqa/
├── app/__init__.py
│   ├── db.py          # Database wrapper + DDL (9 tables) + audit helper
│   ├── models.py      # schema constants + pydantic payloads
│   ├── tasks.py       # claim/lease/submit/skip/undo state machine
│   ├── lf.py          # 4 labeling functions × 2 lang profiles
│   ├── quality.py     # Cohen's κ (plain+weighted), golden scoring, aggregation, matrix
│   ├── judge.py       # JudgeVerdict, MockJudge, OpenAIJudge, get_judge, health
│   ├── importer.py    # profiles demo/aqb/generic, golden load, LF pre-run, demo seed
│   ├── export.py      # canonical snapshot + sha256 + golden-style JSONL
│   └── main.py        # create_app(): all routes + static mount
├── static/index.html · static/style.css · static/app.js
├── data/demo_tasks.jsonl · data/demo_golden.jsonl · data/demo_seed_annotations.jsonl · data/guideline.md
├── tests/test_db.py · test_models.py · test_tasks.py · test_lf.py · test_quality.py
│         · test_judge.py · test_importer.py · test_export.py · test_api.py
├── docs/plans/2026-07-18-propioqa-workbench.md   (this file)
├── run.py · requirements.txt · README.md · .gitignore
```

---

### Task 1: Scaffold + db.py + models.py

**Files:**
- Create: `propioqa/.gitignore`, `propioqa/requirements.txt`, `propioqa/app/__init__.py`, `propioqa/app/db.py`, `propioqa/app/models.py`
- Test: `propioqa/tests/test_db.py`, `propioqa/tests/test_models.py`

**Interfaces:**
- Produces: `db.Database(path)` with `.query(sql, params) -> list[dict]`, `.one(sql, params) -> dict|None`, `.execute(sql, params) -> lastrowid`, `.audit(actor, action, entity_type, entity_id, payload=None)`, `.lock` (RLock); `models.ERROR_TYPES`, `models.SEVERITIES`, `models.SEVERITY_WEIGHTS`, `models.AnnotationPayload`, `models.SubmitRequest`.

- [ ] **Step 1: Create venv + scaffold**

```bash
mkdir -p ~/Documents/Propio_Prep_Materials/propioqa/{app,static,data,tests,exports}
cd ~/Documents/Propio_Prep_Materials/propioqa
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn httpx pytest
touch app/__init__.py tests/__init__.py
```

`requirements.txt`:
```
fastapi>=0.115
uvicorn>=0.30
httpx>=0.27
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
*.db
exports/
.pytest_cache/
```

- [ ] **Step 2: Write failing tests for db**

`tests/test_db.py`:
```python
from app.db import Database

def test_init_creates_all_tables():
    db = Database(":memory:")
    names = {r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert names == {"batches", "tasks", "golden_answers", "assignments", "annotations",
                     "judge_results", "reviews", "audit_log", "exports"}

def test_audit_appends_row():
    db = Database(":memory:")
    db.audit("chao", "import", "batch", 1, {"n": 30})
    rows = db.query("SELECT * FROM audit_log")
    assert len(rows) == 1 and rows[0]["actor"] == "chao" and '"n": 30' in rows[0]["payload"]

def test_execute_returns_lastrowid():
    db = Database(":memory:")
    rid = db.execute("INSERT INTO batches(name) VALUES(?)", ("b1",))
    assert rid == 1
    assert db.one("SELECT * FROM batches WHERE id=1")["overlap"] == 1
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: FAIL (`ModuleNotFoundError: app.db`)

- [ ] **Step 4: Implement `app/db.py`**

```python
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
```

- [ ] **Step 5: Run db tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_db.py -v` — Expected: 3 PASS

- [ ] **Step 6: Write failing tests for models**

`tests/test_models.py`:
```python
import pytest
from pydantic import ValidationError
from app.models import ERROR_TYPES, SEVERITIES, SEVERITY_WEIGHTS, AnnotationPayload

BASE = dict(error_types=["omission"], worst_severity="major", adequacy=3, fluency=4)

def test_constants():
    assert ERROR_TYPES[0] == "no_error" and len(ERROR_TYPES) == 9
    assert SEVERITIES == ["neutral", "minor", "major", "critical"]
    assert SEVERITY_WEIGHTS == {"neutral": 0, "minor": 1, "major": 5, "critical": 25}

def test_valid_payload():
    assert AnnotationPayload(**BASE).error_types == ["omission"]

def test_no_error_exclusive():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "error_types": ["no_error", "omission"], "worst_severity": "neutral"})

def test_no_error_forces_neutral():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "error_types": ["no_error"], "worst_severity": "minor"})
    ok = AnnotationPayload(**{**BASE, "error_types": ["no_error"], "worst_severity": "neutral"})
    assert ok.worst_severity == "neutral"

def test_real_error_cannot_be_neutral():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "worst_severity": "neutral"})

def test_critical_requires_note():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "worst_severity": "critical"})
    ok = AnnotationPayload(**{**BASE, "worst_severity": "critical", "note": "dosage flipped"})
    assert ok.note

def test_unknown_error_type_rejected():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "error_types": ["typo"]})
```

- [ ] **Step 7: Run, verify fail** — `.venv/bin/python -m pytest tests/test_models.py -v` → FAIL (import error)

- [ ] **Step 8: Implement `app/models.py`**

```python
"""Schema constants + request payloads. Mirrors AQB Argilla schema exactly."""
from pydantic import BaseModel, Field, model_validator

ERROR_TYPES = ["no_error", "mistranslation", "omission", "addition", "terminology",
               "number_unit", "negation_polarity", "grammar", "punctuation"]
SEVERITIES = ["neutral", "minor", "major", "critical"]
SEVERITY_WEIGHTS = {"neutral": 0, "minor": 1, "major": 5, "critical": 25}
LANG_PROFILES = ["en-es", "zh-en"]


class AnnotationPayload(BaseModel):
    error_types: list[str] = Field(min_length=1)
    worst_severity: str
    adequacy: int = Field(ge=1, le=5)
    fluency: int = Field(ge=1, le=5)
    correction: str = ""
    note: str = ""
    elapsed_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_rules(self):
        unknown = set(self.error_types) - set(ERROR_TYPES)
        if unknown:
            raise ValueError(f"unknown error_types: {unknown}")
        if len(set(self.error_types)) != len(self.error_types):
            raise ValueError("duplicate error_types")
        if self.worst_severity not in SEVERITIES:
            raise ValueError(f"unknown severity: {self.worst_severity}")
        if "no_error" in self.error_types:
            if len(self.error_types) > 1:
                raise ValueError("no_error is exclusive")
            if self.worst_severity != "neutral":
                raise ValueError("no_error forces severity=neutral")
        elif self.worst_severity == "neutral":
            raise ValueError("a real error cannot have severity=neutral")
        if self.worst_severity == "critical" and not self.note.strip():
            raise ValueError("critical requires a note (QA rubric rule)")
        return self


class SubmitRequest(AnnotationPayload):
    assignment_id: int
    annotator: str = Field(min_length=1)
```

- [ ] **Step 9: Run all tests** — `.venv/bin/python -m pytest -v` → all PASS

- [ ] **Step 10: Commit**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: scaffold + sqlite layer (9 tables) + schema payload validation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: tasks.py — distribution state machine

**Files:**
- Create: `propioqa/app/tasks.py` — Test: `propioqa/tests/test_tasks.py`

**Interfaces:**
- Consumes: `db.Database`, `models.AnnotationPayload`.
- Produces: `tasks.claim(db, annotator, batch_id=None) -> dict|None` (keys: `assignment_id, task_id, batch_id, source, hypothesis, reference, metadata(dict), lf_flags(list), batch(dict: id,name,show_suggestions,overlap,guideline_version,lang_profile)`); `tasks.submit(db, annotator, assignment_id, payload: AnnotationPayload) -> int` (annotation id; raises `LookupError` unknown/foreign assignment, `ValueError` if not 'assigned'); `tasks.skip(db, annotator, assignment_id, reason="") -> None`; `tasks.undo(db, annotator) -> dict|None` (same shape as claim); `tasks.progress(db, annotator, batch_id) -> dict(done,total)`; `tasks.LEASE_MINUTES = 30`.

- [ ] **Step 1: Write failing tests**

`tests/test_tasks.py`:
```python
import pytest
from app.db import Database
from app.models import AnnotationPayload
from app import tasks

PAYLOAD = AnnotationPayload(error_types=["omission"], worst_severity="major",
                            adequacy=2, fluency=3, note="", elapsed_ms=1200)

def make_db(overlap=1, n_tasks=3):
    db = Database(":memory:")
    bid = db.execute("INSERT INTO batches(name, overlap) VALUES('b', ?)", (overlap,))
    for i in range(n_tasks):
        db.execute("INSERT INTO tasks(id, batch_id, source, hypothesis) VALUES(?,?,?,?)",
                   (f"t{i:03d}", bid, f"src{i}", f"hyp{i}"))
    return db, bid

def test_claim_assigns_first_task_and_resumes_same_assignment():
    db, bid = make_db()
    c1 = tasks.claim(db, "chao")
    assert c1["task_id"] == "t000" and c1["batch"]["show_suggestions"] == 0
    assert tasks.claim(db, "chao")["assignment_id"] == c1["assignment_id"]  # resume, not double-claim

def test_two_annotators_overlap1_get_different_tasks():
    db, bid = make_db(overlap=1)
    a = tasks.claim(db, "chao"); b = tasks.claim(db, "maria")
    assert a["task_id"] != b["task_id"]

def test_two_annotators_overlap2_share_tasks():
    db, bid = make_db(overlap=2, n_tasks=1)
    assert tasks.claim(db, "chao")["task_id"] == "t000"
    assert tasks.claim(db, "maria")["task_id"] == "t000"
    assert tasks.claim(db, "third") is None  # overlap satisfied

def test_submit_marks_done_and_appends_annotation():
    db, bid = make_db()
    c = tasks.claim(db, "chao")
    ann_id = tasks.submit(db, "chao", c["assignment_id"], PAYLOAD)
    assert db.one("SELECT status FROM assignments WHERE id=?", (c["assignment_id"],))["status"] == "submitted"
    assert db.one("SELECT annotator FROM annotations WHERE id=?", (ann_id,))["annotator"] == "chao"
    with pytest.raises(ValueError):
        tasks.submit(db, "chao", c["assignment_id"], PAYLOAD)  # already submitted

def test_submit_foreign_assignment_rejected():
    db, bid = make_db()
    c = tasks.claim(db, "chao")
    with pytest.raises(LookupError):
        tasks.submit(db, "maria", c["assignment_id"], PAYLOAD)

def test_lease_expiry_reclaims():
    db, bid = make_db(n_tasks=1)
    c = tasks.claim(db, "chao")
    db.execute("UPDATE assignments SET lease_expires_at=datetime('now','-1 minute') WHERE id=?",
               (c["assignment_id"],))
    assert tasks.claim(db, "maria")["task_id"] == "t000"  # expired lease reaped

def test_skip_then_others_still_can_claim():
    db, bid = make_db(n_tasks=1)
    c = tasks.claim(db, "chao")
    tasks.skip(db, "chao", c["assignment_id"], "unreadable")
    assert tasks.claim(db, "chao") is None          # skipper never sees it again
    assert tasks.claim(db, "maria")["task_id"] == "t000"

def test_undo_reopens_last_submitted():
    db, bid = make_db()
    c = tasks.claim(db, "chao"); tasks.submit(db, "chao", c["assignment_id"], PAYLOAD)
    u = tasks.undo(db, "chao")
    assert u["task_id"] == c["task_id"]
    ann2 = tasks.submit(db, "chao", u["assignment_id"], PAYLOAD)  # resubmit appends new row
    assert db.query("SELECT id FROM annotations WHERE task_id=?", (c["task_id"],)).__len__() == 2

def test_progress():
    db, bid = make_db(n_tasks=3)
    c = tasks.claim(db, "chao"); tasks.submit(db, "chao", c["assignment_id"], PAYLOAD)
    assert tasks.progress(db, "chao", bid) == {"done": 1, "total": 3}
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_tasks.py -v` → FAIL (no module)

- [ ] **Step 3: Implement `app/tasks.py`**

```python
"""Task distribution: claim -> lease(30min) -> submit/skip; undo reopens.
Assignments are the mutable state machine; annotations stay append-only."""
import json

LEASE_MINUTES = 30


def _reap_expired(db):
    db.execute("DELETE FROM assignments WHERE status='assigned' "
               "AND lease_expires_at < datetime('now')")


def _task_payload(db, assignment_id, task_id):
    t = db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
    b = db.one("SELECT id,name,show_suggestions,overlap,guideline_version,lang_profile "
               "FROM batches WHERE id=?", (t["batch_id"],))
    return {"assignment_id": assignment_id, "task_id": t["id"], "batch_id": t["batch_id"],
            "source": t["source"], "hypothesis": t["hypothesis"], "reference": t["reference"],
            "metadata": json.loads(t["metadata"]), "lf_flags": json.loads(t["lf_flags"]),
            "batch": b}


def claim(db, annotator, batch_id=None):
    with db.lock:
        _reap_expired(db)
        open_row = db.one(
            "SELECT id, task_id FROM assignments WHERE annotator=? AND status='assigned' "
            "ORDER BY id LIMIT 1", (annotator,))
        if open_row:
            return _task_payload(db, open_row["id"], open_row["task_id"])
        clause, params = "", [annotator, annotator]
        if batch_id is not None:
            clause = "AND t.batch_id=?"
            params.append(batch_id)
        cand = db.one(f"""
            SELECT t.id FROM tasks t JOIN batches b ON b.id=t.batch_id
            WHERE NOT EXISTS (SELECT 1 FROM assignments a
                              WHERE a.task_id=t.id AND a.annotator=?)
              AND (SELECT COUNT(*) FROM assignments a2 WHERE a2.task_id=t.id
                   AND a2.annotator != ? AND a2.status IN ('assigned','submitted')) < b.overlap
              {clause}
            ORDER BY t.rowid LIMIT 1""", tuple(params))
        if cand is None:
            return None
        aid = db.execute(
            "INSERT INTO assignments(task_id, annotator, lease_expires_at) "
            "VALUES(?,?,datetime('now', ?))",
            (cand["id"], annotator, f"+{LEASE_MINUTES} minutes"))
        db.audit(annotator, "claim", "task", cand["id"])
        return _task_payload(db, aid, cand["id"])


def _owned_assignment(db, annotator, assignment_id):
    a = db.one("SELECT * FROM assignments WHERE id=?", (assignment_id,))
    if a is None or a["annotator"] != annotator:
        raise LookupError("assignment not found for this annotator")
    return a


def submit(db, annotator, assignment_id, payload):
    with db.lock:
        a = _owned_assignment(db, annotator, assignment_id)
        if a["status"] != "assigned":
            raise ValueError(f"assignment is '{a['status']}', not open")
        gv = db.one("SELECT b.guideline_version gv FROM tasks t JOIN batches b "
                    "ON b.id=t.batch_id WHERE t.id=?", (a["task_id"],))["gv"]
        ann_id = db.execute(
            "INSERT INTO annotations(task_id, annotator, error_types, worst_severity, adequacy,"
            " fluency, correction, note, elapsed_ms, guideline_version) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (a["task_id"], annotator, json.dumps(payload.error_types), payload.worst_severity,
             payload.adequacy, payload.fluency, payload.correction, payload.note,
             payload.elapsed_ms, gv))
        db.execute("UPDATE assignments SET status='submitted' WHERE id=?", (assignment_id,))
        db.audit(annotator, "submit", "annotation", ann_id, {"task_id": a["task_id"]})
        return ann_id


def skip(db, annotator, assignment_id, reason=""):
    with db.lock:
        a = _owned_assignment(db, annotator, assignment_id)
        if a["status"] != "assigned":
            raise ValueError(f"assignment is '{a['status']}', not open")
        db.execute("UPDATE assignments SET status='skipped' WHERE id=?", (assignment_id,))
        db.audit(annotator, "skip", "assignment", assignment_id, {"reason": reason})


def undo(db, annotator):
    with db.lock:
        a = db.one("SELECT id, task_id FROM assignments WHERE annotator=? AND status='submitted' "
                   "ORDER BY id DESC LIMIT 1", (annotator,))
        if a is None:
            return None
        db.execute("UPDATE assignments SET status='assigned', "
                   "lease_expires_at=datetime('now', ?) WHERE id=?",
                   (f"+{LEASE_MINUTES} minutes", a["id"]))
        db.audit(annotator, "undo", "assignment", a["id"], {"task_id": a["task_id"]})
        return _task_payload(db, a["id"], a["task_id"])


def progress(db, annotator, batch_id):
    total = db.one("SELECT COUNT(*) n FROM tasks WHERE batch_id=?", (batch_id,))["n"]
    done = db.one("SELECT COUNT(*) n FROM assignments a JOIN tasks t ON t.id=a.task_id "
                  "WHERE a.annotator=? AND a.status='submitted' AND t.batch_id=?",
                  (annotator, batch_id))["n"]
    return {"done": done, "total": total}
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/test_tasks.py -v` → all PASS
- [ ] **Step 5: Full suite** — `.venv/bin/python -m pytest -q` → all PASS
- [ ] **Step 6: Commit**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: task distribution state machine (claim/lease/submit/skip/undo)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---
### Task 3: lf.py — labeling functions

**Files:**
- Create: `propioqa/app/lf.py` — Test: `propioqa/tests/test_lf.py`

**Interfaces:**
- Produces: `lf.ERROR, lf.OK, lf.ABSTAIN` (strings `"ERROR"/"OK"/"ABSTAIN"`); `lf.run_lfs(source: str, hypothesis: str, lang_profile: str) -> list[dict]` — always 4 dicts, each `{"lf": name, "label": ERROR|OK|ABSTAIN, "evidence": str}`, names exactly `lf_negation_drop, lf_number_mismatch, lf_untranslated_fragment, lf_length_ratio`; `lf.LF_TO_ERROR` mapping lf name → error_type (`negation_polarity, number_unit, omission, omission`).

- [ ] **Step 1: Write failing tests**

`tests/test_lf.py`:
```python
from app.lf import run_lfs, ERROR, OK, ABSTAIN

def get(results, name):
    return next(r for r in results if r["lf"] == name)

def test_negation_drop_en_es():
    r = run_lfs("Do not stop taking this medication.", "Deje de tomar este medicamento.", "en-es")
    assert get(r, "lf_negation_drop")["label"] == ERROR
    r2 = run_lfs("Do not stop taking this medication.", "No deje de tomar este medicamento.", "en-es")
    assert get(r2, "lf_negation_drop")["label"] == OK
    r3 = run_lfs("Take with food.", "Tome con comida.", "en-es")
    assert get(r3, "lf_negation_drop")["label"] == ABSTAIN  # no negation in source

def test_negation_drop_zh_en():
    r = run_lfs("他没有过敏史。", "He has a history of allergies.", "zh-en")
    assert get(r, "lf_negation_drop")["label"] == ERROR
    r2 = run_lfs("他没有过敏史。", "He has no history of allergies.", "zh-en")
    assert get(r2, "lf_negation_drop")["label"] == OK

def test_number_mismatch():
    r = run_lfs("Take 5 mg twice a day.", "Tome 50 mg dos veces al día.", "en-es")
    assert get(r, "lf_number_mismatch")["label"] == ERROR
    assert "5" in get(r, "lf_number_mismatch")["evidence"]
    r2 = run_lfs("Take 5 mg twice a day.", "Tome 5 mg dos veces al día.", "en-es")
    assert get(r2, "lf_number_mismatch")["label"] == OK
    r3 = run_lfs("Rest well.", "Descanse bien.", "en-es")
    assert get(r3, "lf_number_mismatch")["label"] == ABSTAIN

def test_number_zh_normalization():
    r = run_lfs("每天两次，每次五毫克。", "Twice daily, 5 mg each time.", "zh-en")
    assert get(r, "lf_number_mismatch")["label"] == OK  # 五->5, 两->2 both found

def test_untranslated_fragment_en_es():
    r = run_lfs("Apply the ointment to the affected area every night.",
                "Aplique the ointment to the affected area cada noche.", "en-es")
    assert get(r, "lf_untranslated_fragment")["label"] == ERROR
    r2 = run_lfs("Apply the ointment nightly.", "Aplique la pomada cada noche.", "en-es")
    assert get(r2, "lf_untranslated_fragment")["label"] == OK

def test_untranslated_cjk_zh_en():
    r = run_lfs("请按时服药。", "Please take 药 on time.", "zh-en")
    assert get(r, "lf_untranslated_fragment")["label"] == ERROR

def test_length_ratio():
    r = run_lfs("Take one tablet every morning before breakfast with a full glass of water.",
                "Sí.", "en-es")
    assert get(r, "lf_length_ratio")["label"] == ERROR
    r2 = run_lfs("Short.", "Corto.", "en-es")
    assert get(r2, "lf_length_ratio")["label"] == ABSTAIN  # source under 10 chars

def test_always_four_results():
    r = run_lfs("Take with food.", "Tome con comida.", "en-es")
    assert [x["lf"] for x in r] == ["lf_negation_drop", "lf_number_mismatch",
                                    "lf_untranslated_fragment", "lf_length_ratio"]
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_lf.py -v` → FAIL

- [ ] **Step 3: Implement `app/lf.py`**

```python
"""Four built-in labeling functions (Snorkel lesson: prefer ABSTAIN over guessing).
Rules mirror AQB weak_supervision/lfs.py; profiles select cue lexicons per language pair."""
import re

ERROR, OK, ABSTAIN = "ERROR", "OK", "ABSTAIN"

LF_TO_ERROR = {"lf_negation_drop": "negation_polarity", "lf_number_mismatch": "number_unit",
               "lf_untranslated_fragment": "omission", "lf_length_ratio": "omission"}

PROFILES = {
    "en-es": {
        "src_neg": re.compile(r"\b(no|not|never|without|don't|do not|cannot|must not)\b", re.I),
        "hyp_neg": re.compile(r"\b(no|nunca|sin|jamás|tampoco|ni)\b", re.I),
        "len_bounds": (0.7, 1.6),
    },
    "zh-en": {
        "src_neg": re.compile(r"[不没無无别勿禁未]"),
        "hyp_neg": re.compile(r"\b(no|not|never|without|none|neither|nor)\b", re.I),
        "len_bounds": (1.0, 6.0),
    },
}

ZH_DIGITS = {"零": "0", "一": "1", "两": "2", "二": "2", "三": "3", "四": "4",
             "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
CJK_RE = re.compile(r"[一-鿿]")


def _numbers(text):
    for zh, d in ZH_DIGITS.items():
        text = text.replace(zh, d)
    return set(NUM_RE.findall(text))


def _lf_negation(src, hyp, prof):
    m = prof["src_neg"].search(src)
    if not m:
        return ABSTAIN, ""
    if prof["hyp_neg"].search(hyp):
        return OK, m.group(0)
    return ERROR, f"source negation '{m.group(0)}' has no counterpart"


def _lf_numbers(src, hyp):
    s, h = _numbers(src), _numbers(hyp)
    if not s:
        return ABSTAIN, ""
    missing = s - h
    if missing:
        return ERROR, f"missing numbers: {sorted(missing)}"
    return OK, f"all {len(s)} numbers preserved"


def _lf_untranslated(src, hyp, lang_profile):
    if lang_profile == "zh-en":
        residue = CJK_RE.findall(hyp)
        if residue:
            return ERROR, f"CJK residue: {''.join(residue[:5])}"
        return OK, ""
    src_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ']+", src)]
    hyp_lower = hyp.lower()
    for i in range(len(src_tokens) - 3):
        window = " ".join(src_tokens[i:i + 4])
        if window in hyp_lower:
            return ERROR, f"untranslated span: '{window}'"
    return OK, ""


def _lf_length(src, hyp, prof):
    if len(src) < 10:
        return ABSTAIN, ""
    ratio = len(hyp) / len(src)
    lo, hi = prof["len_bounds"]
    if ratio < lo or ratio > hi:
        return ERROR, f"length ratio {ratio:.2f} outside [{lo}, {hi}]"
    return OK, f"ratio {ratio:.2f}"


def run_lfs(source, hypothesis, lang_profile):
    prof = PROFILES[lang_profile]
    results = []
    for name, (label, evidence) in [
        ("lf_negation_drop", _lf_negation(source, hypothesis, prof)),
        ("lf_number_mismatch", _lf_numbers(source, hypothesis)),
        ("lf_untranslated_fragment", _lf_untranslated(source, hypothesis, lang_profile)),
        ("lf_length_ratio", _lf_length(source, hypothesis, prof)),
    ]:
        results.append({"lf": name, "label": label, "evidence": evidence})
    return results
```

Note on `test_negation_drop_en_es` case 2: Spanish hyp "No deje…" matches `hyp_neg` → OK. On `zh-en` ERROR case: source has 没, hyp lacks English negation → ERROR. On `en-es` length bounds: "Sí." vs long source → ratio ≪ 0.7 → ERROR.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/test_lf.py -v` → all PASS. If a lexicon test fails, fix the LEXICON (not the test) — cues above are the contract.
- [ ] **Step 5: Commit**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: 4 labeling functions with en-es/zh-en profiles

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: quality.py — κ, golden scoring, aggregation, matrix

**Files:**
- Create: `propioqa/app/quality.py` — Test: `propioqa/tests/test_quality.py`

**Interfaces:**
- Consumes: `db.Database`, `models.ERROR_TYPES/SEVERITIES`.
- Produces:
  - `quality.cohen_kappa(a: list, b: list, categories: list, weights: str|None=None) -> float` (weights `None` or `"linear"`)
  - `quality.golden_score(answer: dict, ann: dict) -> dict(passed: bool, severity_match: bool, types_match: bool)` — `answer`/`ann` both have `error_types` (list) and `worst_severity`
  - `quality.final_label(db, task_id) -> dict|None` — keys `error_types, worst_severity, adequacy, fluency, correction, note, source_kind ("review"|"single"|"aggregate"), unresolved: bool`
  - `quality.annotator_stats(db) -> list[dict(annotator, n_submitted, avg_elapsed_ms, golden_total, golden_passed)]`
  - `quality.pairwise_kappa(db, min_shared=3) -> list[dict(a, b, n, kappa_severity, kappa_binary)]`
  - `quality.judge_human_agreement(db) -> dict(n, kappa_binary, kappa_severity)|None`
  - `quality.error_arm_matrix(db) -> dict(arms: list, error_types: list, cells: {arm: {etype: rate}}, n: {arm: count}, sources: {human: int, judge: int})` — uses the human final label when one exists, else falls back to the latest judge verdict (keeps the demo dashboard alive before humans have labeled everything; `sources` discloses the mix)
  - helper `quality.latest_annotations(db, task_id) -> list[dict]` (latest row per human annotator, reviewer rows excluded)

- [ ] **Step 1: Write failing tests**

`tests/test_quality.py`:
```python
import json
import pytest
from app.db import Database
from app import quality

def test_kappa_textbook_2x2():
    # 50 pairs: 20 yes/yes, 15 no/no, 5 yes/no, 10 no/yes -> po=0.7, pe=0.5, kappa=0.4
    a = ["yes"] * 20 + ["no"] * 15 + ["yes"] * 5 + ["no"] * 10
    b = ["yes"] * 20 + ["no"] * 15 + ["no"] * 5 + ["yes"] * 10
    assert quality.cohen_kappa(a, b, ["yes", "no"]) == pytest.approx(0.4, abs=1e-9)

def test_kappa_perfect_and_weighted_extremes():
    sev = ["neutral", "minor", "major", "critical"]
    assert quality.cohen_kappa(sev, sev, sev, weights="linear") == pytest.approx(1.0)
    # systematic swap of adjacent categories -> weighted kappa exactly -1
    assert quality.cohen_kappa(["neutral", "minor"], ["minor", "neutral"], sev,
                               weights="linear") == pytest.approx(-1.0, abs=1e-9)

def test_golden_score():
    ans = {"error_types": ["negation_polarity"], "worst_severity": "critical"}
    good = {"error_types": ["negation_polarity"], "worst_severity": "critical"}
    bad = {"error_types": ["no_error"], "worst_severity": "neutral"}
    assert quality.golden_score(ans, good)["passed"] is True
    assert quality.golden_score(ans, bad)["passed"] is False

def seed(db, overlap=2):
    bid = db.execute("INSERT INTO batches(name, overlap) VALUES('b', ?)", (overlap,))
    db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis,metadata) VALUES(?,?,?,?,?)",
               ("t1", bid, "s", "h", json.dumps({"arm": "wait1"})))
    return bid

def add_ann(db, task, who, types, sev, adequacy=3):
    return db.execute(
        "INSERT INTO annotations(task_id,annotator,error_types,worst_severity,adequacy,fluency)"
        " VALUES(?,?,?,?,?,4)", (task, who, json.dumps(types), sev, adequacy))

def test_final_label_review_wins():
    db = Database(":memory:"); seed(db)
    a1 = add_ann(db, "t1", "chao", ["omission"], "major")
    rep = add_ann(db, "t1", "reviewer:lead", ["negation_polarity"], "critical")
    db.execute("INSERT INTO reviews(task_id,reviewed_annotation_id,reviewer,verdict,"
               "replacement_annotation_id,case_note) VALUES('t1',?,?,'overturned',?,'polarity')",
               (a1, "lead", rep))
    fl = quality.final_label(db, "t1")
    assert fl["worst_severity"] == "critical" and fl["source_kind"] == "review"

def test_final_label_majority_and_tie_severity():
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission", "terminology"], "major", adequacy=2)
    add_ann(db, "t1", "maria", ["omission"], "critical", adequacy=4)
    fl = quality.final_label(db, "t1")
    assert fl["error_types"] == ["omission"]          # strict majority only
    assert fl["worst_severity"] == "critical"         # mode tie -> stricter wins
    assert fl["adequacy"] == 3                        # median of 2,4
    assert fl["source_kind"] == "aggregate"

def test_latest_annotation_wins_per_annotator():
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission"], "major")
    add_ann(db, "t1", "chao", ["no_error"], "neutral")   # re-submit after undo
    fl = quality.final_label(db, "t1")
    assert fl["error_types"] == ["no_error"] and fl["source_kind"] == "single"

def test_error_arm_matrix():
    db = Database(":memory:"); bid = seed(db)
    db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis,metadata) VALUES(?,?,?,?,?)",
               ("t2", bid, "s", "h", json.dumps({"arm": "offline"})))
    add_ann(db, "t1", "chao", ["omission"], "major")
    add_ann(db, "t2", "chao", ["no_error"], "neutral")
    m = quality.error_arm_matrix(db)
    assert m["cells"]["wait1"]["omission"] == 1.0
    assert m["cells"]["offline"]["omission"] == 0.0
    assert m["n"] == {"wait1": 1, "offline": 1}

def test_annotator_stats_and_golden():
    db = Database(":memory:"); seed(db)
    db.execute("UPDATE tasks SET is_golden=1 WHERE id='t1'")
    db.execute("INSERT INTO golden_answers(task_id, answer) VALUES('t1', ?)",
               (json.dumps({"error_types": ["omission"], "worst_severity": "major"}),))
    add_ann(db, "t1", "chao", ["omission"], "major")
    s = quality.annotator_stats(db)[0]
    assert s["annotator"] == "chao" and s["golden_passed"] == 1 and s["golden_total"] == 1

def test_pairwise_kappa_needs_min_shared():
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission"], "major")
    add_ann(db, "t1", "maria", ["omission"], "major")
    assert quality.pairwise_kappa(db, min_shared=3) == []
    assert quality.pairwise_kappa(db, min_shared=1)[0]["n"] == 1
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_quality.py -v` → FAIL

- [ ] **Step 3: Implement `app/quality.py`**

```python
"""Quality math: hand-written Cohen's kappa (tested against textbook values),
golden scoring, final-label aggregation, dashboard aggregates. No sklearn on purpose."""
import json
from statistics import median

from .models import ERROR_TYPES, SEVERITIES


def cohen_kappa(a, b, categories, weights=None):
    assert len(a) == len(b) and a, "need equal non-empty lists"
    idx = {c: i for i, c in enumerate(categories)}
    k, n = len(categories), len(a)
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[idx[x]][idx[y]] += 1.0 / n
    pa = [sum(row) for row in obs]
    pb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    if weights == "linear":
        w = [[abs(i - j) / (k - 1) for j in range(k)] for i in range(k)]
    else:
        w = [[0.0 if i == j else 1.0 for j in range(k)] for i in range(k)]
    po_w = sum(w[i][j] * obs[i][j] for i in range(k) for j in range(k))
    pe_w = sum(w[i][j] * pa[i] * pb[j] for i in range(k) for j in range(k))
    if pe_w == 0:
        return 1.0
    return 1.0 - po_w / pe_w


def golden_score(answer, ann):
    types_match = set(answer["error_types"]) == set(ann["error_types"])
    severity_match = answer["worst_severity"] == ann["worst_severity"]
    return {"passed": types_match and severity_match,
            "types_match": types_match, "severity_match": severity_match}


def _parse(ann_row):
    d = dict(ann_row)
    d["error_types"] = json.loads(d["error_types"])
    return d


def latest_annotations(db, task_id):
    rows = db.query(
        "SELECT * FROM annotations WHERE task_id=? AND annotator NOT LIKE 'reviewer:%' "
        "ORDER BY id", (task_id,))
    latest = {}
    for r in rows:
        latest[r["annotator"]] = r
    return [_parse(r) for r in latest.values()]


def final_label(db, task_id):
    rev = db.one("SELECT * FROM reviews WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,))
    if rev and rev["verdict"] == "overturned" and rev["replacement_annotation_id"]:
        rep = _parse(db.one("SELECT * FROM annotations WHERE id=?",
                            (rev["replacement_annotation_id"],)))
        return {**_core(rep), "source_kind": "review", "unresolved": False}
    anns = latest_annotations(db, task_id)
    if not anns:
        return None
    if len(anns) == 1:
        return {**_core(anns[0]), "source_kind": "single", "unresolved": False}
    n = len(anns)
    counts = {}
    for a in anns:
        for e in a["error_types"]:
            counts[e] = counts.get(e, 0) + 1
    maj = [e for e in ERROR_TYPES if counts.get(e, 0) * 2 > n]
    unresolved = not maj
    sev_counts = {}
    for a in anns:
        sev_counts[a["worst_severity"]] = sev_counts.get(a["worst_severity"], 0) + 1
    top = max(sev_counts.values())
    tied = [s for s in SEVERITIES if sev_counts.get(s, 0) == top]
    severity = tied[-1]  # tie -> stricter (SEVERITIES is ordered mild->severe)
    newest = max(anns, key=lambda a: a["id"])
    return {"error_types": maj or ["no_error"], "worst_severity": severity,
            "adequacy": int(median(a["adequacy"] for a in anns)),
            "fluency": int(median(a["fluency"] for a in anns)),
            "correction": newest["correction"], "note": newest["note"],
            "source_kind": "aggregate", "unresolved": unresolved}


def _core(a):
    return {k: a[k] for k in
            ("error_types", "worst_severity", "adequacy", "fluency", "correction", "note")}


def annotator_stats(db):
    out = []
    for r in db.query("SELECT DISTINCT annotator FROM annotations "
                      "WHERE annotator NOT LIKE 'reviewer:%'"):
        who = r["annotator"]
        rows = db.query("SELECT * FROM annotations WHERE annotator=? ORDER BY id", (who,))
        latest = {}
        for a in rows:
            latest[a["task_id"]] = a
        golden_total = golden_passed = 0
        for task_id, a in latest.items():
            g = db.one("SELECT answer FROM golden_answers WHERE task_id=?", (task_id,))
            if g:
                golden_total += 1
                if golden_score(json.loads(g["answer"]), _parse(a))["passed"]:
                    golden_passed += 1
        n = len(latest)
        avg_ms = sum(a["elapsed_ms"] for a in latest.values()) / n if n else 0
        out.append({"annotator": who, "n_submitted": n, "avg_elapsed_ms": round(avg_ms),
                    "golden_total": golden_total, "golden_passed": golden_passed})
    return sorted(out, key=lambda s: -s["n_submitted"])


def _shared_labels(db, a, b):
    rows_a = {r["task_id"]: _parse(r) for r in db.query(
        "SELECT * FROM annotations WHERE annotator=? ORDER BY id", (a,))}
    rows_b = {r["task_id"]: _parse(r) for r in db.query(
        "SELECT * FROM annotations WHERE annotator=? ORDER BY id", (b,))}
    shared = sorted(set(rows_a) & set(rows_b))
    return [rows_a[t] for t in shared], [rows_b[t] for t in shared]


def _binary(ann):
    return "error" if ann["error_types"] != ["no_error"] else "clean"


def pairwise_kappa(db, min_shared=3):
    annotators = sorted(r["annotator"] for r in db.query(
        "SELECT DISTINCT annotator FROM annotations WHERE annotator NOT LIKE 'reviewer:%'"))
    out = []
    for i in range(len(annotators)):
        for j in range(i + 1, len(annotators)):
            xa, xb = _shared_labels(db, annotators[i], annotators[j])
            if len(xa) < min_shared:
                continue
            out.append({
                "a": annotators[i], "b": annotators[j], "n": len(xa),
                "kappa_severity": round(cohen_kappa(
                    [x["worst_severity"] for x in xa], [x["worst_severity"] for x in xb],
                    SEVERITIES, weights="linear"), 3),
                "kappa_binary": round(cohen_kappa(
                    [_binary(x) for x in xa], [_binary(x) for x in xb],
                    ["error", "clean"]), 3)})
    return out


def judge_human_agreement(db):
    pairs = []
    for t in db.query("SELECT id FROM tasks"):
        fl = final_label(db, t["id"])
        jr = db.one("SELECT verdict FROM judge_results WHERE task_id=? ORDER BY id DESC LIMIT 1",
                    (t["id"],))
        if fl and jr:
            pairs.append((fl, json.loads(jr["verdict"])))
    if not pairs:
        return None
    return {"n": len(pairs),
            "kappa_binary": round(cohen_kappa(
                [_binary(h) for h, _ in pairs], [_binary(j) for _, j in pairs],
                ["error", "clean"]), 3),
            "kappa_severity": round(cohen_kappa(
                [h["worst_severity"] for h, _ in pairs],
                [j["worst_severity"] for _, j in pairs], SEVERITIES, weights="linear"), 3)}


def error_arm_matrix(db):
    per_arm, sources = {}, {"human": 0, "judge": 0}
    for t in db.query("SELECT id, metadata FROM tasks"):
        fl = final_label(db, t["id"])
        if fl is not None:
            sources["human"] += 1
        else:
            jr = db.one("SELECT verdict FROM judge_results WHERE task_id=? "
                        "ORDER BY id DESC LIMIT 1", (t["id"],))
            if jr is None:
                continue
            fl = json.loads(jr["verdict"])
            sources["judge"] += 1
        arm = json.loads(t["metadata"]).get("arm", "unknown")
        per_arm.setdefault(arm, []).append(fl)
    etypes = [e for e in ERROR_TYPES if e != "no_error"]
    cells = {arm: {e: round(sum(1 for fl in fls if e in fl["error_types"]) / len(fls), 3)
                   for e in etypes}
             for arm, fls in per_arm.items()}
    return {"arms": sorted(per_arm), "error_types": etypes, "cells": cells,
            "n": {arm: len(fls) for arm, fls in per_arm.items()}, "sources": sources}
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/test_quality.py -v` → all PASS
- [ ] **Step 5: Full suite + commit**

```bash
.venv/bin/python -m pytest -q
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: quality math (hand-written kappa, golden scoring, aggregation, error-arm matrix)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---
### Task 5: judge.py — MockJudge + OpenAIJudge

**Files:**
- Create: `propioqa/app/judge.py` — Test: `propioqa/tests/test_judge.py`

**Interfaces:**
- Consumes: `lf.LF_TO_ERROR`.
- Produces: `judge.JudgeUnavailable(Exception)`; `judge.MockJudge` / `judge.OpenAIJudge`, both with attrs `.model: str`, `.is_mock: bool` and `.evaluate(task: dict, lf_results: list[dict]) -> dict` returning keys `error_types, worst_severity, adequacy, rationale, confidence`; `judge.parse_judge_json(content: str) -> dict` (validated/clamped); `judge.get_judge(guideline_text="") -> MockJudge|OpenAIJudge` (env `PROPIOQA_JUDGE=mock|openai`, `PROPIOQA_JUDGE_BASE_URL` default `http://localhost:8000/v1`, `PROPIOQA_JUDGE_MODEL`, `PROPIOQA_JUDGE_API_KEY`); `judge.judge_health(j) -> dict(mode, reachable)`.

- [ ] **Step 1: Write failing tests**

`tests/test_judge.py`:
```python
import pytest
from app.judge import MockJudge, OpenAIJudge, JudgeUnavailable, parse_judge_json, get_judge

LF_CLEAN = [{"lf": n, "label": "OK", "evidence": ""} for n in
            ["lf_negation_drop", "lf_number_mismatch", "lf_untranslated_fragment", "lf_length_ratio"]]
LF_NEG = [{"lf": "lf_negation_drop", "label": "ERROR", "evidence": "no counterpart"}] + LF_CLEAN[1:]

def test_mock_is_deterministic():
    t = {"id": "t001", "source": "s", "hypothesis": "h"}
    assert MockJudge().evaluate(t, LF_NEG) == MockJudge().evaluate(t, LF_NEG)

def test_mock_negation_maps_to_critical():
    v = MockJudge().evaluate({"id": "t002"}, LF_NEG)
    assert v["error_types"] == ["negation_polarity"] and v["worst_severity"] == "critical"
    assert v["adequacy"] < 5 and 0 < v["confidence"] < 1

def test_mock_clean_is_no_error():
    v = MockJudge().evaluate({"id": "t003"}, LF_CLEAN)
    assert v["error_types"] == ["no_error"] and v["worst_severity"] == "neutral"
    assert v["confidence"] > 0.8

def test_parse_judge_json_extracts_and_clamps():
    content = 'Reasoning first. {"rationale":"x","error_types":["omission","bogus"],' \
              '"worst_severity":"major","adequacy":9,"confidence":1.4}'
    v = parse_judge_json(content)
    assert v["error_types"] == ["omission"]      # unknown types dropped
    assert v["adequacy"] == 5 and v["confidence"] == 1.0

def test_parse_judge_json_garbage_raises():
    with pytest.raises(JudgeUnavailable):
        parse_judge_json("no json here")

def test_openai_judge_unreachable_raises():
    j = OpenAIJudge("http://127.0.0.1:1", model="m", timeout=0.2)
    with pytest.raises(JudgeUnavailable):
        j.evaluate({"id": "t1", "source": "s", "hypothesis": "h"}, [])

def test_get_judge_default_mock(monkeypatch):
    monkeypatch.delenv("PROPIOQA_JUDGE", raising=False)
    assert get_judge().is_mock is True
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_judge.py -v` → FAIL

- [ ] **Step 3: Implement `app/judge.py`**

```python
"""Judge abstraction. Judge is a first-pass filter, never the final arbiter:
if it is down, annotation continues untouched. MockJudge keeps demos honest & offline."""
import hashlib
import json
import os
import re

import httpx

from .lf import LF_TO_ERROR
from .models import ERROR_TYPES, SEVERITIES


class JudgeUnavailable(Exception):
    pass


class MockJudge:
    model = "mock-judge"
    is_mock = True

    def evaluate(self, task, lf_results):
        h = int(hashlib.sha256(str(task["id"]).encode()).hexdigest(), 16)
        error_lfs = [r["lf"] for r in lf_results if r["label"] == "ERROR"]
        abstains = sum(1 for r in lf_results if r["label"] == "ABSTAIN")
        errs = sorted({LF_TO_ERROR[n] for n in error_lfs})
        if errs:
            severity = "critical" if {"negation_polarity", "number_unit"} & set(errs) else "major"
        else:
            errs, severity = ["no_error"], "neutral"
        n_err = 0 if errs == ["no_error"] else len(errs)
        adequacy = max(1, int(5 - 1.5 * n_err + 0.5))
        if not error_lfs:
            base = 0.90
        elif error_lfs == ["lf_length_ratio"]:
            base = 0.55          # weak single signal -> low confidence -> routing candidate
        else:
            base = 0.85
        conf = base - 0.08 * abstains + ((h % 7) - 3) / 100.0
        return {"error_types": errs, "worst_severity": severity, "adequacy": adequacy,
                "rationale": "[MOCK] derived from lint signals: "
                             + (", ".join(error_lfs) or "none"),
                "confidence": round(min(0.99, max(0.05, conf)), 3)}


def parse_judge_json(content):
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise JudgeUnavailable("judge returned no JSON")
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise JudgeUnavailable(f"judge JSON invalid: {e}") from e
    errs = [e for e in raw.get("error_types", []) if e in ERROR_TYPES] or ["no_error"]
    sev = raw.get("worst_severity")
    if sev not in SEVERITIES:
        sev = "neutral" if errs == ["no_error"] else "major"
    return {"error_types": errs, "worst_severity": sev,
            "adequacy": min(5, max(1, int(raw.get("adequacy", 3)))),
            "rationale": str(raw.get("rationale", ""))[:2000],
            "confidence": min(1.0, max(0.0, float(raw.get("confidence", 0.7))))}


SYSTEM_TEMPLATE = """You are a strict medical-translation QA judge. Apply this rubric verbatim:

{guideline}

Judge the HYPOTHESIS against the SOURCE. Reason first, then output ONLY one JSON object:
{{"rationale": "<short>", "error_types": [<subset of {etypes}>],
"worst_severity": "<one of {sevs}>", "adequacy": <1-5>, "confidence": <0.0-1.0>}}"""


class OpenAIJudge:
    is_mock = False

    def __init__(self, base_url, model=None, api_key="EMPTY", guideline_text="", timeout=30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.guideline_text = guideline_text
        self.timeout = timeout

    def _resolve_model(self):
        if self.model:
            return self.model
        r = httpx.get(f"{self.base_url}/models",
                      headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout)
        self.model = r.json()["data"][0]["id"]
        return self.model

    def evaluate(self, task, lf_results):
        system = SYSTEM_TEMPLATE.format(guideline=self.guideline_text,
                                        etypes=ERROR_TYPES, sevs=SEVERITIES)
        user = f"SOURCE:\n{task['source']}\n\nHYPOTHESIS:\n{task['hypothesis']}"
        try:
            model = self._resolve_model()
            r = httpx.post(f"{self.base_url}/chat/completions",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"model": model, "temperature": 0.0,
                                 "messages": [{"role": "system", "content": system},
                                              {"role": "user", "content": user}]},
                           timeout=self.timeout)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError) as e:
            raise JudgeUnavailable(str(e)) from e
        return parse_judge_json(content)


def get_judge(guideline_text=""):
    if os.environ.get("PROPIOQA_JUDGE", "mock") == "openai":
        return OpenAIJudge(os.environ.get("PROPIOQA_JUDGE_BASE_URL", "http://localhost:8000/v1"),
                           os.environ.get("PROPIOQA_JUDGE_MODEL") or None,
                           os.environ.get("PROPIOQA_JUDGE_API_KEY", "EMPTY"),
                           guideline_text)
    return MockJudge()


def judge_health(j):
    if j.is_mock:
        return {"mode": "mock", "reachable": True}
    try:
        httpx.get(f"{j.base_url}/models", timeout=2.0)
        return {"mode": "openai", "reachable": True}
    except httpx.HTTPError:
        return {"mode": "openai", "reachable": False}
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/test_judge.py -v` → all PASS
- [ ] **Step 5: Commit**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: judge abstraction (deterministic MockJudge + OpenAI-compatible client)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: importer.py + demo data + guideline

**Files:**
- Create: `propioqa/app/importer.py`, `propioqa/data/demo_tasks.jsonl`, `propioqa/data/demo_golden.jsonl`, `propioqa/data/demo_seed_annotations.jsonl`, `propioqa/data/guideline.md` — Test: `propioqa/tests/test_importer.py`

**Interfaces:**
- Consumes: `lf.run_lfs`, `judge.MockJudge`, `db.Database`.
- Produces: `importer.import_jsonl(db, path, profile, batch_name, lang_profile="en-es", show_suggestions=False, overlap=1, actor="system") -> dict(batch_id, n)`; `importer.load_golden(db, path, actor="system") -> int`; `importer.import_demo(db, data_dir) -> dict(batch_id, n)`; `importer.PROFILES` (keys `generic`, `aqb`).

- [ ] **Step 1: Author `data/demo_tasks.jsonl`** (30 synthetic EN→ES medical pairs, zero PHI; arms: t001–t010 `wait1` (5 planted errors), t011–t020 `wait3` (4), t021–t030 `offline` (3) — quality gradient makes the dashboard matrix tell a story). Planted: negation t002/t009/t024, number t007/t015/t022, terminology t010/t019/t027, omission t005/t012/t017.

```jsonl
{"id":"t001","source":"Take one tablet every morning with breakfast.","translation":"Tome una tableta cada mañana con el desayuno.","metadata":{"arm":"wait1","al_ms":920}}
{"id":"t002","source":"Do not stop taking this medication without talking to your doctor.","translation":"Deje de tomar este medicamento sin hablar con su médico.","metadata":{"arm":"wait1","al_ms":940}}
{"id":"t003","source":"Drink plenty of water throughout the day.","translation":"Beba mucha agua durante el día.","metadata":{"arm":"wait1","al_ms":880}}
{"id":"t004","source":"Call our office if you have a fever above 38 degrees.","translation":"Llame a nuestra oficina si tiene fiebre de más de 38 grados.","metadata":{"arm":"wait1","al_ms":905}}
{"id":"t005","source":"Apply the cream to the affected area twice a day for two weeks.","translation":"Aplique la crema dos veces al día.","metadata":{"arm":"wait1","al_ms":950}}
{"id":"t006","source":"Your appointment is scheduled for next Tuesday at 10 a.m.","translation":"Su cita está programada para el próximo martes a las 10 de la mañana.","metadata":{"arm":"wait1","al_ms":915}}
{"id":"t007","source":"Take 5 mg twice a day for 7 days.","translation":"Tome 50 mg dos veces al día durante 7 días.","metadata":{"arm":"wait1","al_ms":890}}
{"id":"t008","source":"This medication may cause drowsiness.","translation":"Este medicamento puede causar somnolencia.","metadata":{"arm":"wait1","al_ms":900}}
{"id":"t009","source":"You must not drive after taking this medicine.","translation":"Puede conducir después de tomar este medicamento.","metadata":{"arm":"wait1","al_ms":930}}
{"id":"t010","source":"The patient has a history of hypertension.","translation":"El paciente tiene antecedentes de hipotensión.","metadata":{"arm":"wait1","al_ms":925}}
{"id":"t011","source":"Keep this medication out of reach of children.","translation":"Mantenga este medicamento fuera del alcance de los niños.","metadata":{"arm":"wait3","al_ms":1810}}
{"id":"t012","source":"If you miss a dose, take it as soon as you remember, unless it is almost time for your next dose.","translation":"Si olvida una dosis, tómela tan pronto como lo recuerde.","metadata":{"arm":"wait3","al_ms":1850}}
{"id":"t013","source":"Avoid alcohol while taking this medication.","translation":"Evite el alcohol mientras toma este medicamento.","metadata":{"arm":"wait3","al_ms":1790}}
{"id":"t014","source":"Your blood pressure today is 130 over 85.","translation":"Su presión arterial hoy es 130 sobre 85.","metadata":{"arm":"wait3","al_ms":1820}}
{"id":"t015","source":"Give the child 2.5 ml of the syrup every 8 hours.","translation":"Dele al niño 25 ml del jarabe cada 8 horas.","metadata":{"arm":"wait3","al_ms":1840}}
{"id":"t016","source":"The wound should be kept clean and dry.","translation":"La herida debe mantenerse limpia y seca.","metadata":{"arm":"wait3","al_ms":1800}}
{"id":"t017","source":"Fasting is required: no food or drink after midnight before your surgery.","translation":"Se requiere ayuno antes de su cirugía.","metadata":{"arm":"wait3","al_ms":1860}}
{"id":"t018","source":"Take the antibiotic until it is completely finished.","translation":"Tome el antibiótico hasta terminarlo por completo.","metadata":{"arm":"wait3","al_ms":1795}}
{"id":"t019","source":"This inhaler is for asthma attacks.","translation":"Este inhalador es para ataques de ansiedad.","metadata":{"arm":"wait3","al_ms":1830}}
{"id":"t020","source":"Wear the compression stockings during the day.","translation":"Use las medias de compresión durante el día.","metadata":{"arm":"wait3","al_ms":1815}}
{"id":"t021","source":"Check your blood sugar before each meal.","translation":"Controle su nivel de azúcar en la sangre antes de cada comida.","metadata":{"arm":"offline","al_ms":3620}}
{"id":"t022","source":"The maximum dose is 4 tablets in 24 hours.","translation":"La dosis máxima es de 4 tabletas en 2 horas.","metadata":{"arm":"offline","al_ms":3650}}
{"id":"t023","source":"Bring a list of all your current medications to your visit.","translation":"Traiga una lista de todos sus medicamentos actuales a su visita.","metadata":{"arm":"offline","al_ms":3600}}
{"id":"t024","source":"Do not take this medication on an empty stomach.","translation":"Tome este medicamento con el estómago vacío.","metadata":{"arm":"offline","al_ms":3640}}
{"id":"t025","source":"Physical therapy sessions will begin next week.","translation":"Las sesiones de fisioterapia comenzarán la próxima semana.","metadata":{"arm":"offline","al_ms":3610}}
{"id":"t026","source":"Use sunscreen while taking this antibiotic.","translation":"Use protector solar mientras toma este antibiótico.","metadata":{"arm":"offline","al_ms":3590}}
{"id":"t027","source":"You will need a referral to see the cardiologist.","translation":"Necesitará una referencia para ver al dermatólogo.","metadata":{"arm":"offline","al_ms":3630}}
{"id":"t028","source":"Elevate your leg and apply ice for 20 minutes.","translation":"Eleve la pierna y aplique hielo durante 20 minutos.","metadata":{"arm":"offline","al_ms":3605}}
{"id":"t029","source":"The test results will be ready in 3 business days.","translation":"Los resultados estarán listos en 3 días hábiles.","metadata":{"arm":"offline","al_ms":3615}}
{"id":"t030","source":"Schedule a follow-up appointment in two months.","translation":"Programe una cita de seguimiento en dos meses.","metadata":{"arm":"offline","al_ms":3625}}
```

Deliberate nuance to demo: t002/t010/t019/t027 planted errors that LFs **miss** (Spanish `sin` satisfies the negation lexicon; terminology swaps are invisible to lint) — human/golden catches them, which is exactly the "judge/LF is first-pass, humans arbitrate" storyline.

- [ ] **Step 2: Author `data/demo_golden.jsonl`** (server-side only; 4 planted + 2 clean)

```jsonl
{"task_id":"t002","answer":{"error_types":["negation_polarity"],"worst_severity":"critical","adequacy":2}}
{"task_id":"t003","answer":{"error_types":["no_error"],"worst_severity":"neutral","adequacy":5}}
{"task_id":"t007","answer":{"error_types":["number_unit"],"worst_severity":"critical","adequacy":2}}
{"task_id":"t012","answer":{"error_types":["omission"],"worst_severity":"major","adequacy":3}}
{"task_id":"t021","answer":{"error_types":["no_error"],"worst_severity":"neutral","adequacy":5}}
{"task_id":"t027","answer":{"error_types":["terminology"],"worst_severity":"critical","adequacy":2}}
```

- [ ] **Step 3: Author `data/demo_seed_annotations.jsonl`** (second annotator "maria", makes κ panel non-empty)

```jsonl
{"task_id":"t001","annotator":"maria","error_types":["no_error"],"worst_severity":"neutral","adequacy":5,"fluency":5,"note":"","elapsed_ms":41000}
{"task_id":"t002","annotator":"maria","error_types":["negation_polarity"],"worst_severity":"critical","adequacy":2,"fluency":4,"note":"'do not' dropped — meaning inverted","elapsed_ms":67000}
{"task_id":"t003","annotator":"maria","error_types":["no_error"],"worst_severity":"neutral","adequacy":5,"fluency":5,"note":"","elapsed_ms":38000}
```

- [ ] **Step 4: Author `data/guideline.md`** (English; rendered in the help modal AND injected into the judge system prompt — single source of truth)

```markdown
# MQM-lite Translation QA Guideline — v1.0

## Decision tree (apply in order)
1. **Meaning first**: is anything missing (omission) or added (addition)?
2. **Critical triggers**: dosage/number changed (number_unit)? negation flipped (negation_polarity)? medical term swapped (terminology)?
3. **Everything else**: wrong meaning (mistranslation), grammar, punctuation.
4. If nothing is wrong: `no_error` (severity neutral, adequacy 5 unless style issues).

## Severity (weights 0 / 1 / 5 / 25)
| Severity | Rule of thumb | Example |
|---|---|---|
| neutral | no error | — |
| minor | noticeable, meaning intact | awkward word order |
| major | meaning distorted but detectable | a clause silently dropped |
| critical | would change clinical action | "do not take" → "take"; 5 mg → 50 mg |

## Error types
`no_error` exclusive · `mistranslation` wrong meaning · `omission` content dropped ·
`addition` content invented · `terminology` wrong domain term (hypertension≠hipotensión) ·
`number_unit` any digit/dose/unit changed · `negation_polarity` negation added/dropped ·
`grammar` · `punctuation`.

## Hard rules (enforced by the tool)
- `no_error` cannot be combined with other labels and forces severity neutral.
- A real error can never be severity neutral.
- `critical` requires a note quoting the evidence.
- Judge suggestions are shown only in routing batches — never while collecting golden labels
  (anchoring discipline). Form your own judgment first.

## Revision log
- v1.0 (2026-07): initial version, adapted from the MQM top-level typology with the three
  medical critical triggers (dosage, negation, terminology) promoted to first-class checks.
```

- [ ] **Step 5: Write failing tests**

`tests/test_importer.py`:
```python
import json
from pathlib import Path
from app.db import Database
from app import importer

DATA = Path(__file__).resolve().parent.parent / "data"

def test_import_demo_counts_and_flags():
    db = Database(":memory:")
    res = importer.import_demo(db, DATA)
    assert res["n"] == 30
    t7 = db.one("SELECT * FROM tasks WHERE id='t007'")
    flags = json.loads(t7["lf_flags"])
    assert any(f["lf"] == "lf_number_mismatch" and f["label"] == "ERROR" for f in flags)
    assert db.one("SELECT COUNT(*) n FROM golden_answers")["n"] == 6
    assert db.one("SELECT COUNT(*) n FROM annotations WHERE annotator='maria'")["n"] == 3
    assert db.one("SELECT COUNT(*) n FROM judge_results")["n"] == 30
    b = db.one("SELECT * FROM batches")
    assert b["show_suggestions"] == 0 and b["overlap"] == 2

def test_aqb_profile_maps_fields(tmp_path):
    p = tmp_path / "corpus.jsonl"
    p.write_text(json.dumps({"id": "z1", "source_zh": "他没有过敏史。",
                             "hypothesis_en": "He has a history of allergies.",
                             "reference_en": "He has no history of allergies.",
                             "arm": "wait3", "AL_ms": 1750.5}) + "\n", encoding="utf-8")
    db = Database(":memory:")
    res = importer.import_jsonl(db, p, "aqb", "aqb-batch", lang_profile="zh-en")
    t = db.one("SELECT * FROM tasks WHERE id='z1'")
    assert t["source"] == "他没有过敏史。" and t["reference"].startswith("He has no")
    meta = json.loads(t["metadata"])
    assert meta["arm"] == "wait3" and meta["al_ms"] == 1750.5
    flags = json.loads(t["lf_flags"])
    assert any(f["lf"] == "lf_negation_drop" and f["label"] == "ERROR" for f in flags)
```

- [ ] **Step 6: Run, verify fail** — `.venv/bin/python -m pytest tests/test_importer.py -v` → FAIL

- [ ] **Step 7: Implement `app/importer.py`**

```python
"""Import profiles (demo/aqb/generic) + golden loading + demo seeding.
LFs run once at import time; demo seeding also pre-runs MockJudge so the
dashboard is alive on first open."""
import json
from pathlib import Path

from .judge import MockJudge
from .lf import run_lfs

PROFILES = {
    "generic": {"id": "id", "source": "source",
                "hypothesis": ["translation", "hypothesis"], "reference": "reference"},
    "aqb": {"id": "id", "source": "source_zh",
            "hypothesis": ["hypothesis_en"], "reference": "reference_en"},
}


def _hyp(row, keys):
    for k in keys:
        if k in row:
            return row[k]
    raise KeyError(f"none of {keys} present in row")


def _read_jsonl(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def import_jsonl(db, path, profile, batch_name, lang_profile="en-es",
                 show_suggestions=False, overlap=1, actor="system"):
    p = PROFILES[profile]
    bid = db.execute(
        "INSERT INTO batches(name, show_suggestions, overlap, lang_profile) VALUES(?,?,?,?)",
        (batch_name, int(show_suggestions), overlap, lang_profile))
    n = 0
    for row in _read_jsonl(path):
        src, hyp = row[p["source"]], _hyp(row, p["hypothesis"])
        meta = dict(row.get("metadata", {}))
        if "arm" in row:
            meta["arm"] = row["arm"]
        if "AL_ms" in row:
            meta["al_ms"] = row["AL_ms"]
        flags = run_lfs(src, hyp, lang_profile)
        db.execute(
            "INSERT INTO tasks(id,batch_id,source,hypothesis,reference,metadata,lf_flags) "
            "VALUES(?,?,?,?,?,?,?)",
            (str(row[p["id"]]), bid, src, hyp, row.get(p["reference"]),
             json.dumps(meta, ensure_ascii=False), json.dumps(flags, ensure_ascii=False)))
        n += 1
    db.audit(actor, "import", "batch", bid, {"path": str(path), "n": n, "profile": profile})
    return {"batch_id": bid, "n": n}


def load_golden(db, path, actor="system"):
    n = 0
    for row in _read_jsonl(path):
        db.execute("UPDATE tasks SET is_golden=1 WHERE id=?", (row["task_id"],))
        db.execute("INSERT OR REPLACE INTO golden_answers(task_id, answer) VALUES(?,?)",
                   (row["task_id"], json.dumps(row["answer"], ensure_ascii=False)))
        n += 1
    db.audit(actor, "load_golden", "golden", path, {"n": n})
    return n


def import_demo(db, data_dir):
    data_dir = Path(data_dir)
    res = import_jsonl(db, data_dir / "demo_tasks.jsonl", "generic",
                       "demo-golden-collection", "en-es",
                       show_suggestions=False, overlap=2, actor="demo")
    load_golden(db, data_dir / "demo_golden.jsonl", actor="demo")
    for row in _read_jsonl(data_dir / "demo_seed_annotations.jsonl"):
        db.execute("INSERT INTO assignments(task_id, annotator, status, lease_expires_at) "
                   "VALUES(?,?,'submitted',datetime('now'))",
                   (row["task_id"], row["annotator"]))
        db.execute(
            "INSERT INTO annotations(task_id,annotator,error_types,worst_severity,adequacy,"
            "fluency,note,elapsed_ms) VALUES(?,?,?,?,?,?,?,?)",
            (row["task_id"], row["annotator"], json.dumps(row["error_types"]),
             row["worst_severity"], row["adequacy"], row["fluency"], row.get("note", ""),
             row.get("elapsed_ms", 45000)))
    mj = MockJudge()
    for t in db.query("SELECT * FROM tasks"):
        v = mj.evaluate(t, json.loads(t["lf_flags"]))
        db.execute(
            "INSERT INTO judge_results(task_id,verdict,confidence,model,is_mock) "
            "VALUES(?,?,?,?,1)",
            (t["id"], json.dumps({k: v[k] for k in
                                  ("error_types", "worst_severity", "adequacy", "rationale")},
                                 ensure_ascii=False), v["confidence"], mj.model))
    return res
```

- [ ] **Step 8: Run tests** — `.venv/bin/python -m pytest tests/test_importer.py -v` → all PASS
- [ ] **Step 9: Full suite + commit**

```bash
.venv/bin/python -m pytest -q
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: importer (demo/aqb profiles), synthetic demo corpus, MQM-lite guideline

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---
### Task 7: export.py — versioned snapshots

**Files:**
- Create: `propioqa/app/export.py` — Test: `propioqa/tests/test_export.py`

**Interfaces:**
- Consumes: `quality.final_label`.
- Produces: `export.canonical_json(obj) -> str`; `export.build_snapshot(db, filters=None, include_golden=False) -> dict` (keys `guideline_version, filters, items, annotators`; each item `{task:{id,source,hypothesis,reference,metadata}, final_label, annotations:[…], golden?}`); `export.export_snapshot(db, out_dir, filters=None, include_golden=False, actor="system") -> dict(version, path, sha256)`; `export.export_golden_jsonl(db, out_path) -> int`.

- [ ] **Step 1: Write failing tests**

`tests/test_export.py`:
```python
import json
from app.db import Database
from app import export
from tests.test_quality import seed, add_ann  # reuse helpers

def test_snapshot_sha_is_deterministic(tmp_path):
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission"], "major")
    r1 = export.export_snapshot(db, tmp_path)
    r2 = export.export_snapshot(db, tmp_path)
    assert r1["sha256"] == r2["sha256"]           # same content -> same hash
    assert r1["version"] == "v1" and r2["version"] == "v2"
    on_disk = json.loads((tmp_path / "dataset_v1.json").read_text())
    assert on_disk["sha256"] == r1["sha256"]

def test_snapshot_changes_when_data_changes(tmp_path):
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission"], "major")
    r1 = export.export_snapshot(db, tmp_path)
    add_ann(db, "t1", "chao", ["no_error"], "neutral")
    assert export.export_snapshot(db, tmp_path)["sha256"] != r1["sha256"]

def test_golden_excluded_by_default(tmp_path):
    db = Database(":memory:"); seed(db)
    db.execute("UPDATE tasks SET is_golden=1 WHERE id='t1'")
    db.execute("INSERT INTO golden_answers(task_id, answer) VALUES('t1', ?)",
               (json.dumps({"error_types": ["omission"], "worst_severity": "major"}),))
    add_ann(db, "t1", "chao", ["omission"], "major")
    snap = export.build_snapshot(db)
    assert "golden" not in snap["items"][0]
    snap2 = export.build_snapshot(db, include_golden=True)
    assert snap2["items"][0]["golden"]["worst_severity"] == "major"

def test_golden_jsonl_roundtrip(tmp_path):
    db = Database(":memory:"); seed(db)
    db.execute("UPDATE tasks SET is_golden=1 WHERE id='t1'")
    db.execute("INSERT INTO golden_answers(task_id, answer) VALUES('t1', ?)",
               (json.dumps({"error_types": ["omission"], "worst_severity": "major"}),))
    out = tmp_path / "golden.jsonl"
    assert export.export_golden_jsonl(db, out) == 1
    row = json.loads(out.read_text().splitlines()[0])
    assert row["id"] == "t1" and row["answer"]["error_types"] == ["omission"]
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_export.py -v` → FAIL

- [ ] **Step 3: Implement `app/export.py`**

```python
"""Versioned snapshot export. sha256 over canonical JSON of the *content*
(no export timestamps) -> same data always hashes identically; downstream
refers to dataset@vN, never to 'the latest file'."""
import hashlib
import json
from pathlib import Path

from .quality import final_label


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_snapshot(db, filters=None, include_golden=False):
    filters = filters or {}
    tasks = db.query("SELECT * FROM tasks ORDER BY rowid")
    if filters.get("batch_id"):
        tasks = [t for t in tasks if t["batch_id"] == filters["batch_id"]]
    items, annotators = [], set()
    for t in tasks:
        anns = db.query("SELECT * FROM annotations WHERE task_id=? ORDER BY id", (t["id"],))
        item = {"task": {"id": t["id"], "source": t["source"], "hypothesis": t["hypothesis"],
                         "reference": t["reference"], "metadata": json.loads(t["metadata"])},
                "final_label": final_label(db, t["id"]),
                "annotations": [{"id": a["id"], "annotator": a["annotator"],
                                 "error_types": json.loads(a["error_types"]),
                                 "worst_severity": a["worst_severity"], "adequacy": a["adequacy"],
                                 "fluency": a["fluency"], "correction": a["correction"],
                                 "note": a["note"], "elapsed_ms": a["elapsed_ms"],
                                 "guideline_version": a["guideline_version"],
                                 "created_at": a["created_at"]} for a in anns]}
        annotators.update(a["annotator"] for a in anns)
        if include_golden and t["is_golden"]:
            g = db.one("SELECT answer FROM golden_answers WHERE task_id=?", (t["id"],))
            if g:
                item["golden"] = json.loads(g["answer"])
        items.append(item)
    gv = db.one("SELECT guideline_version FROM batches ORDER BY id LIMIT 1")
    return {"guideline_version": gv["guideline_version"] if gv else "v1.0",
            "filters": filters, "items": items, "annotators": sorted(annotators)}


def export_snapshot(db, out_dir, filters=None, include_golden=False, actor="system"):
    snap = build_snapshot(db, filters, include_golden)
    digest = hashlib.sha256(canonical_json(snap).encode("utf-8")).hexdigest()
    version = f"v{db.one('SELECT COUNT(*) n FROM exports')['n'] + 1}"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"dataset_{version}.json"
    path.write_text(json.dumps({"version": version, "sha256": digest, **snap},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    db.execute("INSERT INTO exports(version, filters, sha256, path) VALUES(?,?,?,?)",
               (version, json.dumps(filters or {}), digest, str(path)))
    db.audit(actor, "export", "export", version, {"sha256": digest, "path": str(path)})
    return {"version": version, "path": str(path), "sha256": digest}


def export_golden_jsonl(db, out_path):
    rows = db.query("SELECT t.id, t.source, t.hypothesis, t.reference, g.answer "
                    "FROM tasks t JOIN golden_answers g ON g.task_id=t.id ORDER BY t.rowid")
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"id": r["id"], "source": r["source"],
                                "hypothesis": r["hypothesis"], "reference": r["reference"],
                                "answer": json.loads(r["answer"])}, ensure_ascii=False) + "\n")
    return len(rows)
```

- [ ] **Step 4: Run tests + full suite** — `.venv/bin/python -m pytest -q` → all PASS
- [ ] **Step 5: Commit**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: deterministic versioned snapshot export + golden JSONL

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: main.py — API layer + run.py

**Files:**
- Create: `propioqa/app/main.py`, `propioqa/run.py` — Test: `propioqa/tests/test_api.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main.create_app(db_path=":memory:", demo=False, data_dir=None, export_dir=None) -> FastAPI`. Routes (all JSON): `POST /api/claim {annotator, batch_id?}` → `{task: {...}|null, progress:{done,total}}` — task carries `suggestions` key **only** for `show_suggestions=1` batches and never carries `lf_flags`/`is_golden`; `POST /api/submit` (SubmitRequest) → `{annotation_id}` (422 invalid payload / 404 foreign / 409 not-open); `POST /api/skip {annotator, assignment_id, reason?}`; `POST /api/undo {annotator}` → `{task|null}`; `GET /api/batches` → list with `n_tasks`; `GET /api/review/queue` → `[{task, annotation, judge, lf_errors, disagreement}]` sorted desc; `POST /api/review/{annotation_id}` (ReviewRequest) → `{review_id}`; `GET /api/stats/overview|matrix|annotators|agreement`; `POST /api/judge/run {batch_id}` → `{n, model, is_mock}` (503 when unreachable); `POST /api/routing/build {top_n, signal}` → `{batch_id, name, n}`; `POST /api/export {batch_id?, include_golden?}` → export result; `GET /api/guideline` → `{text}`; `GET /api/health` → `{status, judge:{mode, reachable}}`. Static SPA mounted at `/` (html mode).

- [ ] **Step 1: Write failing tests**

`tests/test_api.py`:
```python
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app

DATA = Path(__file__).resolve().parent.parent / "data"

@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=":memory:", demo=True, data_dir=DATA,
                     export_dir=str(tmp_path / "exports"))
    return TestClient(app)

SUBMIT_OK = dict(error_types=["no_error"], worst_severity="neutral",
                 adequacy=5, fluency=5, elapsed_ms=1500)

def claim(client, who="chao"):
    return client.post("/api/claim", json={"annotator": who}).json()

def test_claim_clean_batch_has_no_signals(client):
    c = claim(client)
    assert c["task"]["task_id"] == "t001"
    assert "suggestions" not in c["task"] and "lf_flags" not in c["task"]
    assert "is_golden" not in c["task"]
    assert c["progress"] == {"done": 0, "total": 30}

def test_submit_and_validation(client):
    c = claim(client)
    r = client.post("/api/submit", json={**SUBMIT_OK, "annotator": "chao",
                                         "assignment_id": c["task"]["assignment_id"]})
    assert r.status_code == 200
    # critical without note -> 422 from pydantic
    c2 = claim(client)
    bad = {**SUBMIT_OK, "error_types": ["number_unit"], "worst_severity": "critical",
           "annotator": "chao", "assignment_id": c2["task"]["assignment_id"]}
    assert client.post("/api/submit", json=bad).status_code == 422
    # double submit -> 409
    ok = {**SUBMIT_OK, "annotator": "chao", "assignment_id": c["task"]["assignment_id"]}
    assert client.post("/api/submit", json=ok).status_code == 409
    # foreign assignment -> 404
    assert client.post("/api/submit", json={**SUBMIT_OK, "annotator": "eve",
                                            "assignment_id": c2["task"]["assignment_id"]}).status_code == 404

def test_undo_reopens(client):
    c = claim(client)
    client.post("/api/submit", json={**SUBMIT_OK, "annotator": "chao",
                                     "assignment_id": c["task"]["assignment_id"]})
    u = client.post("/api/undo", json={"annotator": "chao"}).json()
    assert u["task"]["task_id"] == c["task"]["task_id"]

def test_review_flow(client):
    q = client.get("/api/review/queue").json()
    assert len(q) >= 3                        # maria's seeded annotations
    target = q[0]
    r = client.post(f"/api/review/{target['annotation']['id']}",
                    json={"reviewer": "lead", "verdict": "approved"})
    assert r.status_code == 200
    q2 = client.get("/api/review/queue").json()
    assert all(x["annotation"]["id"] != target["annotation"]["id"] for x in q2)

def test_review_overturn_appends_replacement(client):
    q = client.get("/api/review/queue").json()
    target = q[0]
    rep = dict(error_types=["terminology"], worst_severity="critical", adequacy=2,
               fluency=3, note="term swapped", correction="")
    r = client.post(f"/api/review/{target['annotation']['id']}",
                    json={"reviewer": "lead", "verdict": "overturned",
                          "case_note": "guideline case", "replacement": rep})
    assert r.status_code == 200

def test_stats_endpoints(client):
    m = client.get("/api/stats/matrix").json()
    assert set(m["arms"]) == {"wait1", "wait3", "offline"}
    a = client.get("/api/stats/annotators").json()
    assert any(s["annotator"] == "maria" for s in a)
    o = client.get("/api/stats/overview").json()
    assert o["n_tasks"] == 30 and o["judge"]["mode"] == "mock"
    client.get("/api/stats/agreement").json()   # must not 500

def test_routing_batch_exposes_suggestions(client):
    r = client.post("/api/routing/build", json={"top_n": 5, "signal": "judge_confidence"}).json()
    assert r["n"] == 5
    c = client.post("/api/claim", json={"annotator": "router", "batch_id": r["batch_id"]}).json()
    assert "suggestions" in c["task"]
    assert c["task"]["suggestions"]["judge"]["is_mock"] is True

def test_export_deterministic(client):
    e1 = client.post("/api/export", json={}).json()
    e2 = client.post("/api/export", json={}).json()
    assert e1["sha256"] == e2["sha256"] and e1["version"] != e2["version"]

def test_health_and_guideline(client):
    assert client.get("/api/health").json()["judge"]["mode"] == "mock"
    assert "MQM-lite" in client.get("/api/guideline").json()["text"]
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/python -m pytest tests/test_api.py -v` → FAIL

- [ ] **Step 3: Implement `app/main.py`**

```python
"""API layer. The one non-negotiable: batch policy is enforced HERE, server-side —
a clean-batch claim response never even contains the suggestion fields."""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import export as exportmod
from . import importer, quality
from . import tasks as taskmod
from .db import Database
from .judge import JudgeUnavailable, get_judge, judge_health
from .models import SEVERITIES, AnnotationPayload, SubmitRequest

APP_DIR = Path(__file__).resolve().parent.parent


class ClaimRequest(BaseModel):
    annotator: str
    batch_id: int | None = None


class SkipRequest(BaseModel):
    annotator: str
    assignment_id: int
    reason: str = ""


class UndoRequest(BaseModel):
    annotator: str


class ReviewRequest(BaseModel):
    reviewer: str
    verdict: str                      # approved | overturned
    case_note: str = ""
    replacement: AnnotationPayload | None = None


class JudgeRunRequest(BaseModel):
    batch_id: int


class RoutingRequest(BaseModel):
    top_n: int = 10
    signal: str = "judge_confidence"  # judge_confidence | lf_conflict


class ExportRequest(BaseModel):
    batch_id: int | None = None
    include_golden: bool = False


def create_app(db_path=":memory:", demo=False, data_dir=None, export_dir=None):
    app = FastAPI(title="PropioQA Workbench")
    db = Database(db_path)
    app.state.db = db
    app.state.export_dir = export_dir or str(APP_DIR / "exports")
    gl_path = Path(data_dir or APP_DIR / "data") / "guideline.md"
    guideline = gl_path.read_text(encoding="utf-8") if gl_path.exists() else ""
    if demo:
        importer.import_demo(db, data_dir or APP_DIR / "data")

    def with_policy(payload):
        """Strip or attach machine signals according to batch policy."""
        if payload is None:
            return None
        if payload["batch"]["show_suggestions"]:
            jr = db.one("SELECT verdict, confidence, model, is_mock FROM judge_results "
                        "WHERE task_id=? ORDER BY id DESC LIMIT 1", (payload["task_id"],))
            payload["suggestions"] = {
                "lf": [f for f in payload["lf_flags"] if f["label"] == "ERROR"],
                "judge": ({**json.loads(jr["verdict"]), "confidence": jr["confidence"],
                           "model": jr["model"], "is_mock": bool(jr["is_mock"])} if jr else None)}
        payload.pop("lf_flags", None)
        return payload

    @app.post("/api/claim")
    def api_claim(req: ClaimRequest):
        c = taskmod.claim(db, req.annotator, req.batch_id)
        if c is None:
            bid = req.batch_id or (db.one("SELECT id FROM batches ORDER BY id LIMIT 1") or {}).get("id", 0)
            return {"task": None, "progress": taskmod.progress(db, req.annotator, bid) if bid else {"done": 0, "total": 0}}
        prog = taskmod.progress(db, req.annotator, c["batch_id"])
        return {"task": with_policy(c), "progress": prog}

    @app.post("/api/submit")
    def api_submit(req: SubmitRequest):
        try:
            ann_id = taskmod.submit(db, req.annotator, req.assignment_id, req)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"annotation_id": ann_id}

    @app.post("/api/skip")
    def api_skip(req: SkipRequest):
        try:
            taskmod.skip(db, req.annotator, req.assignment_id, req.reason)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/undo")
    def api_undo(req: UndoRequest):
        return {"task": with_policy(taskmod.undo(db, req.annotator))}

    @app.get("/api/batches")
    def api_batches():
        return [dict(b, n_tasks=db.one("SELECT COUNT(*) n FROM tasks WHERE batch_id=?",
                                       (b["id"],))["n"])
                for b in db.query("SELECT * FROM batches ORDER BY id")]

    def _disagreement(ann, judge_v, lf_errors):
        score = 0.0
        if judge_v:
            score += abs(SEVERITIES.index(ann["worst_severity"])
                         - SEVERITIES.index(judge_v["worst_severity"]))
            ha, ja = set(ann["error_types"]), set(judge_v["error_types"])
            union = ha | ja
            score += 2 * (1 - len(ha & ja) / len(union)) if union else 0
        score += 0.5 * len(lf_errors)
        return round(score, 2)

    @app.get("/api/review/queue")
    def api_review_queue():
        out = []
        for tr in db.query("SELECT DISTINCT task_id FROM annotations "
                           "WHERE annotator NOT LIKE 'reviewer:%'"):
            for ann in quality.latest_annotations(db, tr["task_id"]):
                if db.one("SELECT id FROM reviews WHERE reviewed_annotation_id=?", (ann["id"],)):
                    continue
                t = db.one("SELECT * FROM tasks WHERE id=?", (tr["task_id"],))
                jr = db.one("SELECT verdict, confidence, is_mock FROM judge_results "
                            "WHERE task_id=? ORDER BY id DESC LIMIT 1", (tr["task_id"],))
                judge_v = ({**json.loads(jr["verdict"]), "confidence": jr["confidence"],
                            "is_mock": bool(jr["is_mock"])} if jr else None)
                lf_errors = [f for f in json.loads(t["lf_flags"]) if f["label"] == "ERROR"]
                out.append({"task": {"id": t["id"], "source": t["source"],
                                     "hypothesis": t["hypothesis"], "reference": t["reference"]},
                            "annotation": ann, "judge": judge_v, "lf_errors": lf_errors,
                            "disagreement": _disagreement(ann, judge_v, lf_errors)})
        return sorted(out, key=lambda x: -x["disagreement"])

    @app.post("/api/review/{annotation_id}")
    def api_review(annotation_id: int, req: ReviewRequest):
        ann = db.one("SELECT * FROM annotations WHERE id=?", (annotation_id,))
        if ann is None:
            raise HTTPException(404, "annotation not found")
        if req.verdict not in ("approved", "overturned"):
            raise HTTPException(422, "verdict must be approved|overturned")
        replacement_id = None
        if req.verdict == "overturned":
            if req.replacement is None:
                raise HTTPException(422, "overturn requires a replacement annotation")
            p = req.replacement
            replacement_id = db.execute(
                "INSERT INTO annotations(task_id,annotator,error_types,worst_severity,adequacy,"
                "fluency,correction,note,guideline_version) VALUES(?,?,?,?,?,?,?,?,?)",
                (ann["task_id"], f"reviewer:{req.reviewer}", json.dumps(p.error_types),
                 p.worst_severity, p.adequacy, p.fluency, p.correction, p.note,
                 ann["guideline_version"]))
        review_id = db.execute(
            "INSERT INTO reviews(task_id,reviewed_annotation_id,reviewer,verdict,"
            "replacement_annotation_id,case_note) VALUES(?,?,?,?,?,?)",
            (ann["task_id"], annotation_id, req.reviewer, req.verdict,
             replacement_id, req.case_note))
        db.audit(f"reviewer:{req.reviewer}", "review", "annotation", annotation_id,
                 {"verdict": req.verdict, "case_note": req.case_note})
        return {"review_id": review_id}

    @app.get("/api/stats/overview")
    def api_overview():
        recent = [r["elapsed_ms"] for r in db.query(
            "SELECT elapsed_ms FROM annotations WHERE elapsed_ms>0 ORDER BY id DESC LIMIT 30")]
        return {"n_tasks": db.one("SELECT COUNT(*) n FROM tasks")["n"],
                "n_annotations": db.one("SELECT COUNT(*) n FROM annotations")["n"],
                "n_batches": db.one("SELECT COUNT(*) n FROM batches")["n"],
                "recent_elapsed_ms": list(reversed(recent)),
                "judge": judge_health(get_judge(guideline))}

    @app.get("/api/stats/matrix")
    def api_matrix():
        return quality.error_arm_matrix(db)

    @app.get("/api/stats/annotators")
    def api_annotators():
        return quality.annotator_stats(db)

    @app.get("/api/stats/agreement")
    def api_agreement():
        return {"pairwise": quality.pairwise_kappa(db),
                "judge_human": quality.judge_human_agreement(db)}

    @app.post("/api/judge/run")
    def api_judge_run(req: JudgeRunRequest):
        j = get_judge(guideline)
        n = 0
        for t in db.query("SELECT * FROM tasks WHERE batch_id=?", (req.batch_id,)):
            try:
                v = j.evaluate(t, json.loads(t["lf_flags"]))
            except JudgeUnavailable as e:
                raise HTTPException(503, f"judge unavailable: {e}")
            db.execute("INSERT INTO judge_results(task_id,verdict,confidence,model,is_mock) "
                       "VALUES(?,?,?,?,?)",
                       (t["id"], json.dumps({k: v[k] for k in ("error_types", "worst_severity",
                                                               "adequacy", "rationale")},
                                            ensure_ascii=False),
                        v["confidence"], j.model, int(j.is_mock)))
            n += 1
        db.audit("system", "judge_run", "batch", req.batch_id, {"n": n, "model": j.model})
        return {"n": n, "model": j.model, "is_mock": j.is_mock}

    @app.post("/api/routing/build")
    def api_routing(req: RoutingRequest):
        if req.signal == "judge_confidence":
            ranked = db.query("""
                SELECT t.id, t.batch_id FROM tasks t JOIN (
                  SELECT task_id, confidence, MAX(id) FROM judge_results GROUP BY task_id
                ) j ON j.task_id = t.id
                WHERE t.id NOT LIKE '%::r%'
                ORDER BY j.confidence ASC LIMIT ?""", (req.top_n,))
        elif req.signal == "lf_conflict":
            cands = [t for t in db.query("SELECT * FROM tasks WHERE id NOT LIKE '%::r%'")
                     if any(f["label"] == "ERROR" for f in json.loads(t["lf_flags"]))]
            cands.sort(key=lambda t: -sum(1 for f in json.loads(t["lf_flags"])
                                          if f["label"] == "ERROR"))
            ranked = cands[:req.top_n]
        else:
            raise HTTPException(422, "signal must be judge_confidence|lf_conflict")
        if not ranked:
            raise HTTPException(409, "no candidates — run the judge first")
        src_batch = db.one("SELECT * FROM batches WHERE id=?", (ranked[0]["batch_id"],))
        n_existing = db.one("SELECT COUNT(*) n FROM batches WHERE name LIKE 'routing-%'")["n"]
        name = f"routing-{n_existing + 1}"
        new_bid = db.execute(
            "INSERT INTO batches(name, show_suggestions, overlap, lang_profile, guideline_version)"
            " VALUES(?,1,1,?,?)", (name, src_batch["lang_profile"], src_batch["guideline_version"]))
        for r in ranked:
            t = db.one("SELECT * FROM tasks WHERE id=?", (r["id"],))
            new_id = f"{t['id']}::r{new_bid}"
            db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis,reference,metadata,"
                       "lf_flags) VALUES(?,?,?,?,?,?,?)",
                       (new_id, new_bid, t["source"], t["hypothesis"], t["reference"],
                        t["metadata"], t["lf_flags"]))
            jr = db.one("SELECT * FROM judge_results WHERE task_id=? ORDER BY id DESC LIMIT 1",
                        (t["id"],))
            if jr:
                db.execute("INSERT INTO judge_results(task_id,verdict,confidence,model,is_mock)"
                           " VALUES(?,?,?,?,?)", (new_id, jr["verdict"], jr["confidence"],
                                                  jr["model"], jr["is_mock"]))
        db.audit("system", "routing_build", "batch", new_bid,
                 {"signal": req.signal, "n": len(ranked)})
        return {"batch_id": new_bid, "name": name, "n": len(ranked)}

    @app.post("/api/export")
    def api_export(req: ExportRequest):
        filters = {"batch_id": req.batch_id} if req.batch_id else {}
        return exportmod.export_snapshot(db, app.state.export_dir, filters, req.include_golden)

    @app.get("/api/guideline")
    def api_guideline():
        return {"text": guideline}

    @app.get("/api/health")
    def api_health():
        return {"status": "ok", "judge": judge_health(get_judge(guideline))}

    static_dir = APP_DIR / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app
```

- [ ] **Step 4: Implement `run.py`**

```python
#!/usr/bin/env python3
"""PropioQA Workbench entry point.  python run.py --demo  ->  http://localhost:8420"""
import argparse
import os
import tempfile

import uvicorn

from app.main import create_app

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PropioQA Workbench")
    ap.add_argument("--demo", action="store_true", help="fresh demo DB + synthetic data")
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument("--db", default="propioqa.db")
    a = ap.parse_args()
    db_path = (os.path.join(tempfile.mkdtemp(prefix="propioqa_demo_"), "demo.db")
               if a.demo else a.db)
    app = create_app(db_path=db_path, demo=a.demo)
    print(f"PropioQA Workbench → http://localhost:{a.port}   (db: {db_path})")
    uvicorn.run(app, host="127.0.0.1", port=a.port)
```

- [ ] **Step 5: Run tests** — `.venv/bin/python -m pytest tests/test_api.py -v` → all PASS; then full suite `-q` → all PASS
- [ ] **Step 6: Manual boot check** — `.venv/bin/python run.py --demo` then `curl -s localhost:8420/api/health` → `{"status":"ok",...}`; Ctrl-C.
- [ ] **Step 7: Commit**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: full REST API with server-side batch-policy enforcement + run.py

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---
### Task 9: Frontend shell + Annotate tab

**Files:**
- Create: `propioqa/static/index.html`, `propioqa/static/style.css`, `propioqa/static/app.js`

**Interfaces:**
- Consumes: `/api/claim /api/submit /api/skip /api/undo /api/guideline /api/health`.
- Produces: `window.PQA = {api, esc, toast, state}` for review.js / dash.js (Tasks 10–11); tab switcher calls `window.renderReview?.()` / `window.renderDashboard?.()` (optional chaining — safe before those files exist). Keyboard map: `1-5` adequacy · `Shift+1-5` fluency · `v` cycle severity · letters `m o a t n g r p` toggle error types, `0` = no_error · `c` focus correction · `x` focus note · `Space` submit · `u` undo · `s` skip · `?` help. (Refinement over spec §5.1: letter keys are always live instead of behind an `e` palette — fewer keystrokes; `e` kept as alias that focuses the error chip row.)

- [ ] **Step 1: Write `static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PropioQA Workbench</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header id="topbar">
  <div class="brand">Propio<span>QA</span> <small>Workbench</small></div>
  <nav id="tabs">
    <button data-tab="annotate" class="tab active">Annotate</button>
    <button data-tab="review" class="tab">Review</button>
    <button data-tab="dashboard" class="tab">Dashboard</button>
  </nav>
  <div class="top-right">
    <span id="judge-badge" class="badge">judge: …</span>
    <input id="annotator" placeholder="annotator id" autocomplete="off">
    <button id="start-btn" class="primary">Start</button>
    <button id="theme-btn" title="toggle theme">◐</button>
  </div>
</header>
<main>
  <section id="view-annotate" class="view active">
    <div class="empty">Enter an annotator id and press <b>Start</b>.</div>
  </section>
  <section id="view-review" class="view"><div class="empty">Review queue loads here.</div></section>
  <section id="view-dashboard" class="view"><div class="empty">Dashboard loads here.</div></section>
</main>
<div id="modal" class="hidden"><div id="modal-card"><button id="modal-close">✕</button><div id="modal-body"></div></div></div>
<div id="toast" class="hidden"></div>
<script src="app.js"></script>
<script src="review.js"></script>
<script src="dash.js"></script>
</body>
</html>
```

(`review.js`/`dash.js` 404 until Tasks 10–11 — harmless; guarded by optional chaining.)

- [ ] **Step 2: Write `static/style.css`**

```css
:root{
  --bg:#0e1320; --panel:#161d2e; --panel2:#1c2537; --border:#2a3550;
  --text:#e9edf5; --muted:#8d96aa; --accent:#5b8dff; --accent2:#7fb2ff;
  --ok:#3ddc97; --warn:#ffb454; --err:#ff5d73; --mock:#c792ea;
  --mono:ui-monospace,'SF Mono',Menlo,monospace;
}
[data-theme="light"]{
  --bg:#f4f6fb; --panel:#ffffff; --panel2:#eef1f8; --border:#d5dbe8;
  --text:#1c2333; --muted:#5d6678; --accent:#2f6bff; --accent2:#1f4fd6;
}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,'Segoe UI',sans-serif}
#topbar{display:flex;align-items:center;gap:18px;padding:10px 18px;
  background:var(--panel);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:5}
.brand{font-weight:700;font-size:17px}
.brand span{color:var(--accent)} .brand small{color:var(--muted);font-weight:400;margin-left:4px}
#tabs{display:flex;gap:4px}
.tab{background:none;border:0;color:var(--muted);padding:8px 14px;border-radius:8px;cursor:pointer;font-size:14px}
.tab.active{background:var(--panel2);color:var(--text);font-weight:600}
.top-right{margin-left:auto;display:flex;gap:8px;align-items:center}
#annotator{background:var(--panel2);border:1px solid var(--border);color:var(--text);
  padding:7px 10px;border-radius:8px;width:130px}
button{font:inherit;cursor:pointer}
button.primary{background:var(--accent);color:#fff;border:0;padding:7px 14px;border-radius:8px;font-weight:600}
#theme-btn{background:none;border:1px solid var(--border);color:var(--muted);border-radius:8px;padding:5px 9px}
.badge{font:12px var(--mono);border:1px solid var(--border);border-radius:999px;
  padding:3px 10px;color:var(--muted)}
.badge.on{color:var(--ok);border-color:var(--ok)} .badge.mock{color:var(--mock);border-color:var(--mock)}
.badge.off{color:var(--err);border-color:var(--err)}
main{max-width:980px;margin:24px auto;padding:0 18px}
.view{display:none}.view.active{display:block}
.empty{color:var(--muted);text-align:center;padding:80px 0}
.card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:14px}
.progress-line{display:flex;gap:16px;color:var(--muted);font:13px var(--mono);margin-bottom:10px}
.progress-line b{color:var(--text)}
.textpanel{background:var(--panel2);border-radius:10px;padding:12px 14px;margin:8px 0}
.textpanel .lbl{font:11px var(--mono);color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.textpanel .txt{font-size:16px;margin-top:4px}
details.ref{margin:6px 0;color:var(--muted)} details.ref summary{cursor:pointer;font-size:13px}
.sugg{border:1px dashed var(--border);border-radius:10px;padding:10px 12px;margin:10px 0}
.sugg .lbl{font:11px var(--mono);color:var(--warn)}
.anchor-note{color:var(--muted);font-size:12.5px;font-style:italic;margin:10px 0}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.chip{border:1px solid var(--border);background:var(--panel2);color:var(--text);
  border-radius:999px;padding:5px 12px;font-size:13.5px}
.chip.sel{background:var(--accent);border-color:var(--accent);color:#fff}
.chip kbd{opacity:.55;font:11px var(--mono);margin-right:4px}
.chip.warnc{border-color:var(--warn);color:var(--warn);background:none}
.chip.mockc{border-color:var(--mock);color:var(--mock);background:none}
.seg{display:flex;gap:6px;margin:6px 0}
.seg button{border:1px solid var(--border);background:var(--panel2);color:var(--text);
  border-radius:8px;padding:6px 12px;min-width:40px}
.seg button.sel{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:700}
.seg button.sev-critical.sel{background:var(--err);border-color:var(--err)}
.row{display:flex;gap:22px;flex-wrap:wrap;margin:10px 0}
.field label{display:block;font:12px var(--mono);color:var(--muted);margin:8px 0 4px}
textarea{width:100%;background:var(--panel2);border:1px solid var(--border);color:var(--text);
  border-radius:8px;padding:8px;font:14px/1.4 inherit;resize:vertical}
.actions{display:flex;gap:10px;margin-top:14px;align-items:center}
.actions .hint{color:var(--muted);font:12px var(--mono);margin-left:auto}
kbd.k{background:var(--panel2);border:1px solid var(--border);border-bottom-width:2px;
  border-radius:5px;padding:1px 6px;font:12px var(--mono)}
#toast{position:fixed;bottom:26px;left:50%;transform:translateX(-50%);
  background:var(--panel);border:1px solid var(--border);border-radius:10px;
  padding:10px 18px;box-shadow:0 8px 30px #0007;z-index:9}
#toast.err{border-color:var(--err);color:var(--err)}
.hidden{display:none!important}
#modal{position:fixed;inset:0;background:#0009;z-index:8;display:flex;align-items:center;justify-content:center}
#modal-card{background:var(--panel);border:1px solid var(--border);border-radius:14px;
  max-width:720px;max-height:80vh;overflow:auto;padding:22px;position:relative}
#modal-close{position:absolute;top:10px;right:12px;background:none;border:0;color:var(--muted);font-size:16px}
#modal-body pre{white-space:pre-wrap;font:13px/1.55 var(--mono)}
/* review */
.review-grid{display:grid;grid-template-columns:280px 1fr;gap:14px}
.rq-item{padding:10px 12px;border-bottom:1px solid var(--border);cursor:pointer;font-size:13.5px}
.rq-item:hover,.rq-item.sel{background:var(--panel2)}
.rq-item .dis{float:right;color:var(--warn);font:12px var(--mono)}
.threeway{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:10px 0}
.threeway .col{background:var(--panel2);border-radius:10px;padding:10px}
.threeway .col h4{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.disagree{outline:1px solid var(--warn)}
select{background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:6px}
/* dashboard */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.stat .num{font-size:26px;font-weight:700} .stat .lbl{color:var(--muted);font-size:12.5px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font:12px var(--mono);text-transform:uppercase}
.matrix{display:grid;gap:3px;font:12px var(--mono)}
.matrix .hcell{color:var(--muted);padding:4px}
.matrix .cell{border-radius:6px;padding:8px 4px;text-align:center;background:var(--panel2)}
.sparkline{width:100%;height:46px}
.form-inline{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input[type=number]{background:var(--panel2);border:1px solid var(--border);color:var(--text);
  border-radius:8px;padding:6px;width:70px}
```

- [ ] **Step 3: Write `static/app.js`** (core + Annotate)

```javascript
/* PropioQA Workbench — core + Annotate tab. Review/Dashboard live in review.js/dash.js. */
"use strict";

const state = { annotator: localStorage.getItem("pqa_annotator") || "",
                task: null, startTs: 0, sel: null, timerId: null };

const $ = (s, el) => (el || document).querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path, body) {
  const r = await fetch("/api" + path, body === undefined ? {} :
    { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = r.status;
    try { const j = await r.json(); msg = j.detail?.[0]?.msg || j.detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

let toastId = null;
function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg; t.className = isErr ? "err" : "";
  clearTimeout(toastId); toastId = setTimeout(() => t.classList.add("hidden"), 2600);
}

function openModal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
$("#modal-close").onclick = () => $("#modal").classList.add("hidden");

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === b));
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("active", v.id === "view-" + b.dataset.tab));
  if (b.dataset.tab === "review") window.renderReview?.();
  if (b.dataset.tab === "dashboard") window.renderDashboard?.(true); else window.stopDashPoll?.();
});

/* ---------- theme + health ---------- */
$("#theme-btn").onclick = () => {
  const cur = document.documentElement.dataset.theme === "light" ? "" : "light";
  document.documentElement.dataset.theme = cur; localStorage.setItem("pqa_theme", cur);
};
document.documentElement.dataset.theme = localStorage.getItem("pqa_theme") || "";
async function health() {
  try {
    const h = await api("/health");
    const b = $("#judge-badge");
    b.textContent = "judge: " + h.judge.mode + (h.judge.reachable ? "" : " (offline)");
    b.className = "badge " + (h.judge.mode === "mock" ? "mock" : h.judge.reachable ? "on" : "off");
  } catch { $("#judge-badge").className = "badge off"; }
}
health();

/* ---------- annotate ---------- */
const ERRORS = [["m","mistranslation"],["o","omission"],["a","addition"],["t","terminology"],
                ["n","number_unit"],["g","negation_polarity"],["r","grammar"],["p","punctuation"]];
const SEVERITIES = ["neutral","minor","major","critical"];

function freshSel() { return { errors: new Set(), severity: null, adequacy: null, fluency: null }; }

$("#annotator").value = state.annotator;
$("#start-btn").onclick = start;
function start() {
  state.annotator = $("#annotator").value.trim();
  if (!state.annotator) return toast("annotator id required", true);
  localStorage.setItem("pqa_annotator", state.annotator);
  claimNext();
}

async function claimNext() {
  try {
    const r = await api("/claim", { annotator: state.annotator });
    state.progress = r.progress;
    renderTask(r.task);
  } catch (e) { toast("claim failed: " + e.message, true); }
}

function renderTask(task) {
  clearInterval(state.timerId);
  state.task = task; state.sel = freshSel(); state.startTs = Date.now();
  const v = $("#view-annotate");
  if (!task) { v.innerHTML = `<div class="empty">🎉 Queue empty — nothing left to annotate.</div>`; return; }
  const meta = task.metadata || {};
  const sugg = task.suggestions;
  v.innerHTML = `
  <div class="card">
    <div class="progress-line">
      <span>#<b>${state.progress.done + 1}</b>/${state.progress.total}</span>
      <span>task <b>${esc(task.task_id)}</b></span>
      ${meta.arm ? `<span>arm <b>${esc(meta.arm)}</b></span>` : ""}
      <span id="timer">0:00</span>
      <span>batch <b>${esc(task.batch.name)}</b></span>
    </div>
    <div class="textpanel"><div class="lbl">Source</div><div class="txt">${esc(task.source)}</div></div>
    <div class="textpanel"><div class="lbl">Hypothesis</div><div class="txt">${esc(task.hypothesis)}</div></div>
    ${task.reference ? `<details class="ref"><summary>reference</summary>
      <div class="textpanel"><div class="txt">${esc(task.reference)}</div></details>` : ""}
    ${sugg ? renderSuggestions(sugg) :
      `<div class="anchor-note">No machine signals: this batch collects golden labels —
       anchoring discipline is enforced server-side.</div>`}
    <div class="row">
      <div class="field"><label>adequacy <kbd class="k">1-5</kbd></label>
        <div class="seg" id="seg-adequacy">${[1,2,3,4,5].map(n=>`<button data-v="${n}">${n}</button>`).join("")}</div></div>
      <div class="field"><label>fluency <kbd class="k">⇧1-5</kbd></label>
        <div class="seg" id="seg-fluency">${[1,2,3,4,5].map(n=>`<button data-v="${n}">${n}</button>`).join("")}</div></div>
      <div class="field"><label>severity <kbd class="k">v</kbd></label>
        <div class="seg" id="seg-severity">${SEVERITIES.map(s=>`<button class="sev-${s}" data-v="${s}">${s}</button>`).join("")}</div></div>
    </div>
    <div class="field"><label>error types <kbd class="k">letter keys · 0 = no error</kbd></label>
      <div class="chips" id="chips-errors">
        <button class="chip" data-v="no_error"><kbd>0</kbd>no_error</button>
        ${ERRORS.map(([k,e])=>`<button class="chip" data-v="${e}"><kbd>${k}</kbd>${e}</button>`).join("")}
      </div></div>
    <div class="field"><label>correction (optional) <kbd class="k">c</kbd></label>
      <textarea id="correction" rows="2"></textarea></div>
    <div class="field"><label>note — required for critical <kbd class="k">x</kbd></label>
      <textarea id="note" rows="2"></textarea></div>
    <div class="actions">
      <button class="primary" id="btn-submit">Save &amp; Next <kbd class="k">␣</kbd></button>
      <button id="btn-skip">Skip <kbd class="k">s</kbd></button>
      <button id="btn-undo">Undo <kbd class="k">u</kbd></button>
      <span class="hint">? for guideline</span>
    </div>
  </div>`;
  state.timerId = setInterval(() => {
    const s = Math.floor((Date.now() - state.startTs) / 1000);
    const el = $("#timer"); if (el) el.textContent = `${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`;
  }, 1000);
  $("#seg-adequacy").onclick = e => e.target.dataset.v && setSeg("adequacy", +e.target.dataset.v);
  $("#seg-fluency").onclick = e => e.target.dataset.v && setSeg("fluency", +e.target.dataset.v);
  $("#seg-severity").onclick = e => e.target.dataset.v && setSeg("severity", e.target.dataset.v);
  $("#chips-errors").onclick = e => { const c = e.target.closest(".chip"); if (c) toggleError(c.dataset.v); };
  $("#btn-submit").onclick = submit; $("#btn-skip").onclick = skip; $("#btn-undo").onclick = undo;
}

function renderSuggestions(sugg) {
  const j = sugg.judge;
  return `<div class="sugg"><div class="lbl">machine suggestions (routing batch)</div><div class="chips">
    ${j ? `<span class="chip ${j.is_mock ? "mockc" : "warnc"}"
        title="${esc(j.rationale)}">judge${j.is_mock ? " · MOCK" : ""}: ${esc(j.worst_severity)}
        · adequacy ${j.adequacy} · conf ${j.confidence}</span>` : ""}
    ${sugg.lf.map(f => `<span class="chip warnc" title="${esc(f.evidence)}">⚠ ${esc(f.lf)}</span>`).join("")}
  </div></div>`;
}

function setSeg(name, val) {
  state.sel[name] = val;
  document.querySelectorAll(`#seg-${name} button`).forEach(b =>
    b.classList.toggle("sel", String(b.dataset.v) === String(val)));
}
function toggleError(e) {
  const s = state.sel.errors;
  if (e === "no_error") { s.clear(); s.add("no_error"); setSeg("severity", "neutral"); }
  else { s.delete("no_error"); s.has(e) ? s.delete(e) : s.add(e); }
  document.querySelectorAll("#chips-errors .chip").forEach(c =>
    c.classList.toggle("sel", s.has(c.dataset.v)));
}

async function submit() {
  const { sel, task } = state;
  if (!task) return;
  if (!sel.errors.size) return toast("pick error types (0 = no error)", true);
  if (!sel.severity) return toast("pick severity (v)", true);
  if (!sel.adequacy || !sel.fluency) return toast("rate adequacy (1-5) and fluency (⇧1-5)", true);
  try {
    await api("/submit", { annotator: state.annotator, assignment_id: task.assignment_id,
      error_types: [...sel.errors], worst_severity: sel.severity,
      adequacy: sel.adequacy, fluency: sel.fluency,
      correction: $("#correction").value, note: $("#note").value,
      elapsed_ms: Date.now() - state.startTs });
    toast("saved ✓"); claimNext();
  } catch (e) { toast(e.message, true); }
}
async function skip() {
  if (!state.task) return;
  try { await api("/skip", { annotator: state.annotator, assignment_id: state.task.assignment_id });
        toast("skipped"); claimNext(); } catch (e) { toast(e.message, true); }
}
async function undo() {
  try {
    const r = await api("/undo", { annotator: state.annotator });
    if (!r.task) return toast("nothing to undo", true);
    state.progress.done = Math.max(0, state.progress.done - 1);
    renderTask(r.task); toast("reopened — previous row kept (append-only)");
  } catch (e) { toast(e.message, true); }
}
async function showGuideline() {
  const g = await api("/guideline");
  openModal(`<pre>${esc(g.text)}</pre>`);
}

/* ---------- keyboard ---------- */
document.addEventListener("keydown", e => {
  if (e.key === "Escape") return $("#modal").classList.add("hidden");
  if (e.target.matches("input, textarea, select")) return;
  if (!$("#view-annotate").classList.contains("active") || !state.task) {
    if (e.key === "?") showGuideline();
    return;
  }
  if (/^[1-5]$/.test(e.key) && !e.shiftKey) return setSeg("adequacy", +e.key);
  if (e.shiftKey && /^[!@#$%]$/.test(e.key))
    return setSeg("fluency", {"!":1,"@":2,"#":3,"$":4,"%":5}[e.key]);
  const err = ERRORS.find(([k]) => k === e.key.toLowerCase() && !e.metaKey && !e.ctrlKey);
  if (err && e.key !== "s" && e.key !== "u") return toggleError(err[1]);
  switch (e.key) {
    case "0": return toggleError("no_error");
    case "v": {
      const i = SEVERITIES.indexOf(state.sel.severity);
      return setSeg("severity", SEVERITIES[(i + 1) % SEVERITIES.length]);
    }
    case " ": e.preventDefault(); return submit();
    case "u": return undo();
    case "s": return skip();
    case "c": e.preventDefault(); return $("#correction")?.focus();
    case "x": e.preventDefault(); return $("#note")?.focus();
    case "e": return $("#chips-errors")?.scrollIntoView({behavior:"smooth", block:"center"});
    case "?": return showGuideline();
  }
});

window.PQA = { api, esc, toast, state };
if (state.annotator) claimNext();
```

Note: none of the ERRORS letter keys collide with action keys — the ERRORS list uses `m o a t n g r p`; `s`/`u`/`c`/`x`/`v`/`e` are actions (the `err && e.key !== "s" && e.key !== "u"` guard is belt-and-suspenders and effectively inert).

- [ ] **Step 4: Browser smoke** — `.venv/bin/python run.py --demo`, open `http://localhost:8420`: start as `chao`, annotate 2 records with keyboard only (`0 Space` for a clean one; `g v v v 1 x<type note>Esc Space` style for an error), verify timer, undo, `?` modal, theme toggle. Fix any console errors.
- [ ] **Step 5: Commit**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: SPA shell + keyboard-first Annotate tab

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Review tab

**Files:**
- Create: `propioqa/static/review.js`

**Interfaces:**
- Consumes: `window.PQA`, `GET /api/review/queue`, `POST /api/review/{id}`.
- Produces: `window.renderReview()`.

- [ ] **Step 1: Write `static/review.js`**

```javascript
/* Review tab: three-way comparison (human vs judge vs LF) + adjudication. */
"use strict";
(() => {
  const { api, esc, toast } = window.PQA;
  const SEVS = ["neutral", "minor", "major", "critical"];
  const ETYPES = ["no_error","mistranslation","omission","addition","terminology",
                  "number_unit","negation_polarity","grammar","punctuation"];
  let queue = [], selIdx = -1;

  window.renderReview = async function () {
    const v = document.querySelector("#view-review");
    try { queue = await api("/review/queue"); }
    catch (e) { v.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    if (!queue.length) { v.innerHTML = `<div class="empty">Review queue is empty ✓</div>`; return; }
    v.innerHTML = `<div class="review-grid">
      <div class="card" id="rq-list" style="padding:6px">
        ${queue.map((q, i) => `<div class="rq-item" data-i="${i}">
           <b>${esc(q.task.id)}</b> · ${esc(q.annotation.annotator)}
           <span class="dis">Δ ${q.disagreement}</span></div>`).join("")}
      </div>
      <div class="card" id="rq-detail"><div class="empty">Select an item.</div></div></div>`;
    v.querySelector("#rq-list").onclick = e => {
      const it = e.target.closest(".rq-item"); if (!it) return;
      selIdx = +it.dataset.i;
      v.querySelectorAll(".rq-item").forEach(x => x.classList.toggle("sel", x === it));
      renderDetail(queue[selIdx]);
    };
  };

  function col(title, body) { return `<div class="col"><h4>${title}</h4>${body}</div>`; }
  function labelBlock(x) {
    return `severity <b>${esc(x.worst_severity)}</b><br>
            types ${x.error_types.map(esc).join(", ") || "—"}<br>
            ${x.adequacy !== undefined ? `adequacy ${x.adequacy}` : ""}
            ${x.note ? `<br><i>${esc(x.note)}</i>` : ""}`;
  }

  function renderDetail(q) {
    const d = document.querySelector("#rq-detail");
    const judge = q.judge, disagree = judge && judge.worst_severity !== q.annotation.worst_severity;
    d.innerHTML = `
      <div class="textpanel"><div class="lbl">Source</div><div class="txt">${esc(q.task.source)}</div></div>
      <div class="textpanel"><div class="lbl">Hypothesis</div><div class="txt">${esc(q.task.hypothesis)}</div></div>
      <div class="threeway">
        ${col("Human · " + esc(q.annotation.annotator), labelBlock(q.annotation))}
        ${col("Judge" + (judge?.is_mock ? " · MOCK" : ""),
              judge ? `<span class="${disagree ? "disagree" : ""}">${labelBlock(judge)}</span>
                       <br>conf ${judge.confidence}` : "—")}
        ${col("LF lint", q.lf_errors.length
              ? q.lf_errors.map(f => `⚠ ${esc(f.lf)}<br><i>${esc(f.evidence)}</i>`).join("<br>") : "clean")}
      </div>
      <div class="actions">
        <button class="primary" id="rv-approve">Approve</button>
        <button id="rv-overturn-toggle">Overturn…</button>
      </div>
      <div id="rv-form" class="hidden" style="margin-top:12px">
        <div class="row">
          <div class="field"><label>severity</label>
            <select id="rv-sev">${SEVS.map(s=>`<option>${s}</option>`).join("")}</select></div>
          <div class="field"><label>adequacy</label>
            <select id="rv-adq">${[1,2,3,4,5].map(n=>`<option>${n}</option>`).join("")}</select></div>
          <div class="field"><label>fluency</label>
            <select id="rv-flu">${[1,2,3,4,5].map(n=>`<option>${n}</option>`).join("")}</select></div>
        </div>
        <div class="chips">${ETYPES.map(t=>`<button class="chip" data-v="${t}">${t}</button>`).join("")}</div>
        <div class="field"><label>case note → guideline appendix (required)</label>
          <textarea id="rv-case" rows="2"></textarea></div>
        <button class="primary" id="rv-overturn">Submit overturn</button>
      </div>`;
    const chosen = new Set();
    d.querySelector(".chips").onclick = e => {
      const c = e.target.closest(".chip"); if (!c) return;
      const t = c.dataset.v;
      if (t === "no_error") { chosen.clear(); chosen.add(t); }
      else { chosen.delete("no_error"); chosen.has(t) ? chosen.delete(t) : chosen.add(t); }
      d.querySelectorAll(".chip").forEach(x => x.classList.toggle("sel", chosen.has(x.dataset.v)));
    };
    d.querySelector("#rv-approve").onclick = () => verdict(q, { reviewer: "lead", verdict: "approved" });
    d.querySelector("#rv-overturn-toggle").onclick = () =>
      d.querySelector("#rv-form").classList.toggle("hidden");
    d.querySelector("#rv-overturn").onclick = () => {
      const caseNote = d.querySelector("#rv-case").value.trim();
      if (!chosen.size) return toast("pick replacement error types", true);
      if (!caseNote) return toast("case note required — it feeds the guideline", true);
      const sev = d.querySelector("#rv-sev").value;
      verdict(q, { reviewer: "lead", verdict: "overturned", case_note: caseNote,
        replacement: { error_types: [...chosen], worst_severity: sev,
          adequacy: +d.querySelector("#rv-adq").value, fluency: +d.querySelector("#rv-flu").value,
          correction: "", note: sev === "critical" ? caseNote : "" } });
    };
  }

  async function verdict(q, body) {
    try { await api(`/review/${q.annotation.id}`, body); toast("verdict recorded ✓"); window.renderReview(); }
    catch (e) { toast(e.message, true); }
  }
})();
```

- [ ] **Step 2: Browser smoke** — restart `run.py --demo`; Review tab shows maria's 3 items sorted by Δ; approve one (disappears), overturn another with a case note (t002 is ideal: judge MOCK missed the `sin`-fooled negation — the storyline writes itself).
- [ ] **Step 3: Commit**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: review tab with three-way comparison + adjudication

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Ops Dashboard tab

**Files:**
- Create: `propioqa/static/dash.js`

**Interfaces:**
- Consumes: `window.PQA`, `GET /api/stats/*`, `GET /api/batches`, `POST /api/routing/build`, `POST /api/export`.
- Produces: `window.renderDashboard(startPoll)`, `window.stopDashPoll()`.

- [ ] **Step 0: Read the dataviz skill** — invoke `Skill(dataviz)` before writing the heatmap/sparkline code; keep the single-hue sequential fill below unless the skill's guidance contradicts it (then adapt colors only, not structure).

- [ ] **Step 1: Write `static/dash.js`**

```javascript
/* Ops dashboard: throughput, golden accuracy, agreement, error×arm matrix, routing builder. */
"use strict";
(() => {
  const { api, esc, toast } = window.PQA;
  let pollId = null;

  window.stopDashPoll = () => { clearInterval(pollId); pollId = null; };
  window.renderDashboard = async function (startPoll) {
    const v = document.querySelector("#view-dashboard");
    let o, m, a, g, b;
    try {
      [o, m, a, g, b] = await Promise.all([api("/stats/overview"), api("/stats/matrix"),
        api("/stats/annotators"), api("/stats/agreement"), api("/batches")]);
    } catch (e) { v.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    v.innerHTML = `
      <div class="cards">
        ${stat(o.n_tasks, "tasks")} ${stat(o.n_annotations, "annotations")}
        ${stat(o.n_batches, "batches")}
        ${stat(o.judge.mode + (o.judge.reachable ? "" : " ⚠"), "judge")}
      </div>
      <div class="card"><h3>Throughput — last ${o.recent_elapsed_ms.length} annotations</h3>
        ${sparkline(o.recent_elapsed_ms)}</div>
      <div class="card"><h3>Error type × latency arm</h3>${matrix(m)}
        <div class="anchor-note">rate = share of tasks in arm carrying the error type
        (human label first, judge fallback; sources: human ${m.sources.human} / judge ${m.sources.judge})</div></div>
      <div class="card"><h3>Annotators</h3><table><tr><th>annotator</th><th>submitted</th>
        <th>avg time</th><th>golden</th></tr>
        ${a.map(s => `<tr><td>${esc(s.annotator)}</td><td>${s.n_submitted}</td>
          <td>${(s.avg_elapsed_ms/1000).toFixed(1)}s</td>
          <td>${s.golden_total ? s.golden_passed + "/" + s.golden_total : "—"}</td></tr>`).join("")}
      </table></div>
      <div class="card"><h3>Agreement</h3>
        ${g.pairwise.length ? g.pairwise.map(p => `<div>${esc(p.a)} × ${esc(p.b)} (n=${p.n}):
          κ<sub>sev</sub> <b>${p.kappa_severity}</b> · κ<sub>bin</sub> <b>${p.kappa_binary}</b></div>`).join("")
          : `<div class="anchor-note">pairwise κ needs ≥3 shared tasks between two annotators</div>`}
        ${g.judge_human ? `<div style="margin-top:6px">judge × human (n=${g.judge_human.n}):
          κ<sub>sev</sub> <b>${g.judge_human.kappa_severity}</b> ·
          κ<sub>bin</sub> <b>${g.judge_human.kappa_binary}</b></div>` : ""}</div>
      <div class="card"><h3>Batches & routing</h3>
        <table><tr><th>batch</th><th>tasks</th><th>overlap</th><th>suggestions</th></tr>
        ${b.map(x => `<tr><td>${esc(x.name)}</td><td>${x.n_tasks}</td><td>${x.overlap}</td>
          <td>${x.show_suggestions ? "ON" : "off"}</td></tr>`).join("")}</table>
        <div class="form-inline" style="margin-top:10px">
          <label>route top</label><input type="number" id="rt-n" value="10" min="1">
          <select id="rt-signal"><option value="judge_confidence">lowest judge confidence</option>
            <option value="lf_conflict">most LF errors</option></select>
          <button class="primary" id="rt-build">Build routing batch</button>
          <button id="ex-btn">Export snapshot</button>
        </div></div>`;
    v.querySelector("#rt-build").onclick = async () => {
      try {
        const r = await api("/routing/build",
          { top_n: +v.querySelector("#rt-n").value, signal: v.querySelector("#rt-signal").value });
        toast(`${r.name}: ${r.n} tasks — suggestions ON for this batch`);
        window.renderDashboard();
      } catch (e) { toast(e.message, true); }
    };
    v.querySelector("#ex-btn").onclick = async () => {
      try { const r = await api("/export", {}); toast(`${r.version} → sha ${r.sha256.slice(0, 12)}…`); }
      catch (e) { toast(e.message, true); }
    };
    if (startPoll && !pollId) pollId = setInterval(() => window.renderDashboard(), 5000);
  };

  const stat = (n, l) => `<div class="card stat"><div class="num">${esc(n)}</div><div class="lbl">${l}</div></div>`;

  function sparkline(xs) {
    if (!xs.length) return `<div class="anchor-note">no timed annotations yet</div>`;
    const max = Math.max(...xs), w = 600, h = 40;
    const pts = xs.map((x, i) => `${(i / Math.max(1, xs.length - 1)) * w},${h - (x / max) * (h - 4)}`);
    return `<svg class="sparkline" viewBox="0 0 ${w} ${h + 4}" preserveAspectRatio="none">
      <polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${pts.join(" ")}"/></svg>`;
  }

  function matrix(m) {
    if (!m.arms.length) return `<div class="anchor-note">no labels yet</div>`;
    const cols = `140px repeat(${m.arms.length}, 1fr)`;
    let html = `<div class="matrix" style="grid-template-columns:${cols}">`;
    html += `<div class="hcell"></div>` + m.arms.map(a =>
      `<div class="hcell" style="text-align:center">${esc(a)} (n=${m.n[a]})</div>`).join("");
    for (const e of m.error_types) {
      html += `<div class="hcell">${esc(e)}</div>`;
      for (const a of m.arms) {
        const r = m.cells[a][e];
        const alpha = r > 0 ? 0.10 + 0.75 * r : 0.03;
        html += `<div class="cell" style="background:rgba(91,141,255,${alpha.toFixed(2)})">
                 ${(r * 100).toFixed(0)}%</div>`;
      }
    }
    return html + `</div>`;
  }
})();
```

- [ ] **Step 2: Browser smoke** — Dashboard shows: 30 tasks, matrix with wait1 > wait3 > offline error gradient (judge-fallback), maria in annotators, judge badge `mock`. Click **Build routing batch** → toast; switch to Annotate as a new annotator id, claim from the routing batch (`batch_id` — via the batches table note or claim default order), verify suggestion chips render with MOCK badge.
- [ ] **Step 3: Commit**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: ops dashboard (matrix heatmap, agreement, routing builder)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: README + full verification + repo push + vault docs

**Files:**
- Create: `propioqa/README.md`
- Modify: vault `future_direction/PropioQA_Workbench/`（新增 `01_使用说明_与演示剧本.md`）、vault `00_INDEX.md`、spec 文档追加实施偏差记录

- [ ] **Step 1: Write `propioqa/README.md`** (English; no vault/job-hunt content, no wikilinks)

```markdown
# PropioQA Workbench

A self-hosted, keyboard-first translation-QA annotation workbench.
FastAPI + SQLite + vanilla JS — no ORM, no build chain, three runtime deps.

**Demo-grade by design, with production notes inline. All bundled data is synthetic
(EN→ES medication instructions written for this demo). No PHI anywhere.**

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --demo        # → http://localhost:8420
```

`--demo` boots a throwaway DB with 30 synthetic tasks (12 planted errors), a golden
set, a second annotator's labels, and pre-computed mock-judge verdicts.

## What it embodies (one lesson per platform)

| Lesson from | Mechanism here |
|---|---|
| Prodigy | keyboard-first flow, per-record timing |
| Argilla | suggestions vs responses; task distribution with lease |
| Snorkel | labeling functions as import-time lint |
| Labelbox | consensus (overlap) + benchmark (golden) quality modes |
| Scale | reviewer workflow, QA rubric as single source of truth |
| Humanloop | judge/eval loop feeding human feedback back to data |

The one non-negotiable design rule: **batch policy is enforced server-side** —
a golden-collection batch's claim response never even contains judge/LF fields
(anchoring discipline), while routing batches carry them explicitly.

## Real judge

```bash
PROPIOQA_JUDGE=openai PROPIOQA_JUDGE_BASE_URL=http://localhost:8000/v1 \
  .venv/bin/python run.py --demo
```
Any OpenAI-compatible endpoint works. If the judge is down, annotation is untouched —
the judge is a first-pass filter, never the final arbiter.

## 5-minute demo script

1. **Annotate** (2 min): start as `chao`, label t001 with `0 Space` (clean),
   t002 by hand — negation dropped, `g v→critical, 2, ⇧4, x note, Space`.
   Point out: no machine hints on screen, and why (anchoring).
2. **Dashboard** (1 min): error×arm matrix — low-latency arm degrades to
   omission/negation errors; golden accuracy per annotator; κ panel.
3. **Routing loop** (2 min): Build routing batch from lowest judge confidence →
   claim from it as `chao` → suggestion chips (MOCK-labeled) now visible →
   Review tab → overturn one with a case note that feeds the guideline.

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Production upgrade path (deliberately not built)

SSO/RBAC instead of self-reported ids · Postgres + row locks at >10 annotators ·
SSE instead of 5s polling · object storage for audio · honeypot rotation scheduling ·
per-language golden sets · Argilla/Label Studio as the UI layer with this API as
the quality/routing brain.
```

- [ ] **Step 2: Full verification**

```bash
.venv/bin/python -m pytest -q                       # expect: all green
.venv/bin/python run.py --demo &                    # boot
sleep 2 && curl -s localhost:8420/api/health        # {"status":"ok",...}
curl -s -X POST localhost:8420/api/claim -H 'Content-Type: application/json' \
  -d '{"annotator":"smoke"}' | head -c 400          # task JSON, no "suggestions" key
kill %1
```
Then a real browser pass of the 5-minute demo script above (all three tabs). If the `run` skill or browser tooling is available, drive it; otherwise do it manually and record any console error as a bug to fix before commit.

- [ ] **Step 3: Commit + push**

```bash
git -C ~/Documents/Propio_Prep_Materials add propioqa
git -C ~/Documents/Propio_Prep_Materials commit -m "propioqa: README with demo script; final polish

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git -C ~/Documents/Propio_Prep_Materials push
```

- [ ] **Step 4: Vault docs**（中文）— Create `/Users/caochao/Documents/Obsidian Vault/OnePointThreeAcres/Propio/future_direction/PropioQA_Workbench/01_使用说明_与演示剧本.md`:

```markdown
---
tags:
  - 域/面试
  - 类型/教程
  - 状态/活跃
---

# PropioQA Workbench · 使用说明与演示剧本

> 代码：`~/Documents/Propio_Prep_Materials/propioqa/`（私有 repo 内）。设计定案见 [[00_设计文档_PropioQA_Workbench]]。
> 一条命令起动：`cd ~/Documents/Propio_Prep_Materials/propioqa && .venv/bin/python run.py --demo` → http://localhost:8420

## 三个 tab 速览
- **Annotate**：键盘流标注（1-5 评分 / ⇧1-5 流畅度 / v 切 severity / 字母键勾错误类型 / 0=无错 / 空格提交 / u 撤回 / s 跳过 / ? 看 guideline）。golden 采集批界面纯净（服务端强制，连 LF 警告都不发）——这就是 anchoring 纪律的代码化。
- **Review**：人标 vs judge vs LF 三方对比、分歧排序、approve/overturn 仲裁，case note 回流 guideline。
- **Dashboard**：error×arm 热力矩阵（首席交付物可视化）、golden 正确率、κ 面板、路由批生成器、版本化导出。

## 5 分钟面试演示剧本（英文讲法见 repo README "5-minute demo script"）
1. 标 2 条给面试官看键盘流与"无机器提示"的设计理由（anchoring）
2. Dashboard 讲矩阵：低延迟 arm 的质量损失以 omission/negation 为主
3. 现场建 routing 批 → 再 claim 出现 MOCK judge 建议 → Review 里 overturn 一条并写判例——闭环讲完
4. 收尾一句：judge 是 first-pass filter 不是 final arbiter；接真 judge 只要两个环境变量（vLLM 隧道）

## 口径红线
- 本工具是 **demo-grade 自研实物**，可以说 "I built this workbench myself"（已亲手跑通前提下）；不得说它是生产系统或声称运营过商业平台。
- demo 数据全合成（自写 EN→ES 用药句），无任何 PHI；被问合规即讲"真 PHI 场景=这套自托管架构 + BAA 边界"的设计论点。
- 与 [[实验策划_AnnotationQualityBench]] 的分工：AQB=会用平台（Argilla 实操），本工具=懂平台内部（custom internal tooling 答案）。

## 实施偏差记录（相对 spec）
1. `batches.overlap` 字段新增（demo 批 overlap=2 喂 κ 面板）
2. 第 9 张表 `reviews`（仲裁需可查询）
3. LF 结果存 `tasks.lf_flags` JSON 列
4. 前端错误类型字母键常驻生效（比 spec 的 'e' 面板少一次按键；'e' 保留为定位错误行）
```

Also: append the same 4-item 实施偏差记录 to the spec doc `00_设计文档_PropioQA_Workbench.md` as a final `## 十三、实施偏差记录` section (Read the file first, Edit to append), and add to vault `00_INDEX.md`'s future_direction section (Read it first — it was edited recently — then Edit) this bullet after the `标注平台入门指南/` entry:

```
- `PropioQA_Workbench/` — 🆕 2026-07-18：自研标注工作台（"custom internal tooling" 实物）：[[00_设计文档_PropioQA_Workbench]]（spec 定案）· [[01_使用说明_与演示剧本]]（起动命令 + 5 分钟演示剧本 + 口径红线）；代码在 `~/Documents/Propio_Prep_Materials/propioqa/`（FastAPI+SQLite+vanilla 前端，三 tab：键盘流标注/三方审核/运营面板；batch policy 服务端强制 anchoring 纪律；MockJudge 离线可 demo，两个环境变量切真 judge）
```

- [ ] **Step 5: Final task-list check** — all plan checkboxes ticked; `pytest -q` green one last time; report completion.

---

## Plan Self-Review (completed by plan author)

1. **Spec coverage**: §1.2 验收清单 → Tasks 8/9/10/11/12（含浏览器冒烟）；八表+增补 → Task 1；API 面 → Task 8；三 tab → Tasks 9-11；LF → Task 3；judge/Mock → Task 5；导入导出 → Tasks 6-7；测试计划 → 各任务 TDD 步骤；红线 → Tasks 6 (合成数据)/12 (README/口径)。Gap fixed inline: spec §5.1 未给 severity 快捷键 → 定为 `v` 循环；error_arm_matrix 需 judge fallback 才能让 demo 面板开箱有形 → Task 4 实现含 fallback + sources 计数。
2. **Placeholder scan**: 无 TBD/TODO；所有代码步骤含完整代码；style.css 中的一处书写噪音已在任务内注明修正。
3. **Type consistency**: `claim → {assignment_id, task_id, batch:{show_suggestions}}` 贯穿 tasks.py/main.py/app.js；`suggestions.{lf, judge{error_types, worst_severity, adequacy, rationale, confidence, model, is_mock}}` 贯穿 main.py/app.js/review.js；`SEVERITIES` 顺序（mild→severe）被 quality.py tie-break 与前端 `v` 循环共同依赖——两处一致。
```
