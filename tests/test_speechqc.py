"""Algorithm tests use explicit fixture VAD; real model checks live in the experiment run."""

import copy
import json
import subprocess
import sys

import pytest

from speechqc.schema import (
    DEFAULT_CONFIG,
    canonical_json,
    digest_json,
    file_sha256,
    report_digest,
    resolve_config,
    transcript_digest,
    validate_report,
)


def test_import_and_schema_are_stdlib_only():
    code = ("import sys; import speechqc; import speechqc.schema; "
            "assert not any(n in sys.modules for n in ('numpy','soundfile','faster_whisper','av','app','speechprep'))")
    subprocess.run([sys.executable, "-c", code], check=True)


def test_transcript_digest_normalizes_numbers_and_ignores_metadata():
    a = {"asr_text": "hello", "words": [{"start": 0, "end": 1, "w": "hello", "confidence": .7}]}
    b = {"asr_text": "hello", "segments": [], "words": [{"start": 0.0, "end": 1.0, "w": "hello"}], "speaker": "x"}
    assert transcript_digest(a) == transcript_digest(b)
    b["words"][0]["w"] = "bye"
    assert transcript_digest(a) != transcript_digest(b)
    with pytest.raises(ValueError):
        transcript_digest({"words": [{"start": float("nan"), "end": 1, "w": "bad"}]})


@pytest.fixture
def libs():
    return pytest.importorskip("numpy"), pytest.importorskip("soundfile")


@pytest.fixture
def audio(tmp_path, libs):
    np, sf = libs
    path = tmp_path / "sample.wav"
    tone = .25 * np.sin(2 * np.pi * 440 * np.arange(32000) / 16000)
    sf.write(path, tone, 16000, subtype="FLOAT")
    return path


@pytest.fixture
def fixture_vad(monkeypatch):
    from speechqc import analysis
    intervals = [(0.25, 1.75)]
    provenance = [{"name": "faster-whisper-silero", "version": "fixture-only", "model_sha256": "a" * 64}]
    calls = []

    def run(samples, rate, config):
        calls.append(samples.copy())
        return intervals.copy(), copy.deepcopy(provenance)

    monkeypatch.setattr(analysis, "run_vad", run)
    return intervals, calls, provenance


def test_silence_finite_json_and_no_speech_ratio(tmp_path, libs, fixture_vad):
    from speechqc import analyze
    np, sf = libs
    fixture_vad[0].clear()
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(32000), 16000)
    report = analyze(path, {})
    assert report["metrics"]["rms_dbfs"] is None
    assert report["metrics"]["peak_dbfs"] is None
    assert report["coverage"]["uncovered_ratio"] is None
    assert {i["code"] for i in report["issues"]} == {"low_level", "low_energy"}
    assert "Infinity" not in canonical_json(report)
    assert report["vad"]["intervals"] == []


def test_antiphase_channels_preserved(tmp_path, libs, fixture_vad):
    from speechqc import analyze
    np, sf = libs
    signal = .4 * np.sin(2 * np.pi * 440 * np.arange(32000) / 16000)
    path = tmp_path / "stereo.wav"
    sf.write(path, np.column_stack([signal, -signal]), 16000, subtype="FLOAT")
    report = analyze(path, {})
    assert report["channels"] == 2
    assert report["metrics"]["rms_dbfs"] > -12
    assert "low_level" not in {i["code"] for i in report["issues"]}
    assert min(p[0] for p in report["waveform"]["peaks"]) < -.39
    assert max(p[1] for p in report["waveform"]["peaks"]) > .39
    assert fixture_vad[1][0].shape == (32000, 2)


