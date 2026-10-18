"""Speaker-isolated development, frozen ML ranking, and one-shot evaluation."""
from __future__ import annotations

import argparse
import gzip
import importlib.metadata
import json
import math
import platform
import shutil
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_qc_ranking import analyze_cohort, check_prediction_identity
from scripts.evaluate_speech_smoke import evaluate
from scripts.qc_ranking_metrics import (
    DEFAULT_CONFIG as RANK_CONFIG,
)
from scripts.qc_ranking_metrics import (
    build_selection_plan,
    evaluate_rankings,
    select_prefix,
    selection_metrics,
)
from scripts.speechrank_input_audit import audit_inputs, verify_v1
from speechprep.manifest import atomic_jsonl, canonical_json, file_sha256, read_manifest
from speechprep.state import exclusive_run
from speechqc.schema import validate_report
from speechqc.vad import vad_provenance

V1 = ROOT / "evals/runs/2026-10-17-librispeech-qc-ranking-v1"
DEV = ROOT / "runtime/datasets/librispeech/official-dev/sample-ml-v2"
DEV_ASR = ROOT / "runtime/librispeech-dev-ml-v2-r1"
ARMS = ("legacy", "confidence")
ROLES = ("train", "tune", "holdout")
GOLD_FIELDS = ("errors", "reference_units", "hypothesis_units", "substitutions", "deletions",
               "insertions", "normalized_reference", "normalized_hypothesis")


def utc():
    return datetime.now(UTC).isoformat()


def save(path, value):
    atomic_jsonl(path, [value])


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def rows(path):
    values = [json.loads(x) for x in Path(path).read_text(encoding="utf-8-sig").splitlines() if x.strip()]
    if not values or len({r["item_id"] for r in values}) != len(values):
        raise ValueError("empty input or duplicate item IDs")
    return values


