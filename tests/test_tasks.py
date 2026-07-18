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

def test_undo_releases_current_open_assignment():
    db, bid = make_db()
    c0 = tasks.claim(db, "chao")
    tasks.submit(db, "chao", c0["assignment_id"], PAYLOAD)
    c1 = tasks.claim(db, "chao")
    assert c1["task_id"] == "t001"
    u = tasks.undo(db, "chao")
    assert u["task_id"] == c0["task_id"]
    assert db.one("SELECT COUNT(*) n FROM assignments WHERE id=?",
                  (c1["assignment_id"],))["n"] == 0
    tasks.submit(db, "chao", u["assignment_id"], PAYLOAD)  # free chao up again
    c2 = tasks.claim(db, "chao")
    assert c2["task_id"] == "t001"  # released task is claimable again

def test_claim_resume_respects_batch_id():
    db, bid = make_db()
    bid2 = db.execute("INSERT INTO batches(name, overlap) VALUES('b2', 1)")
    db.execute("INSERT INTO tasks(id, batch_id, source, hypothesis) VALUES('x000', ?, 'sx', 'hx')",
               (bid2,))
    c1 = tasks.claim(db, "chao")  # opens assignment in batch 1
    assert c1["batch_id"] == bid
    c2 = tasks.claim(db, "chao", batch_id=bid2)
    assert c2["task_id"] == "x000"  # resume skips the batch-1 open assignment
    assert db.one("SELECT status FROM assignments WHERE id=?",
                  (c1["assignment_id"],))["status"] == "assigned"  # untouched

def test_resume_refreshes_lease():
    db, bid = make_db(n_tasks=1)
    c = tasks.claim(db, "chao")
    db.execute("UPDATE assignments SET lease_expires_at=datetime('now','+1 minute') WHERE id=?",
               (c["assignment_id"],))
    tasks.claim(db, "chao")  # resume path
    row = db.one("SELECT (lease_expires_at > datetime('now','+25 minutes')) ok "
                 "FROM assignments WHERE id=?", (c["assignment_id"],))
    assert row["ok"] == 1

def test_reap_is_audited():
    db, bid = make_db(n_tasks=1)
    c = tasks.claim(db, "chao")
    db.execute("UPDATE assignments SET lease_expires_at=datetime('now','-1 minute') WHERE id=?",
               (c["assignment_id"],))
    tasks.claim(db, "maria")  # triggers reap
    rows = db.query("SELECT * FROM audit_log WHERE action='lease_expired'")
    assert len(rows) == 1
    assert rows[0]["actor"] == "system"

def test_skip_foreign_assignment_rejected():
    db, bid = make_db()
    c = tasks.claim(db, "chao")
    with pytest.raises(LookupError):
        tasks.skip(db, "maria", c["assignment_id"], "reason")
