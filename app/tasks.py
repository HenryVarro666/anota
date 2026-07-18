"""Task distribution: claim -> lease(30min) -> submit/skip; undo reopens.
Assignments are the mutable state machine; annotations stay append-only."""
import json

LEASE_MINUTES = 30


def _reap_expired(db):
    expired = db.query(
        "SELECT id, task_id, annotator FROM assignments WHERE status='assigned' "
        "AND lease_expires_at < datetime('now')")
    for row in expired:
        db.audit("system", "lease_expired", "assignment", row["id"],
                  {"task_id": row["task_id"], "annotator": row["annotator"]})
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
        resume_clause, resume_params = "", [annotator]
        if batch_id is not None:
            resume_clause = "AND t.batch_id=?"
            resume_params.append(batch_id)
        open_row = db.one(f"""
            SELECT a.id, a.task_id FROM assignments a JOIN tasks t ON t.id=a.task_id
            WHERE a.annotator=? AND a.status='assigned' {resume_clause}
            ORDER BY a.id LIMIT 1""", tuple(resume_params))
        if open_row:
            db.execute(
                "UPDATE assignments SET lease_expires_at=datetime('now', ?) WHERE id=?",
                (f"+{LEASE_MINUTES} minutes", open_row["id"]))
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
        open_row = db.one(
            "SELECT id, task_id FROM assignments WHERE annotator=? AND status='assigned' "
            "ORDER BY id LIMIT 1", (annotator,))
        if open_row:
            db.execute("DELETE FROM assignments WHERE id=?", (open_row["id"],))
            db.audit(annotator, "release", "assignment", open_row["id"],
                      {"task_id": open_row["task_id"]})
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