def rel(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def record(path):
    return {"path": rel(path), "sha256": file_sha256(path)}


def gzip_json(path, value):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(canonical_json(value) + "\n")


def env_identity():
    packages = ("numpy", "scipy", "scikit-learn", "joblib", "threadpoolctl", "soundfile",
                "faster-whisper", "ctranslate2", "onnxruntime", "av", "matplotlib")
    return {"python": sys.version, "platform": platform.platform(),
            "packages": {p: importlib.metadata.version(p) for p in packages}, "vad": vad_provenance()}


def source_files():
    return sorted({*ROOT.joinpath("speechrank").glob("*.py"), *ROOT.joinpath("speechqc").glob("*.py"),
        *ROOT.joinpath("speechprep").rglob("*.py"), ROOT / "scripts/evaluate_ml_ranking.py",
        ROOT / "scripts/run_speechrank_dev_asr.py", ROOT / "scripts/prepare_librispeech_dev.py",
        ROOT / "scripts/speechrank_input_audit.py",
        ROOT / "scripts/qc_ranking_metrics.py", ROOT / "scripts/evaluate_qc_ranking.py",
        ROOT / "scripts/evaluate_speech_smoke.py", ROOT / "requirements-speechrank.txt",
        ROOT / "requirements-audioqc-eval.txt", ROOT / "requirements-audioqc.txt",
        ROOT / "docs/audio-qc-ml-ranking-protocol-v2.md",
        *ROOT.joinpath("tests").glob("test_speechrank*.py"),
        ROOT / "tests/test_ml_ranking_runner.py", ROOT / "tests/test_prepare_librispeech_dev.py"})


def freeze(study):
    from speechrank.features import FEATURE_NAMES, FEATURE_VERSION
    from speechrank.models import build_estimator, get_candidates

    if study.exists():
        raise ValueError("study already exists; never overwrite an experiment")
    dev_run = load(DEV_ASR / "run.json")
    if dev_run["status"] != "completed":
        raise ValueError("development ASR must be completed before freezing training inputs")
    if file_sha256(DEV_ASR / "asr.jsonl") != dev_run["results_sha256"]:
        raise ValueError("development ASR checksum mismatch")
    input_paths = {"dev_manifest": DEV / "manifest.jsonl", "dev_assignment": DEV / "assignment.json",
                   "dev_selection": DEV / "selection.json", "dev_asr": DEV_ASR / "asr.jsonl",
                   "dev_asr_run": DEV_ASR / "run.json", "v1_config": V1 / "config.json",
                   "dev_prepared_manifest": DEV.parent / "prepared/manifest.jsonl",
                   "dev_prepared_references": DEV.parent / "prepared/references.jsonl",
                   "dev_verification": DEV.parent / "prepared/verification.json",
                   "dev_download": DEV.parent / "download-verification.json",
                   "test_source_manifest": ROOT / "runtime/datasets/librispeech/official-test/prepared/manifest.jsonl",
                   "v1_protocol": V1 / "protocol.json",
                   "v1_features": V1 / "cohorts/full/qc-features.jsonl",
                   "v1_qc_reports": V1 / "cohorts/full/qc-reports.jsonl.gz",
                   "v1_seal": V1 / "cohorts/full/ranking-seal.json",
                   "v1_summary": V1 / "cohorts/full/summary.json",
                   "v1_artifacts": V1 / "cohorts/full/artifact-hashes.json",
                   "test_asr_run": ROOT / "runtime/librispeech-base-2026-10-17/run.json"}
    old_seal = load(input_paths["v1_seal"])
    old_artifacts = load(input_paths["v1_artifacts"])
    for name, seal_key in (("v1_features", "features_sha256"), ("v1_qc_reports", "qc_reports_sha256")):
        if file_sha256(input_paths[name]) != old_seal[seal_key]:
            raise ValueError("v1 feature/report differs from its pre-gold seal")
    for name in ("v1_features", "v1_qc_reports", "v1_seal", "v1_summary"):
        if file_sha256(input_paths[name]) != old_artifacts[input_paths[name].name]:
            raise ValueError("v1 artifact differs from original index")
    old_inputs = load(V1 / "cohorts/full/inputs.json")
    for name in ("manifest", "asr", "references"):
        input_paths["test_" + name] = ROOT / old_inputs[name]["path"]
        if file_sha256(input_paths["test_" + name]) != old_inputs[name]["sha256"]:
            raise ValueError("old benchmark input changed")
    for role in ROLES:
        input_paths[role + "_manifest"] = DEV / role / "manifest.jsonl"
        input_paths[role + "_references"] = DEV / role / "references.jsonl"
    manifests = {k: read_manifest(input_paths[k + "_manifest"]) for k in ("dev", "test", *ROLES)}
    validate_disjoint(manifests)
    input_audit = audit_inputs(input_paths, dev_run, manifests)
    v1_audit = verify_v1(input_paths)
    inputs = {name: record(path) for name, path in input_paths.items()}
    resolved_parameters = {c["candidate_id"]: build_estimator(c).named_steps["model"].get_params(deep=False)
                           for c in get_candidates()}
    config = {"ranking": dict(RANK_CONFIG), "arms": list(ARMS), "candidates": get_candidates(),
              "feature_version": FEATURE_VERSION, "feature_names": {k: list(v) for k, v in FEATURE_NAMES.items()},
              "resolved_estimator_parameters": resolved_parameters,
              "candidate_failure_policy": "record then stop whole experiment; never silently drop candidates",
              "warning_policy": "record; finite completed fits remain eligible",
              "failure_tier_policy": "v1: ASR failure 2, QC/coverage unavailable 1, else 0; then predicted density descending; v1 tie hash ascending",
              "target": "errors/audio_duration_s", "sample_weight": "audio_duration_s",
              "selection": "maximize tune captured_errors(10)+captured_errors(20), then candidate_id ascending",
              "refit_roles": ["train", "tune"], "primary_evaluation": "fresh_holdout",
              "secondary_evaluation": "previously_exposed_test", "test_confidence_arm": "unavailable; never imputed as a measured comparison"}
    study.mkdir(parents=True)
    save(study / "config.json", config)
    source_hashes = {}
    for source in source_files():
        target = study / "source-snapshot" / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_hashes[rel(source)] = file_sha256(source)
    protocol = {"schema_version": "speechrank-study-v2", "frozen_at_utc": utc(), "inputs": inputs,
                "source_hashes": source_hashes, "environment": env_identity(),
                "new_test_asr_calls": 0, "test_gold_previously_exposed": True,
                "input_audit": input_audit, "v1_identity_audit": v1_audit,
                "new_dev_asr_calls": dev_run["new_dev_asr_calls"], "config_sha256": file_sha256(study / "config.json")}
    save(study / "protocol.json", protocol)
    save(study / "frozen-hashes.json", {rel(p): file_sha256(p) for p in study.rglob("*") if p.is_file()})
    print(canonical_json({"phase": "frozen", "study": rel(study)}), flush=True)


def verify(study):
    protocol = load(study / "protocol.json")
    for path, digest in load(study / "frozen-hashes.json").items():
        if file_sha256(ROOT / path) != digest:
            raise ValueError("frozen experiment artifact changed: " + path)
    for path, digest in protocol["source_hashes"].items():
        if file_sha256(ROOT / path) != digest:
            raise ValueError("frozen source changed: " + path)
    for value in protocol["inputs"].values():
        if file_sha256(ROOT / value["path"]) != value["sha256"]:
            raise ValueError("frozen input changed: " + value["path"])
    if env_identity() != protocol["environment"]:
        raise ValueError("frozen environment/model changed")
    return protocol


def validate_disjoint(manifests):
    all_dev = set()
    for role in ROLES:
        current = manifests[role]
        if len({r["item_id"] for r in current}) != len(current):
            raise ValueError("duplicate role IDs")
        if {r["split"] for r in current} - {"dev-clean", "dev-other"}:
            raise ValueError("nondevelopment recording in role")
        all_dev.update(r["item_id"] for r in current)
    if all_dev != {r["item_id"] for r in manifests["dev"]}:
        raise ValueError("development role union mismatch")
    names = [*ROLES, "test"]
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            for key in ("item_id", "speaker_id", "audio_sha256"):
                if {r[key] for r in manifests[left]} & {r[key] for r in manifests[right]}:
                    raise ValueError(f"{key} leakage between {left} and {right}")


def validate_asr(manifest, predictions, identity, *, dev=False):
    expected = {r["item_id"]: r for r in manifest}
    if set(predictions) != set(expected):
        raise ValueError("ASR/manifest IDs mismatch")
    for item_id, prediction in predictions.items():
        source = expected[item_id]
        if prediction["audio_sha256"] != source["audio_sha256"]:
            raise ValueError("ASR/audio identity mismatch")
        if prediction["status"] not in {"succeeded", "failed"}:
            raise ValueError("ASR terminal status required")
        if dev:
            if prediction["config"] != identity["identity"]["config"]:
                raise ValueError("dev ASR config mismatch")
            if prediction["model"]["revision"] != identity["identity"]["model_revision"]:
                raise ValueError("dev model revision mismatch")
        else:
            check_prediction_identity(prediction, identity)
        if prediction["status"] == "succeeded" and not isinstance(prediction.get("asr_text"), str):
            raise ValueError("successful ASR text missing")
        if file_sha256(source["audio_path"]) != source["audio_sha256"]:
            raise ValueError("original audio hash mismatch")


def groups(values):
    return {"pooled": list(values), **{"split:" + name: [r for r in values if r["source_split"] == name]
            for name in sorted({r["source_split"] for r in values})}}


def ordered_ml(values, predictions):
    if len({r["item_id"] for r in values}) != len(values):
        raise ValueError("duplicate ML item IDs")
    if set(predictions) != {r["item_id"] for r in values}:
        raise ValueError("prediction ID set mismatch")
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) or x < 0
           for x in predictions.values()):
        raise ValueError("invalid predicted error density")
    return sorted(values, key=lambda r: (-r["failure_tier"], -predictions[r["item_id"]], r["tie_hash"]))


