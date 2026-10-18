"""Durable, serial offline ASR runs; the web server never loads these models."""

from __future__ import annotations

import math
import time
import uuid
import wave
from collections.abc import Callable
from pathlib import Path

from .asr import FasterWhisperAdapter, ModelSpec, runtime_versions
from .manifest import atomic_jsonl, digest_json, file_sha256, read_manifest
from .state import State, exclusive_run
from .vendor.audio_io import wav_info


def default_config(device: str = "cpu", compute_type: str = "int8",
                   beam_size: int = 5, cpu_threads: int = 4,
                   vad_filter: bool = True) -> dict:
    if beam_size < 1 or cpu_threads < 1:
        raise ValueError("beam_size and cpu_threads must be positive")
    return {"adapter_version": "speechprep-faster-whisper-v1",
            "preprocessing": "decode-original-audio-v1",
            "device": device, "compute_type": compute_type,
            "beam_size": beam_size, "cpu_threads": cpu_threads,
            "vad_filter": vad_filter, "word_timestamps": True,
            "condition_on_previous_text": False,
            "task": "transcribe", "runtime_versions": runtime_versions()}


def cache_key(row: dict, model: ModelSpec, config: dict) -> str:
    return digest_json({"audio_sha256": row["audio_sha256"],
                        "language": row["language"],
                        "model": {"name": model.name, "revision": model.revision,
                                  "repo_id": model.repo_id}, "config": config})


def audio_info(path: str) -> dict:
    if Path(path).suffix.lower() == ".wav":
        try:
            result = wav_info(path)
        except (wave.Error, EOFError):
            # FLEURS WAV sources can use IEEE float rather than PCM16.
            result = None
    else:
        result = None
    if result is None:
        import soundfile
        info = soundfile.info(path)
        result = {"duration_s": info.duration, "sample_rate": info.samplerate,
                  "channels": info.channels, "frames": info.frames}
    if result["frames"] <= 0 or result["sample_rate"] <= 0 or result["channels"] <= 0:
        raise ValueError("empty audio or invalid audio metadata")
    return result


def validate_prediction(prediction: dict, duration_s: float | None = None) -> None:
    if not isinstance(prediction.get("text"), str):
        raise TypeError("ASR adapter returned invalid text")
    if not isinstance(prediction.get("segments"), list):
        raise TypeError("ASR adapter returned invalid segments")
    for segment in prediction["segments"]:
        start, end = segment.get("start"), segment.get("end")
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end < start or not isinstance(segment.get("text"), str)):
            raise ValueError("ASR adapter returned invalid timestamp segment")
        if duration_s is not None and end > duration_s + 0.05:
            raise ValueError("ASR segment exceeds audio duration")


def run_pipeline(manifest: str | Path, output: str | Path, state_path: str | Path,
                 model: ModelSpec, config: dict | None = None, limit: int | None = None,
                 adapter_factory: Callable = FasterWhisperAdapter) -> dict:
    """Infer missing results, atomically publish terminal rows, and return counters.

    limit caps new attempts, including input failures, while preserving available
    cache hits. Missing rows in a controlled partial output are reported as remaining.
    adapter_factory is for unit-test injection only; the CLI always uses real ASR.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")
    manifest, output, state_path = (Path(p).resolve() for p in (manifest, output, state_path))
    if len({manifest, output, state_path}) != 3:
        raise ValueError("manifest, output and state must have different paths")
    if output == Path(str(state_path) + ".lock"):
        raise ValueError("output cannot replace the process lock file")
    rows = read_manifest(manifest)
    config = dict(config or default_config())
    run_id = str(uuid.uuid4())
    summary = {"run_id": run_id, "total": len(rows), "succeeded": 0, "failed": 0,
               "new_inference": 0, "cache_hits": 0, "recovered": 0,
               "attempted": 0, "remaining": 0, "complete": False,
               "output": str(output), "state": str(state_path)}
    started = time.perf_counter()
    with exclusive_run(state_path):
        state = State(state_path)
        try:
            summary["recovered"] = state.recover()
            adapter, output_rows = None, []
            for row in rows:
                key = cache_key(row, model, config)
                cached = state.cached(key)
                # Hash verification happens on every reuse, never trusting filename alone.
                within_limit = limit is None or summary["attempted"] < limit
                if cached is None and not within_limit:
                    summary["remaining"] += 1
                    continue
                if cached is None:
                    summary["attempted"] += 1
                item_started = time.perf_counter()
                result = {**row, "status": "running", "asr_text": "", "segments": [],
                          "words": [], "audio_duration_s": None, "sample_rate": None,
                          "channels": None, "model": {"name": model.name, "revision": model.revision,
                             "repo_id": model.repo_id, "device": config["device"],
                             "compute_type": config["compute_type"]},
                          "config": config, "cache_key": key, "run_id": run_id,
                          "processing_ms": 0, "cache_hit": False, "error": None}
                state.mark(row["item_id"], key, "running", run_id)
                try:
                    actual_hash = file_sha256(row["audio_path"])
                    if actual_hash != row["audio_sha256"]:
                        raise ValueError("audio SHA-256 mismatch; regenerate the manifest for changed audio")
                    if cached is not None:
                        for field in ("asr_text", "segments", "words", "audio_duration_s",
                                      "sample_rate", "channels", "detected_language",
                                      "language_probability"):
                            if field in cached:
                                result[field] = cached[field]
                        validate_prediction({"text": result["asr_text"], "segments": result["segments"]},
                                            result["audio_duration_s"])
                        result.update(status="succeeded", cache_hit=True,
                            inference_run_id=cached.get("inference_run_id", cached["run_id"]),
                            inference_processing_ms=cached.get("inference_processing_ms", cached["processing_ms"]))
                        summary["cache_hits"] += 1
                    else:
                        info = audio_info(row["audio_path"])
                        result.update(audio_duration_s=info["duration_s"],
                                      sample_rate=info["sample_rate"], channels=info["channels"])
                        if adapter is None:
                            adapter = adapter_factory(model, config)
                        summary["new_inference"] += 1
                        prediction = adapter.transcribe(row["audio_path"], row["language"])
                        validate_prediction(prediction, info["duration_s"])
                        result.update(status="succeeded", asr_text=prediction["text"],
                                      segments=prediction["segments"], words=prediction.get("words", []),
                                      detected_language=prediction.get("language", row["language"]),
                                      language_probability=prediction.get("language_prob"),
                                      inference_run_id=run_id)
                        result["inference_processing_ms"] = round((time.perf_counter() - item_started) * 1000, 3)
                except Exception as exc:  # noqa: BLE001 -- persist engine failures per item, never substitute output
                    # A bad input, unavailable dependency, or model error stays in the denominator.
                    if cached is not None:
                        summary["attempted"] += 1
                    result.update(status="failed", cache_hit=False,
                                  error={"type": type(exc).__name__, "message": str(exc)})
                result["processing_ms"] = round((time.perf_counter() - item_started) * 1000, 3)
                state.mark(row["item_id"], key, result["status"], run_id, result,
                           str(result["error"]) if result["error"] else None)
                output_rows.append(result)
                summary[result["status"]] += 1
            atomic_jsonl(output, output_rows)
            summary["complete"] = summary["remaining"] == 0
        finally:
            state.close()
    summary["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return summary
