"""Independent audit; reads gold-derived outputs only after completion and seal."""
import hashlib
import json
import gzip
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "evals/runs/2026-10-17-librispeech-qc-ranking-v1"
COHORT = STUDY / "cohorts/interim"
OUTPUT = ROOT / "evals/reviews/librispeech-qc-ranking-v1-interim-audit.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rows(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def equal(left, right):
    if left is None or right is None:
        return left is right
    if isinstance(left, float) or isinstance(right, float):
        return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def dt(value):
    return datetime.fromisoformat(value)


wait_started = stamp()
while True:
    state = load(COHORT / "status.json")
    if state["status"] == "failed":
        raise RuntimeError("Upstream cohort failed; no gold-derived artifact was opened")
    if (state["status"] == "completed" and (COHORT / "ranking-seal.json").is_file()
            and (COHORT / "artifact-hashes.json").is_file()):
        break
    time.sleep(5)

opened_at = stamp()
seal = load(COHORT / "ranking-seal.json")
validation = load(COHORT / "validation.json")
summary = load(COHORT / "summary.json")
inputs = load(COHORT / "inputs.json")
protocol = load(STUDY / "protocol.json")
config = load(STUDY / "config.json")
scored = rows(COHORT / "scored-items.jsonl")
features = rows(COHORT / "qc-features.jsonl")
with gzip.open(COHORT / "selection-plan-before-gold.json.gz", "rt", encoding="utf-8") as stream:
    plan = json.load(stream)

checks = []


def check(name, ok, detail=None):
    entry = {"check": name, "passed": bool(ok)}
    if detail is not None:
        entry["detail"] = detail
    checks.append(entry)


artifacts = load(COHORT / "artifact-hashes.json")
artifact_mismatches = [name for name, expected in artifacts.items() if digest(COHORT / name) != expected]
check("all_cohort_artifact_sha256", not artifact_mismatches,
      {"files": len(artifacts), "mismatches": artifact_mismatches})
frozen = load(STUDY / "frozen-hashes.json")
frozen_mismatches = [name for name, expected in frozen.items() if digest(STUDY / name) != expected]
check("frozen_protocol_config_source_snapshot_sha256", not frozen_mismatches,
      {"files": len(frozen), "mismatches": frozen_mismatches})
live_source_mismatches = [name for name, expected in protocol["source_sha256"].items()
                          if digest(ROOT / name) != expected]
check("current_source_matches_frozen_source", not live_source_mismatches,
      {"files": len(protocol["source_sha256"]), "mismatches": live_source_mismatches})
input_mismatches = [name for name in ("manifest", "asr", "references")
                    if digest(ROOT / inputs[name]["path"]) != inputs[name]["sha256"]]
check("cohort_inputs_and_reference_sha256", not input_mismatches, input_mismatches)
sealed_files = {"selection_plan_sha256": "selection-plan-before-gold.json.gz",
                "features_sha256": "qc-features.jsonl",
                "ranking_sha256": "primary-ranking-before-gold.jsonl",
                "qc_reports_sha256": "qc-reports.jsonl.gz"}
seal_mismatches = [key for key, name in sealed_files.items() if digest(COHORT / name) != seal[key]]
check("pre_gold_seal_artifact_sha256", not seal_mismatches, seal_mismatches)
check("seal_precedes_gold_and_completion",
      dt(protocol["frozen_at_utc"]) <= dt(seal["sealed_at_utc"])
      <= dt(validation["gold_scoring_started_at_utc"]) <= dt(state["completed_at_utc"])
      <= dt(opened_at),
      {"protocol_frozen": protocol["frozen_at_utc"], "ranking_sealed": seal["sealed_at_utc"],
       "gold_started": validation["gold_scoring_started_at_utc"],
       "cohort_completed": state["completed_at_utc"], "audit_opened_gold_outputs": opened_at})
check("seal_marked_reference_unopened", seal["reference_labels_opened"] is False)
check("reported_no_asr_calls", validation["new_asr_calls"] == 0)
feature_by_id = {r["item_id"]: r for r in features}
scored_by_id = {r["item_id"]: r for r in scored}
check("cohort_item_ids_unique_and_aligned", len(feature_by_id) == len(features)
      == len(scored_by_id) == len(scored) == inputs["cohort_items"]
      and set(feature_by_id) == set(scored_by_id))
check("cohort_exclusion_count", inputs["cohort_items"] + inputs["excluded_pending_or_not_started"]
      == inputs["expected_full_items"])


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[-1] + 1,
                               previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