def plan_ml(values, predictions, budgets):
    ordered_ml(values, predictions)  # Validate the complete ID set before grouping.
    result = {}
    for name, current in groups(values).items():
        ordered = ordered_ml(current, {r["item_id"]: predictions[r["item_id"]] for r in current})
        selections = {}
        for budget in budgets:
            selected, target = select_prefix(ordered, budget)
            selections[str(budget)] = {"selected_count": len(selected), "target_audio_us": target,
                "selected_audio_us": sum(round(r["audio_duration_s"] * 1_000_000) for r in selected)}
        result[name] = {"order_ids": [r["item_id"] for r in ordered], "budgets": selections}
    return result


def measured_ml(values, plan):
    result = {}
    for name, current in groups(values).items():
        by_id = {r["item_id"]: r for r in current}
        saved = plan[name]
        if set(saved["order_ids"]) != set(by_id) or len(saved["order_ids"]) != len(by_id):
            raise ValueError("sealed ML order ID mismatch")
        ordered = [by_id[key] for key in saved["order_ids"]]
        result[name] = {}
        for budget, selection in saved["budgets"].items():
            selected, target = select_prefix(ordered, int(budget))
            if (len(selected) != selection["selected_count"] or target != selection["target_audio_us"]
                    or sum(round(r["audio_duration_s"] * 1_000_000) for r in selected) != selection["selected_audio_us"]):
                raise ValueError("sealed ML budget mismatch")
            result[name][budget] = selection_metrics(current, selected, target)
    return result


