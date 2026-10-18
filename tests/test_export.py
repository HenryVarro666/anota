import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest
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

def test_snapshot_guideline_version_scoped_to_filtered_batch():
    db = Database(":memory:"); b1_id = seed(db)
    # Create second batch with v2.0
    b2_id = db.execute("INSERT INTO batches(name, guideline_version) VALUES('b2','v2.0')")
    db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis,metadata) VALUES(?,?,?,?,?)",
               ("t2", b2_id, "s", "h", json.dumps({"arm": "wait1"})))
    add_ann(db, "t2", "chao", ["omission"], "major")
    # Unfiltered snapshot should use first batch's version (v1.0 default)
    snap_unfiltered = export.build_snapshot(db)
    assert snap_unfiltered["guideline_version"] == "v1.0"
    # Filtered snapshot for b2 should use b2's version (v2.0)
    snap_filtered = export.build_snapshot(db, {"batch_id": b2_id})
    assert snap_filtered["guideline_version"] == "v2.0"
    assert len(snap_filtered["items"]) == 1
    assert snap_filtered["items"][0]["task"]["id"] == "t2"


def test_concurrent_exports_have_unique_versions_and_intact_content(tmp_path):
    db = Database(":memory:"); seed(db)
    add_ann(db, "t1", "chao", ["omission"], "major")
    start = threading.Barrier(8)

    def write(_):
        start.wait(timeout=10)
        return export.export_snapshot(db, tmp_path, actor="concurrent-test")

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(write, range(8)))
    assert {result["version"] for result in results} == {f"v{i}" for i in range(1, 9)}
    assert len({result["path"] for result in results}) == 8
    assert len({result["sha256"] for result in results}) == 1
    for result in results:
        saved = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        assert saved.pop("version") == result["version"]
        assert saved.pop("sha256") == result["sha256"]
        assert hashlib.sha256(export.canonical_json(saved).encode("utf-8")).hexdigest() == result["sha256"]
    assert db.one("SELECT COUNT(*) n FROM exports")["n"] == 8
    assert db.one("SELECT COUNT(*) n FROM audit_log WHERE action='export'")["n"] == 8
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_atomic_export_does_not_publish_file_or_database_record(tmp_path, monkeypatch):
    db = Database(":memory:"); seed(db)

    def fail_replace(*args):
        raise OSError("simulated publication failure")

    monkeypatch.setattr(export.os, "replace", fail_replace)
    with pytest.raises(OSError, match="publication failure"):
        export.export_snapshot(db, tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert db.one("SELECT COUNT(*) n FROM exports")["n"] == 0
    assert db.one("SELECT COUNT(*) n FROM audit_log WHERE action='export'")["n"] == 0
