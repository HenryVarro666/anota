"""Synthetic dependency/snapshot and reference-isolation checks, no real labels."""

import gzip
import hashlib
import json
import sqlite3

import pytest

from scripts import evaluate_qc_ranking as runner
from speechqc.schema import canonical_json, file_sha256


def fixture_run(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    asr = tmp_path / "asr"
    asr.mkdir()
    sources, results, refs = [], [], []
    for i in range(3):
        audio = data / f"{i}.wav"
        audio.write_bytes(b"synthetic audio " + bytes([i]))
        source = {"item_id": f"i{i}", "audio_path": audio.name, "audio_sha256": file_sha256(audio),
                  "language": "en", "reference_id": f"r{i}", "duration": 1.0 + i,
                  "speaker_id": str(i), "split": "test-clean"}
        result = {**source, "status": "failed" if i == 1 else "succeeded", "asr_text": "hello",
                  "cache_key": f"key{i}", "run_id": "run", "segments": [], "words": [],
                  "audio_duration_s": source["duration"]}
        result["model"] = {"repo_id": "fixture", "revision": "fixed", "device": "cpu", "compute_type": "int8"}
        result["config"] = {"device": "cpu", "compute_type": "int8", "beam_size": 5,
                            "vad_filter": True, "word_timestamps": True, "condition_on_previous_text": False}
        sources.append(source)
        results.append(result)
        refs.append({"item_id": source["item_id"], "language": "en", "reference_id": source["reference_id"],
                     "text": "hello world"})
    runner.atomic_jsonl(data / "manifest.jsonl", sources)
    runner.atomic_jsonl(data / "references.jsonl", refs)
    metadata = {"status": "running", "dataset": "synthetic", "expected_items": 3,
                "manifest": {"sha256": file_sha256(data / "manifest.jsonl")},
                "references": {"sha256": file_sha256(data / "references.jsonl")},
                "model": {"repository": "fixture", "revision": "fixed"}, "device": "cpu", "compute_type": "int8",
                "beam_size": 5, "vad_filter": True, "word_timestamps": True,
                "condition_on_previous_text": False, "workers": [{"worker": 0}]}
    runner.write_json(asr / "run.json", metadata)
    worker = asr / "worker-0"
    worker.mkdir()
    database = worker / "asr-state.db"
    with sqlite3.connect(database) as con:
        con.execute("CREATE TABLE items(item_id TEXT,cache_key TEXT,status TEXT,result_json TEXT)")
        con.execute("CREATE TABLE cache(cache_key TEXT,result_sha256 TEXT)")
        for i, row in enumerate(results):
            encoded = canonical_json(row)
            con.execute("INSERT INTO items VALUES(?,?,?,?)", (row["item_id"], row["cache_key"],
                        "running" if i == 2 else row["status"], None if i == 2 else encoded))
            if i == 0:
                con.execute("INSERT INTO cache VALUES(?,?)", (row["cache_key"], hashlib.sha256(encoded.encode()).hexdigest()))
    protocol = {"manifest": runner.input_record(data / "manifest.jsonl"),
                "references": runner.input_record(data / "references.jsonl"),
                "asr_run": "asr", "asr_identity": runner.clean_asr_identity(metadata)}
    return protocol, metadata, sources, results, database


def test_interim_snapshot_retains_terminal_failure_excludes_pending_and_is_readonly(tmp_path, monkeypatch):
    protocol, _meta, _sources, _results, database = fixture_run(tmp_path, monkeypatch)
    before = file_sha256(database)
    sources, results, record = runner.snapshot_existing(protocol, "interim", tmp_path / "snapshot")
    assert [r["item_id"] for r in sources] == ["i0", "i1"]
    assert results["i1"]["status"] == "failed"
    assert record["excluded_pending_or_not_started"] == 1
    assert record["captures"][0]["read_only"] is True
    assert file_sha256(database) == before
    assert (tmp_path / "snapshot/asr.jsonl").exists()


def test_full_waits_for_completed_metadata_and_requires_exact_denominator(tmp_path, monkeypatch):
    protocol, metadata, _sources, results, _database = fixture_run(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="not complete"):
        runner.snapshot_existing(protocol, "full", tmp_path / "snapshot")
    output = tmp_path / "asr/asr.jsonl"
    runner.atomic_jsonl(output, results[:-1])
    metadata.update(status="completed", results_sha256=file_sha256(output))
    runner.write_json(tmp_path / "asr/run.json", metadata)
    with pytest.raises(ValueError, match="entire manifest"):
        runner.snapshot_existing(protocol, "full", tmp_path / "snapshot")
    runner.atomic_jsonl(output, results)
    metadata["results_sha256"] = file_sha256(output)
    runner.write_json(tmp_path / "asr/run.json", metadata)
    sources, _, record = runner.snapshot_existing(protocol, "full", tmp_path / "snapshot")
    assert len(sources) == 3 and record["excluded_pending_or_not_started"] == 0


def test_snapshot_rejects_cache_corruption_without_dropping_row(tmp_path, monkeypatch):
    protocol, _, _, _, database = fixture_run(tmp_path, monkeypatch)
    with sqlite3.connect(database) as con:
        con.execute("UPDATE cache SET result_sha256='bad'")
    with pytest.raises(ValueError, match="checksum"):
        runner.snapshot_existing(protocol, "interim", tmp_path / "snapshot")


@pytest.mark.parametrize("field", ["model", "config", "asr_text"])
def test_snapshot_rejects_stale_prediction_or_invalid_success(tmp_path, monkeypatch, field):
    protocol, _, _, results, database = fixture_run(tmp_path, monkeypatch)
    row = results[0]
    if field == "model":
        row["model"]["revision"] = "old-model"
    elif field == "config":
        row["config"]["beam_size"] = 1
    else:
        row["asr_text"] = None
    encoded = canonical_json(row)
    with sqlite3.connect(database) as con:
        con.execute("UPDATE items SET result_json=? WHERE item_id='i0'", (encoded,))
        con.execute("UPDATE cache SET result_sha256=? WHERE cache_key='key0'", (hashlib.sha256(encoded.encode()).hexdigest(),))
    with pytest.raises(ValueError, match="model|configuration|transcript"):
        runner.snapshot_existing(protocol, "interim", tmp_path / "snapshot")


def test_freeze_detects_source_and_environment_drift(tmp_path, monkeypatch):
    _, _, _, _, _ = fixture_run(tmp_path, monkeypatch)
    code = tmp_path / "source.py"
    code.write_text("# original")
    monkeypatch.setattr(runner, "source_paths", lambda: [code])
    monkeypatch.setattr(runner, "environment_identity", lambda: {"vad": "fixed-model"})
    study = tmp_path / "study"
    runner.freeze(study, tmp_path / "data/manifest.jsonl", tmp_path / "data/references.jsonl", tmp_path / "asr")
    runner.verify_frozen(study)
    monkeypatch.setattr(runner, "environment_identity", lambda: {"vad": "changed-model"})
    with pytest.raises(ValueError, match="runtime or VAD"):
        runner.verify_frozen(study)
    monkeypatch.setattr(runner, "environment_identity", lambda: {"vad": "fixed-model"})
    code.write_text("# changed")
    with pytest.raises(ValueError, match="source changed"):
        runner.verify_frozen(study)


def test_full_followup_waits_for_upstream_without_running_asr(tmp_path, monkeypatch):
    protocol, metadata, _, _, _ = fixture_run(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "verify_frozen", lambda _: protocol)
    calls = []

    def finish_upstream(seconds):
        assert seconds == 30
        metadata["status"] = "completed"
        runner.write_json(tmp_path / "asr/run.json", metadata)

    monkeypatch.setattr(runner.time, "sleep", finish_upstream)
    monkeypatch.setattr(runner, "run_cohort", lambda *args: calls.append(args))
    runner.watch_full(tmp_path / "study", tmp_path / "work", tmp_path / "cache")
    assert len(calls) == 1 and calls[0][1] == "full"
    assert runner.read_json(tmp_path / "work/full-followup-status.json")["status"] == "completed"


def test_scoring_opens_gold_only_after_all_selection_plans_are_sealed(tmp_path, monkeypatch):
    protocol, _, _, _, _ = fixture_run(tmp_path, monkeypatch)
    study = tmp_path / "study"
    study.mkdir()
    config = {"budgets_percent": [5, 10, 20, 30, 50, 100], "primary_budgets_percent": [10, 20],
              "random_repetitions": 5, "random_seed": 20261017, "tie_salt": "audio-qc-ranking-v1:",
              "analysis_config": {}}
    runner.write_json(study / "config.json", config)
    monkeypatch.setattr(runner, "verify_frozen", lambda _: protocol)

    def fake_analyze(path, transcript, cache, config):
        if path.endswith("1.wav"):
            raise RuntimeError("synthetic decoder failure")
        return {"status": "complete", "coverage": {"status": "ok"}, "issues": [],
                "analysis_id": "fixed", "metrics": {}, "transcript_sha256": "fixed"}

    monkeypatch.setattr(runner, "analyze_cached", fake_analyze)
    monkeypatch.setattr(runner, "validate_report", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "render_results", lambda *a: None)
    original_evaluate = runner.evaluate
    destination = study / "cohorts/interim"

    def guarded_evaluate(*args, **kwargs):
        seal = runner.read_json(destination / "ranking-seal.json")
        assert seal["reference_labels_opened"] is False
        assert file_sha256(destination / "selection-plan-before-gold.json.gz") == seal["selection_plan_sha256"]
        assert "errors" not in (destination / "qc-features.jsonl").read_text()
        return original_evaluate(*args, **kwargs)

    monkeypatch.setattr(runner, "evaluate", guarded_evaluate)
    runner.run_cohort(study, "interim", tmp_path / "work", tmp_path / "cache")
    summary = runner.read_json(destination / "summary.json")
    assert summary["cohort_items"] == 2
    assert summary["asr_coverage"]["failed_or_invalid"] == 1
    assert summary["qc"]["qc_statuses"] == {"complete": 1, "failed": 1}
    assert summary["comparison"]["groups"]["pooled"]["methods"]["qc"]["100"]["error_capture_rate"] == 1
    with gzip.open(destination / "selection-plan-before-gold.json.gz", "rt", encoding="utf-8") as stream:
        assert "groups" in json.load(stream)
    hashes = runner.read_json(destination / "artifact-hashes.json")
    assert all(file_sha256(destination / p) == h for p, h in hashes.items())
    assert runner.read_json(destination / "validation.json")["new_asr_calls"] == 0
    with pytest.raises(ValueError, match="already exists"):
        runner.run_cohort(study, "interim", tmp_path / "work", tmp_path / "cache")