def join_gold(features, score):
    gold = {r["item_id"]: r for r in score["items"]}
    if set(gold) != {r["item_id"] for r in features}:
        raise ValueError("feature/gold IDs mismatch")
    return [{**r, **{k: gold[r["item_id"]][k] for k in GOLD_FIELDS}} for r in features]


def materialize_features(qc_rows, reports, predictions, arms):
    from speechrank.features import extract_features

    by_id = {r["item_id"]: r for r in qc_rows}
    output, seen = [], set()
    with gzip.open(reports, "rt", encoding="utf-8") as stream:
        for line in stream:
            raw = json.loads(line)
            key = raw["item_id"]
            if key in seen or key not in by_id:
                raise ValueError("QC report ID mismatch")
            seen.add(key)
            source = by_id[key]
            asr = {**predictions[key], "audio_duration_s": source["audio_duration_s"]}
            if source["audio_sha256"] != asr["audio_sha256"] or source["asr_status"] != asr["status"]:
                raise ValueError("QC feature/ASR identity mismatch")
            report = raw.get("report")
            if report is not None:
                transcript = asr if asr["status"] == "succeeded" else {}
                validate_report(report, audio_sha256=source["audio_sha256"], transcript=transcript,
                                duration_s=source["audio_duration_s"])
                if (report["issues"] != source["issues"] or report["status"] != source["qc_status"]
                        or report["coverage"]["status"] != source["qc_coverage_status"]):
                    raise ValueError("QC feature/report differs")
            elif source["qc_status"] == "complete":
                raise ValueError("complete QC feature has no raw report")
            output.append({**source, "ml_features": {arm: extract_features(asr, raw.get("report"), arm=arm)
                                                     for arm in arms}})
    if seen != set(by_id):
        raise ValueError("QC report missing")
    return sorted(output, key=lambda r: r["item_id"])


def score_role(output, name, feature_rows, references, manifest):
    score = evaluate(output / "inputs" / (name + "-asr.jsonl"), references, manifest,
                     evaluation_kind="benchmark", purpose="Frozen ML triage ranking; independent development roles")
    scored = join_gold(feature_rows, score)
    atomic_jsonl(output / (name + "-scored.jsonl"), scored)
    return scored


def choose_candidate(trials):
    if not trials:
        raise ValueError("no candidate trials")
    return min(trials, key=lambda r: (-r["objective_captured_error_sum"], r["candidate"]["candidate_id"]))


