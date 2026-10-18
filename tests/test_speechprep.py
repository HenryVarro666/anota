"""State-machine tests use an explicit injected test adapter, never a CLI fallback."""

import json
import sqlite3
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

from speechprep.asr import ModelSpec, check_decoder_compatibility
from speechprep.manifest import atomic_jsonl, file_sha256
from speechprep.pipeline import default_config, run_pipeline
from speechprep.state import RunLockedError

MODEL = ModelSpec("test-only-model", "a" * 40, repo_id="test-only/model")


class TestAdapter:
    __test__ = False

    def __init__(self, model, config):
        self.calls = []

    def transcribe(self, path, language):
        self.calls.append(path)
        return {"text": "测试" if language == "zh" else "test transcript",
                "segments": [{"start": 0.0, "end": 0.1, "text": "test"}],
                "words": [], "language": language, "language_prob": 0.9}


@pytest.fixture
def sample(tmp_path):
    rows = []
    for index in range(3):
        audio = tmp_path / f"audio{index}.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            # Different content means genuinely different cache identities.
            stream.writeframes(bytes([index, 0]) * 1600)
        rows.append({"schema_version": "speech-v1", "item_id": f"item-{index}",
                     "language": "zh" if index == 2 else "en", "audio_path": audio.name,
                     "audio_sha256": file_sha256(audio), "corpus": "unit-test-only",
                     "corpus_revision": "fixture-v1", "split": "test", "source_id": str(index),
                     "reference_id": f"ref-{index}", "license": "test fixture"})
    manifest, output, state = (tmp_path / name for name in ("manifest.jsonl", "asr.jsonl", "state.sqlite"))
    atomic_jsonl(manifest, rows)
    return manifest, output, state, rows


def run(sample, **kwargs):
    manifest, output, state, _ = sample
    return run_pipeline(manifest, output, state, MODEL,
                        adapter_factory=kwargs.pop("adapter_factory", TestAdapter), **kwargs)


def output_rows(sample):
    return [json.loads(line) for line in sample[1].read_text(encoding="utf-8").splitlines()]


def test_partial_resume_and_warm_cache_never_constructs_model(sample):
    partial = run(sample, limit=1)
    assert partial["new_inference"] == 1 and partial["remaining"] == 2
    assert not partial["complete"] and len(output_rows(sample)) == 1
    resumed = run(sample)
    assert resumed["complete"] and resumed["cache_hits"] == 1
    assert resumed["new_inference"] == 2 and resumed["succeeded"] == 3

    def forbidden_model(*args):
        raise AssertionError("warm cache must not construct a model")

    warm = run(sample, adapter_factory=forbidden_model)
    assert warm["cache_hits"] == 3 and warm["new_inference"] == 0
    rows = output_rows(sample)
    assert all(row["cache_hit"] for row in rows)
    assert all(Path(row["audio_path"]).is_absolute() for row in rows)
    assert len({row["item_id"] for row in rows}) == 3


def test_configuration_and_model_revision_invalidate_cache(sample):
    run(sample)
    config = default_config(beam_size=1)
    changed = run(sample, config=config)
    assert changed["new_inference"] == 3 and changed["cache_hits"] == 0
    model = ModelSpec(MODEL.name, "b" * 40, repo_id=MODEL.repo_id)
    manifest, output, state, _ = sample
    changed_model = run_pipeline(manifest, output, state, model, config,
                                 adapter_factory=TestAdapter)
    assert changed_model["new_inference"] == 3


def test_changed_audio_cannot_reuse_cache_until_manifest_hash_changes(sample):
    run(sample)
    manifest, _, _, rows = sample
    audio = manifest.parent / rows[0]["audio_path"]
    with audio.open("ab") as stream:
        stream.write(b"changed")
    mismatch = run(sample)
    assert mismatch["failed"] == 1 and mismatch["cache_hits"] == 2
    failed = next(row for row in output_rows(sample) if row["item_id"] == rows[0]["item_id"])
    assert failed["status"] == "failed" and "SHA-256 mismatch" in failed["error"]["message"]
    rows[0]["audio_sha256"] = file_sha256(audio)
    atomic_jsonl(manifest, rows)
    refreshed = run(sample)
    assert refreshed["new_inference"] == 1 and refreshed["cache_hits"] == 2


def test_cache_checksum_tampering_triggers_recomputation(sample):
    run(sample)
    key = output_rows(sample)[0]["cache_key"]
    with sqlite3.connect(sample[2]) as db:
        db.execute("UPDATE cache SET result_json='{}' WHERE cache_key=?", (key,))
    repaired = run(sample)
    assert repaired["new_inference"] == 1 and repaired["cache_hits"] == 2


