"""Download/verify official LibriSpeech dev data and freeze a label-blind subset.

This new entry point does not modify the frozen test preparer or any ASR result.
Audio stays byte-identical; text references are exported to separate files.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from speechprep.manifest import atomic_jsonl, file_sha256
from speechprep.prepare_librispeech import (
    MD5_URL,
    SOURCE_URL,
    _archive_record,
    _prepare_split,
    _preserve,
    _safe_target,
)

REVISION = "openslr-12-official-dev-v1"
ARCHIVE_SPECS = {
    "dev-clean": {"filename": "dev-clean.tar.gz", "bytes": 337926286,
                  "md5": "42e2234ba48799c1f50f24a7926300a1", "items": 2703},
    "dev-other": {"filename": "dev-other.tar.gz", "bytes": 314305928,
                  "md5": "c8d0bcc9cca99d4f8b62fcc847357931", "items": 2864},
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017 - keep the helper compatible with Python 3.10


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.with_suffix(path.suffix + ".part").is_symlink():
        raise ValueError(f"Symlink at output: {path}")
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def verify_checksum_snapshot(root):
    source = Path(root) / "md5sum.official.txt"
    rows = {}
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        fields = line.split()
        if len(fields) == 2:
            rows[fields[1].lstrip("*")] = fields[0].lower()
    for spec in ARCHIVE_SPECS.values():
        if rows.get(spec["filename"]) != spec["md5"]:
            raise ValueError(f"Official checksum snapshot differs: {spec['filename']}")
    return file_sha256(source)


def _range_archive(root, split, spec, record):
    """Six total concurrent connections (three per archive), verified by range."""
    root = Path(root)
    target = root / spec["filename"]
    chunk_root = root / "range-parts" / split
    chunk_root.mkdir(parents=True, exist_ok=True)
    chunk_size = 16 * 1024 * 1024
    hosts = ("https://openslr.elda.org", "https://openslr.magicdatatech.com", "https://www.openslr.org")

    def chunk(index):
        start = index * chunk_size
        end = min(spec["bytes"], start + chunk_size) - 1
        path = chunk_root / f"{index:04d}.bin"
        log = chunk_root / f"{index:04d}.json"
        if path.is_symlink() or log.is_symlink():
            raise ValueError("Symlink in range cache")
        state = json.loads(log.read_text()) if log.exists() else {
            "start": start, "end": end, "expected_total": spec["bytes"], "attempts": []}
        if (path.is_file() and state.get("status") == "verified"
                and path.stat().st_size == end - start + 1 and file_sha256(path) == state.get("sha256")):
            return state
        for host in hosts:
            url = f"{host}/resources/12/{spec['filename']}"
            event = {"url": url, "started_at": utc_now(), "status": "running"}
            state["attempts"].append(event)
            write_json(log, state)
            try:
                req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "Anota-dev-benchmark/1.0"})
                with urllib.request.urlopen(req, timeout=45) as response:
                    expected_range = f"bytes {start}-{end}/{spec['bytes']}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected_range:
                        raise ValueError(f"Invalid partial response: {response.status} {response.headers.get('Content-Range')}")
                    event.update(resolved_url=response.url, http_status=response.status, content_range=expected_range)
                    content = response.read(end - start + 2)
                event["bytes_read"] = len(content)
                if len(content) != end - start + 1:
                    raise ValueError("Range response length differs")
                temporary = path.with_suffix(".part")
                if temporary.is_symlink():
                    raise ValueError("Symlink at range temporary output")
                temporary.write_bytes(content)
                os.replace(temporary, path)
                event.update(status="verified", completed_at=utc_now())
                state.update(status="verified", sha256=file_sha256(path), bytes=len(content))
                write_json(log, state)
                return state
            except (OSError, ValueError) as error:
                event.update(status="failed", completed_at=utc_now(), error=f"{type(error).__name__}: {error}")
                state["status"] = "failed"
                write_json(log, state)
        raise RuntimeError(f"All official mirrors failed for {split} range {index}")

    record.update(status="downloading_ranges", range_method="3 concurrent validated 16MiB ranges per archive; official listed mirrors")
    write_json(root / f"download-{split}.json", record)
    count = (spec["bytes"] + chunk_size - 1) // chunk_size
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(chunk, range(count)))
    temporary = target.with_name(target.name + ".assembled.part")
    if temporary.is_symlink():
        raise ValueError("Symlink at assembled archive")
    with temporary.open("wb") as stream:
        for index in range(count):
            with (chunk_root / f"{index:04d}.bin").open("rb") as source:
                while content := source.read(1024 * 1024):
                    stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    validated = _archive_record(temporary, spec)
    os.replace(temporary, target)
    validated.update(filename=target.name, url=f"https://www.openslr.org/resources/12/{target.name}")
    record.update(status="verified", archive=validated, verified_at=utc_now(), ranges=records)
    write_json(root / f"download-{split}.json", record)
    print(f"{split}: verified {validated['bytes']} bytes from official mirror ranges", flush=True)
    return record


def download(archives, *, ranged=False):
    root = Path(archives).resolve()
    root.mkdir(parents=True, exist_ok=True)
    snapshots = []
    for filename, url in (("openslr-12.official.html", SOURCE_URL), ("md5sum.official.txt", MD5_URL)):
        target = root / filename
        if target.exists():
            if not target.is_file() or target.is_symlink():
                raise ValueError(f"Unsafe metadata path: {target}")
            snapshots.append({"path": filename, "url": url, "sha256": file_sha256(target), "reused": True})
            continue
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Anota-dev-benchmark/1.0"}), timeout=60) as response:
            content = response.read()
            resolved = response.url
        _preserve(root, filename, content)
        snapshots.append({"path": filename, "url": url, "resolved_url": resolved,
                          "sha256": file_sha256(target), "bytes": len(content), "downloaded_at": utc_now()})
    verify_checksum_snapshot(root)

    def one(pair):
        split, spec = pair
        target = root / spec["filename"]
        log_path = root / f"download-{split}.json"
        record = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {
            "schema_version": "librispeech-dev-download-v1", "split": split, "attempts": []}
        if target.exists():
            validated = _archive_record(target, spec)
            record.update(status="verified", archive=validated, verified_at=utc_now())
            write_json(log_path, record)
            return record
        if ranged:
            return _range_archive(root, split, spec, record)
        temporary = target.with_name(target.name + ".part")
        if temporary.is_symlink():
            raise ValueError(f"Symlink at download path: {temporary}")
        url = f"https://www.openslr.org/resources/12/{spec['filename']}"
        for attempt in range(3):
            event = {"url": url, "started_at": utc_now(), "bytes_read": 0, "status": "running"}
            record["attempts"].append(event)
            record["status"] = "downloading"
            write_json(log_path, record)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Anota-dev-benchmark/1.0"}), timeout=90) as response, temporary.open("wb") as output:
                    event.update(resolved_url=response.url, http_status=response.status,
                                 content_length=response.headers.get("Content-Length"))
                    next_log = 16 * 1024 * 1024
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        event["bytes_read"] += len(chunk)
                        if event["bytes_read"] >= next_log:
                            write_json(log_path, record)
                            next_log += 16 * 1024 * 1024
                    output.flush()
                    os.fsync(output.fileno())
                validated = _archive_record(temporary, spec)
                os.replace(temporary, target)
                validated.update(filename=target.name, url=url)
                event.update(status="verified", completed_at=utc_now())
                record.update(status="verified", archive=validated, verified_at=utc_now())
                write_json(log_path, record)
                print(f"{split}: verified {validated['bytes']} bytes", flush=True)
                return record
            except (OSError, ValueError) as error:
                event.update(status="failed", completed_at=utc_now(), error=f"{type(error).__name__}: {error}")
                record["status"] = "failed"
                write_json(log_path, record)
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(one, ARCHIVE_SPECS.items()))
    summary = {"schema_version": "librispeech-dev-download-summary-v1", "source_url": SOURCE_URL,
               "license": "CC-BY-4.0", "metadata": snapshots, "archives": records,
               "status": "verified", "completed_at": utc_now()}
    write_json(root / "download-verification.json", summary)
    return summary


def prepare(archives, output):
    archives, root = Path(archives).resolve(), Path(output).resolve()
    if root == archives or root in archives.parents:
        raise ValueError("Prepared output must be separate from archive inputs")
    checksum_sha = verify_checksum_snapshot(archives)
    records = {split: _archive_record(archives / spec["filename"], spec)
               for split, spec in ARCHIVE_SPECS.items()}
    root.mkdir(parents=True, exist_ok=True)
    _preserve(root, "source_metadata/md5sum.official.txt", (archives / "md5sum.official.txt").read_bytes())
    all_rows, all_references, groups = [], [], {}
    for split, spec in ARCHIVE_SPECS.items():
        rows, references, summary = _prepare_split(archives / spec["filename"], root, split, spec, records[split])
        # The reusable extractor emits its module revision; this separate wrapper
        # assigns the dev identity before publishing any manifest/reference row.
        for row in [*rows, *references]:
            row["corpus_revision"] = REVISION
        all_rows.extend(rows)
        all_references.extend(references)
        groups[split] = (rows, references, summary)
        print(f"{split}: validated {len(rows)} original recordings", flush=True)
    for split, (rows, references, summary) in groups.items():
        for suffix, values in (("manifest", rows), ("references", references)):
            target = _safe_target(root, f"{split}.{suffix}.jsonl")
            atomic_jsonl(target, values)
            summary[f"{suffix}_sha256"] = file_sha256(target)
    atomic_jsonl(_safe_target(root, "manifest.jsonl"), all_rows)
    atomic_jsonl(_safe_target(root, "references.jsonl"), all_references)
    result = {"schema_version": "librispeech-dev-preparation-v1", "corpus": "LibriSpeech",
              "corpus_revision": REVISION, "source_url": SOURCE_URL, "license": "CC-BY-4.0",
              "items": len(all_rows), "audio_seconds": sum(r["duration"] for r in all_rows),
              "audio_bytes": sum(r["audio_bytes"] for r in all_rows),
              "selection": "All original dev-clean and dev-other utterances; no sampling",
              "audio_transform": "none; original FLAC bytes, fully decoded for validation",
              "reference_policy": "Reference text only in separate references JSONL and source_metadata",
              "splits": {split: group[2] for split, group in groups.items()},
              "official_md5_sha256": checksum_sha,
              "preparation_source_sha256": file_sha256(Path(__file__)),
              "shared_extractor_source_sha256": file_sha256(Path(__file__).resolve().parents[1] / "speechprep/prepare_librispeech.py"),
              "runtime_versions": {name: importlib.metadata.version(name) for name in ("numpy", "soundfile")},
              "manifest_sha256": file_sha256(root / "manifest.jsonl"),
              "references_sha256": file_sha256(root / "references.jsonl")}
    write_json(_safe_target(root, "verification.json"), result)
    return result


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def select_subset(prepared, output, *, per_speaker, seed, test_manifest):
    """Freeze label-blind speaker partitions before joining any reference text."""
    source, target = Path(prepared).resolve(), Path(output).resolve()
    if per_speaker < 1 or not isinstance(seed, str) or not seed:
        raise ValueError("Positive per_speaker and nonempty seed are required")
    if target.exists():
        raise ValueError("Subset output already exists; choose a new frozen directory")
    rows = _read_jsonl(source / "manifest.jsonl")
    if len({r["item_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate development item IDs")
    groups, speaker_split = {}, {}
    for row in rows:
        if row["split"] not in ARCHIVE_SPECS or not row.get("speaker_id"):
            raise ValueError("Expected official dev rows with speaker IDs")
        speaker = row["speaker_id"]
        if speaker in speaker_split and speaker_split[speaker] != row["split"]:
            raise ValueError("Speaker appears in multiple official development splits")
        speaker_split[speaker] = row["split"]
        groups.setdefault((row["split"], speaker), []).append(row)
    test_rows = _read_jsonl(test_manifest)
    disjoint = {}
    for key in ("speaker_id", "item_id", "audio_sha256"):
        overlap = sorted({r[key] for r in rows} & {r[key] for r in test_rows})
        if overlap:
            raise ValueError(f"Development/test {key} overlap: {overlap}")
        disjoint[key] = {"overlap": [], "development_unique": len({r[key] for r in rows}),
                         "test_unique": len({r[key] for r in test_rows})}

    def identifier_hash(kind, identifier):
        return hashlib.sha256(f"{seed}:{kind}:{identifier}".encode()).hexdigest()

    speaker_roles, partitions = {}, {}
    for split in sorted(ARCHIVE_SPECS):
        speakers = sorted((speaker for group_split, speaker in groups if group_split == split),
                          key=lambda speaker: (identifier_hash("speaker", speaker), speaker))
        train_count, tune_count = len(speakers) * 6 // 10, len(speakers) * 2 // 10
        if not train_count or not tune_count or train_count + tune_count == len(speakers):
            raise ValueError("Each official split needs nonempty train/tune/holdout speaker groups")
        partitions[split] = {"train": speakers[:train_count],
                             "tune": speakers[train_count:train_count + tune_count],
                             "holdout": speakers[train_count + tune_count:]}
        for role, selected_speakers in partitions[split].items():
            speaker_roles.update({speaker: role for speaker in selected_speakers})

    selected, assignments, group_counts = [], [], []
    for (split, speaker), candidates in sorted(groups.items()):
        ranked = sorted(candidates, key=lambda row: (identifier_hash("item", row["item_id"]), row["item_id"]))
        role = speaker_roles[speaker]
        for index, row in enumerate(ranked):
            chosen = index < per_speaker
            if chosen:
                selected.append(row)
            assignments.append({"item_id": row["item_id"], "split": split, "speaker_id": speaker,
                                "role": role, "selected": chosen, "within_speaker_item_rank": index,
                                "item_hash": identifier_hash("item", row["item_id"]),
                                "speaker_hash": identifier_hash("speaker", speaker),
                                "audio_sha256": row["audio_sha256"], "duration": row["duration"]})
        group_counts.append({"split": split, "speaker_id": speaker, "role": role,
                             "available": len(candidates), "selected": min(len(candidates), per_speaker),
                             "selected_audio_seconds": sum(r["duration"] for r in ranked[:per_speaker])})
    internal_disjoint = {}
    for left, right in (("train", "tune"), ("train", "holdout"), ("tune", "holdout")):
        checks = {}
        for key in ("speaker_id", "item_id", "audio_sha256"):
            overlap = sorted({r[key] for r in assignments if r["role"] == left}
                             & {r[key] for r in assignments if r["role"] == right})
            if overlap:
                raise ValueError(f"Development {left}/{right} {key} overlap: {overlap}")
            checks[key] = []
        internal_disjoint[f"{left}/{right}"] = checks
    selected.sort(key=lambda row: row["item_id"])
    assignments.sort(key=lambda row: row["item_id"])
    assignment = {"schema_version": "librispeech-dev-assignment-v2", "seed": seed,
                  "per_speaker": per_speaker,
                  "selection": "First per_speaker utterances per speaker by SHA256(seed + ':item:' + item_id), tie by item_id",
                  "partition": "Within each official split, speakers ordered by SHA256(seed + ':speaker:' + speaker_id), tie by speaker_id; first floor(n*0.6) train, next floor(n*0.2) tune, remaining holdout",
                  "labels_used_for_selection": False,
                  "created_before_reference_join": True, "created_at": utc_now(),
                  "source_manifest_sha256": file_sha256(source / "manifest.jsonl"),
                  "source_references_sha256": file_sha256(source / "references.jsonl"),
                  "test_manifest_sha256": file_sha256(test_manifest),
                  "source_items": len(rows), "selected_items": len(selected),
                  "selected_audio_seconds": sum(row["duration"] for row in selected),
                  "test_disjoint_checks": disjoint, "role_disjoint_checks": internal_disjoint,
                  "speaker_partitions": partitions, "groups": group_counts, "items": assignments}
    target.mkdir(parents=True)
    write_json(target / "assignment.json", assignment)
    # The complete assignment is already saved. References are used only to
    # export matching labels, never for selection, role assignment or ordering.
    references = _read_jsonl(source / "references.jsonl")
    by_id = {r["item_id"]: r for r in references}
    if len(by_id) != len(references) or set(by_id) != {r["item_id"] for r in rows}:
        raise ValueError("Development manifest/reference IDs differ")
    exports = {}
    for role in ("all", "train", "tune", "holdout"):
        role_rows = [row for row in selected if role == "all" or speaker_roles[row["speaker_id"]] == role]
        selected_rows = []
        for row in role_rows:
            item = dict(row)
            item["audio_path"] = str((source / item["audio_path"]).resolve())
            selected_rows.append(item)
        export_root = target if role == "all" else target / role
        export_root.mkdir(exist_ok=True)
        atomic_jsonl(export_root / "manifest.jsonl", selected_rows)
        atomic_jsonl(export_root / "references.jsonl", [by_id[row["item_id"]] for row in selected_rows])
        exports[role] = {"items": len(selected_rows), "speakers": len({r["speaker_id"] for r in selected_rows}),
                         "audio_seconds": sum(r["duration"] for r in selected_rows),
                         "manifest": str((export_root / "manifest.jsonl").relative_to(target)),
                         "references": str((export_root / "references.jsonl").relative_to(target)),
                         "manifest_sha256": file_sha256(export_root / "manifest.jsonl"),
                         "references_sha256": file_sha256(export_root / "references.jsonl")}
    summary = {"schema_version": "librispeech-dev-subset-v2", "seed": seed, "per_speaker": per_speaker,
               "assignment_sha256": file_sha256(target / "assignment.json"), "exports": exports}
    write_json(target / "selection.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("download")
    fetch.add_argument("--archives", type=Path, required=True)
    fetch.add_argument("--ranged", action="store_true", help="Use six concurrent validated ranges from official mirrors")
    extract = subparsers.add_parser("prepare")
    extract.add_argument("--archives", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    subset = subparsers.add_parser("subset")
    subset.add_argument("--prepared", type=Path, required=True)
    subset.add_argument("--output", type=Path, required=True)
    subset.add_argument("--per-speaker", type=int, required=True)
    subset.add_argument("--seed", required=True)
    subset.add_argument("--test-manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "download":
        result = download(args.archives, ranged=args.ranged)
    elif args.command == "prepare":
        result = prepare(args.archives, args.output)
    else:
        result = select_subset(args.prepared, args.output, per_speaker=args.per_speaker,
                               seed=args.seed, test_manifest=args.test_manifest)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