def train_select_refit(output, features, scored, config):
    import joblib

    from speechrank.models import fit_model, model_card, predict_density

    selected, models, trials = {}, {}, []
    for arm in ARMS:
        arm_trials = []
        for candidate in config["candidates"]:
            started = time.perf_counter()
            trial = {"arm": arm, "candidate": candidate, "status": "running", "started_at_utc": utc()}
            arm_trials.append(trial)
            trials.append(trial)
            save(output / "candidate-trials.json", trials)
            caught = []
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    model = fit_model([r["ml_features"][arm] for r in features["train"]],
                        [r["errors"] for r in scored["train"]], [r["audio_duration_s"] for r in features["train"]],
                        candidate, arm=arm)
                    scores = predict_density(model, [r["ml_features"][arm] for r in features["tune"]], arm=arm)
            except BaseException as exc:
                trial.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                             warnings=[{"category": w.category.__name__, "message": str(w.message)} for w in caught])
                save(output / "candidate-trials.json", trials)
                raise
            by_id = dict(zip([r["item_id"] for r in features["tune"]], scores, strict=True))
            plan = plan_ml(features["tune"], by_id, config["ranking"]["budgets_percent"])
            measures = measured_ml(scored["tune"], plan)
            folder = output / "candidates" / arm / candidate["candidate_id"]
            folder.mkdir(parents=True)
            joblib.dump(model, folder / "model.joblib")
            save(folder / "model-card.json", model_card(model))
            save(folder / "tune-predictions.json", by_id)
            save(folder / "tune-plan.json", plan)
            trial.update({"status": "completed", "tune_metrics": measures,
                     "objective_captured_error_sum": sum(measures["pooled"][str(b)]["captured_errors"] for b in (10, 20)),
                     "fit_predict_seconds": time.perf_counter() - started,
                     "warnings": [{"category": w.category.__name__, "message": str(w.message)} for w in caught],
                     "model_sha256": file_sha256(folder / "model.joblib")})
            save(output / "candidate-trials.json", trials)
            print(canonical_json({"phase": "tune", "arm": arm, "candidate": candidate["candidate_id"]}), flush=True)
        winner = choose_candidate(arm_trials)
        selected[arm] = winner
        combined = [*scored["train"], *scored["tune"]]
        refit_started = time.perf_counter()
        refit_status = {"arm": arm, "candidate": winner["candidate"], "status": "running", "started_at_utc": utc()}
        save(output / ("refit-" + arm + ".json"), refit_status)
        refit_warnings = []
        try:
            with warnings.catch_warnings(record=True) as refit_warnings:
                warnings.simplefilter("always")
                model = fit_model([r["ml_features"][arm] for r in combined], [r["errors"] for r in combined],
                                  [r["audio_duration_s"] for r in combined], winner["candidate"], arm=arm)
        except BaseException as exc:
            refit_status.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                warnings=[{"category": w.category.__name__, "message": str(w.message)} for w in refit_warnings])
            save(output / ("refit-" + arm + ".json"), refit_status)
            raise
        model_path = output / ("model-" + arm + ".joblib")
        joblib.dump(model, model_path)
        card = {**model_card(model), "sha256": file_sha256(model_path),
                "refit_roles": ["train", "tune"], "selected_from_tune_only": True,
                "refit_seconds": time.perf_counter() - refit_started,
                "refit_warnings": [{"category": w.category.__name__, "message": str(w.message)} for w in refit_warnings]}
        save(output / ("model-" + arm + ".json"), card)
        refit_status.update(status="completed", completed_at_utc=utc(), model_sha256=card["sha256"],
                            warnings=card["refit_warnings"])
        save(output / ("refit-" + arm + ".json"), refit_status)
        models[arm] = model
    save(output / "candidate-trials.json", trials)
    save(output / "selected-models.json", {"selected_at_utc": utc(), "selected": selected,
                                           "holdout_and_test_labels_opened_this_run": False})
    return models


