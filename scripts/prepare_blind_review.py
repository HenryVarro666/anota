"""Prepare an Anota batch for two reviewers, without provisional labels or predictions."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import Database
from app.importer import import_jsonl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="new SQLite path; existing files are never overwritten")
    parser.add_argument("--input", default=str(ROOT / "evals/data/holdout_v2.jsonl"))
    args = parser.parse_args()
    db_path = Path(args.db).resolve()
    if db_path.exists():
        parser.error("database exists; choose a fresh path")
    source = Path(args.input)
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    random.Random(20261017).shuffle(rows)
    tasks = [{"id": "blind-" + row["id"], "source": row["source"], "hypothesis": row["hypothesis"],
              "metadata": {"source_dataset_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}}
             for row in rows]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    task_file = db_path.with_suffix(".input.jsonl")
    if task_file.exists():
        parser.error("input export exists; choose a fresh database path")
    task_file.write_text("".join(json.dumps(task, ensure_ascii=False) + "\n" for task in tasks), encoding="utf-8")
    db = Database(str(db_path))
    try:
        result = import_jsonl(db, task_file, "generic", "Holdout v2: independent reference review",
                              lang_profile="en-es", show_suggestions=False, overlap=2, actor="blind-review-preparation")
        assert db.one("SELECT COUNT(*) n FROM golden_answers")["n"] == 0
        assert db.one("SELECT COUNT(*) n FROM judge_results")["n"] == 0
    finally:
        db.conn.close()
    print(json.dumps({"batch": result, "overlap": 2, "gold_loaded": False, "predictions_loaded": False}))
    print("Start with: python scripts/run_local.py --db " + str(db_path) + " --port 8421")


if __name__ == "__main__":
    main()
