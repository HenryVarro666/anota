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
