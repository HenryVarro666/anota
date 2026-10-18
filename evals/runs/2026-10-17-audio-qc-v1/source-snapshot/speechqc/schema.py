"""Strict, dependency-free report boundary shared by CLI and web import.

V1 scores are screening heuristics, never calibrated error probabilities.
All times are seconds and all dB values are dBFS. Digital silence has a null
dB value because JSON must not contain infinity. Transcript hashes deliberately
exclude unrelated manifest metadata, but include untimed asr_text.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

ANALYZER_VERSION = "0.1.0"
DEFAULT_CONFIG = {
    "low_level_dbfs": -35.0,
    "low_energy_dbfs": -50.0,
    "low_energy_min_s": 1.0,
    "near_clip_amplitude": 0.999,
    "near_clip_ratio": 0.01,
    "dc_offset_threshold": 0.03,
    "frame_ms": 20,
    "coverage_tolerance_s": 0.15,
    "coverage_merge_gap_s": 0.25,
    "coverage_min_issue_s": 0.30,
    "vad_threshold": 0.5,
    "vad_min_speech_ms": 250,
    "vad_min_silence_ms": 150,
    "vad_speech_pad_ms": 30,
    "waveform_bins": 1200,
    "max_duration_s": 1800.0,
    "max_channels": 8,
    "max_samples": 100_000_000,
}

ISSUE_EVIDENCE = {
    "low_level": {"rms_dbfs", "threshold_dbfs"},
    "low_energy": {"rms_dbfs", "threshold_dbfs"},
    "near_clipping": {"near_full_scale_ratio", "threshold_amplitude"},
    "dc_offset": {"dc_offset", "threshold"},
    "speech_without_transcript": {"uncovered_s", "basis", "tolerance_s"},
    "transcript_without_speech": {"text_outside_s", "basis", "tolerance_s"},
}


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def digest_json(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _object(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{name}: expected exactly {sorted(keys)}")


def _number(value, name, low=None, high=None, nullable=False, integer=False):
    if nullable and value is None:
        return
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (integer and not isinstance(value, int))
            or (low is not None and value < low) or (high is not None and value > high)):
        raise ValueError(f"{name}: invalid finite number")


def _text(value, name, maximum=1000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{name}: invalid text")


def _hash(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name}: invalid sha256")


def resolve_config(config=None):
    if config is not None and (not isinstance(config, dict) or set(config) - set(DEFAULT_CONFIG)):
        raise ValueError("unknown audio analysis config keys")
    result = {**DEFAULT_CONFIG, **(config or {})}
    for key, value in result.items():
        _number(value, key)
    for key in ("low_level_dbfs", "low_energy_dbfs"):
        _number(result[key], key, -160, 0)
    for key in ("near_clip_amplitude", "near_clip_ratio", "dc_offset_threshold", "vad_threshold"):
        _number(result[key], key, 0.000001, 1)
    for key in ("coverage_tolerance_s", "coverage_merge_gap_s"):
        _number(result[key], key, 0, 10)
    for key in ("low_energy_min_s", "coverage_min_issue_s"):
        _number(result[key], key, 0.001, 60)
    for key in ("vad_min_speech_ms", "vad_min_silence_ms", "vad_speech_pad_ms"):
        _number(result[key], key, 0, 10000, integer=True)
    _number(result["frame_ms"], "frame_ms", 5, 1000, integer=True)
    _number(result["waveform_bins"], "waveform_bins", 1, 1200, integer=True)
    _number(result["max_duration_s"], "max_duration_s", 0.01, 86400)
    _number(result["max_channels"], "max_channels", 1, 64, integer=True)
    _number(result["max_samples"], "max_samples", 1, 500_000_000, integer=True)
    return result


def canonical_transcript(transcript=None):
    if transcript is None:
        transcript = {}
    if not isinstance(transcript, dict):
        raise ValueError("transcript must be an object")
    result = {"asr_text": transcript.get("asr_text", ""), "segments": [], "words": []}
    _text(result["asr_text"], "asr_text", 1_000_000)
    for key, text_key in (("segments", "text"), ("words", "w")):
        rows = transcript.get(key, [])
        if rows is None:
            rows = []
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ValueError(f"{key}: expected a bounded list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{key}: invalid interval")
            item = {"start": row.get("start"), "end": row.get("end"), text_key: row.get(text_key, "")}
            _number(item["start"], "transcript start", 0)
            _number(item["end"], "transcript end", item["start"])
            _text(item[text_key], text_key, 100000)
            item["start"] = float(item["start"])
            item["end"] = float(item["end"])
            result[key].append(item)
    return result


def transcript_digest(transcript=None):
    return digest_json(canonical_transcript(transcript))


def report_digest(report):
    return digest_json({key: value for key, value in report.items() if key != "analysis_id"})


def _interval(row, duration, name):
    _number(row["start"], f"{name}.start", 0, duration + 1e-6)
    _number(row["end"], f"{name}.end", row["start"], duration + 1e-6)
    if row["end"] <= row["start"]:
        raise ValueError(f"{name}: empty interval")


def validate_report(report, audio_sha256=None, transcript=None, duration_s=None):
    """Reject unknown keys, malformed numbers, stale provenance and changed hashes.

    Returns a detached JSON-safe dict. Server code should project its own display
    allowlist after validation; detectors/config belong to review provenance.
    """
    top = {"schema_version", "analysis_id", "audio_sha256", "transcript_sha256",
           "duration_s", "sample_rate", "channels", "analyzer_version", "config",
           "detectors", "status", "waveform", "metrics", "vad", "coverage", "issues", "warnings"}
    _object(report, top, "audio_analysis")
    if report["schema_version"] != "speechqc-v1" or report["analyzer_version"] != ANALYZER_VERSION:
        raise ValueError("unsupported audio analysis schema/version")
    for key in ("analysis_id", "audio_sha256", "transcript_sha256"):
        _hash(report[key], key)
    if audio_sha256 is not None and report["audio_sha256"] != audio_sha256:
        raise ValueError("audio analysis audio hash mismatch")
    if transcript is not None and report["transcript_sha256"] != transcript_digest(transcript):
        raise ValueError("audio analysis transcript hash mismatch")
    _object(report["config"], DEFAULT_CONFIG, "config")
    config = resolve_config(report["config"])
    duration = report["duration_s"]
    _number(duration, "duration_s", 0.000001, config["max_duration_s"] + 1e-6)
    _number(report["sample_rate"], "sample_rate", 1, 768000, integer=True)
    _number(report["channels"], "channels", 1, config["max_channels"], integer=True)
    if duration * report["sample_rate"] * report["channels"] > config["max_samples"] + report["channels"]:
        raise ValueError("audio analysis exceeds sample resource bound")
    if duration_s is not None:
        _number(duration_s, "expected duration_s", 0)
        if abs(duration - duration_s) > max(0.001, 1 / report["sample_rate"]):
            raise ValueError("audio analysis duration mismatch")
    if report["status"] not in {"complete", "partial"}:
        raise ValueError("invalid analysis status")
    detectors = report["detectors"]
    if not isinstance(detectors, list) or not 1 <= len(detectors) <= 10:
        raise ValueError("invalid detectors")
    detector_names = set()
    for detector in detectors:
        _object(detector, {"name", "version", "model_sha256"}, "detector")
        if detector["name"] not in {"speechqc-dsp", "soundfile", "numpy", "pyav", "faster-whisper-silero", "onnxruntime"}:
            raise ValueError("unknown detector")
        if detector["name"] in detector_names:
            raise ValueError("duplicate detector")
        detector_names.add(detector["name"])
        _text(detector["version"], "detector version", 100)
        if not detector["version"]:
            raise ValueError("empty detector version")
        if detector["model_sha256"] is not None:
            _hash(detector["model_sha256"], "model_sha256")
    waveform = report["waveform"]
    _object(waveform, {"duration_s", "peaks", "channels"}, "waveform")
    _number(waveform["duration_s"], "waveform duration", 0.000001)
    _number(waveform["channels"], "waveform channels", 1, config["max_channels"], integer=True)
    if waveform["duration_s"] != duration or waveform["channels"] != report["channels"]:
        raise ValueError("waveform metadata mismatch")
    peaks = waveform["peaks"]
    if not isinstance(peaks, list) or not 1 <= len(peaks) <= config["waveform_bins"]:
        raise ValueError("invalid waveform peak count")
    for peak in peaks:
        if not isinstance(peak, list) or len(peak) != 2:
            raise ValueError("invalid waveform peak")
        _number(peak[0], "peak minimum", -1, 1)
        _number(peak[1], "peak maximum", peak[0], 1)
    metrics = report["metrics"]
    _object(metrics, {"peak_dbfs", "rms_dbfs", "near_full_scale_ratio", "low_energy_ratio", "dc_offset"}, "metrics")
    for key in ("peak_dbfs", "rms_dbfs"):
        _number(metrics[key], key, -1000, 1000, nullable=True)
    for key in ("near_full_scale_ratio", "low_energy_ratio"):
        _number(metrics[key], key, 0, 1)
    _number(metrics["dc_offset"], "dc_offset", 0)
    vad = report["vad"]
    _object(vad, {"status", "intervals", "error"}, "vad")
    if vad["status"] not in {"ok", "unavailable"}:
        raise ValueError("invalid vad status")
    if not isinstance(vad["intervals"], list) or len(vad["intervals"]) > 100000:
        raise ValueError("invalid vad intervals")
    previous = -1
    for row in vad["intervals"]:
        _object(row, {"start", "end"}, "vad interval")
        _interval(row, duration, "vad")
        if row["start"] < previous:
            raise ValueError("VAD intervals must be ordered and disjoint")
        previous = row["end"]
    if vad["status"] == "ok":
        if vad["error"] is not None or report["status"] != "complete":
            raise ValueError("inconsistent successful VAD state")
        silero = next((d for d in detectors if d["name"] == "faster-whisper-silero"), None)
        if not silero or not silero["model_sha256"]:
            raise ValueError("successful VAD requires model provenance")
    else:
        _text(vad["error"], "vad error", 1000)
        if not vad["error"] or vad["intervals"] or report["status"] != "partial":
            raise ValueError("inconsistent unavailable VAD state")
    coverage = report["coverage"]
    _object(coverage, {"status", "basis", "speech_s", "covered_s", "uncovered_s", "uncovered_ratio", "text_outside_s"}, "coverage")
    if coverage["status"] not in {"ok", "unavailable"} or coverage["basis"] not in {"words", "segments", "none"}:
        raise ValueError("invalid coverage metadata")
    if coverage["status"] == "ok":
        if vad["status"] != "ok":
            raise ValueError("coverage requires VAD")
        for key in ("speech_s", "covered_s", "uncovered_s", "text_outside_s"):
            _number(coverage[key], key, 0, duration + 1e-6)
        _number(coverage["uncovered_ratio"], "uncovered_ratio", 0, 1, nullable=True)
        speech_sum = sum(row["end"] - row["start"] for row in vad["intervals"])
        if abs(speech_sum - coverage["speech_s"]) > 1e-4:
            raise ValueError("coverage speech duration mismatch")
        if abs(coverage["covered_s"] + coverage["uncovered_s"] - coverage["speech_s"]) > 1e-4:
            raise ValueError("inconsistent coverage totals")
        expected = coverage["uncovered_s"] / coverage["speech_s"] if coverage["speech_s"] else None
        actual = coverage["uncovered_ratio"]
        if ((expected is None) != (actual is None)) or (expected is not None and abs(expected - actual) > 1e-4):
            raise ValueError("inconsistent uncovered ratio")
    elif any(coverage[key] is not None for key in ("speech_s", "covered_s", "uncovered_s", "uncovered_ratio", "text_outside_s")):
        raise ValueError("unavailable coverage must have null metrics")
    issues = report["issues"]
    if not isinstance(issues, list) or len(issues) > 10000:
        raise ValueError("invalid issue list")
    seen = set()
    for issue in issues:
        _object(issue, {"id", "code", "start", "end", "priority", "message", "evidence"}, "issue")
        _hash(issue["id"], "issue id")
        if issue["id"] in seen:
            raise ValueError("duplicate issue id")
        seen.add(issue["id"])
        if issue["code"] not in ISSUE_EVIDENCE or issue["priority"] not in {"low", "medium", "high"}:
            raise ValueError("invalid issue metadata")
        _interval(issue, duration, "issue")
        _text(issue["message"], "issue message", 500)
        evidence = issue["evidence"]
        _object(evidence, ISSUE_EVIDENCE[issue["code"]], "issue evidence")
        for key, value in evidence.items():
            if key == "basis":
                if value not in {"words", "segments", "none"}:
                    raise ValueError("invalid evidence basis")
            elif key in {"rms_dbfs", "threshold_dbfs"}:
                _number(value, key, -1000, 1000, nullable=key == "rms_dbfs")
            elif key in {"near_full_scale_ratio", "threshold_amplitude", "threshold"}:
                _number(value, key, 0, 1)
            elif key == "tolerance_s":
                _number(value, key, 0, 10)
            else:
                _number(value, key, 0, duration if key in {"uncovered_s", "text_outside_s"} else None)
        if issue["code"] in {"speech_without_transcript", "transcript_without_speech"} and coverage["status"] != "ok":
            raise ValueError("coverage issue without coverage")
        if issue["id"] != digest_json({key: value for key, value in issue.items() if key != "id"}):
            raise ValueError("issue hash mismatch")
    if not isinstance(report["warnings"], list) or len(report["warnings"]) > 30:
        raise ValueError("invalid warnings")
    for warning in report["warnings"]:
        _text(warning, "warning", 1000)
    if report["analysis_id"] != report_digest(report):
        raise ValueError("audio analysis content hash mismatch")
    return json.loads(canonical_json(report))
