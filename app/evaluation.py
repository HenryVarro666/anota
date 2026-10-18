"""Pure-Python metrics for observed judge attempts, separate from annotation storage."""
from collections import Counter
from statistics import median

from .quality import cohen_kappa


def quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def score_pass(records, examples):
    by_id = {row["id"]: row for row in examples}
    attempts = [row for row in records if row["phase"] == "timed"]
    valid = [row for row in attempts if row["verdict"] is not None]
    expected_types = Counter()
    hits = Counter()
    exact = binary = severity = critical = false_alarm = clean = 0
    gold_binary, pred_binary = [], []
    for row in attempts:
        gold = by_id[row["task_id"]]["gold"]
        verdict = row["verdict"]
        expected_types.update(gold["error_types"])
        clean += gold["error_types"] == ["no_error"]
        if verdict is None:
            continue
        types = set(verdict["error_types"])
        for error_type in gold["error_types"]:
            hits[error_type] += error_type in types
        exact += types == set(gold["error_types"])
        severity += verdict["worst_severity"] == gold["worst_severity"]
        gold_clean, predicted_clean = gold["error_types"] == ["no_error"], types == {"no_error"}
        binary += gold_clean == predicted_clean
        gold_binary.append("clean" if gold_clean else "error")
        pred_binary.append("clean" if predicted_clean else "error")
        critical += gold["worst_severity"] == "critical" and verdict["worst_severity"] == "critical"
        false_alarm += gold_clean and not predicted_clean
    critical_total = sum(by_id[row["task_id"]]["gold"]["worst_severity"] == "critical" for row in attempts)
    latencies = [row["trace"]["elapsed_seconds"] for row in attempts]
    completions = [(row["trace"]["response"] or {}).get("usage", {}).get("completion_tokens")
                   for row in valid]
    usage_complete = bool(valid) and all(type(value) is int for value in completions)
    valid_seconds = sum(row["trace"]["elapsed_seconds"] for row in valid)
    return {
        "attempts": len(attempts), "unique_examples": len({r["task_id"] for r in attempts}),
        "valid_output": ratio(len(valid), len(attempts)),
        "exact_error_types": ratio(exact, len(attempts)),
        "binary_accuracy": ratio(binary, len(attempts)),
        "severity_accuracy": ratio(severity, len(attempts)),
        "critical_severity_recall": ratio(critical, critical_total),
        "clean_false_alarm": ratio(false_alarm, clean),
        "per_type_recall": {key: ratio(hits[key], count) for key, count in sorted(expected_types.items())},
        "binary_kappa_valid_only": cohen_kappa(gold_binary, pred_binary, ["clean", "error"]) if valid else None,
        "latency_seconds": {"p50": median(latencies) if latencies else None,
                            "p95": quantile(latencies, 0.95), "total": sum(latencies)},
        "effective_output_tokens_per_second": sum(completions) / valid_seconds if usage_complete and valid_seconds else None,
        "token_rate_scope": "completion_tokens / client request wall time; includes prefill; not decode-only throughput",
    }


def summarize(records, examples, repeats):
    per_pass = {str(i): score_pass([r for r in records if r.get("repeat") == i], examples)
                for i in range(1, repeats + 1)}
    timed = [r for r in records if r["phase"] == "timed"]
    stable = total_repeated = 0
    for example in examples:
        rows = [r for r in timed if r["task_id"] == example["id"]]
        if len(rows) == repeats and repeats > 1 and all(r["verdict"] for r in rows):
            total_repeated += 1
            labels = {(tuple(sorted(r["verdict"]["error_types"])), r["verdict"]["worst_severity"]) for r in rows}
            stable += len(labels) == 1
    return {"expected_attempts": len(examples) * repeats, "completed_attempts": len(timed),
            "unique_dataset_examples": len(examples), "unique_parent_templates": len({r["parent_id"] for r in examples}),
            "complete": len(timed) == len(examples) * repeats, "passes": per_pass,
            "repeat_label_agreement_valid_only": ratio(stable, total_repeated),
            "pooled_timing": score_pass(timed, examples)["latency_seconds"],
            "caveat": "Synthetic EN-ES pilot; repeated runs and related perturbations are not independent evaluation samples."}
