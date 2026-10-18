"""Synthetic ML-runner stage control; never reads real datasets or labels."""

import gzip
import hashlib
import json
import math
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import evaluate_ml_ranking as runner
from scripts import run_speechrank_dev_asr as dev_runner
from speechprep.manifest import atomic_jsonl, file_sha256


def ranking_row(item_id, duration=1.0, *, tier=0, split="dev-clean", errors=0):
    return {
        "item_id": item_id, "speaker_id": "speaker-" + item_id, "source_split": split,
        "audio_duration_s": duration, "asr_status": "failed" if tier == 2 else "succeeded",
        "qc_status": "partial" if tier == 1 else "complete",
        "qc_coverage_status": "unavailable" if tier == 1 else "ok", "issues": [],
        "failure_tier": tier,
        "tie_hash": hashlib.sha256(("audio-qc-ranking-v1:" + item_id).encode()).hexdigest(),
        "errors": errors, "reference_units": max(10, errors), "substitutions": errors,
        "deletions": 0, "insertions": 0,
    }


def test_ml_order_preserves_failure_tiers_density_and_fixed_ties():
    values = [ranking_row("healthy", tier=0), ranking_row("qc_missing", tier=1), ranking_row("asr_fail", tier=2)]
    assert [row["item_id"] for row in runner.ordered_ml(values, {"healthy": 100, "qc_missing": 10, "asr_fail": 0})] == [
        "asr_fail", "qc_missing", "healthy",
    ]
    ties = [ranking_row(name) for name in ("c", "b", "a")]
    expected = sorted(ties, key=lambda row: row["tie_hash"])
    assert runner.ordered_ml(ties, dict.fromkeys(("a", "b", "c"), 1.0)) == expected


@pytest.mark.parametrize("predictions", [{"a": -1}, {"a": math.nan}, {"a": math.inf}, {"a": True}, {}, {"a": 1, "extra": 1}])
def test_ml_order_rejects_invalid_density_or_id_set(predictions):
    with pytest.raises(ValueError):
        runner.ordered_ml([ranking_row("a")], predictions)


def test_ml_plan_rejects_duplicate_or_extra_prediction_ids():
    with pytest.raises(ValueError, match="duplicate|ID"):
        runner.ordered_ml([ranking_row("a"), ranking_row("a")], {"a": 1})
    with pytest.raises(ValueError, match="ID"):
        runner.plan_ml([ranking_row("a")], {"a": 1, "extra": 2}, [10, 20])


def test_ml_budget_never_skips_overlong_clip_and_sealed_selections_are_recomputed():
    values = [ranking_row("long", 8, errors=3), ranking_row("short", 2, errors=1)]
    plan = runner.plan_ml(values, {"long": 2, "short": 1}, [10, 20, 100])
    assert plan["pooled"]["budgets"]["20"] == {
        "selected_count": 0, "target_audio_us": 2_000_000, "selected_audio_us": 0,
    }
    measured = runner.measured_ml(values, plan)
    assert measured["pooled"]["20"]["captured_errors"] == 0
    assert measured["pooled"]["100"]["captured_errors"] == 4
    for field in ("selected_count", "target_audio_us", "selected_audio_us"):
        changed = deepcopy(plan)
        changed["pooled"]["budgets"]["20"][field] += 1
        with pytest.raises(ValueError, match="budget mismatch"):
            runner.measured_ml(values, changed)
    changed = deepcopy(plan)
    changed["pooled"]["order_ids"] = ["short", "short"]
    with pytest.raises(ValueError, match="order ID mismatch"):
        runner.measured_ml(values, changed)


def test_candidate_choice_uses_tune_integer_objective_then_candidate_id_only():
    trials = [
        {"candidate": {"candidate_id": "z"}, "objective_captured_error_sum": 8, "holdout_score": 999},
        {"candidate": {"candidate_id": "b"}, "objective_captured_error_sum": 9, "holdout_score": -999},
        {"candidate": {"candidate_id": "a"}, "objective_captured_error_sum": 9, "holdout_score": -9999},
    ]
    assert runner.choose_candidate(trials)["candidate"]["candidate_id"] == "a"
    with pytest.raises(ValueError, match="no candidate"):
        runner.choose_candidate([])


