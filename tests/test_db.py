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
