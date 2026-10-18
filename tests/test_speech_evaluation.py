"""Regression checks for offline speech scoring, without model or audio mocks."""

import json

import pytest

from scripts.evaluate_speech_smoke import edit_counts, evaluate, main, normalize


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _evaluation_files(tmp_path):
    manifest = [
        {"item_id": "en-1", "language": "en", "reference_id": "ref-en-1",
         "audio_sha256": "a" * 64, "duration": 2},
        {"item_id": "zh-1", "language": "zh", "reference_id": "ref-zh-1",
         "audio_sha256": "b" * 64, "duration": 3},
        {"item_id": "en-2", "language": "en", "reference_id": "ref-en-2",
         "audio_sha256": "c" * 64, "duration": 4},
    ]
    references = [
        {"item_id": row["item_id"], "language": row["language"],
         "reference_id": row["reference_id"], "text": text}
        for row, text in zip(manifest, ["no dose", "不可", "none"], strict=True)
    ]
    results = [
        {**manifest[0], "status": "succeeded", "asr_text": "no dose",
         "cache_hit": True, "processing_ms": 2, "inference_processing_ms": 1000},
        {**manifest[1], "status": "failed", "asr_text": "不可", "processing_ms": 7},
    ]
    paths = {name: tmp_path / f"{name}.jsonl" for name in ("manifest", "references", "results")}
    for name, rows in (("manifest", manifest), ("references", references), ("results", results)):
        _write_jsonl(paths[name], rows)
    return paths, results


def test_failed_and_missing_results_remain_in_corpus_denominator(tmp_path):
    paths, _ = _evaluation_files(tmp_path)
    report = evaluate(paths["results"], paths["references"], paths["manifest"])

    assert report["coverage"]["expected_valid_audio_items"] == 3
    assert report["coverage"]["succeeded"] == 1
    assert report["coverage"]["missing"] == 1
    assert report["coverage"]["failed_or_invalid"] == 1
    assert report["quality_by_language"]["en"]["reference_units"] == 3
    assert report["quality_by_language"]["en"]["corpus_error_rate"] == pytest.approx(1 / 3)
    assert report["quality_by_language"]["zh"]["corpus_error_rate"] == 1
    failed = next(row for row in report["items"] if row["item_id"] == "zh-1")
    assert failed["normalized_hypothesis"] == ""  # Ignore stale text on a failed row.
    assert failed["deletions"] == 2
    assert report["audio"]["total_seconds"] == 9


def test_normalization_preserves_decimal_units_and_negation():
    assert normalize("Do NOT give １.５ mg—10％, don't repeat.", "en") == (
        "do not give 1.5 mg 10% dont repeat"
    )
    assert normalize("不，给１.５毫克，10％。", "zh") == "不给1.5毫克10%"


def test_known_substitution_deletion_insertion_counts():
    assert edit_counts(list("abcdef"), list("axcefy")) == {
        "reference_units": 6, "hypothesis_units": 6, "errors": 3,
        "substitutions": 1, "deletions": 1, "insertions": 1,
    }
    assert edit_counts(["no", "dose"], [])["deletions"] == 2
    assert edit_counts([], ["invented"])["insertions"] == 1


@pytest.mark.parametrize("extra_kind", ["duplicate", "unknown"])
def test_duplicate_or_unknown_result_identity_is_rejected(tmp_path, extra_kind):
    paths, results = _evaluation_files(tmp_path)
    extra = dict(results[0])
    if extra_kind == "unknown":
        extra["item_id"] = "outside-manifest"
    _write_jsonl(paths["results"], [*results, extra])

    with pytest.raises(ValueError, match="Duplicate|outside"):
        evaluate(paths["results"], paths["references"], paths["manifest"])


def test_cache_read_time_does_not_replace_original_inference_time(tmp_path):
    paths, _ = _evaluation_files(tmp_path)
    report = evaluate(paths["results"], paths["references"], paths["manifest"])
    timing = report["timing"]

    assert report["coverage"]["cache_hits"] == 1
    assert timing["original_inference"]["sum_ms"] == 1000
    assert timing["original_inference"]["timed_audio_seconds"] == 2
    assert timing["original_inference"]["aggregate_processing_rtf"] == 0.5
    assert timing["current_run_cache_overhead"]["sum_ms"] == 2
    assert timing["current_run_failed_processing"]["sum_ms"] == 7
    assert timing["current_run_uncached_successful_processing"]["items_with_timing"] == 0


def test_old_manifest_without_splits_keeps_smoke_defaults(tmp_path):
    paths, _ = _evaluation_files(tmp_path)
    report = evaluate(paths["results"], paths["references"], paths["manifest"])

    assert report["evaluation_kind"] == "smoke"
    assert "20 deterministic train recordings" in report["purpose"]
    assert "archive-prefix train samples" in report["limitations"][0]
    assert report["dataset_description"] is None
    assert set(report["by_source_split"]) == {"unspecified"}
    group = report["by_source_split"]["unspecified"]
    assert group["coverage"] == report["coverage"]
    assert group["audio"] == report["audio"]
    assert group["quality_by_language"] == report["quality_by_language"]