def test_actual_vad_adapter_keeps_channels_independent(monkeypatch, libs):
    pytest.importorskip("faster_whisper")
    pytest.importorskip("av")
    import faster_whisper.vad

    from speechqc.vad import run_vad
    np, _ = libs
    seen = []

    def timestamps(pcm, options, sampling_rate):
        seen.append(pcm)
        return [{"start": 0, "end": len(pcm)}]

    monkeypatch.setattr(faster_whisper.vad, "get_speech_timestamps", timestamps)
    source = np.full((44100, 2), .2, dtype=np.float32)
    source[:, 1] *= -1
    spans, detectors = run_vad(source, 44100, DEFAULT_CONFIG)
    assert len(seen) == 2 and all(len(pcm) == 16000 for pcm in seen)
    assert seen[0].mean() > .19 and seen[1].mean() < -.19
    assert spans == [(0, 1), (0, 1)]
    assert len(detectors[0]["model_sha256"]) == 64


def test_clipping_and_dc_have_local_evidence(tmp_path, libs, fixture_vad):
    from speechqc import analyze
    np, sf = libs
    signal = np.full(32000, .1, dtype=np.float32)
    signal[8000:9600] = 1
    path = tmp_path / "clip.wav"
    sf.write(path, signal, 16000, subtype="FLOAT")
    report = analyze(path, {})
    clipping = next(i for i in report["issues"] if i["code"] == "near_clipping")
    assert clipping["start"] == .5 and clipping["end"] == .6
    assert clipping["evidence"]["near_full_scale_ratio"] == 1
    assert "dc_offset" in {i["code"] for i in report["issues"]}


def test_word_coverage_tolerance_merges_small_gaps():
    from speechqc.analysis import coverage_analysis
    transcript = {"asr_text": "a b c", "words": [
        {"w": "a", "start": .1, "end": .4}, {"w": "b", "start": .65, "end": 1},
        {"w": "c", "start": 1.3, "end": 1.9}]}
    coverage, issues, _ = coverage_analysis(transcript, [(0, 2)], 2, DEFAULT_CONFIG)
    assert coverage["basis"] == "words"
    assert coverage["covered_s"] == 2
    assert issues == []


def test_coverage_detects_omitted_span_and_silent_text():
    from speechqc.analysis import coverage_analysis
    transcript = {"asr_text": "a b", "words": [{"w": "a", "start": 0, "end": 1}, {"w": "b", "start": 3, "end": 4}]}
    coverage, issues, _ = coverage_analysis(transcript, [(0, 2)], 4, DEFAULT_CONFIG)
    assert coverage["uncovered_s"] == .85
    assert coverage["text_outside_s"] == 1
    assert {i["code"] for i in issues} == {"speech_without_transcript", "transcript_without_speech"}


def test_segments_fallback_declared_and_empty_transcript_checked():
    from speechqc.analysis import coverage_analysis
    result, issues, warnings = coverage_analysis({"segments": [{"start": 0, "end": 2, "text": "hello"}]}, [(0, 2)], 2, DEFAULT_CONFIG)
    assert result["basis"] == "segments" and warnings and issues == []
    result, issues, _ = coverage_analysis({}, [(0, 2)], 2, DEFAULT_CONFIG)
    assert result["basis"] == "none" and result["uncovered_ratio"] == 1
    assert issues[0]["code"] == "speech_without_transcript"


def test_untimed_nonempty_text_does_not_claim_missing_transcript():
    from speechqc.analysis import coverage_analysis
    result, issues, warnings = coverage_analysis({"asr_text": "hello"}, [(0, 2)], 2, DEFAULT_CONFIG)
    assert result["status"] == "unavailable" and result["uncovered_s"] is None
    assert issues == [] and warnings


@pytest.mark.parametrize("bad_word", [
    {"w": "world", "start": 2, "end": 2},
    {"w": "world", "start": 2, "end": 3.5},
])
def test_incomplete_word_timing_falls_back_to_reliable_segments(bad_word):
    from speechqc.analysis import coverage_analysis
    transcript = {"asr_text": "hello world", "words": [
        {"w": "hello", "start": 0, "end": .5}, bad_word],
        "segments": [{"start": 0, "end": 3, "text": "hello world"}]}
    coverage, issues, warnings = coverage_analysis(transcript, [(0, 3)], 3, DEFAULT_CONFIG)
    assert coverage["basis"] == "segments"
    assert coverage["uncovered_s"] == 0 and issues == []
    assert any("zero-duration or out-of-audio" in warning for warning in warnings)
    transcript["segments"] = []
    coverage, issues, warnings = coverage_analysis(transcript, [(0, 3)], 3, DEFAULT_CONFIG)
    assert coverage["status"] == "unavailable" and issues == []