def test_role_validation_rejects_speaker_audio_item_leakage_and_test_in_dev():
    roles = {role: [{"item_id": role, "speaker_id": role, "audio_sha256": role,
                      "split": "test-clean" if role == "test" else "dev-clean"}]
             for role in (*runner.ROLES, "test")}
    roles["dev"] = [deepcopy(value) for role in runner.ROLES for value in roles[role]]
    runner.validate_disjoint(roles)
    for field in ("item_id", "speaker_id", "audio_sha256"):
        changed = deepcopy(roles)
        changed["test"][0][field] = changed["holdout"][0][field]
        with pytest.raises(ValueError, match="leakage"):
            runner.validate_disjoint(changed)
    changed = deepcopy(roles)
    changed["train"][0]["split"] = "test-clean"
    with pytest.raises(ValueError, match="nondevelopment"):
        runner.validate_disjoint(changed)


def test_dev_worker_rejects_test_or_reference_bearing_rows_before_inference(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dev_runner, "run_pipeline", lambda *args, **kwargs: calls.append((args, kwargs)))
    clean = {"item_id": "librispeech:dev-clean:123", "split": "dev-clean", "language": "en"}
    dev_runner.validate_dev_rows([clean])
    for bad in ({**clean, "split": "test-clean"}, {**clean, "item_id": "librispeech:test-clean:123"},
                {**clean, "language": "zh"}, {**clean, "reference_text": "secret"},
                {**clean, "errors": 1}, {**clean, "asr_text": "output"}):
        monkeypatch.setattr(dev_runner, "read_manifest", lambda _, value=bad: [value])
        with pytest.raises(ValueError, match="label-free official development"):
            dev_runner.worker(tmp_path / "manifest.jsonl", tmp_path)
    assert calls == []
    with pytest.raises(ValueError, match="empty or duplicate"):
        dev_runner.validate_dev_rows([clean, clean])


