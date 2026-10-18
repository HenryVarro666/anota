"""Atomic content-addressed cache. Corrupt/stale entries are recomputed."""

from __future__ import annotations

import importlib.metadata
import json
import os
import tempfile
from pathlib import Path

from .analysis import analyze
from .schema import (
    ANALYZER_VERSION,
    canonical_json,
    digest_json,
    file_sha256,
    resolve_config,
    transcript_digest,
    validate_report,
)
from .vad import vad_provenance


def atomic_jsonl(path, rows):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(canonical_json(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _environment_identity():
    identity = {}
    for package in ("numpy", "soundfile"):
        try:
            identity[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            identity[package] = None
    try:
        identity["vad"] = vad_provenance()
    except Exception:  # noqa: BLE001 - missing or broken optional runtime means no reusable VAD identity
        identity["vad"] = None
    return identity


def analyze_cached(audio_path, transcript, cache_dir, config=None):
    config = resolve_config(config)
    audio_path = Path(audio_path).resolve(strict=True)
    audio_hash = file_sha256(audio_path)
    environment = _environment_identity()
    key = digest_json({"audio_sha256": audio_hash, "transcript_sha256": transcript_digest(transcript),
                       "config": config, "analyzer_version": ANALYZER_VERSION,
                       "environment": environment})
    cache_path = Path(cache_dir).resolve() / f"{key}.json"
    if cache_path.resolve() == audio_path:
        raise ValueError("cache output collides with source audio")
    if cache_path.is_symlink():
        raise ValueError("cache entries must not be symbolic links")
    if cache_path.is_file():
        try:
            report = validate_report(json.loads(cache_path.read_text(encoding="utf-8")),
                                     audio_sha256=audio_hash, transcript=transcript)
            by_name = {detector["name"]: detector for detector in report["detectors"]}
            provenance_matches = (environment["vad"] is not None
                                  and all(by_name.get(d["name"]) == d for d in environment["vad"])
                                  and all(by_name.get(name, {}).get("version") == environment[name]
                                          for name in ("numpy", "soundfile")))
            if report["status"] == "complete" and report["config"] == config and provenance_matches:
                return report
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            pass
    report = analyze(audio_path, transcript, config)
    if report["audio_sha256"] != audio_hash:
        raise ValueError("audio changed during cache analysis")
    if report["status"] == "complete":
        atomic_jsonl(cache_path, [report])
    return report
