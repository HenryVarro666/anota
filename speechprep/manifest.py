"""Strict JSONL manifests and atomic serialization shared by CLI commands."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_manifest(path: str | Path) -> list[dict]:
    path = Path(path).resolve()
    rows, seen = [], set()
    required = ("item_id", "language", "audio_path", "audio_sha256", "corpus",
                "corpus_revision", "split", "source_id", "reference_id", "license")
    with path.open(encoding="utf-8-sig") as stream:
        for lineno, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("schema_version") != "speech-v1":
                raise ValueError(f"manifest line {lineno}: expected speech-v1 object")
            for key in required:
                if not isinstance(row.get(key), str) or not row[key].strip():
                    raise ValueError(f"manifest line {lineno}: missing string {key}")
            if row["item_id"] in seen:
                raise ValueError(f"manifest line {lineno}: duplicate item_id {row['item_id']}")
            if row["language"] not in {"en", "zh"}:
                raise ValueError(f"manifest line {lineno}: only en and zh are enabled")
            audio_hash = row["audio_sha256"].lower()
            if len(audio_hash) != 64 or any(c not in "0123456789abcdef" for c in audio_hash):
                raise ValueError(f"manifest line {lineno}: invalid audio_sha256")
            row["audio_sha256"] = audio_hash
            audio_path = Path(row["audio_path"])
            row["audio_path"] = str((path.parent / audio_path).resolve())
            seen.add(row["item_id"])
            rows.append(row)
    return rows


def atomic_jsonl(path: str | Path, rows: Iterable[dict]) -> None:
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
