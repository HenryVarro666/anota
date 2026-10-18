"""Prepare a tiny, pinned FLEURS smoke set without downloading whole archives.

Run: python -m speechprep.prepare_fleurs --output runtime/speech-smoke
The official archive's first N regular WAV entries are selected deterministically.
Reference text is deliberately absent from manifest.jsonl. Audio remains byte-for-
byte identical to the upstream WAV; soundfile is used only to validate decoding.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import tarfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
REPOSITORY = "https://huggingface.co/datasets/google/fleurs"
CONFIGS = (("en_us", "en"), ("cmn_hans_cn", "zh"))
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/legalcode.txt"


def _utc() -> str:
    return datetime.now(UTC).isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    with part.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(part, path)


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    part = path.with_suffix(path.suffix + ".part")
    with part.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(part, path)


def _open(url: str):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Anota-speech-smoke/1.0"}),
        timeout=60,
    )


def _fetch_metadata(url: str, target: Path, state: dict[str, Any], state_path: Path) -> bytes:
    record = state["metadata_downloads"].get(target.name)
    if target.is_file() and record and _sha(target) == record["sha256"]:
        return target.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _open(url) as response:
        content = response.read()
    part = target.with_suffix(target.suffix + ".part")
    part.write_bytes(content)
    os.replace(part, target)
    state["metadata_downloads"][target.name] = {
        "url": url,
        "local_path": str(target.relative_to(state_path.parent)),
        "bytes_read": len(content),
        "sha256": _sha(target),
        "downloaded_at": _utc(),
    }
    _json(state_path, state)
    return content


def _audio_info(path: Path) -> dict[str, Any]:
    import soundfile as sf

    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    import numpy as np

    if samples.shape[0] <= 0 or sample_rate <= 0 or not np.isfinite(samples).all():
        raise ValueError(f"Empty or non-finite audio: {path}")
    if not np.any(samples):
        raise ValueError(f"Silent sample cannot be used as real speech: {path}")
    return {
        "audio_sha256": _sha(path),
        "audio_bytes": path.stat().st_size,
        "num_samples": int(samples.shape[0]),
        "sample_rate": int(sample_rate),
        "channels": int(samples.shape[1]),
        "duration": float(samples.shape[0] / sample_rate),
    }


def _valid_cached(root: Path, row: dict[str, Any]) -> bool:
    audio = root / row["audio_path"]
    try:
        return _audio_info(audio) == {key: row[key] for key in _audio_info_keys()}
    except (OSError, ValueError, RuntimeError, KeyError):
        return False


def _audio_info_keys() -> tuple[str, ...]:
    return ("audio_sha256", "audio_bytes", "num_samples", "sample_rate", "channels", "duration")


class _CountingReader:
    def __init__(self, response):
        self.response = response
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.response.read(size)
        self.bytes_read += len(chunk)
        return chunk


def _prepare_language(
    root: Path,
    config: str,
    language: str,
    count: int,
    metadata: dict[str, list[str]],
    state: dict[str, Any],
    state_path: Path,
) -> None:
    previous = state["languages"].setdefault(config, {"items": [], "complete": False})
    if (
        previous["complete"]
        and len(previous["items"]) == count
        and all(_valid_cached(root, row) for row in previous["items"])
    ):
        print(f"{config}: verified {count} cached audio files; no archive request", flush=True)
        return
    known = {row["source_audio_filename"]: row for row in previous["items"]}
    archive_url = f"{REPOSITORY}/resolve/{REVISION}/data/{config}/audio/train.tar.gz"
    # Stream only the deterministic prefix needed for N recordings. Never extract
    # upstream paths to disk; generated local filenames avoid tar traversal.
    for attempt in range(1, 4):
        event: dict[str, Any] = {
            "url": archive_url,
            "started_at": _utc(),
            "selection": f"first {count} regular WAV entries in upstream tar order",
            "attempt": attempt,
        }
        state["archive_requests"].append(event)
        _json(state_path, state)
        counter = None
        selected: list[dict[str, Any]] = []
        try:
            with _open(archive_url) as response:
                event["archive_content_length"] = response.headers.get("Content-Length")
                counter = _CountingReader(response)
                with tarfile.open(fileobj=counter, mode="r|gz") as archive:
                    for entry in archive:
                        if not entry.isfile() or not entry.name.lower().endswith(".wav"):
                            continue
                        filename = Path(entry.name).name
                        if filename not in metadata:
                            raise ValueError(f"Archive audio missing in train.tsv: {filename}")
                        fields = metadata[filename]
                        source_id = fields[0]
                        item_id = f"fleurs-{config}-train-{Path(filename).stem}"
                        audio_path = Path("audio") / config / filename
                        target = root / audio_path
                        row = known.get(filename)
                        if row and _valid_cached(root, row):
                            print(f"{config}: reused {filename}", flush=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            # An interrupted per-file .part is safely overwritten.
                            part = target.with_suffix(target.suffix + ".part")
                            audio_stream = archive.extractfile(entry)
                            if audio_stream is None:
                                raise ValueError(f"Cannot read {entry.name}")
                            with part.open("wb") as output:
                                while chunk := audio_stream.read(256 * 1024):
                                    output.write(chunk)
                                output.flush()
                                os.fsync(output.fileno())
                            info = _audio_info(part)
                            if info["num_samples"] != int(fields[5]):
                                raise ValueError(f"TSV sample count mismatch for {filename}")
                            os.replace(part, target)
                            row = {
                                "schema_version": "speech-v1",
                                "item_id": item_id,
                                "language": language,
                                "language_config": config,
                                "audio_path": audio_path.as_posix(),
                                **info,
                                "corpus": "google/fleurs",
                                "corpus_revision": REVISION,
                                "split": "train",
                                "source_id": source_id,
                                "source_audio_filename": filename,
                                "reference_id": f"ref-{item_id}",
                                "license": "CC-BY-4.0",
                                "speaker_id": None,
                                "speaker_id_status": "not_provided_by_source",
                            }
                            known[filename] = row
                            print(f"{config}: downloaded {filename} ({info['duration']:.2f}s)", flush=True)
                        selected.append(row)
                        previous["items"] = selected
                        previous["complete"] = False
                        event["response_body_bytes_read"] = counter.bytes_read
                        _json(state_path, state)
                        if len(selected) == count:
                            break
            if len(selected) != count:
                raise ValueError(f"Expected {count} recordings in {config}, got {len(selected)}")
            previous["items"] = selected
            previous["complete"] = True
            event["completed_at"] = _utc()
            event["status"] = "selected_prefix_complete"
            _json(state_path, state)
            return
        except (OSError, ValueError, tarfile.TarError) as error:
            event["status"] = "interrupted_or_failed"
            event["error"] = f"{type(error).__name__}: {error}"
            event["response_body_bytes_read"] = counter.bytes_read if counter else 0
            _json(state_path, state)
            if attempt == 3:
                raise
            time.sleep(attempt)


def prepare(output: Path, per_language: int = 10) -> dict[str, Any]:
    if per_language < 1:
        raise ValueError("--per-language must be positive")
    root = output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "prepare_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("revision") != REVISION or state.get("per_language") != per_language:
            raise ValueError("Output contains a different preparation contract; choose a new output directory")
    else:
        state = {
            "schema_version": "fleurs-preparation-v1",
            "revision": REVISION,
            "per_language": per_language,
            "created_at": _utc(),
            "metadata_downloads": {},
            "archive_requests": [],
            "languages": {},
        }
    sources = root / "source_metadata"
    _fetch_metadata(f"{REPOSITORY}/resolve/{REVISION}/README.md", sources / "FLEURS_README.md", state, state_path)
    _fetch_metadata(LICENSE_URL, sources / "CC-BY-4.0.txt", state, state_path)
    source_tables: dict[str, dict[str, list[str]]] = {}
    for config, language in CONFIGS:
        tsv = _fetch_metadata(
            f"{REPOSITORY}/resolve/{REVISION}/data/{config}/train.tsv",
            sources / f"{config}-train.tsv", state, state_path,
        ).decode("utf-8")
        # The source has no header; raw transcription is column 3, normalized
        # transcription column 4, sample count column 6 (one-based).
        table = {row[1]: row for row in csv.reader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE) if row}
        source_tables[config] = table
        _prepare_language(root, config, language, per_language, table, state, state_path)
    rows = [row for config, _ in CONFIGS for row in state["languages"][config]["items"]]
    references = []
    for row in rows:
        source = source_tables[row["language_config"]][row["source_audio_filename"]]
        references.append({
            "reference_id": row["reference_id"],
            "item_id": row["item_id"],
            "text": source[2],
            "normalized_source_text": source[3],
            "language": row["language"],
            "source_id": row["source_id"],
            "corpus_revision": REVISION,
        })
    if len({row["item_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate item IDs")
    if not all(_valid_cached(root, row) for row in rows):
        raise ValueError("Final decoded-audio integrity check failed")
    _jsonl(root / "manifest.jsonl", rows)
    _jsonl(root / "references.jsonl", references)
    summary = {
        "schema_version": "speech-smoke-verification-v1",
        "corpus": "google/fleurs",
        "corpus_revision": REVISION,
        "license": "CC-BY-4.0",
        "selection": f"first {per_language} regular WAV entries in each pinned train archive; no random sampling",
        "audio_transform": "none; original upstream WAV bytes",
        "reference_policy": "reference texts stored only in references.jsonl and source_metadata; manifest has identifiers only",
        "speaker_policy": "individual speaker IDs unavailable; unknown values retained",
        "items": len(rows),
        "languages": {language: sum(row["language"] == language for row in rows) for _, language in CONFIGS},
        "audio_seconds": sum(row["duration"] for row in rows),
        "audio_bytes": sum(row["audio_bytes"] for row in rows),
        "manifest_sha256": _sha(root / "manifest.jsonl"),
        "references_sha256": _sha(root / "references.jsonl"),
        "source_archive_body_bytes_read_across_attempts": sum(event.get("response_body_bytes_read", 0) for event in state["archive_requests"]),
        "byte_accounting": "HTTP response body bytes consumed by Python, including archive-prefix retries; not a packet-level network count",
        "verified_at": _utc(),
        "limitations": ["Read speech, not medical or telephone speech", "Smoke test only; no representative quality claim", "Source references have not been independently re-annotated"],
    }
    _json(root / "verification.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runtime/speech-smoke"))
    parser.add_argument("--per-language", type=int, default=10)
    args = parser.parse_args()
    prepare(args.output, args.per_language)


if __name__ == "__main__":
    main()
