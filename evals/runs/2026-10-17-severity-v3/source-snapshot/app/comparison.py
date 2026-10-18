"""Paired comparison metrics. Each pass retains its own unique-item denominator."""
from .evaluation import score_pass, ratio


def measured_score(records, examples):
    return score_pass([dict(row, phase="timed") for row in records], examples)


def correct(verdict, gold, field):
    if verdict is None:
        return False
    if field == "binary":
        return (verdict["error_types"] == ["no_error"]) == (gold["error_types"] == ["no_error"])
    if field == "types":
        return set(verdict["error_types"]) == set(gold["error_types"])
    return verdict["worst_severity"] == gold["worst_severity"]


def comparison_summary(records, holdout, development):
    expected = len(holdout) * 4 + len(development)
    timed = [r for r in records if r["phase"] == "holdout"]
    development_rows = [r for r in records if r["phase"] == "development"]
    result = {"holdout_unique_items": len(holdout), "development_unique_items": len(development),
              "completed_measured_requests": len(timed) + len(development_rows),
              "expected_measured_requests": expected, "complete": len(timed) + len(development_rows) == expected,
              "arms": {}, "paired": {}, "gates": {},
              "caveat": "Novel synthetic source parents; labels authored by the same agent before inference, not independently expert-adjudicated. Repeats are dependent."}
    for arm in ("baseline", "candidate"):
        arm_rows = [r for r in timed if r["arm"] == arm]
        result["arms"][arm] = {"passes": {}, "pooled_timing": measured_score(arm_rows, holdout)["latency_seconds"]}
        for repeat in (1, 2):
            result["arms"][arm]["passes"][str(repeat)] = measured_score([r for r in arm_rows if r["repeat"] == repeat], holdout)
        pairs = [[r["verdict"] for r in arm_rows if r["task_id"] == e["id"]] for e in holdout]
        valid_pairs = [p for p in pairs if len(p) == 2 and all(p)]
        stable = sum(set(p[0]["error_types"]) == set(p[1]["error_types"]) and
                     p[0]["worst_severity"] == p[1]["worst_severity"] for p in valid_pairs)
        result["arms"][arm]["repeat_label_agreement"] = ratio(stable, len(valid_pairs))
    for repeat in (1, 2):
        index = {(r["arm"], r["task_id"]): r for r in timed if r["repeat"] == repeat}
        result["paired"][str(repeat)] = {}
        for field in ("binary", "types", "severity"):
            counts = {"candidate_wins": 0, "candidate_losses": 0, "ties": 0}
            for example in holdout:
                b, c = index.get(("baseline", example["id"])), index.get(("candidate", example["id"]))
                if b is None or c is None:
                    continue
                bc, cc = correct(b["verdict"], example["gold"], field), correct(c["verdict"], example["gold"], field)
                counts["candidate_wins" if cc and not bc else "candidate_losses" if bc and not cc else "ties"] += 1
            result["paired"][str(repeat)][field] = counts
        b, c = [result["arms"][arm]["passes"][str(repeat)] for arm in ("baseline", "candidate")]
        if b["attempts"] == c["attempts"] == len(holdout):
            result["gates"][str(repeat)] = {
                "validity_no_worse": c["valid_output"]["rate"] >= b["valid_output"]["rate"],
                "binary_no_worse": c["binary_accuracy"]["rate"] >= b["binary_accuracy"]["rate"],
                "critical_recall_no_worse": c["critical_severity_recall"]["rate"] >= b["critical_severity_recall"]["rate"],
                "clean_false_alarm_no_worse": c["clean_false_alarm"]["rate"] <= b["clean_false_alarm"]["rate"],
                "p95_within_1_5x": c["latency_seconds"]["p95"] <= 1.5 * b["latency_seconds"]["p95"],
            }
    result["development_candidate"] = measured_score(development_rows, development)
    result["all_pilot_gates_pass"] = (result["complete"] and len(result["gates"]) == 2
                                      and all(all(g.values()) for g in result["gates"].values()))
    return result