count_mismatches = []
distance_mismatches = []
feature_mismatches = []
for row in scored:
    if (row["errors"] != row["substitutions"] + row["deletions"] + row["insertions"]
            or row["reference_units"] != len(row["normalized_reference"].split())
            or row["hypothesis_units"] != len(row["normalized_hypothesis"].split())):
        count_mismatches.append(row["item_id"])
    if edit_distance(row["normalized_reference"].split(), row["normalized_hypothesis"].split()) != row["errors"]:
        distance_mismatches.append(row["item_id"])
    if any(row[key] != value for key, value in feature_by_id[row["item_id"]].items()):
        feature_mismatches.append(row["item_id"])
check("per_item_counts_and_normalized_word_denominators", not count_mismatches,
      {"items": len(scored), "mismatches": count_mismatches})
check("independent_levenshtein_distance_from_saved_normalized_text", not distance_mismatches,
      {"items": len(scored), "mismatches": distance_mismatches,
       "scope": "Recomputes edit distance; does not independently validate source reference or normalization policy"})
check("gold_scoring_preserved_all_presealed_features", not feature_mismatches, feature_mismatches)


def independent_issue_score(row, allowed_codes=None):
    duration = row["audio_duration_s"]
    spans = [(min(i["start"], duration), min(i["end"], duration),
              {"low": 1, "medium": 2, "high": 3}[i["priority"]]) for i in row["issues"]
             if allowed_codes is None or i["code"] in allowed_codes]
    bounds = sorted({0.0, duration, *(x for span in spans for x in span[:2])})
    integral = 0.0
    for a, b in zip(bounds[:-1], bounds[1:]):
        mid = (a + b) / 2
        weight = max([0] + [weight for start, end, weight in spans if start <= mid < end])
        integral += (b - a) * weight
    return min(3.0, max(0.0, integral / duration))


score_mismatches = []
for row in features:
    tier = (2 if row["asr_status"] != "succeeded" else
            1 if row["qc_status"] != "complete" or row["qc_coverage_status"] != "ok" else 0)
    tie = hashlib.sha256(("audio-qc-ranking-v1:" + row["item_id"]).encode()).hexdigest()
    if (tier != row["failure_tier"] or tie != row["tie_hash"]
            or not equal(independent_issue_score(row), row["primary_score"])):
        score_mismatches.append(row["item_id"])
check("independent_priority_integral_tier_and_tie_hash", not score_mismatches,
      {"items": len(features), "mismatches": score_mismatches,
       "score_method": "independent midpoint maximum over every unique interval boundary"})


def us(row):
    return round(row["audio_duration_s"] * 1_000_000)


def selected_prefix(ordered, pct):
    target = sum(us(r) for r in ordered) * pct // 100
    selected = []
    elapsed = 0
    for row in ordered:
        if elapsed + us(row) > target:
            break
        selected.append(row)
        elapsed += us(row)
    return selected, target, elapsed


def ratio(a, b):
    return a / b if b else None


def calculate(group, selected, target, elapsed):
    total_errors = sum(r["substitutions"] + r["deletions"] + r["insertions"] for r in group)
    total_units = sum(r["reference_units"] for r in group)
    captured = sum(r["substitutions"] + r["deletions"] + r["insertions"] for r in selected)
    selected_units = sum(r["reference_units"] for r in selected)
    return {"selected_count": len(selected), "target_audio_s": target / 1_000_000,
            "selected_audio_s": elapsed / 1_000_000,
            "unused_budget_s": (target - elapsed) / 1_000_000,
            "actual_audio_fraction": ratio(elapsed, sum(us(r) for r in group)),
            "captured_errors": captured,
            "captured_reference_units": selected_units,
            "selected_wer": ratio(captured, selected_units),
            "error_capture_rate": ratio(captured, total_errors),
            "residual_errors": total_errors - captured,
            "residual_reference_units": total_units - selected_units,
            "residual_wer": ratio(total_errors - captured, total_units - selected_units),
            "captured_error_clips": sum(r["errors"] > 0 for r in selected),
            "error_clip_recall": ratio(sum(r["errors"] > 0 for r in selected), sum(r["errors"] > 0 for r in group))}


groups = {"pooled": scored}
for split in sorted({r["source_split"] for r in scored}):
    groups["split:" + split] = [r for r in scored if r["source_split"] == split]