def test_segment_without_words_falls_back_without_erasing_intersegment_gap():
    from speechqc.analysis import coverage_analysis
    transcript = {"asr_text": "hello world", "words": [{"w": "hello", "start": 0, "end": .5}],
                  "segments": [{"text": "hello", "start": 0, "end": .5},
                               {"text": "world", "start": 2, "end": 3}]}
    coverage, issues, warnings = coverage_analysis(transcript, [(0, 3)], 3, DEFAULT_CONFIG)
    assert coverage["basis"] == "segments" and coverage["uncovered_s"] == 1.2
    assert len(issues) == 1 and issues[0]["start"] == .65 and issues[0]["end"] == 1.85
    assert any("no overlapping word" in warning for warning in warnings)


def test_vad_failure_is_partial_no_coverage_flags(audio, monkeypatch):
    import speechqc.analysis
    from speechqc import analyze

    def fail(*args):
        raise RuntimeError("private model path C:/secrets/model.onnx")

    monkeypatch.setattr(speechqc.analysis, "run_vad", fail)
    report = analyze(audio, {})
    assert report["status"] == "partial"
    assert report["vad"]["status"] == "unavailable"
    assert report["coverage"]["status"] == "unavailable"
    assert "C:/secrets" not in canonical_json(report)
    assert not {"speech_without_transcript", "transcript_without_speech"} & {i["code"] for i in report["issues"]}


def test_bounded_waveform_resource_limits_and_empty_audio(audio, tmp_path, libs, fixture_vad):
    from speechqc import analyze
    np, sf = libs
    assert len(analyze(audio, {}, {"waveform_bins": 17})["waveform"]["peaks"]) == 17
    with pytest.raises(ValueError, match="resource bound"):
        analyze(audio, {}, {"max_duration_s": 1})
    empty = tmp_path / "empty.wav"
    sf.write(empty, np.zeros(0), 16000)
    with pytest.raises(ValueError, match="at least one"):
        analyze(empty, {})
    with pytest.raises(ValueError):
        resolve_config({"waveform_bins": 1201})
    with pytest.raises(ValueError):
        resolve_config({"secret": "bad"})


def test_strict_report_rejects_poisoned_metadata_and_mismatched_inputs(audio, fixture_vad):
    from speechqc import analyze
    report = analyze(audio, {})
    for mutate in (
        lambda x: x.update(secret_reference="leak"),
        lambda x: x["waveform"].update(reference="leak"),
        lambda x: x["metrics"].update(rms_dbfs=float("nan")),
        lambda x: x["vad"]["intervals"][0].update(secret="leak"),
        lambda x: x["issues"][0]["evidence"].update(reference="leak"),
        lambda x: x["detectors"][0].update(path="C:/private"),
    ):
        changed = copy.deepcopy(report)
        mutate(changed)
        with pytest.raises(ValueError):
            validate_report(changed)
    with pytest.raises(ValueError, match="audio hash"):
        validate_report(report, audio_sha256="b" * 64)
    with pytest.raises(ValueError, match="transcript hash"):
        validate_report(report, transcript={"asr_text": "different"})
    with pytest.raises(ValueError, match="duration"):
        validate_report(report, duration_s=3)
    with pytest.raises(ValueError, match="duration"):
        validate_report(report, duration_s=report["duration_s"] + .01)
    changed = copy.deepcopy(report)
    changed["waveform"]["peaks"][0] = [0, 0]
    with pytest.raises(ValueError, match="content hash"):
        validate_report(changed)


