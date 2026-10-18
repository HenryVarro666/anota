"""Observable speech review priorities; no references or corrections enter scoring.

The original QC priority function is shared with the frozen study. Model failure
falls back for the whole hinted queue so QC units never mix with edits/second.
Blind batches retain their original slots and expose no ranking evidence.
"""
import hashlib
import json

from speechqc.priority import TIE_SALT, priority_score

SORTS = ("ml", "qc", "fifo")


def _score_rows(rows):
    # Optional ML dependencies are loaded by the runtime only on first use.
    try:
        from speechrank.runtime import score_rows
        return score_rows(rows)
    except (ImportError, OSError, ValueError, RuntimeError):
        return [{"status": "unavailable", "reason": "ranking_unavailable"} for _ in rows]


def _records(db, candidates):
    wanted = {row["id"] for row in candidates if row["task_type"] == "speech"}
    records = {}
    for row in db.query("SELECT s.task_id,s.provenance,t.source FROM speech_tasks s "
                        "JOIN tasks t ON t.id=s.task_id"):
        if row["task_id"] not in wanted:
            continue
        source = json.loads(row["provenance"])
        report = source.get("audio_analysis") or {}
        duration = source["audio_duration_s"]
        coverage = report.get("coverage") or {}
        complete = report.get("status") == "complete" and coverage.get("status") == "ok"
        try:
            qc_score = priority_score(report.get("issues", []), duration)
        except (KeyError, TypeError, ValueError):
            qc_score, complete = 0.0, False
        records[row["task_id"]] = {
            "source": row["source"], "duration_s": duration, "_provenance": source,
            "_tie": hashlib.sha256((TIE_SALT + source["item_id"]).encode()).hexdigest(),
            "ranking": {"method": "qc", "arm": None, "density": None,
                        "model_id": None, "reason": None, "qc_score": qc_score,
                        "failure_tier": int(not complete)},
        }
    return records


def prepare(db, candidates, sort="ml"):
    """Return reordered candidate rows and safe evidence keyed by task ID."""
    if sort not in SORTS:
        raise ValueError("unknown speech queue sort")
    records = _records(db, candidates)
    hinted = [row for row in candidates if row["task_type"] == "speech" and row["show_suggestions"]]
    effective = sort
    if sort == "ml" and hinted:
        predictions = _score_rows([records[row["id"]]["_provenance"] for row in hinted])
        if len(predictions) != len(hinted):
            raise ValueError("speech ranking returned misaligned results")
        failures = [row for row in predictions if row["status"] != "ok"]
        if failures:
            effective = "qc"
            reason = failures[0]["reason"]
            for candidate in hinted:
                records[candidate["id"]]["ranking"]["reason"] = reason
        else:
            for candidate, prediction in zip(hinted, predictions, strict=True):
                records[candidate["id"]]["ranking"].update(
                    method="ml", arm=prediction["arm"], density=prediction["density"],
                    model_id=prediction["model_id"],
                    bundle_fingerprint=prediction["bundle_fingerprint"],
                    feature_schema=prediction["feature_schema"])
    if effective == "fifo":
        for candidate in hinted:
            records[candidate["id"]]["ranking"]["method"] = "fifo"
    else:
        def key(candidate):
            record = records[candidate["id"]]
            evidence = record["ranking"]
            value = evidence["density"] if effective == "ml" else evidence["qc_score"]
            return (-evidence["failure_tier"], -value, record["_tie"], candidate["id"])
        ordered = iter(sorted(hinted, key=key))
        candidates = [next(ordered) if row["task_type"] == "speech" and row["show_suggestions"]
                      else row for row in candidates]
    return candidates, records


def review_queue(db, annotator, batch_id=None, sort="ml", limit=200):
    from .tasks import _claim_candidates, _reap_expired
    with db.lock:
        _reap_expired(db)
        candidates = [row for row in _claim_candidates(db, annotator, batch_id)
                      if row["task_type"] == "speech"]
        ordered, records = prepare(db, candidates, sort)
        items = []
        counts = {"ml_confidence": 0, "ml_legacy": 0, "qc": 0, "fifo": 0, "blind": 0}
        for candidate in ordered:
            record = records[candidate["id"]]
            item = {"task_id": candidate["id"], "batch_id": candidate["batch_id"],
                    "batch_name": candidate["batch_name"], "source": record["source"],
                    "duration_s": record["duration_s"]}
            if candidate["show_suggestions"]:
                evidence = record["ranking"]
                item["ranking"] = evidence
                category = "ml_" + evidence["arm"] if evidence["method"] == "ml" else evidence["method"]
                counts[category] += 1
            else:
                counts["blind"] += 1
            items.append(item)
        return {"items": items[:limit], "total": len(items), "sort": sort, "counts": counts}
