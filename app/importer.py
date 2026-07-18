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
