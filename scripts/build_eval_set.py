"""Freeze existing synthetic golden items and probe items; no model is consulted."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import Database
from app.importer import import_demo
from app.perturb import build_probe


def main():
    db = Database(":memory:")
    import_demo(db, ROOT / "data")
    bid = db.one("SELECT id FROM batches ORDER BY id LIMIT 1")["id"]
    build_probe(db, bid, per_type=3)
    rows = db.query("SELECT t.*,g.answer FROM tasks t JOIN golden_answers g ON g.task_id=t.id ORDER BY t.rowid")
    output = ROOT / "evals/data/translation_probe_v1.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    seen = {}
    for row in rows:
        key = (row["source"], row["hypothesis"])
        if key in seen:
            previous = seen[key]
            if previous["gold"] != json.loads(row["answer"]):
                raise ValueError("Identical text has contradictory labels")
            previous.setdefault("aliases", []).append(row["id"])
            continue
        records.append({"id": row["id"], "parent_id": row["id"].split("::")[0],
                        "source": row["source"], "hypothesis": row["hypothesis"],
                        "language_pair": "en-es", "gold": json.loads(row["answer"]),
                        "label_origin": "synthetic_perturbation" if "::" in row["id"] else "existing_demo_golden"})
        seen[key] = records[-1]
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    db.conn.close()
    print(f"Frozen {len(records)} synthetic examples at {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
