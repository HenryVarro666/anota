"""Independent post-completion ML ranking audit; never changes study artifacts.

No gold/score file is decoded before completed status, artifact index, and seal
are present and their byte hashes have been verified. --self-test is synthetic.
Ranking, budgets, normalization and edit counts below do not import production
measurement code. Trusted, hash-verified local joblib pipelines are used only
to replay predictions; no estimator is trained or selected by this audit.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import math
import random
import statistics
import sys
import time
import unicodedata
from datetime import UTC, datetime
from itertools import combinations, pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUDY = ROOT / "evals/runs/2026-10-17-librispeech-ml-ranking-v2"
OUTPUT = ROOT / "evals/reviews/librispeech-ml-ranking-v2-audit.json"
BUDGETS = (5, 10, 20, 30, 50, 100)
METHODS = ("qc", "dsp_only", "coverage_only", "shortest", "longest")
ARMS = ("legacy", "confidence")
ROLES = ("train", "tune", "holdout")
COHORTS = {"fresh_holdout": "holdout", "previously_exposed_test": "test"}
SEEDS_TO_MEASURE = (20261017, 20261018, 20261517, 20262016)


def utc():
    return datetime.now(UTC).isoformat()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def gzload(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def gzrows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def same(left, right):
    if left is None or right is None:
        return left is right
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(same(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    if isinstance(left, float) or isinstance(right, float):
        return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def by_id(values):
    result = {row["item_id"]: row for row in values}
    if len(result) != len(values) or not values:
        raise ValueError("empty or duplicate item IDs")
    return result


def hash_identifier(kind, identifier):
    return hashlib.sha256(f"audio-qc-ml-v2:{kind}:{identifier}".encode()).hexdigest()


def tie(row):
    return hashlib.sha256(("audio-qc-ranking-v1:" + row["item_id"]).encode()).hexdigest()


def tier(row):
    if row["asr_status"] != "succeeded":
        return 2
    return int(row["qc_status"] != "complete" or row["qc_coverage_status"] != "ok")


def issue_score(row, method="qc"):
    allowed = {"dsp_only": {"low_level", "low_energy", "near_clipping", "dc_offset"},
               "coverage_only": {"speech_without_transcript", "transcript_without_speech"}}.get(method)
    duration = row["audio_duration_s"]
    spans = [(min(issue["start"], duration), min(issue["end"], duration),
              {"low": 1, "medium": 2, "high": 3}[issue["priority"]]) for issue in row["issues"]
             if allowed is None or issue["code"] in allowed]
    endpoints = sorted({0.0, duration, *(value for span in spans for value in span[:2])})
    integral = 0.0
    for start, end in pairwise(endpoints):
        midpoint = (start + end) / 2
        weight = max((weight for a, b, weight in spans if a <= midpoint < b), default=0)
        integral += (end - start) * weight
    return min(3.0, max(0.0, integral / duration))


def duration_us(row):
    return round(row["audio_duration_s"] * 1_000_000)


def ordered(values, method="qc", predictions=None):
    if predictions is not None:
        return sorted(values, key=lambda row: (-tier(row), -predictions[row["item_id"]], tie(row)))
    if method in ("shortest", "longest"):
        sign = 1 if method == "shortest" else -1
        return sorted(values, key=lambda row: (sign * duration_us(row), tie(row)))
    return sorted(values, key=lambda row: (-tier(row), -issue_score(row, method), tie(row)))


def prefix(values, budget):
    target = sum(duration_us(row) for row in values) * budget // 100
    used, count = 0, 0
    for row in values:
        duration = duration_us(row)
        if used + duration > target:
            break
        count += 1
        used += duration
    return values[:count], {"selected_count": count, "target_audio_us": target, "selected_audio_us": used}


def groups(values):
    return {"pooled": values, **{"split:" + split: [r for r in values if r["source_split"] == split]
            for split in sorted({r["source_split"] for r in values})}}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def metrics(all_values, chosen, target):
    errors = sum(r["errors"] for r in all_values)
    units = sum(r["reference_units"] for r in all_values)
    captured = sum(r["errors"] for r in chosen)
    captured_units = sum(r["reference_units"] for r in chosen)
    selected_us = sum(duration_us(r) for r in chosen)
    captured_clips = sum(r["errors"] > 0 for r in chosen)
    return {"selected_count": len(chosen), "target_audio_s": target / 1e6,
        "selected_audio_s": selected_us / 1e6, "unused_budget_s": (target - selected_us) / 1e6,
        "actual_audio_fraction": ratio(selected_us, sum(duration_us(r) for r in all_values)),
        "captured_errors": captured, "captured_reference_units": captured_units,
        "selected_wer": ratio(captured, captured_units), "error_capture_rate": ratio(captured, errors),
        "captured_error_clips": captured_clips, "error_clip_recall": ratio(captured_clips, sum(r["errors"] > 0 for r in all_values)),
        "residual_errors": errors - captured, "residual_reference_units": units - captured_units,
        "residual_wer": ratio(errors - captured, units - captured_units),
        "captured_asr_failure_errors": sum(r["errors"] for r in chosen if r["asr_status"] != "succeeded"),
        "qc_unavailable_selected_count": sum(r["qc_status"] != "complete" or r["qc_coverage_status"] != "ok" for r in chosen)}


def normalize_english(value):
    text = unicodedata.normalize("NFKC", value).casefold()
    output = []
    for index, char in enumerate(text):
        if char in "'’ʼ":
            continue
        between_digits = char in ".," and index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit()
        punctuation = unicodedata.category(char)[0] == "P"
        output.append(" " if punctuation and char not in "%‰" and not between_digits else char)
    return " ".join("".join(output).split())


def edit_counts(reference, hypothesis):
    """Full DP + independent backtrace, substitution/deletion/insertion ties."""
    table = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for i in range(len(reference) + 1):
        table[i][0] = i
    for j in range(len(hypothesis) + 1):
        table[0][j] = j
    for i, ref in enumerate(reference, 1):
        for j, hyp in enumerate(hypothesis, 1):
            table[i][j] = min(table[i - 1][j - 1] + (ref != hyp), table[i - 1][j] + 1, table[i][j - 1] + 1)
    i, j, substitutions, deletions, insertions = len(reference), len(hypothesis), 0, 0, 0
    while i or j:
        if i and j and reference[i - 1] == hypothesis[j - 1]:
            i, j = i - 1, j - 1
        elif i and j and table[i][j] == table[i - 1][j - 1] + 1:
            substitutions += 1
            i, j = i - 1, j - 1
        elif i and table[i][j] == table[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return {"errors": table[-1][-1], "substitutions": substitutions, "deletions": deletions,
            "insertions": insertions, "reference_units": len(reference), "hypothesis_units": len(hypothesis)}


def quantile(values, q):
    position = (len(values) - 1) * q
    left, right = math.floor(position), math.ceil(position)
    return values[left] + (values[right] - values[left]) * (position - left)


def aggregate(values):
    result = {"repetitions": len(values)}
    for field in values[0]:
        defined = sorted(row[field] for row in values if row[field] is not None)
        result[field] = {"mean": statistics.fmean(defined) if defined else None,
            "median": statistics.median(defined) if defined else None,
            "p2_5": quantile(defined, .025) if defined else None,
            "p97_5": quantile(defined, .975) if defined else None, "defined_count": len(defined)}
    return result


class Audit:
    def __init__(self):
        self.checks = []

    def check(self, name, condition, detail=None):
        record = {"check": name, "passed": bool(condition)}
        if detail is not None:
            record["detail"] = detail
        self.checks.append(record)

    def require(self, name, condition, detail=None):
        self.check(name, condition, detail)
        if not condition:
            raise ValueError(name)

    def hashes(self, name, base, mapping):
        mismatches = [path for path, expected in mapping.items() if not (base / path).is_file() or digest(base / path) != expected]
        self.require(name, not mismatches, {"files": len(mapping), "mismatches": mismatches})


def verify_assignment(audit, paths, manifests):
    source = rows(paths["dev_prepared_manifest"])
    assignment = load(paths["dev_assignment"])
    source_by_id = by_id(source)
    members = {}
    for row in source:
        members.setdefault((row["split"], row["speaker_id"]), []).append(row)
    expected_roles, partitions = {}, {}
    for split in ("dev-clean", "dev-other"):
        speakers = sorted({r["speaker_id"] for r in source if r["split"] == split}, key=lambda speaker: (hash_identifier("speaker", speaker), speaker))
        a, b = len(speakers) * 6 // 10, len(speakers) * 2 // 10
        partitions[split] = {"train": speakers[:a], "tune": speakers[a:a + b], "holdout": speakers[a + b:]}
        for role, names in partitions[split].items():
            expected_roles.update({name: role for name in names})
    selected = {role: set() for role in ROLES}
    rebuilt = []
    for (split, speaker), values in sorted(members.items()):
        ordered_values = sorted(values, key=lambda row: (hash_identifier("item", row["item_id"]), row["item_id"]))
        for index, row in enumerate(ordered_values):
            role = expected_roles[speaker]
            if index < 30:
                selected[role].add(row["item_id"])
            rebuilt.append({"item_id": row["item_id"], "split": split, "speaker_id": speaker,
                "role": role, "selected": index < 30, "within_speaker_item_rank": index,
                "item_hash": hash_identifier("item", row["item_id"]), "speaker_hash": hash_identifier("speaker", speaker),
                "audio_sha256": row["audio_sha256"], "duration": row["duration"]})
    audit.check("independent_full_assignment_reconstruction", assignment["items"] == sorted(rebuilt, key=lambda row: row["item_id"])
                and assignment["speaker_partitions"] == partitions and len(source) == assignment["source_items"])
    role_ids = {name: set(by_id(values)) for name, values in manifests.items()}
    audit.check("exact_selected_role_id_sets", all(selected[role] == role_ids[role] for role in ROLES)
                and set.union(*selected.values()) == role_ids["dev"])
    mismatch = []
    for role in (*ROLES, "dev"):
        for row in manifests[role]:
            original = dict(source_by_id[row["item_id"]])
            original["audio_path"] = str((paths["dev_prepared_manifest"].parent / original["audio_path"]).resolve())
            if original != row:
                mismatch.append(row["item_id"])
    audit.check("all_role_metadata_equals_prepared_sources", not mismatch, {"mismatches": mismatch})
    full = {role: [r for r in source if expected_roles[r["speaker_id"]] == role] for role in ROLES}
    full["test"] = manifests["test"]
    overlaps = []
    for left, right in combinations(full, 2):
        for key in ("item_id", "speaker_id", "audio_sha256"):
            overlap = {r[key] for r in full[left]} & {r[key] for r in full[right]}
            if overlap:
                overlaps.append([left, right, key, sorted(overlap)])
    audit.check("all_source_speaker_item_audio_isolation_including_unselected", not overlaps, overlaps)


def verify_plan(audit, label, values, plan, predictions=None):
    expected_groups = groups(values)
    audit.require(label + " group IDs", set(plan) == set(expected_groups))
    for name, current in expected_groups.items():
        ranked = ordered(current, predictions=predictions)
        saved = plan[name]
        audit.check(label + " " + name + " order", saved["order_ids"] == [row["item_id"] for row in ranked])
        audit.check(label + " " + name + " all budgets", saved["budgets"] == {str(b): prefix(ranked, b)[1] for b in BUDGETS})


def replay_model(audit, path, features, arm, names, recorded, label, parameters):
    import joblib
    from threadpoolctl import threadpool_limits

    model = joblib.load(path)  # Callers have verified index and seal before load.
    audit.require(label + " model feature schema", model.speechrank_feature_names_ == names
                  and model.speechrank_feature_arm_ == arm)
    audit.check(label + " actual estimator parameters", model.named_steps["model"].get_params(deep=False) == parameters)
    matrix = [[float("nan") if row["ml_features"][arm][name] is None else row["ml_features"][arm][name]
               for name in names] for row in features]
    with threadpool_limits(limits=4):
        actual = [float(x) for x in model.predict(matrix)]
    audit.check(label + " replay predictions", set(recorded) == set(by_id(features))
                and all(math.isfinite(x) and x >= 0 and same(x, recorded[row["item_id"]]) for row, x in zip(features, actual)),
                {"items": len(features), "absolute_relative_tolerance": 1e-12})
    return model


def verify_checked_files(audit, protocol, paths):
    assignment, selection = load(paths["dev_assignment"]), load(paths["dev_selection"])
    dev_run = load(paths["dev_asr_run"])
    expected = {
        "assignment": (paths["dev_assignment"], selection["assignment_sha256"]),
        "asr_manifest": (paths["dev_manifest"], dev_run["identity"]["manifest_sha256"]),
        "test_manifest": (paths["test_manifest"], digest(paths["test_manifest"])),
        "test_source_manifest": (paths.get("test_source_manifest", paths["test_manifest"]), assignment["test_manifest_sha256"]),
        "prepared_manifest": (paths["dev_prepared_manifest"], assignment["source_manifest_sha256"]),
        "prepared_references": (paths["dev_prepared_references"], assignment["source_references_sha256"]),
        "verification": (paths["dev_verification"], digest(paths["dev_verification"])),
    }
    test_manifests = []
    for path in (paths["test_manifest"], paths.get("test_source_manifest", paths["test_manifest"])):
        values = rows(path)
        for row in values:
            row["audio_path"] = str((path.parent / row["audio_path"]).resolve())
        test_manifests.append(by_id(values))
    audit.check("independent test source snapshot complete resolved metadata equality",
                test_manifests[0] == test_manifests[1]
                and protocol["input_audit"]["test_manifests_metadata_equal"] is True
                and protocol["input_audit"]["schema_version"] == "speechrank-input-audit-v2")
    verification = load(paths["dev_verification"])
    audit.check("prepared verification hashes", verification["manifest_sha256"] == assignment["source_manifest_sha256"]
                and verification["references_sha256"] == assignment["source_references_sha256"])
    for role, spec in selection["exports"].items():
        for kind in ("manifest", "references"):
            expected[role + "_" + kind] = ((paths["dev_manifest"].parent / spec[kind]).resolve(), spec[kind + "_sha256"])
    actual = {name: {"path": str(path.resolve()), "sha256": digest(path)} for name, (path, _) in expected.items()}
    audit.check("independent input checked_files exact identities", actual == protocol["input_audit"]["checked_files"]
                and all(actual[name]["sha256"] == sha for name, (_, sha) in expected.items()))
    old = load(paths["v1_protocol"])
    required = {"scripts/evaluate_qc_ranking.py", "scripts/qc_ranking_metrics.py",
                "scripts/evaluate_speech_smoke.py", "speechprep/manifest.py", "speechprep/state.py"}
    required.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "speechqc").glob("*.py"))
    required.update(p for p in old["source_sha256"] if p.startswith("speechqc/") and p.endswith(".py"))
    expected = {name: (ROOT / name, old["source_sha256"][name]) for name in required}
    expected["v1_config"] = (paths["v1_config"], old["config_sha256"])
    actual = {name: {"path": str(path.resolve()), "sha256": digest(path)} for name, (path, _) in expected.items()}
    audit.check("independent v1 checked_files exact identities", actual == protocol["v1_identity_audit"]["checked_files"]
                and all(actual[name]["sha256"] == sha for name, (_, sha) in expected.items())
                and digest(paths["v1_protocol"]) == protocol["v1_identity_audit"]["v1_protocol_sha256"])


def execute(study, audit):
    output = study / "outputs"
    state = load(output / "status.json")
    audit.require("completed gate", state["status"] == "completed")
    index = load(output / "artifact-hashes.json")
    audit.hashes("all_output_artifact_hashes_before_gold", output, index)
    seal = load(output / "evaluation-seal.json")
    audit.require("status_excluded_from_pre_gold_seal", "status.json" not in seal["files"])
    audit.hashes("all_pre_gold_sealed_artifact_hashes", output, seal["files"])
    protocol, config = load(study / "protocol.json"), load(study / "config.json")
    audit.hashes("frozen_study_protocol_config_source_snapshot_hashes", ROOT, load(study / "frozen-hashes.json"))
    audit.hashes("live_source_hashes", ROOT, protocol["source_hashes"])
    paths = {name: ROOT / spec["path"] for name, spec in protocol["inputs"].items()}
    audit.hashes("all_frozen_input_hashes", ROOT, {spec["path"]: spec["sha256"] for spec in protocol["inputs"].values()})
    audit.require("configuration_hash", digest(study / "config.json") == protocol["config_sha256"])
    for package, version in protocol["environment"]["packages"].items():
        audit.check("environment " + package, importlib.metadata.version(package) == version)
    audit.check("same_python_environment", sys.version == protocol["environment"]["python"])
    opened = utc()
    validation = load(output / "validation.json")
    times = [protocol["frozen_at_utc"], seal["train_tune_gold_started_at_utc"],
             seal["sealed_at_utc"], validation["heldout_gold_started_at_utc"], state["completed_at_utc"], opened]
    audit.check("frozen_training_seal_heldout_completion_order", all(datetime.fromisoformat(a) <= datetime.fromisoformat(b) for a, b in pairwise(times)), times)
    audit.check("labels_and_no_new_test_asr_declarations", seal["heldout_labels_opened_this_run"] is False
                and protocol["new_test_asr_calls"] == validation["new_test_asr_calls"] == 0
                and protocol["test_gold_previously_exposed"] is True)
    manifests = {name: rows(paths[name + "_manifest"]) for name in ("dev", "test", *ROLES)}
    for name, values in manifests.items():
        for row in values:
            row["audio_path"] = str((paths[name + "_manifest"].parent / row["audio_path"]).resolve())
    verify_assignment(audit, paths, manifests)
    audio_rows = [*manifests["dev"], *manifests["test"]]
    audio_bad = [r["item_id"] for r in audio_rows if digest(r["audio_path"]) != r["audio_sha256"]]
    audit.check("all_selected_dev_and_test_audio_hashes", not audio_bad, {"count": len(audio_rows), "mismatches": audio_bad})
    dev_run = load(paths["dev_asr_run"])
    audit.check("completed_dev_asr_identity", dev_run["status"] == "completed"
                and dev_run["identity"]["expected_items"] == len(manifests["dev"])
                and dev_run["identity"]["manifest_sha256"] == digest(paths["dev_manifest"])
                and dev_run["results_sha256"] == digest(paths["dev_asr"]))
    audit.hashes("dev_asr_model_file_hashes", Path(dev_run["model_snapshot"]), dev_run["model_file_hashes"])
    audit.hashes("dev_asr_source_snapshot_hashes", paths["dev_asr_run"].parent / "source-snapshot", dev_run["source_hashes"])

    verify_checked_files(audit, protocol, paths)
    features, scored = {}, {}
    for role in (*ROLES, "test"):
        features[role] = rows(output / (role + "-features.jsonl"))
        scored[role] = rows(output / (role + "-scored.jsonl"))
        feature_arms = ("legacy",) if role == "test" else ARMS
        audit.require(role + " exact feature allowlists and numeric values", all(
            set(r["ml_features"]) == set(feature_arms) and all(
                set(r["ml_features"][arm]) == set(config["feature_names"][arm])
                and all(v is None or (not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v))
                        for v in r["ml_features"][arm].values()) for arm in feature_arms) for r in features[role]))
        source, feature, score = by_id(manifests[role]), by_id(features[role]), by_id(scored[role])
        reference = by_id(rows(paths[role + "_references"]))
        prediction = by_id(rows(output / "inputs" / (role + "-asr.jsonl")))
        audit.require(role + " exact IDs", set(source) == set(feature) == set(score) == set(reference) == set(prediction))
        mismatches = []
        for key in source:
            ref, pred, row = reference[key], prediction[key], score[key]
            expected_ref = normalize_english(ref["text"])
            expected_hyp = normalize_english(pred["asr_text"] if pred["status"] == "succeeded" else "")
            computed = edit_counts(expected_ref.split(), expected_hyp.split())
            if (ref["reference_id"] != source[key]["reference_id"] or ref["language"] != "en"
                    or pred["audio_sha256"] != source[key]["audio_sha256"]
                    or any(row[k] != v for k, v in computed.items())
                    or row["normalized_reference"] != expected_ref or row["normalized_hypothesis"] != expected_hyp
                    or any(row[k] != v for k, v in feature[key].items())):
                mismatches.append(key)
        audit.check(role + " independent_normalization_SDI_and_source_identity", not mismatches,
                    {"items": len(source), "mismatches": mismatches})
        audit.check(role + " failure_tier_and_tie_hash", all(row["failure_tier"] == tier(row) and row["tie_hash"] == tie(row) for row in features[role]))

    trials = load(output / "candidate-trials.json")
    winners = load(output / "selected-models.json")
    candidate_ids = {candidate["candidate_id"] for candidate in config["candidates"]}
    audit.require("complete_frozen_14_candidate_grid", len(trials) == 14 and len(candidate_ids) == 7
                  and {(r["arm"], r["candidate"]["candidate_id"]) for r in trials} == {(arm, cid) for arm in ARMS for cid in candidate_ids}
                  and all(r["status"] == "completed" for r in trials))
    objective = {}
    for trial in trials:
        arm, candidate = trial["arm"], trial["candidate"]["candidate_id"]
        folder = output / "candidates" / arm / candidate
        predictions, plan = load(folder / "tune-predictions.json"), load(folder / "tune-plan.json")
        verify_plan(audit, "tune " + arm + " " + candidate, features["tune"], plan, predictions)
        ranked = ordered(scored["tune"], predictions=predictions)
        value = sum(sum(row["errors"] for row in prefix(ranked, b)[0]) for b in (10, 20))
        objective[(arm, candidate)] = value
        audit.check("tune objective " + arm + " " + candidate, value == trial["objective_captured_error_sum"])
        replay_model(audit, folder / "model.joblib", features["tune"], arm, config["feature_names"][arm],
                     predictions, "candidate " + arm + " " + candidate, config["resolved_estimator_parameters"][candidate])
        card = load(folder / "model-card.json")
        audit.check("candidate train-only metadata " + arm + " " + candidate,
                    card["training_rows"] == len(features["train"])
                    and card["training_errors"] == sum(r["errors"] for r in scored["train"])
                    and card["estimator_parameters"] == config["resolved_estimator_parameters"][candidate]
                    and trial["model_sha256"] == digest(folder / "model.joblib"))
    selected = {}
    for arm in ARMS:
        winner = min(candidate_ids, key=lambda candidate: (-objective[(arm, candidate)], candidate))
        selected[arm] = winner
        audit.check("tune-only deterministic selected winner " + arm, winners["selected"][arm]["candidate"]["candidate_id"] == winner
                    and winners["holdout_and_test_labels_opened_this_run"] is False)
        card = load(output / ("model-" + arm + ".json"))
        audit.check("final refit parameters and input counts " + arm,
                    card["candidate"]["candidate_id"] == winner
                    and card["refit_roles"] == ["train", "tune"] and card["selected_from_tune_only"] is True
                    and card["training_rows"] == len(features["train"]) + len(features["tune"])
                    and card["training_errors"] == sum(r["errors"] for r in scored["train"] + scored["tune"])
                    and card["sha256"] == digest(output / ("model-" + arm + ".joblib")))

    headline = {}
    for cohort, role in COHORTS.items():
        plan = gzload(output / (cohort + "-plan-before-gold.json.gz"))
        summary = load(output / (cohort + "-summary.json"))
        active_arms = ARMS if role == "holdout" else ("legacy",)
        audit.check(cohort + " available arms", set(plan["ml"]) == set(active_arms))
        for arm in active_arms:
            predictions = load(output / (cohort + "-" + arm + "-predictions.json"))
            replay_model(audit, output / ("model-" + arm + ".joblib"), features[role], arm,
                         config["feature_names"][arm], predictions, cohort + " " + arm, config["resolved_estimator_parameters"][selected[arm]])
            verify_plan(audit, cohort + " " + arm, features[role], plan["ml"][arm], predictions)
            for group, current in groups(scored[role]).items():
                ranked = ordered(current, predictions=predictions)
                for budget in BUDGETS:
                    chosen, counts = prefix(ranked, budget)
                    actual = metrics(current, chosen, counts["target_audio_us"])
                    audit.check(f"{cohort} {group} {arm} metrics {budget}", same(actual, summary["groups"][group]["methods"]["ml_" + arm][str(budget)]))
                    if group == "pooled" and budget in (10, 20):
                        headline[f"{cohort}:{arm}:{budget}"] = actual
        random_rows = gzrows(output / (cohort + "-random-trials.jsonl.gz"))
        random_map = {(r["group"], r["seed"], r["budget_percent"]): r for r in random_rows}
        audit.require(cohort + " random trial unique complete grid", len(random_map) == len(random_rows) == len(groups(scored[role])) * 1000 * len(BUDGETS))
        for group, current in groups(scored[role]).items():
            baseline = plan["baseline"]["groups"][group]
            for method in METHODS:
                ranked = ordered(current, method)
                saved = baseline["methods"][method]
                audit.check(f"{cohort} {group} {method} full order", saved["order_ids"] == [r["item_id"] for r in ranked])
                for budget in BUDGETS:
                    chosen, counts = prefix(ranked, budget)
                    audit.check(f"{cohort} {group} {method} budget {budget}", saved["budgets"][str(budget)] == counts
                                and same(metrics(current, chosen, counts["target_audio_us"]), summary["groups"][group]["methods"][method][str(budget)]))
            planned_seeds = {r["seed"]: r for r in baseline["random"]}
            audit.require(cohort + " " + group + " all planned random seeds", set(planned_seeds) == set(range(20261017, 20262017))
                          and len(baseline["random"]) == 1000)
            random_bad, measured_bad = [], []
            stable = sorted(current, key=lambda row: row["item_id"])
            for seed in range(20261017, 20262017):
                ranked = list(stable)
                random.Random(seed).shuffle(ranked)
                saved = planned_seeds[seed]
                order_hash = hashlib.sha256(canonical([r["item_id"] for r in ranked]).encode()).hexdigest()
                if saved["order_sha256"] != order_hash or any(saved["budgets"][str(b)] != prefix(ranked, b)[1] for b in BUDGETS):
                    random_bad.append(seed)
                if seed in SEEDS_TO_MEASURE:
                    for budget in BUDGETS:
                        chosen, counts = prefix(ranked, budget)
                        raw = random_map[(group, seed, budget)]
                        saved_metric = {k: v for k, v in raw.items() if k not in {"group", "seed", "budget_percent"}}
                        if not same(metrics(current, chosen, counts["target_audio_us"]), saved_metric):
                            measured_bad.append([seed, budget])
            audit.check(cohort + " " + group + " all 1000 random order hashes and budgets", not random_bad, random_bad)
            audit.check(cohort + " " + group + " four random seed metric replays", not measured_bad, measured_bad)
            for budget in BUDGETS:
                metrics_rows = [{k: v for k, v in random_map[(group, seed, budget)].items()
                                 if k not in {"group", "seed", "budget_percent"}} for seed in range(20261017, 20262017)]
                audit.check(f"{cohort} {group} random aggregation {budget}", same(aggregate(metrics_rows), summary["groups"][group]["random"][str(budget)]))
        if role == "test":
            old = load(paths["v1_summary"])["comparison"]["groups"]
            restored = {name: {**value, "methods": {method: metric for method, metric in value["methods"].items() if method in METHODS}}
                        for name, value in summary["groups"].items()}
            audit.check("all_test_baseline_random_and_totals_exactly_equal_v1", restored == old)
    audit.hashes("artifacts_still_unchanged_after_audit", output, index)
    return {"heldout_and_test_gold_opened_at_utc": opened, "selected_candidates": selected,
            "headline_recomputed": headline, "scope": {"scored_roles": [*ROLES, "test"],
            "all_original_selected_audio_hashes": len(audio_rows), "all_candidate_tune_objectives": 14,
            "all_final_model_predictions_replayed": True, "all_1000_random_plans_replayed": True,
            "random_metric_seeds_recomputed": list(SEEDS_TO_MEASURE), "all_baseline_and_ml_budgets_recomputed": list(BUDGETS)}}


def self_test():
    assert normalize_english("Don't—25.5% TEST!") == "dont 25.5% test"
    assert edit_counts(["a", "b"], ["a", "c", "d"]) == {"errors": 2, "substitutions": 1, "deletions": 0, "insertions": 1, "reference_units": 2, "hypothesis_units": 3}
    samples = [{"item_id": str(i), "audio_duration_s": d} for i, d in enumerate((1, 8, 1))]
    chosen, counts = prefix(samples, 20)
    assert [r["item_id"] for r in chosen] == ["0"] and counts == {"selected_count": 1, "target_audio_us": 2000000, "selected_audio_us": 1000000}
    assert prefix(samples, 100)[0] == samples
    assert aggregate([{"x": None}, {"x": None}])["x"]["defined_count"] == 0
    print("Synthetic audit self-test passed; no experiment data opened.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-dir", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    study = args.study_dir.resolve()
    state_path = study / "outputs/status.json"
    if (not state_path.is_file() or load(state_path).get("status") != "completed"
            or not (study / "outputs/artifact-hashes.json").is_file()
            or not (study / "outputs/evaluation-seal.json").is_file()):
        print("Not ready: completed status, artifact index and seal required; no gold opened.")
        return 2
    audit = Audit()
    result = {"schema_version": "speechrank-independent-audit-v2", "started_at_utc": utc(),
              "audit_continued_by_model": "gpt-6.1-sol", "study": str(study), "audit_source_sha256": digest(__file__)}
    started = time.perf_counter()
    try:
        result.update(execute(study, audit))
    except Exception as exc:  # noqa: BLE001 - Preserve evidence of any audit failure.
        audit.check("audit_completed_without_exception", False, f"{type(exc).__name__}: {exc}")
    result.update(status="passed" if all(r["passed"] for r in audit.checks) else "failed",
                  completed_at_utc=utc(), elapsed_seconds=time.perf_counter() - started, checks=audit.checks)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "checks": len(audit.checks), "output": str(OUTPUT)}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
