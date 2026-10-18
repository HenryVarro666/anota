"""Label-blind verification of the frozen ML pilot's complete input chain.

Reference files are hashed as opaque bytes; their contents are never decoded.
Partition and selection are reconstructed from the prepared audio manifest,
including unselected recordings, independently of the preparer's implementation.
"""

from __future__ import annotations

import hashlib
import json
import math
from itertools import combinations
from pathlib import Path

from speechprep.manifest import digest_json, file_sha256, read_manifest

ROOT = Path(__file__).resolve().parents[1]
SEED = "audio-qc-ml-v2"
PER_SPEAKER = 30
ROLES = ("train", "tune", "holdout")
SPLITS = ("dev-clean", "dev-other")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _hashed(path, expected, evidence, name):
    path = Path(path).resolve()
    actual = file_sha256(path)
    _require(actual == expected, f"{name} checksum mismatch")
    evidence[name] = {"path": str(path), "sha256": actual}


def _indexed(values, name):
    result = {row["item_id"]: row for row in values}
    _require(bool(values) and len(result) == len(values), f"empty or duplicate {name} IDs")
    return result


def _identifier_hash(kind, identifier):
    return hashlib.sha256(f"{SEED}:{kind}:{identifier}".encode()).hexdigest()


def _same_rows(actual, expected, name):
    _require(_indexed(actual, name) == _indexed(expected, name + " expected"),
             f"{name} manifest rows differ from source metadata")


def _duration_sum(values):
    return sum(row["duration"] for row in values)


def _same_duration(actual, expected, name):
    _require(isinstance(actual, (int, float)) and not isinstance(actual, bool)
             and math.isfinite(actual) and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-8),
             f"{name} duration mismatch")


