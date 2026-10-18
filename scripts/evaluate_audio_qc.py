"""Frozen engineering experiment for speechqc; no human accuracy claims.

The source manifest and audio are read-only. Reference transcripts are never read.
Saved artifacts contain no audio copies, cache files, or absolute source paths.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FROZEN_CONFIG = {
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
    "max_duration_s": 1800,
    "max_channels": 8,
    "max_samples": 100000000,
}
SYNTHETIC_CASES = (
    "normal_tone", "silence", "quiet_tone", "near_clipping", "dc_offset", "antiphase",
)
CONDITIONS = ("original", "transcript_removed", "audio_silenced", *SYNTHETIC_CASES)
LIMITATIONS = [
    "Engineering checks only: natural-audio issue flags have not been human adjudicated.",
    "Controlled variants share parent recordings and are not independent samples.",
    "Synthetic signals are not natural speech; these checks do not estimate VAD quality.",
    "No reference text was read by the detector or this experiment.",
    "No human time saving, semantic accuracy, natural-error recall or false-alarm rate was measured.",
    "Empty-cache timing includes file I/O, hashing, DSP, VAD, serialization and first-use model setup.",
    "Cache replay timing is recorded separately and is not model inference speed.",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_manifest(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("item_id"), str) or not row["item_id"].strip():
            raise ValueError(f"Manifest line {number} requires a nonempty string item_id")
        rows.append(row)
    if not rows:
        raise ValueError("The manifest is empty; no denominator can be established")
    if len({row["item_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate item_id in manifest")
    return rows


def detector_transcript(row: dict[str, Any]) -> dict[str, Any]:
    """Deliberate allowlist: no references or human decisions enter analysis."""
    return {"asr_text": row.get("asr_text", ""), "segments": row.get("segments", []), "words": row.get("words", [])}


def merged_intervals(intervals: list[dict[str, Any]], duration: float, gap: float) -> list[tuple[float, float]]:
    clean = []
    for span in intervals:
        if not isinstance(span, dict):
            continue
        start, end = span.get("start"), span.get("end")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
            continue
        start, end = max(0.0, float(start)), min(duration, float(end))
        if end > start:
            clean.append((start, end))
    result: list[tuple[float, float]] = []
    for start, end in sorted(clean):
        if result and start <= result[-1][1] + gap:
            result[-1] = result[-1][0], max(result[-1][1], end)
        else:
            result.append((start, end))
    return result


def controlled_eligibility(condition: str, parent_report: dict[str, Any] | None,
                           transcript: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    if parent_report is None:
        return False, "parent analysis unavailable"
    duration = float(parent_report["duration_s"])
    if condition == "transcript_removed":
        if parent_report.get("vad", {}).get("status") != "ok":
            return False, "parent VAD unavailable"
        intervals = parent_report["vad"].get("intervals", [])
    elif condition == "audio_silenced":
        # Use the actual parent time-axis decision. Invalid/partial word timing
        # may require segment fallback; do not reimplement that model contract.
        coverage = parent_report.get("coverage", {})
        basis = coverage.get("basis")
        if coverage.get("status") != "ok" or basis not in {"words", "segments"}:
            return False, "parent has no reliable transcript time axis"
        text_keys = ("w", "word", "text") if basis == "words" else ("text",)
        intervals = [span for span in transcript.get(basis, []) if isinstance(span, dict)
                     and any(isinstance(span.get(key), str) and span[key].strip() for key in text_keys)]
    else:
        raise ValueError("Eligibility applies only to controlled conditions")
    # Reverse coverage uses the union of actual timed words, without bridging
    # silent gaps. An aggregate length across short fragments is not eligible.
    spans = merged_intervals(intervals, duration, 0)
    eligible = any(end - start >= config["coverage_min_issue_s"] for start, end in spans)
    return eligible, "qualifying interval present" if eligible else "no interval reaches frozen minimum duration"


def evaluate_expectation(condition: str, report: dict[str, Any] | None,
                         eligible: bool | None = None) -> dict[str, Any]:
    if condition == "original":
        return {"status": "observational", "reason": "No adjudicated natural-error labels"}
    if eligible is False:
        return {"status": "ineligible", "reason": "See eligibility_reason; retained in full denominator"}
    if report is None:
        return {"status": "failed", "reason": "Analysis did not return a report"}
    codes = {issue["code"] for issue in report.get("issues", [])}
    expected = {
        "transcript_removed": {"speech_without_transcript"},
        "audio_silenced": {"transcript_without_speech"},
        "silence": {"low_level", "low_energy"},
        "quiet_tone": {"low_level"},
        "near_clipping": {"near_clipping"},
        "dc_offset": {"dc_offset"},
    }
    if condition in expected:
        passed = bool(codes & expected[condition])
        return {"status": "passed" if passed else "failed", "expected_any_codes": sorted(expected[condition])}
    if condition == "normal_tone":
        forbidden = {"low_level", "near_clipping", "dc_offset"}
        return {"status": "failed" if codes & forbidden else "passed", "forbidden_codes": sorted(forbidden)}
    if condition == "antiphase":
        rms = report.get("metrics", {}).get("rms_dbfs")
        passed = isinstance(rms, (int, float)) and math.isfinite(rms) and rms > -35 and "low_level" not in codes
        return {"status": "passed" if passed else "failed", "required": "rms_dbfs > -35 and no low_level"}
    raise ValueError(f"Unknown condition: {condition}")


def timing_summary(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    def percentile(q: float) -> float | None:
        if not ordered:
            return None
        position = (len(ordered) - 1) * q
        lo, hi = math.floor(position), math.ceil(position)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)
    return {"calls": len(values), "sum_ms": sum(values), "p50_ms": percentile(.5), "p95_ms": percentile(.95)}


def summarize(records: list[dict[str, Any]], parent_ids: list[str]) -> dict[str, Any]:
    if len(set(parent_ids)) != len(parent_ids):
        raise ValueError("Duplicate parent identities")
    identities = [record["case_id"] for record in records]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate case identities")
    unknown = {record.get("parent_item_id") for record in records if record.get("parent_item_id")} - set(parent_ids)
    if unknown:
        raise ValueError("Record parent outside manifest")
    condition_parents = [(record["condition"], record.get("parent_item_id")) for record in records]
    if len(set(condition_parents)) != len(condition_parents):
        raise ValueError("Duplicate condition for the same parent")
    if any(record["condition"] not in CONDITIONS for record in records):
        raise ValueError("Unknown experimental condition")
    by_condition = {}
    for condition in CONDITIONS:
        selected = [record for record in records if record["condition"] == condition]
        expected_count = len(parent_ids) if condition in CONDITIONS[:3] else 1
        if len(selected) > expected_count:
            raise ValueError("More observations than planned for condition")
        statuses = Counter(record["status"] for record in selected)
        assertions = Counter(record["expectation"]["status"] for record in selected)
        issue_counts: Counter[str] = Counter()
        item_counts: Counter[str] = Counter()
        for record in selected:
            codes = [issue["code"] for issue in (record.get("report") or {}).get("issues", [])]
            issue_counts.update(codes)
            item_counts.update(set(codes))
        by_condition[condition] = {
            "planned_cases": expected_count, "recorded_cases": len(selected),
            "missing_cases": expected_count - len(selected), "statuses": dict(statuses),
            "expectations": dict(assertions),
            "eligible_controlled_cases": sum(record.get("eligible") is True for record in selected),
            "issue_count_by_code": dict(sorted(issue_counts.items())),
            "flagged_item_count_by_code": dict(sorted(item_counts.items())),
            "items_with_any_issue": sum(bool((record.get("report") or {}).get("issues")) for record in selected),
            "vad_ok": sum((record.get("report") or {}).get("vad", {}).get("status") == "ok" for record in selected),
            "audio_seconds": sum(float((record.get("report") or {}).get("duration_s", 0)) for record in selected),
            "empty_cache": timing_summary([record["empty_cache_ms"] for record in selected if "empty_cache_ms" in record]),
            "cache_replay": timing_summary([record["cache_replay_ms"] for record in selected if "cache_replay_ms" in record]),
            "empty_cache_successful": timing_summary([record["empty_cache_ms"] for record in selected if "empty_cache_ms" in record and record["status"] != "failed"]),
            "empty_cache_failed": timing_summary([record["empty_cache_ms"] for record in selected if "empty_cache_ms" in record and record["status"] == "failed"]),
            "cache_replay_successful": timing_summary([record["cache_replay_ms"] for record in selected if "cache_replay_ms" in record and record.get("cache_replay_status") != "failed"]),
            "cache_replay_failed": timing_summary([record["cache_replay_ms"] for record in selected if "cache_replay_ms" in record and record.get("cache_replay_status") == "failed"]),
            "cache_semantically_equal": sum(record.get("cache_semantically_equal") is True for record in selected),
            "cache_entry_unchanged": sum(record.get("cache_entry_unchanged") is True for record in selected),
            "cache_replay_failures": sum(record.get("cache_replay_status") == "failed" for record in selected),
        }
    return {
        "schema_version": "audio-qc-experiment-v1", "parent_recordings": len(parent_ids),
        "planned_cases": len(parent_ids) * 3 + len(SYNTHETIC_CASES),
        "recorded_cases": len(records), "conditions": by_condition,
        "engineering_checks": {
            "analysis_failures": sum(record["status"] == "failed" for record in records),
            "partial_reports": sum(record["status"] == "partial" for record in records),
            "assertion_failures": sum(record["expectation"]["status"] == "failed" for record in records),
            "cache_mismatches": sum(record.get("cache_semantically_equal") is False for record in records),
            "cache_replay_failures": sum(record.get("cache_replay_status") == "failed" for record in records),
        },
        "human_adjudication": "pending", "human_efficiency_trial": "pending",
        "percentile_method": "linear interpolation, position=(n-1)*q",
        "limitations": LIMITATIONS,
    }


def make_synthetic(path: Path, condition: str) -> dict[str, Any]:
    from array import array
    rate, samples = 16000, 48000
    channels = 2 if condition == "antiphase" else 1
    output = array("h")
    for index in range(samples):
        base = 0.2 * math.sin(2 * math.pi * 440 * index / rate)
        if condition == "silence":
            value = 0.0
        elif condition == "quiet_tone":
            value = base * 0.0005
        elif condition == "near_clipping":
            value = 0.9999 if math.sin(2 * math.pi * 440 * index / rate) >= 0 else -0.9999
        elif condition == "dc_offset":
            value = base + .15
        else:
            value = base
        output.append(round(value * 32767))
        if channels == 2:
            output.append(round(-value * 32767))
    if sys.byteorder != "little":
        output.byteswap()
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(output.tobytes())
    return {"generator": "deterministic-pcm16-v1", "condition": condition,
            "sample_rate": rate, "num_samples": samples, "channels": channels,
            "frequency_hz": 440, "base_amplitude": .2}


def make_silenced(source: Path, destination: Path) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf
    info = sf.info(str(source))
    sf.write(str(destination), np.zeros((info.frames, info.channels), dtype=np.float32), info.samplerate, subtype="FLOAT")
    return {"generator": "same-shape-zero-float32-v1", "sample_rate": info.samplerate,
            "num_samples": info.frames, "channels": info.channels}


def safe_value(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: safe_value(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_value(item, replacements) for item in value]
    if isinstance(value, str):
        for source, replacement in replacements.items():
            value = value.replace(source, replacement).replace(source.replace("\\", "/"), replacement)
    return value


def semantic_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key not in {"cache_hit", "processing_ms"}}


def run(manifest: Path, output: Path) -> dict[str, Any]:
    # Imports stay local so denominator/expectation tests need no optional models.
    sys.path.insert(0, str(ROOT))
    from speechqc import analyze_cached

    manifest = manifest.resolve()
    rows = read_manifest(manifest)
    if output.exists():
        raise FileExistsError("Use a new output directory; existing experiment evidence is never overwritten")
    output.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()  # noqa: UP017 - retain Python 3.10 compatibility
    input_sha = sha256(manifest)
    config = dict(FROZEN_CONFIG)
    write_json(output / "config.json", config)
    snapshot_paths = [Path(__file__), ROOT / "docs" / "audio-qc-v1.md", ROOT / "requirements-audioqc.txt",
                      ROOT / "tests" / "test_audio_qc_experiment.py", *sorted((ROOT / "speechqc").rglob("*.py"))]
    for source in snapshot_paths:
        destination = output / "source-snapshot" / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    packages = {}
    for name in ("numpy", "soundfile", "faster-whisper", "onnxruntime", "av"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "unavailable"
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    protocol = {
        "schema_version": "audio-qc-protocol-v1", "frozen_at_utc": started,
        "input_manifest": manifest.name, "input_manifest_sha256": input_sha,
        "parent_recordings": len(rows), "planned_cases": len(rows) * 3 + len(SYNTHETIC_CASES),
        "source_snapshot_frozen_before_analysis": True,
        "config_sha256": sha256(output / "config.json"),
        "conditions": list(CONDITIONS), "synthetic_duration_s": 3, "synthetic_sample_rate": 16000,
        "execution_order": "Six synthetic cases in declared order, then manifest order with original/transcript_removed/audio_silenced per parent",
        "conditions_share_parent_recordings": True,
        "cache_design": "Independent empty cache per case; one first call and one repeat",
        "controlled_eligibility": "Frozen min interval from parent VAD or valid word/segment time axis; all ineligible/failing parents retained",
        "detector_transcript_allowlist": ["asr_text", "segments", "words"],
        "reference_text_read": False, "limitations": LIMITATIONS,
        "python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
        "processor": platform.processor(), "logical_cpu_count": os.cpu_count(),
        "packages": packages, "git_head": revision.stdout.strip() if revision.returncode == 0 else None,
        "source_files_sha256": {str(path.relative_to(output)).replace("\\", "/"): sha256(path) for path in sorted((output / "source-snapshot").rglob("*")) if path.is_file()},
    }
    write_json(output / "protocol.json", protocol)
    records: list[dict[str, Any]] = []
    before_audio: dict[str, tuple[Path, str | None]] = {}
    with tempfile.TemporaryDirectory(prefix="anota-qc-experiment-") as temporary:
        temp = Path(temporary)
        replacements = {str(ROOT): "<repo>", str(temp): "<temporary>", str(manifest.parent): "<input>"}

        def attempt(case_id: str, condition: str, path: Path | None, transcript: dict[str, Any],
                    parent: str | None = None, eligible: bool | None = None, reason: str | None = None,
                    provenance: dict[str, Any] | None = None, setup_error: Exception | None = None) -> dict[str, Any]:
            record: dict[str, Any] = {"case_id": case_id, "condition": condition, "parent_item_id": parent,
                                      "eligible": eligible, "eligibility_reason": reason, "provenance": provenance or {}}
            cache = temp / "cache" / str(len(records))
            report = None
            if setup_error is None:
                clock = time.perf_counter()
                try:
                    report = analyze_cached(path, transcript, cache, config=config)
                    record["status"] = report["status"]
                    record["report"] = report
                except Exception as error:  # noqa: BLE001 - preserve the complete experimental denominator
                    record.update(status="failed", error={"type": type(error).__name__, "message": str(error)})
                record["empty_cache_ms"] = round((time.perf_counter() - clock) * 1000, 4)
            else:
                record.update(status="failed", error={"type": type(setup_error).__name__, "message": str(setup_error)})
            if report is not None:
                cache_before = {entry.name: (sha256(entry), entry.stat().st_mtime_ns)
                                for entry in cache.glob("*.json")}
                clock = time.perf_counter()
                try:
                    replay = analyze_cached(path, transcript, cache, config=config)
                    replay_finished = time.perf_counter()
                    record["cache_replay_status"] = replay["status"]
                    record["cache_semantically_equal"] = semantic_report(report) == semantic_report(replay)
                    cache_after = {entry.name: (sha256(entry), entry.stat().st_mtime_ns)
                                   for entry in cache.glob("*.json")}
                    record["cache_entry_unchanged"] = bool(cache_before) and cache_before == cache_after
                except Exception as error:  # noqa: BLE001 - report replay failures without discarding the first result
                    replay_finished = time.perf_counter()
                    record.update(cache_replay_status="failed", cache_error={"type": type(error).__name__, "message": str(error)})
                record["cache_replay_ms"] = round((replay_finished - clock) * 1000, 4)
            record["expectation"] = evaluate_expectation(condition, report, eligible)
            record = safe_value(record, replacements)
            records.append(record)
            with (output / "observations.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            return record

        for condition in SYNTHETIC_CASES:
            path = temp / f"{condition}.wav"
            provenance = make_synthetic(path, condition)
            attempt(f"synthetic:{condition}", condition, path, detector_transcript({}), provenance=provenance)
        for index, row in enumerate(rows):
            parent = row["item_id"]
            transcript = detector_transcript(row)
            path, error = None, None
            try:
                if not isinstance(row.get("audio_path"), str):
                    raise ValueError("Source row has no audio_path")  # noqa: TRY004 - missing manifest input is invalid data
                path = Path(row["audio_path"])
                if not path.is_absolute():
                    path = manifest.parent / path
                digest = sha256(path)
                before_audio[parent] = path, digest
                if row.get("audio_sha256") and row["audio_sha256"] != digest:
                    raise ValueError("Source audio_sha256 does not match bytes")
                if row.get("status") not in (None, "succeeded"):
                    raise ValueError("Source ASR did not succeed; stale text is not analyzed")
            except Exception as caught:  # noqa: BLE001 - source failures remain explicit per-parent observations
                error = caught
                before_audio[parent] = path, None
            provenance = {key: row.get(key) for key in ("language", "corpus", "corpus_revision", "license", "split", "source_audio_filename", "source_id", "audio_sha256")}
            original = attempt(f"{parent}:original", "original", path, transcript, parent, provenance=provenance, setup_error=error)
            for condition in ("transcript_removed", "audio_silenced"):
                eligible, reason = controlled_eligibility(condition, original.get("report"), transcript, config)
                if condition == "transcript_removed":
                    attempt(f"{parent}:{condition}", condition, path, detector_transcript({}), parent,
                            eligible, reason, provenance, error)
                else:
                    altered = temp / f"silenced-{index}.wav"
                    derivative_provenance = dict(provenance)
                    derivative_error = error
                    if derivative_error is None:
                        try:
                            derivative_provenance.update(make_silenced(path, altered))
                        except Exception as caught:  # noqa: BLE001 - keep failed derivative generation in the denominator
                            derivative_error = caught
                    attempt(f"{parent}:{condition}", condition, altered, transcript, parent,
                            eligible, reason, derivative_provenance, derivative_error)
    summary = summarize(records, [row["item_id"] for row in rows])
    integrity = []
    for parent, (path, digest) in before_audio.items():
        try:
            current = sha256(path) if path is not None else None
        except OSError:
            current = None
        integrity.append({"parent_item_id": parent, "before_sha256": digest, "after_sha256": current,
                          "unchanged": digest is not None and digest == current})
    summary.update(started_at_utc=started, finished_at_utc=datetime.now(timezone.utc).isoformat(),  # noqa: UP017 - Python 3.10
                   input_manifest_sha256=input_sha, manifest_unchanged=sha256(manifest) == input_sha,
                   source_audio_integrity=integrity,
                   implementation_source_unchanged=all(sha256(source) == sha256(output / "source-snapshot" / source.relative_to(ROOT))
                                                       for source in snapshot_paths))
    write_json(output / "summary.json", summary)
    with (output / "human-audit-template.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["parent_item_id", "issue_id", "code", "start_s", "end_s", "review_status", "reviewer_id", "listened_at", "confirmed_problem", "corrected_text", "notes"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            if record["condition"] != "original":
                continue
            issues = (record.get("report") or {}).get("issues", []) or [{}]
            for issue in issues:
                writer.writerow({"parent_item_id": record["parent_item_id"], "issue_id": issue.get("id", ""),
                                 "code": issue.get("code", ""), "start_s": issue.get("start", ""),
                                 "end_s": issue.get("end", ""), "review_status": "pending"})
    write_json(output / "artifact-hashes.json", {
        str(path.relative_to(output)).replace("\\", "/"): sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file() and path.name != "artifact-hashes.json"
    })
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.manifest, args.output_dir)
    print(json.dumps({"planned_cases": report["planned_cases"], "recorded_cases": report["recorded_cases"],
                      "parents": report["parent_recordings"], "manifest_unchanged": report["manifest_unchanged"]}))


if __name__ == "__main__":
    main()
