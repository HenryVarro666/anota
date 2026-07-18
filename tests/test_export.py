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