# Rebuild every pre-gold plan entirely from pre-gold feature rows, without
# importing the production ranking code or consulting any gold counts.
feature_groups = {"pooled": features}
for split in sorted({r["source_split"] for r in features}):
    feature_groups["split:" + split] = [r for r in features if r["source_split"] == split]
dsp_codes = {"low_level", "low_energy", "near_clipping", "dc_offset"}
coverage_codes = {"speech_without_transcript", "transcript_without_speech"}
all_scores = {r["item_id"]: {method: independent_issue_score(r, codes)
                           for method, codes in (("qc", None), ("dsp_only", dsp_codes),
                                                 ("coverage_only", coverage_codes))}
              for r in features}


def all_budgets(ordered):
    result = {}
    for pct in config["budgets_percent"]:
        selected, target, elapsed = selected_prefix(ordered, pct)
        result[str(pct)] = {"selected_count": len(selected), "target_audio_us": target,
                            "selected_audio_us": elapsed}
    return result


rebuilt_plan = {"groups": {}}
all_plan_rechecks = []
for name, group in feature_groups.items():
    stable = sorted(group, key=lambda r: r["item_id"])
    group_plan = {"count": len(group), "total_audio_us": sum(us(r) for r in group),
                  "methods": {}, "random": []}
    for method in ("qc", "dsp_only", "coverage_only", "shortest", "longest"):
        if method in ("shortest", "longest"):
            direction = 1 if method == "shortest" else -1
            ordered = sorted(group, key=lambda r: (direction * us(r), r["tie_hash"]))
        else:
            ordered = sorted(group, key=lambda r: (-r["failure_tier"],
                             -all_scores[r["item_id"]][method], r["tie_hash"]))
        group_plan["methods"][method] = {
            "order_ids": [r["item_id"] for r in ordered], "budgets": all_budgets(ordered)}
        check(f"complete_pre_gold_method_plan_replay:{name}:{method}",
              group_plan["methods"][method] == plan["groups"][name]["methods"][method])
    for seed in range(config["random_seed"], config["random_seed"] + config["random_repetitions"]):
        ordered = list(stable)
        random.Random(seed).shuffle(ordered)
        order_bytes = json.dumps([r["item_id"] for r in ordered], sort_keys=True,
                                separators=(",", ":"), ensure_ascii=False,
                                allow_nan=False).encode("utf-8")
        group_plan["random"].append({"seed": seed, "order_sha256": hashlib.sha256(order_bytes).hexdigest(),
                                     "budgets": all_budgets(ordered)})
    check("all_1000_pre_gold_random_plans_replayed:" + name,
          group_plan["random"] == plan["groups"][name]["random"],
          {"seeds": len(group_plan["random"]), "budget_points_per_seed": len(config["budgets_percent"]),
           "comparison": "exact sequence equality including every full-order SHA256 and integer budget selection"})
    rebuilt_plan["groups"][name] = group_plan
    all_plan_rechecks.append({"group": name, "methods": len(group_plan["methods"]),
                              "random_seeds": len(group_plan["random"]),
                              "budgets": config["budgets_percent"]})
check("entire_pre_gold_selection_plan_exact_replay", rebuilt_plan == plan)

primary_rechecks = []
for name, group in groups.items():
    ordered = sorted(group, key=lambda r: (-r["failure_tier"], -r["primary_score"], r["tie_hash"]))
    order_ids = [r["item_id"] for r in ordered]
    check("primary_order_matches_pre_gold_plan:" + name,
          order_ids == plan["groups"][name]["methods"]["qc"]["order_ids"])
    for pct in (10, 20):
        selected, target, elapsed = selected_prefix(ordered, pct)
        measured = calculate(group, selected, target, elapsed)
        reported = summary["comparison"]["groups"][name]["methods"]["qc"][str(pct)]
        mismatches = [key for key, value in measured.items() if not equal(value, reported[key])]
        boundary_valid = elapsed <= target and (len(selected) == len(ordered)
                                                or elapsed + us(ordered[len(selected)]) > target)
        check(f"primary_10_20_recalculation:{name}:{pct}", not mismatches and boundary_valid,
              {"mismatches": mismatches, "strict_prefix_boundary": boundary_valid})
        primary_rechecks.append({"group": name, "budget_percent": pct,
                                 "selected_ids": [r["item_id"] for r in selected],
                                 "total_errors": sum(r["errors"] for r in group),
                                 "total_reference_units": sum(r["reference_units"] for r in group),
                                 **measured})