def audit_inputs(paths, dev_run, manifests):
    """Return deterministic evidence or fail before any reference is decoded.

    Required paths: dev_manifest, dev_assignment, dev_selection, test_manifest,
    and each role's manifest/references. ``manifests`` contains dev/test and all
    three roles after read_manifest. Optional dev_prepared_manifest,
    dev_prepared_references and dev_verification override the standard layout.
    """
    paths = {name: Path(path).resolve() for name, path in paths.items()}
    subset = paths["dev_manifest"].parent
    source_path = paths.get("dev_prepared_manifest", subset.parent / "prepared/manifest.jsonl")
    source_references = paths.get("dev_prepared_references", source_path.parent / "references.jsonl")
    verification_path = paths.get("dev_verification", source_path.parent / "verification.json")
    assignment = _load(paths["dev_assignment"])
    selection = _load(paths["dev_selection"])
    verification = _load(verification_path)
    checked = {}
    _require(assignment["schema_version"] == "librispeech-dev-assignment-v2"
             and selection["schema_version"] == "librispeech-dev-subset-v2"
             and verification["schema_version"] == "librispeech-dev-preparation-v1",
             "unexpected development metadata schema")
    for name, value in (("assignment", assignment), ("selection", selection)):
        _require(value["seed"] == SEED and value["per_speaker"] == PER_SPEAKER,
                 f"{name} does not use the fixed sampling protocol")
    _require(assignment["labels_used_for_selection"] is False
             and assignment["created_before_reference_join"] is True,
             "assignment does not declare label-blind selection")
    _hashed(paths["dev_assignment"], selection["assignment_sha256"], checked, "assignment")
    _hashed(paths["dev_manifest"], dev_run["identity"]["manifest_sha256"], checked, "asr_manifest")
    test_source = paths.get("test_source_manifest", paths["test_manifest"])
    _hashed(test_source, assignment["test_manifest_sha256"], checked, "test_source_manifest")
    checked["test_manifest"] = {"path": str(paths["test_manifest"]),
                                "sha256": file_sha256(paths["test_manifest"])}
    # v1 captured resolved audio paths; the preparation manifest used relative
    # paths. Compare every metadata field after the common manifest resolver,
    # while preserving both byte identities rather than weakening either seal.
    _same_rows(manifests["test"], read_manifest(test_source), "test source/snapshot")
    _hashed(source_path, assignment["source_manifest_sha256"], checked, "prepared_manifest")
    _hashed(source_references, assignment["source_references_sha256"], checked, "prepared_references")
    _require(checked["prepared_manifest"]["sha256"] == verification["manifest_sha256"]
             and checked["prepared_references"]["sha256"] == verification["references_sha256"],
             "prepared verification hashes differ from assignment")
    checked["verification"] = {"path": str(verification_path), "sha256": file_sha256(verification_path)}

    source = read_manifest(source_path)
    source_by_id = _indexed(source, "prepared")
    _require(len(source) == assignment["source_items"] == verification["items"],
             "prepared source count mismatch")
    _same_rows(manifests["dev"], read_manifest(paths["dev_manifest"]), "supplied dev")
    _same_rows(manifests["test"], read_manifest(paths["test_manifest"]), "supplied test")
    _require(dev_run["identity"]["expected_items"] == len(manifests["dev"]),
             "ASR expected item count differs from development manifest")
    grouped, speaker_split = {}, {}
    for row in source:
        _require(row["split"] in SPLITS and isinstance(row.get("speaker_id"), str)
                 and bool(row["speaker_id"]) and row["language"] == "en",
                 "invalid development split, speaker or language")
        _require(not any(key in row for key in ("text", "reference_text", "asr_text", "errors")),
                 "reference/prediction data in the prepared manifest")
        duration = row["duration"]
        _require(isinstance(duration, (int, float)) and not isinstance(duration, bool)
                 and math.isfinite(duration) and duration > 0, "invalid source duration")
        speaker, split = row["speaker_id"], row["split"]
        _require(speaker not in speaker_split or speaker_split[speaker] == split,
                 "speaker appears in multiple development splits")
        speaker_split[speaker] = split
        grouped.setdefault((split, speaker), []).append(row)
    _require({row["split"] for row in source} == set(SPLITS), "missing development split")

    partitions, speaker_roles = {}, {}
    for split in SPLITS:
        speakers = sorted((speaker for group_split, speaker in grouped if group_split == split),
                          key=lambda speaker: (_identifier_hash("speaker", speaker), speaker))
        train_end, tune_count = len(speakers) * 6 // 10, len(speakers) * 2 // 10
        _require(train_end > 0 and tune_count > 0 and train_end + tune_count < len(speakers),
                 "development split has an empty speaker role")
        partitions[split] = {"train": speakers[:train_end],
                             "tune": speakers[train_end:train_end + tune_count],
                             "holdout": speakers[train_end + tune_count:]}
        for role, members in partitions[split].items():
            speaker_roles.update({speaker: role for speaker in members})
    _require(partitions == assignment["speaker_partitions"], "speaker partition differs from fixed hash rule")

    rebuilt, selected, rebuilt_groups = [], [], []
    for (split, speaker), values in sorted(grouped.items()):
        ranked = sorted(values, key=lambda row: (_identifier_hash("item", row["item_id"]), row["item_id"]))
        role = speaker_roles[speaker]
        selected.extend(ranked[:PER_SPEAKER])
        for index, row in enumerate(ranked):
            rebuilt.append({"item_id": row["item_id"], "split": split, "speaker_id": speaker,
                "role": role, "selected": index < PER_SPEAKER, "within_speaker_item_rank": index,
                "item_hash": _identifier_hash("item", row["item_id"]),
                "speaker_hash": _identifier_hash("speaker", speaker),
                "audio_sha256": row["audio_sha256"], "duration": row["duration"]})
        rebuilt_groups.append({"split": split, "speaker_id": speaker, "role": role,
            "available": len(ranked), "selected": min(len(ranked), PER_SPEAKER),
            "selected_audio_seconds": _duration_sum(ranked[:PER_SPEAKER])})
    rebuilt.sort(key=lambda row: row["item_id"])
    selected.sort(key=lambda row: row["item_id"])
    _require(rebuilt == assignment["items"], "assignment differs from reconstructed selection")
    _require(rebuilt_groups == assignment["groups"], "assignment speaker group totals differ")
    _require(len(selected) == assignment["selected_items"], "selected source count mismatch")
    _same_duration(assignment["selected_audio_seconds"], _duration_sum(selected), "selected")
    _same_rows(manifests["dev"], selected, "selected dev master")

    exports = selection["exports"]
    _require(set(exports) == {"all", *ROLES}, "selection export role set mismatch")
    roles_evidence = {}
    for role in ("all", *ROLES):
        expected = selected if role == "all" else [r for r in selected if speaker_roles[r["speaker_id"]] == role]
        actual = manifests["dev" if role == "all" else role]
        _same_rows(actual, expected, role)
        spec = exports[role]
        for kind in ("manifest", "references"):
            exported = (subset / spec[kind]).resolve()
            _require(exported.is_relative_to(subset), "export escapes development subset")
            supplied_key = ("dev_" if role == "all" else role + "_") + kind
            supplied = paths.get(supplied_key, exported)
            _require(supplied == exported, f"{role} {kind} path differs from export")
            _hashed(exported, spec[kind + "_sha256"], checked, role + "_" + kind)
        _same_rows(actual, read_manifest(subset / spec["manifest"]), role + " disk")
        _require(len(actual) == spec["items"]
                 and len({r["speaker_id"] for r in actual}) == spec["speakers"],
                 f"{role} export count mismatch")
        _same_duration(spec["audio_seconds"], _duration_sum(expected), role)
        roles_evidence[role] = {"items": len(actual), "speakers": spec["speakers"],
            "audio_seconds": _duration_sum(expected), "item_ids_sha256": digest_json([r["item_id"] for r in expected])}

    full_roles = {role: [r for r in source if speaker_roles[r["speaker_id"]] == role] for role in ROLES}
    full_roles["test"] = manifests["test"]
    for left, right in combinations(full_roles, 2):
        for key in ("item_id", "speaker_id", "audio_sha256"):
            _require(not ({r[key] for r in full_roles[left]} & {r[key] for r in full_roles[right]}),
                     f"{left}/{right} {key} overlap including unselected source rows")
    return {"schema_version": "speechrank-input-audit-v2", "status": "passed",
        "test_manifests_metadata_equal": True,
        "reference_bodies_decoded": False, "source_items": len(source_by_id),
        "selected_items": len(selected), "sampling_seed": SEED, "per_speaker": PER_SPEAKER,
        "assignment_reconstructed": True, "role_metadata_equals_prepared_source": True,
        "asr_manifest_identity_matches": True, "all_source_roles_test_disjoint": True,
        "speaker_partitions": partitions, "roles": roles_evidence, "checked_files": checked}


def verify_v1(paths):
    """Verify the reused QC/scorer source against v1 without opening its gold."""
    protocol_path, config_path = Path(paths["v1_protocol"]), Path(paths["v1_config"])
    protocol = _load(protocol_path)
    checked = {}
    _hashed(config_path, protocol["config_sha256"], checked, "v1_config")
    required = {"scripts/evaluate_qc_ranking.py", "scripts/qc_ranking_metrics.py",
                "scripts/evaluate_speech_smoke.py", "speechprep/manifest.py", "speechprep/state.py"}
    required.update(p.relative_to(ROOT).as_posix() for p in (ROOT / "speechqc").glob("*.py"))
    old_hashes = protocol["source_sha256"]
    required.update(path for path in old_hashes if path.startswith("speechqc/") and path.endswith(".py"))
    for path in sorted(required):
        _require(path in old_hashes, "QC/scorer source was not frozen in v1: " + path)
        _hashed(ROOT / path, old_hashes[path], checked, path)
    return {"status": "passed", "reference_bodies_decoded": False,
        "v1_protocol_sha256": file_sha256(protocol_path), "checked_files": checked}
