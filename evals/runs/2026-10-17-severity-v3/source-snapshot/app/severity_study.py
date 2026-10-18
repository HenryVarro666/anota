"""Severity-v3 scoring: disputed references cannot enter the primary denominator."""
from .comparison import comparison_summary
from .evaluation import ratio


def validate_sets(primary, disputed, regression, prior):
    combined = primary + disputed + regression
    if len({e["id"] for e in combined}) != len(combined):
        raise ValueError("study item IDs overlap")
    novel = primary + disputed
    if len({e["parent_id"] for e in novel}) != len(novel):
        raise ValueError("novel parent IDs overlap")
    normalize = lambda e: e["source"].strip().casefold()
    if {normalize(e) for e in novel} & {normalize(e) for e in prior}:
        raise ValueError("novel sources overlap historical data")
    if {e["parent_id"] for e in novel} & {e["parent_id"] for e in prior}:
        raise ValueError("novel parent IDs overlap historical data")
    if len({normalize(e) for e in novel}) != len(novel):
        raise ValueError("novel sources repeat")
    if any("gold" in e for e in disputed):
        raise ValueError("disputed cases must not have a single gold label")
    if any(e.get("reference_status") != "provisional_policy_assigned" for e in primary):
        raise ValueError("primary reference status must be declared before inference")
    severities = {e["gold"]["worst_severity"] for e in primary}
    if severities != {"critical", "major", "minor", "neutral"}:
        raise ValueError("primary set must include all four severities")
    if not any(e["mechanism"] == "clinical_condition" for e in primary):
        raise ValueError("clinical-condition slice is required")


def severity_summary(records, primary, disputed, regression, gate_spec):
    result = comparison_summary(records, primary, regression)
    result["primary_unique_items"] = len(primary)
    result["disputed_unique_items"] = len(disputed)
    result["disputed_attempts"] = sum(r["phase"] == "disputed" for r in records)
    result["complete"] = result["complete"] and result["disputed_attempts"] == len(disputed) * 2
    result["expected_total_calls_with_warmup"] = 2 + len(primary) * 4 + len(disputed) * 2 + len(regression)
    result["observed_total_calls"] = len(records)
    refs = {e["id"]: e for e in primary}
    for arm in ("baseline", "candidate"):
        for repeat in (1, 2):
            rows = [r for r in records if r["phase"] == "holdout" and r["arm"] == arm and r["repeat"] == repeat]
            condition = [r for r in rows if refs[r["task_id"]]["mechanism"] == "clinical_condition"]
            noncritical = [r for r in rows if refs[r["task_id"]]["gold"]["worst_severity"] in ("major", "minor")]
            predicted_critical = [r for r in rows if r["verdict"] and r["verdict"]["worst_severity"] == "critical"]
            metric = result["arms"][arm]["passes"][str(repeat)]
            metric["condition_critical_recall"] = ratio(sum(r["verdict"] is not None and r["verdict"]["worst_severity"] == "critical" for r in condition), len(condition))
            metric["noncritical_false_critical"] = ratio(sum(r in predicted_critical for r in noncritical), len(noncritical))
            metric["critical_precision"] = ratio(sum(refs[r["task_id"]]["gold"]["worst_severity"] == "critical" for r in predicted_critical), len(predicted_critical))
        # Values above only use primary cases, even if disputed rows contain critical predictions.
    result["gates"] = {}
    for p in ("1", "2"):
        b, c = [result["arms"][arm]["passes"][p] for arm in ("baseline", "candidate")]
        if b["attempts"] != len(primary) or c["attempts"] != len(primary):
            continue
        result["gates"][p] = {
            "validity_100_percent": c["valid_output"]["rate"] >= gate_spec["candidate_validity"],
            "critical_recall_100_percent": c["critical_severity_recall"]["rate"] >= gate_spec["candidate_critical_recall"],
            "condition_recall_100_percent": c["condition_critical_recall"]["rate"] >= gate_spec["candidate_condition_recall"],
            "binary_no_worse": c["binary_accuracy"]["rate"] >= b["binary_accuracy"]["rate"],
            "severity_no_worse": c["severity_accuracy"]["rate"] >= b["severity_accuracy"]["rate"],
            "clean_false_alarm_no_worse": c["clean_false_alarm"]["rate"] <= b["clean_false_alarm"]["rate"],
            "noncritical_false_critical_no_worse": c["noncritical_false_critical"]["rate"] <= b["noncritical_false_critical"]["rate"],
            "p95_within_1_5x": c["latency_seconds"]["p95"] <= gate_spec["p95_ratio_max"] * b["latency_seconds"]["p95"],
        }
    result["all_pilot_gates_pass"] = result["complete"] and len(result["gates"]) == 2 and all(all(g.values()) for g in result["gates"].values())
    result["caveat"] = "24 new synthetic source parents; provisional same-agent references; 4 disputed cases excluded before inference; historical regression scoring uses unchanged original gold. No expert/clinical validation."
    return result