seeds = (20261017, 20261018, 20261516, 20262016)
trial_map = {}
trial_count = 0
with gzip.open(COHORT / "random-trials.jsonl.gz", "rt", encoding="utf-8") as stream:
    for line in stream:
        row = json.loads(line)
        trial_count += 1
        if row["seed"] in seeds and row["budget_percent"] in (10, 20):
            key = (row["group"], row["seed"], row["budget_percent"])
            if key in trial_map:
                raise ValueError("duplicate selected random trial")
            trial_map[key] = row
check("random_trial_count", trial_count == len(groups) * 1000 * len(config["budgets_percent"]),
      {"rows": trial_count})
random_rechecks = []
for name, group in groups.items():
    saved_random = {row["seed"]: row for row in plan["groups"][name]["random"]}
    for seed in seeds:
        ordered = sorted(group, key=lambda r: r["item_id"])
        random.Random(seed).shuffle(ordered)
        order_ids = [r["item_id"] for r in ordered]
        encoded = json.dumps(order_ids, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        order_sha = hashlib.sha256(encoded).hexdigest()
        check(f"random_pre_gold_order_hash:{name}:{seed}",
              order_sha == saved_random[seed]["order_sha256"])
        for pct in (10, 20):
            selected, target, elapsed = selected_prefix(ordered, pct)
            measured = calculate(group, selected, target, elapsed)
            reported = trial_map[(name, seed, pct)]
            planned = saved_random[seed]["budgets"][str(pct)]
            mismatches = [key for key, value in measured.items() if not equal(value, reported[key])]
            plan_matches = (planned["selected_count"] == len(selected)
                            and planned["target_audio_us"] == target and planned["selected_audio_us"] == elapsed)
            boundary_valid = elapsed <= target and (len(selected) == len(ordered)
                                                    or elapsed + us(ordered[len(selected)]) > target)
            check(f"random_10_20_recalculation:{name}:{seed}:{pct}",
                  not mismatches and plan_matches and boundary_valid,
                  {"mismatches": mismatches, "frozen_plan_matches": plan_matches,
                   "strict_prefix_boundary": boundary_valid})
            random_rechecks.append({"group": name, "seed": seed, "budget_percent": pct,
                                    "order_sha256": order_sha, **measured})

check("cohort_still_completed_after_audit", load(COHORT / "status.json")["status"] == "completed")
artifact_mismatches_after = [name for name, expected in artifacts.items() if digest(COHORT / name) != expected]
check("cohort_artifacts_unchanged_during_audit", not artifact_mismatches_after, artifact_mismatches_after)

audit = {
    "schema_version": "audio-qc-ranking-independent-audit-v1",
    "status": "passed" if all(c["passed"] for c in checks) else "failed",
    "study": STUDY.relative_to(ROOT).as_posix(), "cohort": "interim",
    "wait_started_at_utc": wait_started, "gold_outputs_first_opened_at_utc": opened_at,
    "completed_at_utc": stamp(), "cohort_items": len(scored),
    "script": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
    "script_sha256": digest(Path(__file__).resolve()),
    "checks": checks, "all_plan_rechecks": all_plan_rechecks,
    "primary_rechecks": primary_rechecks, "random_rechecks": random_rechecks,
    "review_scope": ["Independent standard-library implementation; no runner/metric helper imports",
                     "All cohort artifact and frozen source hashes checked",
                     "All saved normalized transcript edit distances independently recomputed",
                     "All frozen QC priority scores independently recomputed",
                     "Every deterministic method and all 1000 random seeds with every budget exactly replayed from pre-gold features",
                     "Pooled and split QC 10/20 percent selections recomputed",
                     "Four fixed random seeds at 10/20 percent per group replayed"],
    "limitations": ["Interim completion cohort is nonrandom, with partial test-other coverage",
                    "Official labels and text normalization policy are accepted rather than independently audited",
                    "Every random order/selection plan is checked; gold error metrics are independently recomputed for four random seeds",
                    "Error containment is not actual human correction or measured labor savings"],
}
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
if OUTPUT.exists():
    raise FileExistsError("Do not overwrite an existing audit")
OUTPUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
print(json.dumps({"status": audit["status"], "cohort_items": len(scored),
                  "checks": len(checks), "failed_checks": [c for c in checks if not c["passed"]],
                  "output": str(OUTPUT)}, ensure_ascii=False))