def synthetic_study(tmp_path, monkeypatch, *, real_candidates=False):
    """Create only synthetic bytes/labels; inference is the sole QC stub."""
    from speechrank import models

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    dev = tmp_path / "dev"
    old = tmp_path / "v1"
    dev_asr = tmp_path / "dev-asr"
    monkeypatch.setattr(runner, "DEV", dev)
    monkeypatch.setattr(runner, "V1", old)
    monkeypatch.setattr(runner, "DEV_ASR", dev_asr)
    monkeypatch.setattr(runner, "env_identity", lambda: {"fixture": "no-inference"})
    code = tmp_path / "source.py"
    code.write_text("# synthetic frozen source\n", encoding="utf-8")
    monkeypatch.setattr(runner, "source_files", lambda: [code])
    candidates = models.get_candidates() if real_candidates else [
        {"candidate_id": "a-low-first", "kind": "fixture", "params": {}},
        {"candidate_id": "b-high-first", "kind": "fixture", "params": {}},
    ]
    if not real_candidates:
        monkeypatch.setattr(models, "build_estimator", lambda candidate: SimpleNamespace(
            named_steps={"model": SimpleNamespace(get_params=lambda deep=False: {
                "synthetic_candidate_id": candidate["candidate_id"],
            })},
        ))
    monkeypatch.setattr(models, "get_candidates", lambda: deepcopy(candidates))
    # Archive/hash/provenance auditing has a dedicated synthetic boundary suite.
    # Preserve runner replay/evidence equality while isolating this stage test.
    monkeypatch.setattr(runner, "audit_inputs", lambda *args: {"synthetic_input_audit": "fixed"})
    monkeypatch.setattr(runner, "verify_v1", lambda *args: {"synthetic_v1_audit": "fixed"})
    monkeypatch.setattr(runner, "RANK_CONFIG", {**runner.RANK_CONFIG, "random_repetitions": 3})
    basic_config = {"device": "cpu", "compute_type": "int8", "beam_size": 5,
                    "vad_filter": True, "word_timestamps": True, "condition_on_previous_text": False}
    dev_config = {**basic_config, "adapter_version": "synthetic-confidence"}
    model = {"name": "fixture", "repo_id": "fixture", "revision": "fixed", "device": "cpu", "compute_type": "int8"}
    manifests, predictions = {}, {}
    for role in (*runner.ROLES, "test"):
        manifest, result, references = [], [], []
        folder = dev / role if role != "test" else tmp_path / "test"
        folder.mkdir(parents=True)
        for index in range(10):
            split = ("test-" if role == "test" else "dev-") + ("clean" if index % 2 == 0 else "other")
            item_id = f"librispeech:{split}:{role}-{index}"
            audio = folder / f"{index}.wav"
            audio.write_bytes(f"synthetic audio bytes {role} {index}".encode())
            source = {
                "schema_version": "speech-v1", "item_id": item_id, "speaker_id": role + str(index),
                "audio_path": str(audio), "audio_sha256": file_sha256(audio), "duration": 1.0,
                "language": "en", "corpus": "synthetic", "corpus_revision": "fixture-v1", "split": split,
                "source_id": item_id, "reference_id": "ref:" + item_id, "license": "synthetic",
            }
            manifest.append(source)
            result.append({**source, "audio_duration_s": 1.0, "status": "succeeded",
                "asr_text": "alpha " + "word " * index, "segments": [], "words": [],
                "model": dict(model), "config": dict(basic_config if role == "test" else dev_config)})
            references.append({"item_id": item_id, "reference_id": source["reference_id"],
                               "language": "en", "text": "alpha beta"})
        atomic_jsonl(folder / "manifest.jsonl", manifest)
        atomic_jsonl(folder / "references.jsonl", references)
        manifests[role], predictions[role] = manifest, result
    combined_manifest = [value for role in runner.ROLES for value in manifests[role]]
    atomic_jsonl(tmp_path / "runtime/datasets/librispeech/official-test/prepared/manifest.jsonl", manifests["test"])
    combined_asr = [value for role in runner.ROLES for value in predictions[role]]
    atomic_jsonl(dev / "manifest.jsonl", combined_manifest)
    runner.save(dev / "assignment.json", {"synthetic": True})
    runner.save(dev / "selection.json", {"synthetic": True})
    for name in ("manifest.jsonl", "references.jsonl", "verification.json"):
        runner.save(dev.parent / "prepared" / name, {"synthetic": True})
    runner.save(dev.parent / "download-verification.json", {"synthetic": True})
    atomic_jsonl(dev_asr / "asr.jsonl", combined_asr)
    runner.save(dev_asr / "run.json", {
        "status": "completed", "results_sha256": file_sha256(dev_asr / "asr.jsonl"),
        "new_dev_asr_calls": 30, "identity": {"config": dev_config, "model_revision": "fixed"},
    })
    test_asr = tmp_path / "test/asr.jsonl"
    atomic_jsonl(test_asr, predictions["test"])
    runner.save(tmp_path / "runtime/librispeech-base-2026-10-17/run.json", {
        "model": {"repository": "fixture", "revision": "fixed"}, **basic_config,
    })
    runner.save(old / "config.json", {"analysis_config": {}})
    runner.save(old / "protocol.json", {"synthetic": True})
    runner.save(old / "cohorts/full/inputs.json", {
        "manifest": runner.record(tmp_path / "test/manifest.jsonl"),
        "references": runner.record(tmp_path / "test/references.jsonl"), "asr": runner.record(test_asr),
    })

    def raw_features(source, prediction):
        value = ranking_row(source["item_id"], source["duration"], split=source["split"])
        for field in ("errors", "reference_units", "substitutions", "deletions", "insertions"):
            del value[field]
        value["speaker_id"] = source["speaker_id"]
        value["audio_sha256"] = source["audio_sha256"]
        assert prediction["status"] == "succeeded"
        return value

    def reports(path, manifest):
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            for source in manifest:
                report = {"status": "complete", "metrics": {}, "issues": [],
                          "coverage": {"status": "ok", "basis": "words"},
                          "vad": {"status": "ok", "intervals": []}}
                stream.write(json.dumps({"item_id": source["item_id"], "report": report}) + "\n")

    atomic_jsonl(old / "cohorts/full/qc-features.jsonl", [raw_features(source, result)
                for source, result in zip(manifests["test"], predictions["test"])])
    reports(old / "cohorts/full/qc-reports.jsonl.gz", manifests["test"])
    # This synthetic old benchmark was already scored, as the real v1 test was.
    old_score = runner.evaluate(test_asr, tmp_path / "test/references.jsonl", tmp_path / "test/manifest.jsonl",
                                evaluation_kind="benchmark")
    old_scored = runner.join_gold(runner.rows(old / "cohorts/full/qc-features.jsonl"), old_score)
    old_comparison = runner.evaluate_rankings(old_scored, runner.RANK_CONFIG)
    runner.save(old / "cohorts/full/summary.json", {"comparison": old_comparison})
    runner.save(old / "cohorts/full/ranking-seal.json", {
        "features_sha256": file_sha256(old / "cohorts/full/qc-features.jsonl"),
        "qc_reports_sha256": file_sha256(old / "cohorts/full/qc-reports.jsonl.gz"),
    })
    runner.save(old / "cohorts/full/artifact-hashes.json", {
        name: file_sha256(old / "cohorts/full" / name) for name in (
            "qc-features.jsonl", "qc-reports.jsonl.gz", "summary.json", "ranking-seal.json",
        )
    })

    def fake_analyze(sources, results, destination, _cache, _config):
        reports(destination / "qc-reports.jsonl.gz", sources)
        return [raw_features(source, results[source["item_id"]]) for source in sources], {}

    monkeypatch.setattr(runner, "analyze_cohort", fake_analyze)
    # Detector correctness/schema validation has separate tests; keep this test
    # about orchestration while preserving report/ASR identity checks below it.
    monkeypatch.setattr(runner, "validate_report", lambda report, **kwargs: report)
    monkeypatch.setattr(runner, "render", lambda *args: None)
    study = tmp_path / "study"
    runner.freeze(study)
    return study, manifests, predictions, code


