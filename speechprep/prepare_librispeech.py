"""Verify and prepare all official LibriSpeech test-clean and test-other items.

No corpus download is performed. Original FLAC bytes and upstream text metadata
are preserved; references are published separately from ASR input manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
from pathlib import Path, PurePosixPath

from .manifest import atomic_jsonl, file_sha256

SOURCE_URL = "https://www.openslr.org/12/"
MD5_URL = "https://www.openslr.org/resources/12/md5sum.txt"
REVISION = "openslr-12-official-test-v1"
ARCHIVE_SPECS = {
    "test-clean": {"filename": "test-clean.tar.gz", "bytes": 346663984,
                   "md5": "32fa31d27d2e1cad72775fee3f4849a9", "items": 2620},
    "test-other": {"filename": "test-other.tar.gz", "bytes": 328757843,
                   "md5": "fb5a50374b501bb3bac4815ee91d3135", "items": 2939},
}
ROOT_METADATA = {"LICENSE.TXT", "README.TXT", "SPEAKERS.TXT", "CHAPTERS.TXT", "BOOKS.TXT"}
UTTERANCE = re.compile(r"(\d+)-(\d+)-(\d+)\Z")


def _safe_target(root: Path, relative: str) -> Path:
    target = root.joinpath(*PurePosixPath(relative).parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Output path escapes preparation root: {relative}")
    for component in (target, *target.parents):
        if component == root:
            break
        if component.is_symlink():
            raise ValueError(f"Symlink in output path: {component}")
    return target


def _preserve(root: Path, relative: str, content: bytes) -> Path:
    target = _safe_target(root, relative)
    if target.exists():
        if not target.is_file() or target.read_bytes() != content:
            raise ValueError(f"Existing prepared file differs from verified archive: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".part")
    if temporary.is_symlink():
        raise ValueError(f"Symlink at temporary output path: {temporary}")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    return target


def _archive_record(path: Path, spec: dict) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing regular official archive: {path}")
    if path.stat().st_size != spec["bytes"]:
        raise ValueError(f"Archive byte size mismatch: {path.name}")
    # MD5 checks the official distribution checksum; SHA-256 records provenance.
    md5, sha = hashlib.md5(), hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            md5.update(chunk)
            sha.update(chunk)
    if md5.hexdigest() != spec["md5"]:
        raise ValueError(f"Official archive MD5 mismatch: {path.name}")
    return {"filename": path.name, "bytes": path.stat().st_size,
            "md5": md5.hexdigest(), "sha256": sha.hexdigest(),
            "url": f"https://www.openslr.org/resources/12/{path.name}"}


def _audio_info(path: Path) -> dict:
    import numpy as np
    import soundfile as sf

    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    if rate != 16000 or audio.shape[1] != 1:
        raise ValueError(f"Expected mono 16 kHz FLAC: {path}")
    if audio.shape[0] == 0 or not np.isfinite(audio).all() or not np.any(audio):
        raise ValueError(f"Empty, non-finite or silent audio: {path}")
    return {"audio_sha256": file_sha256(path), "audio_bytes": path.stat().st_size,
            "num_samples": int(audio.shape[0]), "sample_rate": rate, "channels": 1,
            "duration": float(audio.shape[0] / rate)}


def _member_parts(member: tarfile.TarInfo) -> tuple[str, ...]:
    name = member.name
    pure = PurePosixPath(name)
    if (not name or not pure.parts or "\\" in name or pure.is_absolute() or ".." in pure.parts
            or any(":" in part for part in pure.parts) or pure.parts[0] != "LibriSpeech"):
        raise ValueError(f"Unsafe archive member path: {name}")
    if not (member.isdir() or member.isfile()):
        raise ValueError(f"Archive links and special members are forbidden: {name}")
    return pure.parts


def _prepare_split(archive: Path, root: Path, split: str, spec: dict,
                   archive_record: dict) -> tuple[list[dict], list[dict], dict]:
    audio_rows, transcripts, snapshots, seen = {}, {}, [], set()
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            parts = _member_parts(member)
            if member.isdir():
                continue
            canonical_name = "/".join(parts)
            if canonical_name in seen:
                raise ValueError(f"Duplicate archive member: {member.name}")
            seen.add(canonical_name)
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError(f"Unreadable regular archive member: {member.name}")
            with stream:
                content = stream.read()
            if len(content) != member.size:
                raise ValueError(f"Truncated archive member: {member.name}")
            if len(parts) == 2 and parts[1] in ROOT_METADATA:
                relative = f"source_metadata/{split}/{parts[1]}"
                path = _preserve(root, relative, content)
                snapshots.append({"path": relative, "bytes": len(content),
                                  "sha256": file_sha256(path)})
                continue
            if len(parts) != 5 or parts[1] != split or not all(p.isdigit() for p in parts[2:4]):
                raise ValueError(f"Unexpected member in official {split} archive: {member.name}")
            speaker, chapter, filename = parts[2:]
            if filename.endswith(".flac"):
                source_id = filename[:-5]
                match = UTTERANCE.fullmatch(source_id)
                if not match or match.groups()[:2] != (speaker, chapter):
                    raise ValueError(f"Audio ID does not match speaker/chapter: {member.name}")
                relative = f"audio/{split}/{speaker}/{chapter}/{filename}"
                path = _preserve(root, relative, content)
                item_id = f"librispeech:{split}:{speaker}:{chapter}:{source_id}"
                audio_rows[source_id] = {
                    "schema_version": "speech-v1", "item_id": item_id, "language": "en",
                    "audio_path": relative, "corpus": "LibriSpeech", "corpus_revision": REVISION,
                    "split": split, "source_id": source_id, "reference_id": f"ref:{item_id}",
                    "speaker_id": speaker, "chapter_id": chapter, "license": "CC-BY-4.0",
                    "source_archive": archive.name, "source_archive_sha256": archive_record["sha256"],
                    **_audio_info(path),
                }
            elif filename == f"{speaker}-{chapter}.trans.txt":
                relative = f"source_metadata/{split}/{speaker}/{chapter}/{filename}"
                _preserve(root, relative, content)
                for line in content.decode("utf-8-sig").splitlines():
                    if not line.strip():
                        continue
                    fields = line.strip().split(maxsplit=1)
                    if len(fields) != 2 or not fields[1].strip():
                        raise ValueError(f"Missing reference text in {member.name}")
                    source_id, text = fields
                    match = UTTERANCE.fullmatch(source_id)
                    if not match or match.groups()[:2] != (speaker, chapter):
                        raise ValueError(f"Reference ID does not match speaker/chapter: {source_id}")
                    if source_id in transcripts:
                        raise ValueError(f"Duplicate reference ID: {source_id}")
                    transcripts[source_id] = (text, relative)
            else:
                raise ValueError(f"Unexpected regular archive member: {member.name}")
    if not any(s["path"].endswith("/LICENSE.TXT") for s in snapshots):
        raise ValueError(f"Missing upstream LICENSE.TXT snapshot in {archive.name}")
    if audio_rows.keys() != transcripts.keys():
        missing = sorted(audio_rows.keys() - transcripts.keys())
        extra = sorted(transcripts.keys() - audio_rows.keys())
        raise ValueError(f"Audio/reference mismatch for {split}: missing={missing[:5]}, extra={extra[:5]}")
    if len(audio_rows) != spec["items"]:
        raise ValueError(f"Wrong {split} item count: expected {spec['items']}, got {len(audio_rows)}")
    rows = [audio_rows[key] for key in sorted(audio_rows)]
    references = [{"reference_id": row["reference_id"], "item_id": row["item_id"],
                   "language": "en", "text": transcripts[row["source_id"]][0],
                   "source_id": row["source_id"], "split": split,
                   "speaker_id": row["speaker_id"], "chapter_id": row["chapter_id"],
                   "corpus_revision": REVISION, "source_transcript": transcripts[row["source_id"]][1]}
                  for row in rows]
    summary = {"items": len(rows), "speakers": len({r["speaker_id"] for r in rows}),
               "chapters": len({(r["speaker_id"], r["chapter_id"]) for r in rows}),
               "audio_seconds": sum(r["duration"] for r in rows),
               "audio_bytes": sum(r["audio_bytes"] for r in rows),
               "source_archive": archive_record, "upstream_metadata_snapshots": snapshots}
    return rows, references, summary


def prepare(archives: str | Path, output: str | Path) -> dict:
    archives, root = Path(archives).resolve(), Path(output).resolve()
    expected_names = {spec["filename"] for spec in ARCHIVE_SPECS.values()}
    unexpected = {p.name for p in archives.iterdir()
                  if p.is_file() and p.name.endswith((".tar", ".tar.gz"))} - expected_names
    if unexpected:
        raise ValueError(f"Only the two official test archives are accepted: {sorted(unexpected)}")
    records = {split: _archive_record(archives / spec["filename"], spec)
               for split, spec in ARCHIVE_SPECS.items()}
    checksums = archives / "md5sum.official.txt"
    if not checksums.is_file():
        raise ValueError(f"Missing official checksum snapshot: {checksums}")
    checksum_content = checksums.read_bytes()
    checksum_rows = {}
    for line in checksum_content.decode("utf-8-sig").splitlines():
        fields = line.split()
        if len(fields) == 2:
            checksum_rows[fields[1].lstrip("*")] = fields[0].lower()
    for spec in ARCHIVE_SPECS.values():
        if checksum_rows.get(spec["filename"]) != spec["md5"]:
            raise ValueError(f"Official checksum snapshot does not match pinned {spec['filename']}")
    root.mkdir(parents=True, exist_ok=True)
    _preserve(root, "source_metadata/md5sum.official.txt", checksum_content)
    all_rows, all_references, groups = [], [], {}
    for split, spec in ARCHIVE_SPECS.items():
        rows, references, summary = _prepare_split(archives / spec["filename"], root, split,
                                                  spec, records[split])
        all_rows.extend(rows)
        all_references.extend(references)
        groups[split] = (rows, references, summary)
    # Publish only after both archives have passed complete one-to-one validation.
    for split, (rows, references, summary) in groups.items():
        for suffix, values in (("manifest", rows), ("references", references)):
            target = _safe_target(root, f"{split}.{suffix}.jsonl")
            atomic_jsonl(target, values)
            summary[f"{suffix}_sha256"] = file_sha256(target)
    atomic_jsonl(_safe_target(root, "manifest.jsonl"), all_rows)
    atomic_jsonl(_safe_target(root, "references.jsonl"), all_references)
    result = {
        "schema_version": "librispeech-preparation-verification-v1", "corpus": "LibriSpeech",
        "corpus_revision": REVISION, "source_url": SOURCE_URL, "license": "CC-BY-4.0",
        "selection": "All utterances in both official test archives; no sampling",
        "audio_transform": "none; original upstream FLAC bytes, fully decoded for validation",
        "reference_policy": "Reference text is only in references JSONL and source_metadata, never in manifest",
        "items": len(all_rows), "audio_seconds": sum(r["duration"] for r in all_rows),
        "audio_bytes": sum(r["audio_bytes"] for r in all_rows),
        "splits": {split: group[2] for split, group in groups.items()},
        "official_md5_snapshot": {"path": "source_metadata/md5sum.official.txt", "url": MD5_URL,
                                  "sha256": file_sha256(root / "source_metadata/md5sum.official.txt")},
        "manifest_sha256": file_sha256(root / "manifest.jsonl"),
        "references_sha256": file_sha256(root / "references.jsonl"),
    }
    encoded = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    target = _safe_target(root, "verification.json")
    temporary = target.with_name(target.name + ".part")
    if temporary.is_symlink():
        raise ValueError(f"Symlink at temporary output path: {temporary}")
    temporary.write_bytes(encoded)
    os.replace(temporary, target)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.archives, args.output), ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
