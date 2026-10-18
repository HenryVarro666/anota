"""Frozen, reference-isolated QC ranking study over existing ASR outputs only."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import math
import platform
import shutil
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_speech_smoke import evaluate
from scripts.qc_ranking_metrics import (
    build_selection_plan,
    evaluate_rankings,
    priority_score,
    rank_rows,
)
from speechprep.state import exclusive_run
from speechqc.cache import analyze_cached, atomic_jsonl
from speechqc.schema import DEFAULT_CONFIG, canonical_json, file_sha256, validate_report
from speechqc.vad import vad_provenance


def utc():
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017 - preserve Python 3.10 time API


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    atomic_jsonl(path, [value])


def read_rows(path):
    rows = [json.loads(s) for s in Path(path).read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    ids = [r["item_id"] for r in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError("empty input or duplicate item IDs")
    return rows


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def input_record(path):
    return {"path": relative(path), "sha256": file_sha256(path)}


def source_paths():
    return sorted([*ROOT.joinpath("speechqc").glob("*.py"),
                   ROOT / "scripts/evaluate_qc_ranking.py", ROOT / "scripts/qc_ranking_metrics.py",
                   ROOT / "scripts/evaluate_speech_smoke.py", ROOT / "speechprep/state.py",
                   ROOT / "speechprep/manifest.py", ROOT / "tests/test_qc_ranking_metrics.py",
                   ROOT / "tests/test_qc_ranking_runner.py", ROOT / "requirements-audioqc.txt",
                   ROOT / "requirements-audioqc-eval.txt",
                   ROOT / "docs/audio-qc-ranking-protocol-v1.md"])


def asr_identity(metadata):
    result = {k: metadata[k] for k in ("dataset", "expected_items", "manifest", "references", "model",
                                    "device", "compute_type", "beam_size", "vad_filter",
                                    "word_timestamps", "condition_on_previous_text")}
    for key in ("cpu_threads_per_worker", "runtime_versions"):
        if key in metadata:
            result[key] = metadata[key]
    return result


def environment_identity():
    packages = {name: importlib.metadata.version(name) for name in
                ("numpy", "soundfile", "faster-whisper", "onnxruntime", "av", "matplotlib")}
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": packages, "vad_model_provenance": vad_provenance()}


def freeze(study, manifest, references, asr_run):
    """Hash reference bytes without parsing/scoring labels; freeze before QC."""
    study, manifest, references, asr_run = (Path(p).resolve() for p in (study, manifest, references, asr_run))
    rows = read_rows(manifest)
    metadata = read_json(asr_run / "run.json")
    if len(rows) != metadata["expected_items"]:
        raise ValueError("manifest count differs from existing ASR run")
    for path, field in ((manifest, "manifest"), (references, "references")):
        if file_sha256(path) != metadata[field]["sha256"]:
            raise ValueError(f"{field} differs from existing ASR inputs")
    sources = source_paths()
    if not all(p.is_file() for p in sources):
        raise ValueError("all implementation, tests and protocol must exist before freezing")
    study.mkdir(parents=True, exist_ok=False)
    config = {"budgets_percent": [5, 10, 20, 30, 50, 100], "primary_budgets_percent": [10, 20],
              "random_repetitions": 1000, "random_seed": 20261017,
              "tie_salt": "audio-qc-ranking-v1:", "analysis_config": dict(DEFAULT_CONFIG)}
    write_json(study / "config.json", config)
    protocol = {
        "schema_version": "audio-qc-ranking-study-v1", "frozen_at_utc": utc(),
        "manifest": input_record(manifest), "references": input_record(references),
        "asr_run": relative(asr_run), "asr_identity": asr_identity(metadata),
        "expected_full_items": len(rows), "config_sha256": file_sha256(study / "config.json"),
        "source_sha256": {}, "environment": environment_identity(),
        "primary_rule": "failure tier descending; max active issue priority integral / clip duration descending; salted SHA ascending",
        "priority_weights": {"high": 3, "medium": 2, "low": 1},
        "failure_tiers": {"asr_not_succeeded": 2, "qc_incomplete_or_coverage_unavailable": 1, "complete": 0},
        "budget_policy": "integer microseconds; whole-clip longest prefix; stop at first over-budget clip; do not skip or fractionally credit",
        "gold_isolation": "freeze protocol; snapshot existing ASR; analyze audio and freeze all ranking features before opening reference labels for scoring",
        "cohorts": {"interim": "one read-only transaction per worker; terminal records only; nonrandom completion cohort; excluded pending count retained",
                    "full": "existing ASR run completed; merged result IDs exactly match full manifest; no ASR execution"},
        "methods": ["qc", "dsp_only", "coverage_only", "shortest", "longest", "random"],
        "random_interval": "2.5th-97.5th percentiles over fixed random orders; not a population confidence interval",
        "not_measured": ["human detection or correction", "human labor time", "semantic safety", "true VAD timing precision"],
    }
    # Model identity may contain its local cache path; its file hashes are sufficient.
    protocol["asr_identity"]["model"] = {k: v for k, v in metadata["model"].items() if k != "local_snapshot"}
    for field in ("manifest", "references"):
        protocol["asr_identity"][field] = {"sha256": metadata[field]["sha256"]}
    for path in sources:
        key = relative(path)
        destination = study / "source-snapshot" / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        protocol["source_sha256"][key] = file_sha256(path)
    write_json(study / "protocol.json", protocol)
    write_json(study / "frozen-hashes.json", {p.relative_to(study).as_posix(): file_sha256(p)
                                             for p in study.rglob("*") if p.is_file()})
    print(canonical_json({"phase": "frozen", "study": relative(study), "items": len(rows)}), flush=True)


def verify_frozen(study):
    protocol = read_json(study / "protocol.json")
    for key, expected in read_json(study / "frozen-hashes.json").items():
        if file_sha256(study / key) != expected:
            raise ValueError(f"frozen study artifact changed: {key}")
    for key, expected in protocol["source_sha256"].items():
        if file_sha256(ROOT / key) != expected:
            raise ValueError(f"source changed since freeze: {key}; create a new study")
    for field in ("manifest", "references"):
        if file_sha256(ROOT / protocol[field]["path"]) != protocol[field]["sha256"]:
            raise ValueError(f"original {field} changed")
    if environment_identity() != protocol["environment"]:
        raise ValueError("runtime or VAD model changed since freeze; create a new study")
    return protocol


def clean_asr_identity(metadata):
    identity = asr_identity(metadata)
    identity["model"] = {k: v for k, v in metadata["model"].items() if k != "local_snapshot"}
    for field in ("manifest", "references"):
        identity[field] = {"sha256": metadata[field]["sha256"]}
    return identity


def snapshot_existing(protocol, cohort, work):
    """Never opens gold labels or writes to the active ASR databases."""
    manifest_path = ROOT / protocol["manifest"]["path"]
    sources = read_rows(manifest_path)
    expected = {r["item_id"]: r for r in sources}
    asr_run = ROOT / protocol["asr_run"]
    metadata = read_json(asr_run / "run.json")
    if clean_asr_identity(metadata) != protocol["asr_identity"]:
        raise ValueError("ASR configuration changed after study freeze")
    records, captures = [], []
    if cohort == "full":
        if metadata["status"] != "completed":
            raise ValueError("full ASR is not complete; pending is not a failed prediction")
        merged = asr_run / "asr.jsonl"
        if file_sha256(merged) != metadata["results_sha256"]:
            raise ValueError("merged ASR result hash differs from completed run")
        records = read_rows(merged)
        captures.append({"source": relative(merged), "sha256": file_sha256(merged), "captured_at_utc": utc(),
                         "completion_evidence": {"run_sha256": file_sha256(asr_run / "run.json"),
                                                 "status": metadata["status"], "completed_at": metadata.get("completed_at"),
                                                 "expected_items": metadata["expected_items"],
                                                 "results_sha256": metadata["results_sha256"]}})
        if {r["item_id"] for r in records} != set(expected):
            raise ValueError("full result IDs do not match entire manifest")
    else:
        for worker in metadata["workers"]:
            database = asr_run / f"worker-{worker['worker']}" / "asr-state.db"
            capture_started = utc()
            con = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                con.execute("BEGIN")
                states = dict(con.execute("SELECT status,count(*) FROM items GROUP BY status").fetchall())
                raw = con.execute("SELECT item_id,cache_key,status,result_json FROM items WHERE status IN ('succeeded','failed')").fetchall()
                captured = utc()
                for item_id, cache_key, status, encoded in raw:
                    if encoded is None:
                        raise ValueError("terminal ASR row lacks result JSON")
                    row = json.loads(encoded)
                    if (row.get("item_id"), row.get("cache_key"), row.get("status")) != (item_id, cache_key, status):
                        raise ValueError("ASR state/result identity mismatch")
                    if status == "succeeded":
                        cached = con.execute("SELECT result_sha256 FROM cache WHERE cache_key=?", (cache_key,)).fetchone()
                        if not cached or hashlib.sha256(canonical_json(row).encode()).hexdigest() != cached[0]:
                            raise ValueError("committed ASR cache checksum mismatch")
                    records.append(row)
                captures.append({"source": relative(database), "capture_started_at_utc": capture_started,
                                 "captured_at_utc": captured,
                                 "states": states, "terminal_rows": len(raw), "read_only": True})
            finally:
                con.rollback()
                con.close()
    ids = [r["item_id"] for r in records]
    if not ids or len(ids) != len(set(ids)) or set(ids) - set(expected):
        raise ValueError("invalid or duplicate ASR snapshot IDs")
    by_id = {r["item_id"]: r for r in records}
    selected = [dict(r) for r in sources if r["item_id"] in by_id]
    for source in selected:
        result = by_id[source["item_id"]]
        duration = source.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("manifest duration must be positive and finite")
        if result.get("status") not in {"succeeded", "failed"}:
            raise ValueError("snapshot may contain only terminal predictions")
        if result.get("audio_sha256") != source["audio_sha256"] or result.get("language") != source["language"]:
            raise ValueError("ASR result audio identity mismatch")
        if result["status"] == "succeeded" and not isinstance(result.get("asr_text"), str):
            raise ValueError("ASR succeeded row lacks a valid transcript")
        check_prediction_identity(result, protocol["asr_identity"])
        source["audio_path"] = str((manifest_path.parent / source["audio_path"]).resolve(strict=True))
        result["audio_path"] = source["audio_path"]
    work.mkdir(parents=True, exist_ok=False)
    atomic_jsonl(work / "manifest.jsonl", selected)
    atomic_jsonl(work / "asr.jsonl", [by_id[r["item_id"]] for r in selected])
    return selected, by_id, {"cohort": cohort, "captured_at_utc": utc(), "expected_full_items": len(sources),
                            "cohort_items": len(selected), "excluded_pending_or_not_started": len(sources) - len(selected),
                            "expected_full_audio_s": sum(r["duration"] for r in sources),
                            "cohort_audio_s": sum(r["duration"] for r in selected),
                            "excluded_pending_audio_s": sum(r["duration"] for r in sources if r["item_id"] not in by_id),
                            "absent_expected_splits": sorted({r["split"] for r in sources} - {r["split"] for r in selected}),
                            "captures": captures, "manifest": input_record(work / "manifest.jsonl"),
                            "asr": input_record(work / "asr.jsonl"),
                            "split_counts": dict(Counter(r["split"] for r in selected)),
                            "speaker_counts": {s: len({r["speaker_id"] for r in selected if r["split"] == s})
                                               for s in sorted({r["split"] for r in selected})}}


def check_prediction_identity(row, identity):
    model, config = row.get("model", {}), row.get("config", {})
    expected_model = {"repo_id": identity["model"]["repository"], "revision": identity["model"]["revision"],
                      "device": identity["device"], "compute_type": identity["compute_type"]}
    if any(model.get(k) != v for k, v in expected_model.items()):
        raise ValueError("ASR row model differs from frozen existing run")
    for key in ("device", "compute_type", "beam_size", "vad_filter", "word_timestamps", "condition_on_previous_text"):
        if config.get(key) != identity[key]:
            raise ValueError("ASR row decoding configuration differs from frozen existing run")
    for result_key, meta_key in (("cpu_threads", "cpu_threads_per_worker"), ("runtime_versions", "runtime_versions")):
        if meta_key in identity and config.get(result_key) != identity[meta_key]:
            raise ValueError("ASR row runtime differs from frozen existing run")


def analyze_cohort(sources, results, destination, cache, config):
    features, totals = [], Counter()
    started = time.perf_counter()
    # No reference file is opened anywhere in this stage.
    with gzip.open(destination / "qc-reports.jsonl.gz", "wt", encoding="utf-8") as stream:
        for index, source in enumerate(sources, 1):
            item_id = source["item_id"]
            result = results[item_id]
            before = time.perf_counter()
            duration = source["duration"]
            if file_sha256(source["audio_path"]) != source["audio_sha256"]:
                raise ValueError(f"source audio bytes changed: {item_id}")
            row = {"item_id": item_id, "speaker_id": source["speaker_id"], "source_split": source["split"],
                   "audio_duration_s": duration, "asr_status": result["status"],
                   "audio_sha256": source["audio_sha256"], "qc_status": "failed",
                   "qc_coverage_status": "unavailable", "issues": []}
            try:
                transcript = result if result["status"] == "succeeded" else {}
                report = analyze_cached(source["audio_path"], transcript, cache, config["analysis_config"])
                validate_report(report, audio_sha256=source["audio_sha256"], transcript=transcript, duration_s=duration)
                row.update(qc_status=report["status"], qc_coverage_status=report["coverage"]["status"],
                           issues=report["issues"], analysis_id=report["analysis_id"],
                           metrics=report["metrics"], coverage=report["coverage"],
                           transcript_sha256=report["transcript_sha256"])
                raw = {"item_id": item_id, "report": report}
            except Exception as exc:  # noqa: BLE001 - preserve per-clip decoder/model failures
                row["qc_error"] = {"type": type(exc).__name__, "message": "QC analysis failed; original audio identity was checked"}
                raw = {"item_id": item_id, "error": row["qc_error"]}
            row["qc_call_ms"] = round((time.perf_counter() - before) * 1000, 4)
            row["primary_score"] = priority_score(row["issues"], duration)
            row["failure_tier"] = (2 if row["asr_status"] != "succeeded" else
                                   1 if row["qc_status"] != "complete" or row["qc_coverage_status"] != "ok" else 0)
            row["tie_hash"] = hashlib.sha256((config["tie_salt"] + item_id).encode("utf-8")).hexdigest()
            stream.write(canonical_json(raw) + "\n")
            features.append(row)
            totals[row["qc_status"]] += 1
            if index % 100 == 0 or index == len(sources):
                print(canonical_json({"phase": "qc", "done": index, "total": len(sources), "statuses": dict(totals)}), flush=True)
    atomic_jsonl(destination / "qc-features.jsonl", features)
    ranking = [{"rank": i, "item_id": r["item_id"], "primary_score": r["primary_score"]}
               for i, r in enumerate(rank_rows(features, "qc"), 1)]
    atomic_jsonl(destination / "primary-ranking-before-gold.jsonl", ranking)
    plan = build_selection_plan(features, ranking_config(config))
    with gzip.open(destination / "selection-plan-before-gold.json.gz", "wt", encoding="utf-8") as stream:
        stream.write(canonical_json(plan) + "\n")
    seal = {"sealed_at_utc": utc(), "reference_labels_opened": False,
            "selection_plan_sha256": file_sha256(destination / "selection-plan-before-gold.json.gz"),
            "features_sha256": file_sha256(destination / "qc-features.jsonl"),
            "ranking_sha256": file_sha256(destination / "primary-ranking-before-gold.jsonl"),
            "qc_reports_sha256": file_sha256(destination / "qc-reports.jsonl.gz"),
            "qc_statuses": dict(totals), "qc_wall_seconds": time.perf_counter() - started,
            "cache_policy": "Content cache may be reused; QC call times are end-to-end including possible cache hits; no ASR called"}
    write_json(destination / "ranking-seal.json", seal)
    return features, seal


def ranking_config(config):
    return {k: config[k] for k in ("budgets_percent", "random_repetitions", "random_seed", "tie_salt")}


def verify_plan_measurements(plan, comparison):
    def check(planned, measured):
        if (planned["selected_count"] != measured["selected_count"]
                or planned["selected_audio_us"] != round(measured["selected_audio_s"] * 1_000_000)
                or planned["target_audio_us"] != round(measured["target_audio_s"] * 1_000_000)):
            raise ValueError("scoring selection differs from reference-isolated frozen plan")
    for group, planned_group in plan["groups"].items():
        for method, planned_method in planned_group["methods"].items():
            for budget, planned in planned_method["budgets"].items():
                check(planned, comparison["groups"][group]["methods"][method][budget])
    random_plans = {(g, r["seed"]): r for g, group in plan["groups"].items() for r in group["random"]}
    for trial in comparison["random_trials"]:
        check(random_plans[(trial["group"], trial["seed"])]["budgets"][str(trial["budget_percent"])], trial)


def render_results(destination, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = summary["comparison"]["groups"]
    fig, axes = plt.subplots(1, len(groups), figsize=(5 * len(groups), 4), squeeze=False)
    for ax, (name, group) in zip(axes[0], groups.items()):
        budgets = [5, 10, 20, 30, 50]
        qc = [group["methods"]["qc"][str(b)]["error_capture_rate"] for b in budgets]
        random = [group["random"][str(b)]["error_capture_rate"] for b in budgets]
        ax.plot(budgets, [100 * x if x is not None else 0 for x in qc], "o-", label="Frozen QC rule")
        ax.plot(budgets, [100 * x["mean"] if x["mean"] is not None else 0 for x in random], "--", label="Random mean")
        ax.fill_between(budgets, [100 * x["p2_5"] if x["p2_5"] is not None else 0 for x in random],
                        [100 * x["p97_5"] if x["p97_5"] is not None else 0 for x in random], alpha=.18,
                        label="Random order 95% range")
        ax.set(title=name, xlabel="Audio-duration budget (%)", ylabel="Errors in selected clips (%)", ylim=(0, 100))
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    fig.suptitle("QC ranking vs random | " + summary["cohort"] + " cohort (not measured human savings)")
    fig.tight_layout()
    fig.savefig(destination / "error-capture.png", dpi=160)
    fig.savefig(destination / "error-capture.svg")
    plt.close(fig)

    def pct(value):
        return "NA" if value is None else f"{100 * value:.2f}%"

    lines = ["# LibriSpeech QC 排序自动评测", "", f"队列：**{summary['cohort']}**；录音 {summary['cohort_items']} / {summary['expected_full_items']} 条。",
             "", "目标为音频时长预算；错误覆盖表示选中录音包含的 S+D+I，不表示人已发现/修正这些错误。",
             "随机范围是 1,000 个固定随机顺序的 2.5%–97.5% 分位，不是数据集泛化置信区间。", "",
             "| 分组 | 时长预算 | QC 实际时长比例 | QC 错误覆盖 | 随机均值（95%顺序范围） | 差值（百分点） | QC 剩余 WER |",
             "| --- | ---: | ---: | ---: | --- | ---: | ---: |"]
    for name, group in groups.items():
        for budget in (10, 20):
            q = group["methods"]["qc"][str(budget)]
            r = group["random"][str(budget)]["error_capture_rate"]
            delta = "NA" if q["error_capture_rate"] is None else f"{100 * (q['error_capture_rate'] - r['mean']):+.2f}"
            lines.append(f"| {name} | {budget}% | {pct(q['actual_audio_fraction'])} | {pct(q['error_capture_rate'])} | {pct(r['mean'])} ({pct(r['p2_5'])}–{pct(r['p97_5'])}) | {delta} | {pct(q['residual_wer'])} |")
    lines += ["", "![错误覆盖曲线](error-capture.png)", "", "## 范围和复验", "",
              f"未进入本队列的原始录音：{summary['excluded_pending_or_not_started']} 条；它们不被伪装成失败预测。",
              "中期队列按已有 ASR 完成状态固定，可能偏向较早的说话人、章节和 split；不能外推到完整数据集。全量队列另出报告，不覆盖本次。",
              "排序和配置在参考评分前冻结；不按中期结果调参。现有 ASR 与 QC 共享 Silero VAD，可能有共同盲点。",
              "低电平等声学疑点并不等于文字错误；此处评估的是其筛选 ASR 错误的价值。", "",
              "- `summary.json`：全部分组、消融、时长基线、预算和随机汇总。",
              "- `scored-items.jsonl`：逐条 QC 特征、S/D/I 和规范化参考/预测。",
              "- `random-trials.jsonl.gz`：每个随机种子和预算的原始指标。",
              "- `qc-reports.jsonl.gz`：逐录音完整原始 DSP/VAD/覆盖报告。",
              "- `ranking-seal.json`、`primary-ranking-before-gold.jsonl`：打开 gold 前的排序与哈希。",
              "- `selection-plan-before-gold.json.gz`：所有组、方法的排序，以及随机种子/顺序哈希与预算选择数。",
              "- `inputs.json`、`validation.json`、`artifact-hashes.json`：输入快照、身份校验和产物哈希。",
              "", "数据来源：[LibriSpeech / CC BY 4.0](https://www.openslr.org/12/)。既有参考来自官方语料，不是本项目新增人工修订。", ""]
    (destination / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def run_cohort(study, cohort, work_root, cache):
    study, work_root, cache = (Path(p).resolve() for p in (study, work_root, cache))
    with exclusive_run(work_root / (cohort + "-evaluation")):
        protocol = verify_frozen(study)
        destination = study / "cohorts" / cohort
        if destination.exists():
            raise ValueError("cohort output already exists; do not overwrite recorded evidence")
        config = read_json(study / "config.json")
        destination.mkdir(parents=True)
        write_json(destination / "status.json", {"status": "running", "started_at_utc": utc()})
        started = time.perf_counter()
        try:
            work = work_root / cohort
            sources, results, inputs = snapshot_existing(protocol, cohort, work)
            inputs["references"] = protocol["references"]
            write_json(destination / "inputs.json", inputs)
            features, seal = analyze_cohort(sources, results, destination, cache, config)
            score_started = utc()
            gold = evaluate(work / "asr.jsonl", ROOT / protocol["references"]["path"], work / "manifest.jsonl",
                            evaluation_kind="benchmark", purpose="Frozen QC triage ranking against existing reference transcripts")
            by_id = {r["item_id"]: r for r in gold["items"]}
            rows = []
            for feature in features:
                score = by_id[feature["item_id"]]
                row = {**feature, **{k: score[k] for k in ("errors", "reference_units", "hypothesis_units", "substitutions", "deletions", "insertions", "normalized_reference", "normalized_hypothesis")}}
                rows.append(row)
            atomic_jsonl(destination / "scored-items.jsonl", rows)
            comparison = evaluate_rankings(rows, ranking_config(config))
            with gzip.open(destination / "selection-plan-before-gold.json.gz", "rt", encoding="utf-8") as stream:
                verify_plan_measurements(json.load(stream), comparison)
            trials = comparison.pop("random_trials")
            with gzip.open(destination / "random-trials.jsonl.gz", "wt", encoding="utf-8") as stream:
                for trial in trials:
                    stream.write(canonical_json(trial) + "\n")
            summary = {"schema_version": "audio-qc-ranking-result-v1", **inputs,
                       "completed_at_utc": utc(), "normalization": gold["normalization"],
                       "asr_coverage": gold["coverage"], "asr_quality": gold["quality_by_language"],
                       "asr_by_split": gold["by_source_split"], "qc": seal, "comparison": comparison,
                       "elapsed_seconds": time.perf_counter() - started}
            summary["failure_tiers"] = {str(tier): {"count": sum(r["failure_tier"] == tier for r in rows),
                                                    "audio_s": sum(r["audio_duration_s"] for r in rows if r["failure_tier"] == tier),
                                                    "errors": sum(r["errors"] for r in rows if r["failure_tier"] == tier)}
                                         for tier in (0, 1, 2)}
            write_json(destination / "summary.json", summary)
            render_results(destination, summary)
            verify_frozen(study)
            for name in ("manifest", "asr"):
                if file_sha256(ROOT / inputs[name]["path"]) != inputs[name]["sha256"]:
                    raise ValueError("frozen cohort input changed during evaluation")
            # Original audio identity was checked before QC and is rechecked after scoring.
            if any(file_sha256(r["audio_path"]) != r["audio_sha256"] for r in sources):
                raise ValueError("original audio changed during evaluation")
            sealed_paths = {"ranking_sha256": "primary-ranking-before-gold.jsonl",
                            "features_sha256": "qc-features.jsonl", "qc_reports_sha256": "qc-reports.jsonl.gz",
                            "selection_plan_sha256": "selection-plan-before-gold.json.gz"}
            if any(file_sha256(destination / path) != seal[key] for key, path in sealed_paths.items()):
                raise ValueError("reference-isolated ranking artifacts changed after seal")
            write_json(destination / "validation.json", {"source_and_original_inputs_unchanged": True,
                       "audio_hashes_verified_before_and_after": len(sources), "new_asr_calls": 0,
                       "all_selections_match_reference_isolated_plan": True,
                       "ranking_sealed_at_utc": seal["sealed_at_utc"], "gold_scoring_started_at_utc": score_started,
                       "ranking_hash_unchanged": file_sha256(destination / "primary-ranking-before-gold.jsonl") == seal["ranking_sha256"],
                       "features_hash_unchanged": file_sha256(destination / "qc-features.jsonl") == seal["features_sha256"]})
            write_json(destination / "status.json", {"status": "completed", "completed_at_utc": utc()})
            write_json(destination / "artifact-hashes.json", {p.relative_to(destination).as_posix(): file_sha256(p)
                                                             for p in destination.rglob("*") if p.is_file()})
            print(canonical_json({"phase": "completed", "cohort": cohort, "items": len(rows), "output": relative(destination)}), flush=True)
        except Exception as exc:
            write_json(destination / "status.json", {"status": "failed", "at_utc": utc(), "error_type": type(exc).__name__, "error": str(exc)})
            raise


def watch_full(study, work_root, cache):
    """Background dependency wait: no inference, no polling of labels."""
    study, work_root = Path(study).resolve(), Path(work_root).resolve()
    with exclusive_run(work_root / "full-watcher"):
        protocol = verify_frozen(study)
        status_path = work_root / "full-followup-status.json"
        write_json(status_path, {"status": "waiting_for_existing_asr", "started_at_utc": utc()})
        try:
            while True:
                state = read_json(ROOT / protocol["asr_run"] / "run.json")
                if state["status"] == "completed":
                    write_json(status_path, {"status": "evaluating_full", "started_at_utc": utc()})
                    run_cohort(study, "full", work_root, cache)
                    write_json(status_path, {"status": "completed", "completed_at_utc": utc(),
                                            "report": relative(study / "cohorts/full/RESULTS.md")})
                    return
                if state["status"] == "failed":
                    raise RuntimeError("upstream ASR run failed; existing results retained, no ASR retry was launched")
                time.sleep(30)
        except Exception as exc:
            write_json(status_path, {"status": "failed", "at_utc": utc(), "error_type": type(exc).__name__, "error": str(exc)})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pre = commands.add_parser("freeze")
    pre.add_argument("--study-dir", required=True)
    pre.add_argument("--manifest", required=True)
    pre.add_argument("--references", required=True)
    pre.add_argument("--asr-run", required=True)
    for command in ("run", "watch-full"):
        sub = commands.add_parser(command)
        sub.add_argument("--study-dir", required=True)
        sub.add_argument("--work-dir", required=True)
        sub.add_argument("--cache-dir", required=True)
        if command == "run":
            sub.add_argument("--cohort", choices=("interim", "full"), required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.study_dir, args.manifest, args.references, args.asr_run)
    elif args.command == "run":
        run_cohort(args.study_dir, args.cohort, args.work_dir, args.cache_dir)
    else:
        watch_full(args.study_dir, args.work_dir, args.cache_dir)


if __name__ == "__main__":
    main()