def test_cache_reuse_invalidation_and_corruption(audio, tmp_path, fixture_vad, monkeypatch):
    from speechqc import analysis, analyze_cached, cache

    environment = cache._environment_identity()
    environment["vad"] = copy.deepcopy(fixture_vad[2])
    monkeypatch.setattr(cache, "_environment_identity", lambda: copy.deepcopy(environment))
    directory = tmp_path / "cache"
    first = analyze_cached(audio, {}, directory)
    assert len(fixture_vad[1]) == 1
    assert analyze_cached(audio, {}, directory) == first
    assert len(fixture_vad[1]) == 1
    target = next(directory.glob("*.json"))
    corrupt = json.loads(target.read_text())
    corrupt["waveform"]["peaks"][0] = [0, 0]
    target.write_text(json.dumps(corrupt))
    assert analyze_cached(audio, {}, directory) == first
    assert len(fixture_vad[1]) == 2
    analyze_cached(audio, {"asr_text": "new"}, directory)
    analyze_cached(audio, {}, directory, {"low_level_dbfs": -36})
    assert len(fixture_vad[1]) == 4
    previous_paths = set(directory.glob("*.json"))
    environment["vad"][0]["model_sha256"] = "b" * 64
    fixture_vad[2][0]["model_sha256"] = "b" * 64
    analyze_cached(audio, {}, directory)
    assert len(fixture_vad[1]) == 5
    # Even a structurally valid old-model report under the new key is rejected.
    new_path = (set(directory.glob("*.json")) - previous_paths).pop()
    new_path.write_text(canonical_json(first), encoding="utf-8")
    analyze_cached(audio, {}, directory)
    assert len(fixture_vad[1]) == 6
    # Partial results never become a reusable 'success' after a dependency recovers.
    monkeypatch.setattr(analysis, "run_vad", lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
    partial_dir = tmp_path / "partial"
    assert analyze_cached(audio, {}, partial_dir)["status"] == "partial"
    assert not list(partial_dir.glob("*.json"))


def test_cli_preserves_rows_relative_paths_failures_and_sources(audio, tmp_path, fixture_vad):
    from speechqc.__main__ import analyze_manifest, main
    manifest = tmp_path / "input.jsonl"
    row = {"schema_version": "speech-v1", "item_id": "a", "audio_path": audio.name,
           "audio_sha256": file_sha256(audio), "asr_text": "", "words": [], "custom": "preserve"}
    bad = {**row, "item_id": "b", "audio_sha256": "0" * 64, "audio_analysis": {"stale": True}}
    manifest.write_text(canonical_json(row) + "\n" + canonical_json(bad), encoding="utf-8")
    output = tmp_path / "elsewhere" / "result.jsonl"
    result = analyze_manifest(manifest, output, tmp_path / "cache")
    assert result == {"total": 2, "complete": 1, "partial": 0, "failed": 1}
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["audio_path"] == str(audio.resolve()) and rows[0]["custom"] == "preserve"
    assert "audio_analysis" not in rows[1] and rows[1]["audio_analysis_error"]["type"] == "ValueError"
    before = audio.read_bytes()
    with pytest.raises(ValueError, match="source audio"):
        analyze_manifest(manifest, audio, tmp_path / "cache")
    with pytest.raises(ValueError, match="differ"):
        analyze_manifest(manifest, manifest, tmp_path / "cache")
    assert audio.read_bytes() == before
    assert main(["analyze", "--manifest", str(manifest), "--output", str(output), "--cache-dir", str(tmp_path / "cache")]) == 1


def test_report_determinism(audio, fixture_vad):
    from speechqc import analyze
    a = analyze(audio, {})
    b = analyze(audio, {})
    assert a == b
    assert a["analysis_id"] == report_digest(a)
    assert all(issue["id"] == digest_json({k: v for k, v in issue.items() if k != "id"}) for issue in a["issues"])
