"""DSP + VAD + temporal coverage v1; no transcript correction or ASR execution.

Defaults are frozen engineering heuristics, not learned/calibrated thresholds.
RMS/low-energy use the loudest channel and DC uses maximum absolute channel
mean; all-channel extrema retain anti-phase stereo. Clip ratio counts sample
positions with ANY channel near full scale. Low-energy runs are acoustic
observations only, not VAD. Coverage pads/merges timed text and pads speech for
the reverse check to absorb timestamp jitter. Words take precedence when usable.
"""

from __future__ import annotations

import importlib.metadata
import math

from .schema import (
    ANALYZER_VERSION,
    canonical_transcript,
    digest_json,
    file_sha256,
    report_digest,
    resolve_config,
    transcript_digest,
    validate_report,
)
from .vad import run_vad


def merge_intervals(intervals, gap=0.0, duration=None, pad=0.0):
    rows = []
    for start, end in intervals:
        start = max(0.0, float(start) - pad)
        end = float(end) + pad
        if duration is not None:
            start, end = min(duration, start), min(duration, end)
        if end > start:
            rows.append((start, end))
    merged = []
    for start, end in sorted(rows):
        if merged and start <= merged[-1][1] + gap + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(source, covered):
    result = []
    for start, end in source:
        cursor = start
        for other_start, other_end in covered:
            if other_end <= cursor:
                continue
            if other_start >= end:
                break
            if other_start > cursor:
                result.append((cursor, min(end, other_start)))
            cursor = max(cursor, other_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def _seconds(intervals):
    return sum(end - start for start, end in intervals)


def _db(amplitude):
    return round(20 * math.log10(float(amplitude)), 6) if amplitude > 0 else None


def _r(value):
    return round(float(value), 6)


def _issue(code, start, end, priority, message, evidence):
    row = {"code": code, "start": _r(start), "end": _r(end), "priority": priority,
           "message": message, "evidence": evidence}
    row["id"] = digest_json(row)
    return row


def _runs(flags, frame_samples, sample_rate, duration):
    return merge_intervals([(index * frame_samples / sample_rate,
                             min(duration, (index + 1) * frame_samples / sample_rate))
                            for index, flag in enumerate(flags) if flag], duration=duration)


def coverage_analysis(transcript, speech, duration, config):
    """Pure temporal coverage. Does not assess semantic correctness."""
    transcript = canonical_transcript(transcript)
    warnings = []
    basis, timed = "none", []
    has_timed_text = False
    for key, text_key in (("words", "w"), ("segments", "text")):
        nonempty = [row for row in transcript[key] if row[text_key].strip()]
        has_timed_text = has_timed_text or bool(nonempty)
        usable = [(row["start"], row["end"]) for row in nonempty
                  if row["end"] > row["start"] and row["start"] < duration and row["end"] <= duration + 0.001]
        if len(usable) != len(nonempty):
            warnings.append(f"Some {key} have zero-duration or out-of-audio timestamps; they cannot supply reliable coverage.")
            continue
        if key == "words" and usable:
            reliable_segments = [row for row in transcript["segments"]
                                 if row["text"].strip() and row["end"] > row["start"]
                                 and row["start"] < duration and row["end"] <= duration + 0.001]
            if any(not any(min(segment["end"], end) > max(segment["start"], start)
                           for start, end in usable) for segment in reliable_segments):
                warnings.append("Some nonempty segments have no overlapping word timestamps; using segment coverage when available.")
                continue
        if nonempty:
            basis, timed = key, usable
            break
    if any(row["end"] > duration + 0.1 for key in ("words", "segments") for row in transcript[key]):
        warnings.append("Transcript timestamps extend beyond audio; unusable timing is excluded or coverage is unavailable.")
    if basis == "none" and (transcript["asr_text"].strip() or has_timed_text):
        warnings.append("Transcript text has no usable timestamps; temporal coverage is unavailable.")
        return {"status": "unavailable", "basis": "none", "speech_s": None, "covered_s": None,
                "uncovered_s": None, "uncovered_ratio": None, "text_outside_s": None}, [], warnings
    if basis == "segments":
        warnings.append("Coverage uses segment spans; omissions inside a segment may be hidden.")
    tolerance = config["coverage_tolerance_s"]
    text = merge_intervals(timed, config["coverage_merge_gap_s"] if basis == "words" else 0.0, duration, tolerance)
    speech = merge_intervals(speech, duration=duration)
    uncovered = subtract_intervals(speech, text)
    outside = subtract_intervals(merge_intervals(timed, duration=duration),
                                 merge_intervals(speech, duration=duration, pad=tolerance))
    speech_s, uncovered_s = _seconds(speech), _seconds(uncovered)
    coverage = {"status": "ok", "basis": basis, "speech_s": _r(speech_s),
                "covered_s": _r(speech_s - uncovered_s), "uncovered_s": _r(uncovered_s),
                "uncovered_ratio": _r(uncovered_s / speech_s) if speech_s else None,
                "text_outside_s": _r(_seconds(outside))}
    issues = []
    for start, end in uncovered:
        if end - start + 1e-9 >= config["coverage_min_issue_s"]:
            issues.append(_issue("speech_without_transcript", start, end, "medium",
                                 "Detected speech has no nearby timed transcript. Listen to verify.",
                                 {"uncovered_s": _r(end - start), "basis": basis, "tolerance_s": tolerance}))
    for start, end in outside:
        if end - start + 1e-9 >= config["coverage_min_issue_s"]:
            issues.append(_issue("transcript_without_speech", start, end, "medium",
                                 "Timed transcript lies outside detected speech. Listen to verify.",
                                 {"text_outside_s": _r(end - start), "basis": basis, "tolerance_s": tolerance}))
    return coverage, issues, warnings


def analyze(audio_path, transcript=None, config=None):
    import numpy as np
    import soundfile as sf

    config = resolve_config(config)
    transcript = canonical_transcript(transcript)
    audio_hash = file_sha256(audio_path)
    info = sf.info(str(audio_path))
    if info.frames <= 0 or info.samplerate <= 0:
        raise ValueError("audio must contain at least one sample")
    if (info.frames / info.samplerate > config["max_duration_s"]
            or info.channels > config["max_channels"]
            or info.frames * info.channels > config["max_samples"]):
        raise ValueError("audio exceeds configured duration/channel/sample resource bound")
    samples, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=True)
    if not np.isfinite(samples).all():
        raise ValueError("audio contains non-finite samples")
    if file_sha256(audio_path) != audio_hash:
        raise ValueError("audio changed during analysis")
    duration = len(samples) / sample_rate
    if duration < 0.000001:
        raise ValueError("audio is too short")
    channels = samples.shape[1]
    absolute = np.abs(samples)
    peak = float(np.max(absolute))
    rms = float(np.max(np.sqrt(np.mean(np.square(samples, dtype=np.float64), axis=0))))
    dc_offset = float(np.max(np.abs(np.mean(samples, axis=0, dtype=np.float64))))
    near = np.any(absolute >= config["near_clip_amplitude"], axis=1)
    frame_samples = max(1, round(sample_rate * config["frame_ms"] / 1000))
    energy, clip = [], []
    for offset in range(0, len(samples), frame_samples):
        frame = samples[offset:offset + frame_samples]
        energy.append(float(np.max(np.sqrt(np.mean(np.square(frame, dtype=np.float64), axis=0)))))
        clip.append(float(np.mean(near[offset:offset + frame_samples])))
    low_flags = np.asarray(energy) <= 10 ** (config["low_energy_dbfs"] / 20)
    low_intervals = _runs(low_flags, frame_samples, sample_rate, duration)
    low_ratio = _seconds(low_intervals) / duration
    near_ratio = float(np.mean(near))
    peaks = []
    for start, end in zip(np.linspace(0, len(samples), min(config["waveform_bins"], len(samples)) + 1, dtype=int)[:-1],
                          np.linspace(0, len(samples), min(config["waveform_bins"], len(samples)) + 1, dtype=int)[1:]):
        chunk = samples[start:end]
        peaks.append([_r(np.clip(chunk.min(), -1, 1)), _r(np.clip(chunk.max(), -1, 1))])
    issues = []
    if rms <= 10 ** (config["low_level_dbfs"] / 20):
        issues.append(_issue("low_level", 0, duration, "low", "Overall recording level is low; check audibility.",
                             {"rms_dbfs": _db(rms), "threshold_dbfs": config["low_level_dbfs"]}))
    for start, end in low_intervals:
        if end - start + 1e-9 >= config["low_energy_min_s"]:
            section = samples[round(start * sample_rate):round(end * sample_rate)]
            section_rms = float(np.max(np.sqrt(np.mean(np.square(section, dtype=np.float64), axis=0))))
            issues.append(_issue("low_energy", start, end, "low", "Sustained low acoustic energy; this does not establish absence of speech.",
                                 {"rms_dbfs": _db(section_rms), "threshold_dbfs": config["low_energy_dbfs"]}))
    for start, end in _runs(np.asarray(clip) >= config["near_clip_ratio"], frame_samples, sample_rate, duration):
        ratio = float(np.mean(near[round(start * sample_rate):round(end * sample_rate)]))
        issues.append(_issue("near_clipping", start, end, "medium", "Samples approach full scale; listen for possible clipping.",
                             {"near_full_scale_ratio": _r(ratio), "threshold_amplitude": config["near_clip_amplitude"]}))
    if dc_offset >= config["dc_offset_threshold"]:
        issues.append(_issue("dc_offset", 0, duration, "low", "A channel has a sustained DC offset.",
                             {"dc_offset": _r(dc_offset), "threshold": config["dc_offset_threshold"]}))
    detectors = [{"name": "speechqc-dsp", "version": ANALYZER_VERSION, "model_sha256": None},
                 {"name": "soundfile", "version": importlib.metadata.version("soundfile"), "model_sha256": None},
                 {"name": "numpy", "version": np.__version__, "model_sha256": None}]
    warnings = ["Heuristic review hints only. Temporal coverage does not establish transcript correctness."]
    if peak > 1:
        warnings.append("Audio contains samples above digital full scale; displayed waveform is clipped to [-1, 1].")
    try:
        spans, provenance = run_vad(samples, sample_rate, config)
        spans = merge_intervals(spans, duration=duration)
        vad = {"status": "ok", "intervals": [{"start": _r(s), "end": _r(e)} for s, e in spans], "error": None}
        detectors.extend(provenance)
    except Exception as exc:  # noqa: BLE001 - optional native VAD backends use heterogeneous exception types
        # Do not expose local paths/tracebacks in a report imported into a UI.
        error = f"Silero VAD unavailable ({type(exc).__name__}); check optional dependencies and local model."
        vad = {"status": "unavailable", "intervals": [], "error": error}
        warnings.append(error)
    if vad["status"] == "ok":
        coverage, coverage_issues, coverage_warnings = coverage_analysis(
            transcript, [(x["start"], x["end"]) for x in vad["intervals"]], duration, config)
        issues.extend(coverage_issues)
        warnings.extend(coverage_warnings)
    else:
        coverage = {"status": "unavailable", "basis": "none", "speech_s": None, "covered_s": None,
                    "uncovered_s": None, "uncovered_ratio": None, "text_outside_s": None}
    if len(issues) > 10000:
        raise ValueError("too many issue intervals; use shorter recordings")
    report = {"schema_version": "speechqc-v1", "analysis_id": "", "audio_sha256": audio_hash,
              "transcript_sha256": transcript_digest(transcript), "duration_s": _r(duration),
              "sample_rate": sample_rate, "channels": channels, "analyzer_version": ANALYZER_VERSION,
              "config": config, "detectors": detectors, "status": "complete" if vad["status"] == "ok" else "partial",
              "waveform": {"duration_s": _r(duration), "peaks": peaks, "channels": channels},
              "metrics": {"peak_dbfs": _db(peak), "rms_dbfs": _db(rms), "near_full_scale_ratio": _r(near_ratio),
                          "low_energy_ratio": _r(low_ratio), "dc_offset": _r(dc_offset)},
              "vad": vad, "coverage": coverage, "issues": sorted(issues, key=lambda row: (row["start"], row["end"], row["code"])),
              "warnings": warnings}
    report["analysis_id"] = report_digest(report)
    return validate_report(report, audio_sha256=audio_hash, transcript=transcript)