def test_freeze_rejects_overwrite_and_detects_source_input_environment_and_artifact_drift(tmp_path, monkeypatch):
    study, _, _, code = synthetic_study(tmp_path, monkeypatch)
    runner.verify(study)
    with pytest.raises(ValueError, match="already exists"):
        runner.freeze(study)
    original = code.read_bytes()
    code.write_bytes(b"# drift")
    with pytest.raises(ValueError, match="source changed"):
        runner.verify(study)
    code.write_bytes(original)
    original_input = (tmp_path / "dev/assignment.json").read_bytes()
    (tmp_path / "dev/assignment.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="input changed"):
        runner.verify(study)
    (tmp_path / "dev/assignment.json").write_bytes(original_input)
    monkeypatch.setattr(runner, "env_identity", lambda: {"fixture": "changed"})
    with pytest.raises(ValueError, match="environment/model changed"):
        runner.verify(study)
    monkeypatch.setattr(runner, "env_identity", lambda: {"fixture": "no-inference"})
    (study / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact changed"):
        runner.verify(study)


def test_freeze_records_resolved_real_estimator_parameters_for_every_candidate(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    from speechrank.models import build_estimator, get_candidates

    expected = {candidate["candidate_id"]: build_estimator(candidate).named_steps["model"].get_params(deep=False)
                for candidate in get_candidates()}
    study, _, _, _ = synthetic_study(tmp_path, monkeypatch, real_candidates=True)
    saved = runner.load(study / "config.json")
    assert saved["resolved_estimator_parameters"] == expected
    assert len(expected) == 7
    assert saved["resolved_estimator_parameters"]["poisson-alpha-1"]["max_iter"] == 1000
    assert saved["resolved_estimator_parameters"]["hgb-leaves-3-l2-1"]["early_stopping"] is False


@pytest.mark.parametrize("helper", ["audit_inputs", "verify_v1"])
def test_run_rejects_input_audit_evidence_that_differs_from_freeze(tmp_path, monkeypatch, helper):
    study, _, _, _ = synthetic_study(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, helper, lambda *args: {"drift": True})
    monkeypatch.setattr(runner, "evaluate", lambda *args, **kwargs: pytest.fail("labels opened after audit evidence drift"))
    with pytest.raises(ValueError, match="audit"):
        runner.run(study)
    assert runner.load(study / "outputs/status.json")["status"] == "failed"


def test_output_overwrite_is_rejected_before_scoring(tmp_path, monkeypatch):
    study, _, _, _ = synthetic_study(tmp_path, monkeypatch)
    output = study / "outputs"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(runner, "evaluate", lambda *args, **kwargs: pytest.fail("gold opened for overwritten run"))
    with pytest.raises(ValueError, match="output exists"):
        runner.run(study)
    assert marker.read_text() == "keep"


def test_asr_identity_validation_rejects_model_config_status_and_audio_drift(tmp_path, monkeypatch):
    _, manifests, predictions, _ = synthetic_study(tmp_path, monkeypatch)
    manifest = manifests["train"]
    original = {value["item_id"]: value for value in predictions["train"]}
    identity = runner.load(tmp_path / "dev-asr/run.json")
    runner.validate_asr(manifest, original, identity, dev=True)
    for kind in ("config", "revision", "status", "audio"):
        changed = deepcopy(original)
        value = next(iter(changed.values()))
        if kind == "config":
            value["config"]["beam_size"] = 1
        elif kind == "revision":
            value["model"]["revision"] = "changed"
        elif kind == "status":
            value["status"] = "running"
        else:
            value["audio_sha256"] = "0" * 64
        with pytest.raises(ValueError):
            runner.validate_asr(manifest, changed, identity, dev=True)


def test_materialization_checks_report_asr_identity_and_never_silently_drops_qc(tmp_path, monkeypatch):
    value = ranking_row("clip")
    for key in ("errors", "reference_units", "substitutions", "deletions", "insertions"):
        del value[key]
    value["audio_sha256"] = "a" * 64
    prediction = {"status": "succeeded", "audio_sha256": "a" * 64, "audio_duration_s": 1.0,
                  "asr_text": "fixture", "words": [], "segments": []}
    report = {"status": "complete", "issues": [], "coverage": {"status": "ok"}}
    path = tmp_path / "reports.gz"
    calls = []
    monkeypatch.setattr(runner, "validate_report", lambda saved, **kwargs: calls.append((saved, kwargs)))

    def write_report(saved):
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            for row in saved:
                stream.write(json.dumps(row) + "\n")

    write_report([{"item_id": "clip", "report": report}])
    assert len(runner.materialize_features([value], path, {"clip": prediction}, ("legacy",))) == 1
    assert calls[0][1] == {"audio_sha256": "a" * 64, "duration_s": 1.0, "transcript": prediction}
    with pytest.raises(ValueError, match="ASR identity mismatch"):
        runner.materialize_features([value], path, {"clip": {**prediction, "audio_sha256": "b" * 64}}, ("legacy",))
    for saved, error in (
        ([{"item_id": "clip", "report": None}], "no raw report"),
        ([{"item_id": "clip", "report": {**report, "issues": [{"code": "unexpected"}]}}], "feature/report differs"),
        ([{"item_id": "clip", "report": report}] * 2, "report ID mismatch"),
        ([], "report missing"),
    ):
        write_report(saved)
        with pytest.raises(ValueError, match=error):
            runner.materialize_features([value], path, {"clip": prediction}, ("legacy",))


def test_training_failure_keeps_failure_record_and_never_opens_heldout_gold(tmp_path, monkeypatch):
    study, _, _, _ = synthetic_study(tmp_path, monkeypatch)
    original_evaluate = runner.evaluate
    opened = []

    def guard(results, references, manifest, **kwargs):
        role = references.parent.name
        assert role in ("train", "tune")
        opened.append(role)
        return original_evaluate(results, references, manifest, **kwargs)

    def fail_training(*args):
        raise RuntimeError("synthetic fit failure")

    monkeypatch.setattr(runner, "evaluate", guard)
    monkeypatch.setattr(runner, "train_select_refit", fail_training)
    with pytest.raises(RuntimeError, match="synthetic fit failure"):
        runner.run(study)
    assert opened == ["train", "tune"]
    assert not (study / "outputs/evaluation-seal.json").exists()
    status = runner.load(study / "outputs/status.json")
    assert status["status"] == "failed" and "synthetic fit failure" in status["error"]
    with pytest.raises(ValueError, match="output exists"):
        runner.run(study)


def test_new_freeze_rejects_v1_features_that_no_longer_match_the_original_seal(tmp_path, monkeypatch):
    synthetic_study(tmp_path, monkeypatch)
    path = tmp_path / "v1/cohorts/full/qc-features.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    fresh = tmp_path / "another-study"
    with pytest.raises(ValueError, match="pre-gold seal"):
        runner.freeze(fresh)
    assert not fresh.exists()


def test_synthetic_end_to_end_gold_opens_after_seal_and_only_tune_selects_model(tmp_path, monkeypatch):
    pytest.importorskip("joblib")
    from speechrank import models

    study, _, _, _ = synthetic_study(tmp_path, monkeypatch)
    output = study / "outputs"
    events = []
    original_evaluate = runner.evaluate

    def guarded_evaluate(results, references, manifest, **kwargs):
        role = references.parent.name
        events.append(("gold", role))
        if role in ("holdout", "test"):
            seal = runner.load(output / "evaluation-seal.json")
            assert seal["heldout_labels_opened_this_run"] is False
            assert seal["selection_roles"] == ["train", "tune"]
            for path, expected in seal["files"].items():
                assert file_sha256(output / path) == expected
            selected = runner.load(output / "selected-models.json")
            assert selected["holdout_and_test_labels_opened_this_run"] is False
            for arm in runner.ARMS:
                assert selected["selected"][arm]["candidate"]["candidate_id"] == "b-high-first"
        else:
            assert role in ("train", "tune")
            assert not (output / "evaluation-seal.json").exists()
        return original_evaluate(results, references, manifest, **kwargs)

    def fake_fit(features, errors, durations, candidate, arm="legacy"):
        assert not any(event[0] == "gold" and event[1] in ("holdout", "test") for event in events)
        assert len(features) == len(errors) == len(durations)
        assert len(features) in (10, 20)
        assert all(not set(runner.GOLD_FIELDS).intersection(value) for value in features)
        events.append(("fit", arm, candidate["candidate_id"], len(features)))
        return {"arm": arm, "candidate": candidate, "training_rows": len(features)}

    def fake_predict(model, features, arm="legacy"):
        assert not any(event[0] == "gold" and event[1] in ("holdout", "test") for event in events)
        assert model["arm"] == arm
        assert all(not set(runner.GOLD_FIELDS).intersection(value) for value in features)
        assert all("item_id" not in value and "speaker_id" not in value for value in features)
        events.append(("predict", arm, len(features)))
        values = [value["text_chars_per_s"] for value in features]
        return values if model["candidate"]["candidate_id"] == "b-high-first" else [100 - value for value in values]

    monkeypatch.setattr(runner, "evaluate", guarded_evaluate)
    monkeypatch.setattr(models, "fit_model", fake_fit)
    monkeypatch.setattr(models, "predict_density", fake_predict)
    monkeypatch.setattr(models, "model_card", lambda model: deepcopy(model))
    runner.run(study)
    assert [event for event in events if event[0] == "gold"] == [
        ("gold", "train"), ("gold", "tune"), ("gold", "holdout"), ("gold", "test"),
    ]
    assert len([event for event in events if event[0] == "fit" and event[-1] == 20]) == 2
    assert runner.load(output / "status.json")["status"] == "completed"
    validation = runner.load(output / "validation.json")
    assert validation["new_test_asr_calls"] == 0
    assert validation["all_ml_selections_from_sealed_order"] is True
    assert validation["all_baseline_random_plans_unchanged"] is True
    holdout = runner.load(output / "fresh_holdout-summary.json")
    test = runner.load(output / "previously_exposed_test-summary.json")
    assert holdout["confidence_arm_available"] is True
    assert test["confidence_arm_available"] is False
    assert "ml_confidence" in holdout["groups"]["pooled"]["methods"]
    assert "ml_confidence" not in test["groups"]["pooled"]["methods"]
    hashes = runner.load(output / "artifact-hashes.json")
    assert all(file_sha256(output / path) == value for path, value in hashes.items())
    trials = runner.load(output / "candidate-trials.json")
    assert len(trials) == 4
