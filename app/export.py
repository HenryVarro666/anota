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