def test_split_metrics_preserve_failed_missing_and_invalid_denominators(tmp_path):
    manifest = [
        {"item_id": f"en-{index}", "language": "en", "reference_id": f"ref-{index}",
         "audio_sha256": str(index) * 64, "duration": duration, "split": split}
        for index, (duration, split) in enumerate([
            (2, "test-clean"), (8, "test-clean"),
            (3, "test-other"), (4, "test-other"), (5, "test-other"),
        ], 1)
    ]
    references = [
        {"item_id": row["item_id"], "language": "en", "reference_id": row["reference_id"],
         "text": text}
        for row, text in zip(manifest, [
            "one two", "three four five six", "seven", "eight nine ten", "eleven twelve",
        ], strict=True)
    ]
    results = [
        {**manifest[0], "split": "spoofed-result-split", "status": "succeeded",
         "asr_text": "one two", "cache_hit": True},
        {**manifest[1], "status": "failed", "asr_text": "three four five six"},
        {**manifest[2], "status": "succeeded", "asr_text": "seven"},
        {**manifest[4], "status": "succeeded", "asr_text": None},
    ]
    paths = {name: tmp_path / f"{name}.jsonl" for name in ("manifest", "references", "results")}
    for name, rows in (("manifest", manifest), ("references", references), ("results", results)):
        _write_jsonl(paths[name], rows)

    report = evaluate(
        paths["results"], paths["references"], paths["manifest"],
        evaluation_kind="benchmark", purpose="Full test-clean and test-other evaluation",
        dataset_description="LibriSpeech published test splits, fixed manifest",
    )
    assert report["evaluation_kind"] == "benchmark"
    assert report["purpose"] == "Full test-clean and test-other evaluation"
    assert report["dataset_description"] == "LibriSpeech published test splits, fixed manifest"
    assert all("archive-prefix" not in value for value in report["limitations"])
    assert set(report["by_source_split"]) == {"test-clean", "test-other"}
    clean, other = (report["by_source_split"][split] for split in ("test-clean", "test-other"))
    assert clean["coverage"] == {
        "expected_valid_audio_items": 2, "result_rows": 2, "succeeded": 1,
        "missing": 0, "failed_or_invalid": 1, "cache_hits": 1, "success_rate": 0.5,
    }
    assert other["coverage"] == {
        "expected_valid_audio_items": 3, "result_rows": 2, "succeeded": 1,
        "missing": 1, "failed_or_invalid": 1, "cache_hits": 0, "success_rate": pytest.approx(1 / 3),
    }
    assert clean["quality_by_language"]["en"]["corpus_error_rate"] == pytest.approx(4 / 6)
    assert other["quality_by_language"]["en"]["corpus_error_rate"] == pytest.approx(5 / 6)
    assert clean["audio"] == {"total_seconds": 10, "items_with_duration": 2}
    assert other["audio"] == {"total_seconds": 12, "items_with_duration": 3}
    assert report["quality_by_language"]["en"]["reference_units"] == 12
    assert report["quality_by_language"]["en"]["deletions"] == 9
    assert report["coverage"]["succeeded"] == 2
    assert report["coverage"]["failed_or_invalid"] == 2
    assert report["coverage"]["missing"] == 1
    assert report["audio"]["total_seconds"] == 22
    assert next(row for row in report["items"] if row["item_id"] == "en-5")["status"] == "invalid_result"


def test_missing_reference_cannot_be_dropped_in_benchmark_mode(tmp_path):
    paths, _ = _evaluation_files(tmp_path)
    reference_rows = [json.loads(line) for line in paths["references"].read_text(encoding="utf-8").splitlines()]
    _write_jsonl(paths["references"], reference_rows[:2])

    with pytest.raises(ValueError, match="Every manifest item must have a reference"):
        evaluate(
            paths["results"], paths["references"], paths["manifest"], evaluation_kind="benchmark",
        )


def test_cli_records_explicit_benchmark_metadata(tmp_path, monkeypatch, capsys):
    paths, _ = _evaluation_files(tmp_path)
    output = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", [
        "evaluate_speech_smoke.py", "--results", str(paths["results"]),
        "--references", str(paths["references"]), "--manifest", str(paths["manifest"]),
        "--output", str(output), "--evaluation-kind", "benchmark",
        "--purpose", "Published ASR test run", "--dataset-description", "All official test recordings",
    ])

    main()

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["evaluation_kind"] == "benchmark"
    assert report["purpose"] == "Published ASR test run"
    assert report["dataset_description"] == "All official test recordings"
    assert "20 deterministic" not in report["purpose"]
    assert report["reference_policy"].startswith("References are joined only in this offline evaluation")
    assert "by_source_split" in json.loads(capsys.readouterr().out)