def render(output, summaries):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def plot_percent(value):
        return float("nan") if value is None else 100 * value

    lines = ["# LibriSpeech 轻量 ML 复核排序 v2", "",
        "开发数据按说话人分离训练/调参/锁定评测；旧 test 在 v1 已被查看，作为独立于训练的已暴露复用基准。",
        "旧 ASR 未存词/句置信度：test 仅评 legacy 模型；真实置信度来自 dev 的首次转录，holdout 同时比较两组模型。",
        "错误覆盖指选中整录音包含的 S+D+I；预算为音频时长，不是人工耗时。随机区间是 1,000 个固定顺序的波动。", ""]
    for name, summary in summaries.items():
        lines += ["## " + name, "", "| 分组 | 时长预算 | 方法 | 错误覆盖 | 实际时长比例 | 剩余 WER |",
                  "| --- | ---: | --- | ---: | ---: | ---: |"]
        fig, axes = plt.subplots(1, len(summary["groups"]), figsize=(6 * len(summary["groups"]), 4.5), squeeze=False)
        for ax, (group, detail) in zip(axes[0], summary["groups"].items()):
            methods = ["qc", "shortest", "ml_legacy"] + (["ml_confidence"] if name == "fresh_holdout" else [])
            for budget in (10, 20):
                for method in methods:
                    metric = detail["methods"][method][str(budget)]
                    pct = lambda x: "NA" if x is None else f"{100*x:.2f}%"
                    lines.append(f"| {group} | {budget}% | {method} | {pct(metric['error_capture_rate'])} | {pct(metric['actual_audio_fraction'])} | {pct(metric['residual_wer'])} |")
                random = detail["random"][str(budget)]
                lines.append(f"| {group} | {budget}% | random mean | {pct(random['error_capture_rate']['mean'])} | {pct(random['actual_audio_fraction']['mean'])} | {pct(random['residual_wer']['mean'])} |")
            budgets = [5, 10, 20, 30, 50]
            for method in methods:
                ax.plot(budgets, [plot_percent(detail["methods"][method][str(b)]["error_capture_rate"]) for b in budgets], "o-", label=method)
            random = [detail["random"][str(b)]["error_capture_rate"] for b in budgets]
            ax.plot(budgets, [plot_percent(r["mean"]) for r in random], "--", label="random mean")
            ax.fill_between(budgets, [plot_percent(r["p2_5"]) for r in random], [plot_percent(r["p97_5"]) for r in random], alpha=.15)
            ax.set(title=f"{group} (n={detail['totals']['count']})", xlabel="Audio-duration budget (%)",
                   ylabel="Errors in selected clips (%)", ylim=(0, 100))
            ax.grid(alpha=.2)
            ax.legend(fontsize=8)
        fig.suptitle(name + " | frozen ML models; no measured human savings")
        fig.tight_layout()
        fig.savefig(output / (name + ".png"), dpi=160)
        fig.savefig(output / (name + ".svg"))
        plt.close(fig)
        lines += ["", f"![{name}]({name}.png)", ""]
    lines += ["## 证据", "", "- `candidate-trials.json` 保存全部 14 个候选的调参指标和警告；`selected-models.json` 记录选择。",
        "- `model-*.json` / `model-*.joblib` 保存最终特征顺序、训练元数据及模型；只加载可信本地模型文件。",
        "- `evaluation-seal.json` 绑定模型、特征、预测、全部排序/预算计划，在 holdout/test 本轮评分前生成。",
        "- `*-summary.json`、`*-scored.jsonl`、`*-random-trials.jsonl.gz` 保存完整分组、逐条错误与随机明细。",
        "- `confidence-availability.json` 区分真实保存的置信度、缺失字段和常量语言概率。",
        "- `validation.json`、`artifact-hashes.json` 保存完整性校验。原 v1 产物未改写。",
        "", "方法文档：[冻结协议](../source-snapshot/docs/audio-qc-ml-ranking-protocol-v2.md)。",
        "数据来源：[LibriSpeech](https://www.openslr.org/12/)，CC BY 4.0。", ""]
    (output / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def run(study):
    from speechrank.features import audit_confidence_availability
    from speechrank.models import predict_density

    with exclusive_run(study / "evaluation"):
        protocol = verify(study)
        config = load(study / "config.json")
        output = study / "outputs"
        if output.exists():
            raise ValueError("output exists; keep it and use a new study for reruns")
        output.mkdir()
        save(output / "status.json", {"status": "running", "started_at_utc": utc()})
        started = time.perf_counter()
        timings = {}
        try:
            paths = {k: ROOT / r["path"] for k, r in protocol["inputs"].items()}
            manifests = {k: read_manifest(paths[k + "_manifest"]) for k in ("dev", "test", *ROLES)}
            validate_disjoint(manifests)
            if audit_inputs(paths, load(paths["dev_asr_run"]), manifests) != protocol["input_audit"]:
                raise ValueError("input chain audit differs from freeze")
            if verify_v1(paths) != protocol["v1_identity_audit"]:
                raise ValueError("v1 identity audit differs from freeze")
            asr = {name: {r["item_id"]: r for r in rows(paths[name + "_asr"])} for name in ("dev", "test")}
            validate_asr(manifests["dev"], asr["dev"], load(paths["dev_asr_run"]), dev=True)
            validate_asr(manifests["test"], asr["test"], load(paths["test_asr_run"]))
            (output / "inputs").mkdir()
            for role in (*ROLES, "test"):
                predictions = asr["test" if role == "test" else "dev"]
                atomic_jsonl(output / "inputs" / (role + "-asr.jsonl"), [predictions[r["item_id"]] for r in manifests[role]])
            save(output / "confidence-availability.json", {name: audit_confidence_availability(list(values.values())) for name, values in asr.items()})
            qc_dir = output / "dev-qc"
            qc_dir.mkdir()
            qc_rows, _ = analyze_cohort(manifests["dev"], asr["dev"], qc_dir,
                ROOT / "runtime/audio-qc-ml-v2/cache", load(paths["v1_config"]))
            dev_features = materialize_features(qc_rows, qc_dir / "qc-reports.jsonl.gz", asr["dev"], ARMS)
            features = {"test": materialize_features(rows(paths["v1_features"]), paths["v1_qc_reports"], asr["test"], ("legacy",))}
            for role in ROLES:
                ids = {r["item_id"] for r in manifests[role]}
                features[role] = [r for r in dev_features if r["item_id"] in ids]
            for name, values in features.items():
                atomic_jsonl(output / (name + "-features.jsonl"), values)
            timings["input_validation_qc_features_s"] = time.perf_counter() - started
            train_gold_at = utc()
            phase_start = time.perf_counter()
            scored = {role: score_role(output, role, features[role], paths[role + "_references"], paths[role + "_manifest"])
                      for role in ("train", "tune")}
            if any(sum(r["errors"] for r in scored[role]) == 0 for role in ("train", "tune")):
                raise ValueError("train/tune error count must be nonzero")
            timings["train_tune_gold_scoring_s"] = time.perf_counter() - phase_start
            phase_start = time.perf_counter()
            models = train_select_refit(output, features, scored, config)
            timings["candidate_fit_tune_selection_refit_s"] = time.perf_counter() - phase_start
            phase_start = time.perf_counter()
            plans = {}
            cohorts = {"fresh_holdout": "holdout", "previously_exposed_test": "test"}
            for name, role in cohorts.items():
                current = features[role]
                plan = {"baseline": build_selection_plan(current, config["ranking"]), "ml": {}}
                for arm in ARMS if role == "holdout" else ("legacy",):
                    predictions = predict_density(models[arm], [r["ml_features"][arm] for r in current], arm=arm)
                    by_id = dict(zip([r["item_id"] for r in current], predictions, strict=True))
                    save(output / (name + "-" + arm + "-predictions.json"), by_id)
                    plan["ml"][arm] = plan_ml(current, by_id, config["ranking"]["budgets_percent"])
                gzip_json(output / (name + "-plan-before-gold.json.gz"), plan)
                plans[name] = plan
            timings["final_prediction_and_plans_s"] = time.perf_counter() - phase_start
            verify(study)
            seal = {"sealed_at_utc": utc(), "train_tune_gold_started_at_utc": train_gold_at,
                    "heldout_labels_opened_this_run": False,
                    "files": {p.relative_to(output).as_posix(): file_sha256(p) for p in output.rglob("*")
                              if p.is_file() and p.name != "status.json"},
                    "selection_roles": ["train", "tune"], "test_previously_exposed": True}
            save(output / "evaluation-seal.json", seal)
            score_at = utc()
            phase_start = time.perf_counter()
            summaries = {}
            for name, role in cohorts.items():
                current = score_role(output, role, features[role], paths[role + "_references"], paths[role + "_manifest"])
                if build_selection_plan(current, config["ranking"]) != plans[name]["baseline"]:
                    raise ValueError("baseline/random plan changed after gold")
                summary = evaluate_rankings(current, config["ranking"])
                trials = summary.pop("random_trials")
                if role == "test" and summary["groups"] != load(paths["v1_summary"])["comparison"]["groups"]:
                    raise ValueError("reused test baselines differ from frozen v1 results")
                with gzip.open(output / (name + "-random-trials.jsonl.gz"), "wt", encoding="utf-8") as stream:
                    for trial in trials:
                        stream.write(canonical_json(trial) + "\n")
                for arm, plan in plans[name]["ml"].items():
                    measured = measured_ml(current, plan)
                    for group, budgets in measured.items():
                        summary["groups"][group]["methods"]["ml_" + arm] = budgets
                summary.update(cohort=name, role=role, items=len(current),
                               confidence_arm_available=role == "holdout")
                summary["failure_tiers"] = {str(tier): {"count": sum(r["failure_tier"] == tier for r in current),
                    "audio_s": sum(r["audio_duration_s"] for r in current if r["failure_tier"] == tier),
                    "errors": sum(r["errors"] for r in current if r["failure_tier"] == tier)} for tier in (0, 1, 2)}
                save(output / (name + "-summary.json"), summary)
                summaries[name] = summary
            render(output, summaries)
            timings["final_gold_metrics_and_render_s"] = time.perf_counter() - phase_start
            verify(study)
            # status is the only intentionally mutable pre-seal progress artifact.
            if any(file_sha256(output / p) != h for p, h in seal["files"].items()):
                raise ValueError("pre-evaluation artifact changed")
            all_sources = [*manifests["dev"], *manifests["test"]]
            if any(file_sha256(r["audio_path"]) != r["audio_sha256"] for r in all_sources):
                raise ValueError("original audio changed during evaluation")
            timings["total_evaluation_s"] = time.perf_counter() - started
            timings["new_dev_asr_s_separate"] = load(paths["dev_asr_run"]).get("elapsed_seconds")
            timings["timing_policy"] = "Observed wall times; QC content cache allowed; concurrent machine load not isolated"
            save(output / "timings.json", timings)
            save(output / "validation.json", {"status": "passed", "speaker_id_audio_roles_disjoint": True,
                "source_environment_inputs_unchanged": True, "all_audio_hashes_checked": len(all_sources),
                "sealed_at_utc": seal["sealed_at_utc"], "heldout_gold_started_at_utc": score_at,
                "all_baseline_random_plans_unchanged": True, "all_ml_selections_from_sealed_order": True,
                "reused_test_baselines_exactly_match_v1": True,
                "sealed_artifacts_unchanged": True, "new_test_asr_calls": 0,
                "new_dev_asr_calls": protocol["new_dev_asr_calls"], "new_human_labels": 0,
                "elapsed_seconds": time.perf_counter() - started})
            save(output / "status.json", {"status": "completed", "completed_at_utc": utc()})
            save(output / "artifact-hashes.json", {p.relative_to(output).as_posix(): file_sha256(p) for p in output.rglob("*") if p.is_file()})
            print(canonical_json({"status": "completed", "study": rel(study)}), flush=True)
        except BaseException as exc:
            save(output / "status.json", {"status": "failed", "at_utc": utc(), "error": f"{type(exc).__name__}: {exc}"})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run"])
    parser.add_argument("--study-dir", type=Path, required=True)
    args = parser.parse_args()
    (freeze if args.command == "freeze" else run)(args.study_dir.resolve())


if __name__ == "__main__":
    main()