def test_missing_audio_failure_is_explicit_and_retryable(sample):
    path = sample[0].parent / sample[3][0]["audio_path"]
    original = path.read_bytes()
    path.unlink()
    failed = run(sample)
    assert failed["failed"] == 1 and failed["new_inference"] == 2
    assert len(output_rows(sample)) == 3
    assert output_rows(sample)[0]["error"]["type"] == "FileNotFoundError"
    path.write_bytes(original)
    retried = run(sample)
    assert retried["failed"] == 0 and retried["new_inference"] == 1
    assert retried["cache_hits"] == 2


def test_real_process_lock_and_hard_kill_recovery(sample):
    manifest, output, state, _ = sample
    code = f"""
import time
from speechprep.asr import ModelSpec
from speechprep.pipeline import run_pipeline
class BlockingTestAdapter:
    def __init__(self, *args): pass
    def transcribe(self, *args): time.sleep(120)
run_pipeline({str(manifest)!r}, {str(output)!r}, {str(state)!r},
    ModelSpec('test-only-model', {'a' * 40!r}, repo_id='test-only/model'),
    adapter_factory=BlockingTestAdapter)
"""
    process = subprocess.Popen([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(process.communicate()[1].decode())
            if state.exists():
                try:
                    with sqlite3.connect(state) as db:
                        if db.execute("SELECT COUNT(*) FROM items WHERE status='running'").fetchone()[0]:
                            break
                except sqlite3.OperationalError:
                    pass
            time.sleep(0.05)
        else:
            raise AssertionError("test worker never marked its running item")
        with pytest.raises(RunLockedError):
            run(sample)
    finally:
        process.kill()
        process.communicate(timeout=10)
    recovered = run(sample)
    assert recovered["recovered"] == 1 and recovered["new_inference"] == 3
    assert recovered["complete"] and recovered["failed"] == 0


def test_invalid_manifest_does_not_replace_existing_output(sample):
    sample[1].write_text("existing-output\n", encoding="utf-8")
    rows = sample[3] + [sample[3][0]]
    atomic_jsonl(sample[0], rows)
    with pytest.raises(ValueError, match="duplicate item_id"):
        run(sample)
    assert sample[1].read_text(encoding="utf-8") == "existing-output\n"


def test_adapter_failure_is_never_mocked_or_cached(sample):
    class FailingTestAdapter(TestAdapter):
        def transcribe(self, *args):
            raise RuntimeError("test engine failure")
    failed = run(sample, adapter_factory=FailingTestAdapter)
    assert failed["new_inference"] == failed["failed"] == 3
    assert all(row["asr_text"] == "" for row in output_rows(sample))
    assert all(row["error"]["message"] == "test engine failure" for row in output_rows(sample))
    retried = run(sample)
    assert retried["new_inference"] == 3 and retried["cache_hits"] == 0


def test_float_wav_metadata_is_supported(sample):
    soundfile = pytest.importorskip("soundfile")
    import numpy as np
    manifest, _, _, rows = sample
    audio = manifest.parent / rows[0]["audio_path"]
    soundfile.write(audio, np.zeros(1600, dtype="float32"), 16000, subtype="FLOAT")
    rows[0]["audio_sha256"] = file_sha256(audio)
    atomic_jsonl(manifest, rows)
    completed = run(sample)
    assert completed["succeeded"] == 3 and completed["failed"] == 0
    assert output_rows(sample)[0]["sample_rate"] == 16000


def test_out_of_bounds_segments_are_explicit_failure(sample):
    class InvalidTimestampTestAdapter(TestAdapter):
        def transcribe(self, *args):
            prediction = super().transcribe(*args)
            prediction["segments"][0]["end"] = 99
            return prediction
    failed = run(sample, adapter_factory=InvalidTimestampTestAdapter)
    assert failed["failed"] == 3
    assert all("exceeds audio duration" in row["error"]["message"] for row in output_rows(sample))
    retried = run(sample)
    assert retried["cache_hits"] == 0 and retried["succeeded"] == 3


def test_real_decoder_api_without_model_or_download():
    pytest.importorskip("faster_whisper.audio")
    check_decoder_compatibility()


def test_incompatible_decoder_explains_dependency_repair():
    def incompatible_decoder(*args, **kwargs):
        raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
    with pytest.raises(RuntimeError, match="requirements-speech.txt") as error:
        check_decoder_compatibility(decoder=incompatible_decoder)
    assert "metadata_errors" in str(error.value)
    assert "faster-whisper=" in str(error.value) and "av=" in str(error.value)
