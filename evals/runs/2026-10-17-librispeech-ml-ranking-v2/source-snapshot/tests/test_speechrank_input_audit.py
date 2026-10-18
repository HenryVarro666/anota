import hashlib
import json
from pathlib import Path

import pytest

from scripts import prepare_librispeech_dev as prepare
from scripts import speechrank_input_audit as audit
from speechprep.manifest import atomic_jsonl, file_sha256, read_manifest


def _save(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _row(item_id, speaker, split, path):
    return {"schema_version": "speech-v1", "item_id": item_id, "speaker_id": speaker,
        "split": split, "language": "en", "audio_path": str(path),
        "audio_sha256": hashlib.sha256(item_id.encode()).hexdigest(), "duration": 1.25,
        "corpus": "LibriSpeech", "corpus_revision": "fixture", "source_id": item_id,
        "reference_id": "ref:" + item_id, "license": "CC-BY-4.0"}


@pytest.fixture
def bundle(tmp_path):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    values = [_row(f"{split}-{speaker}-{item}", f"{split}-{speaker}", split,
                   f"audio/{split}-{speaker}-{item}.flac")
              for split in audit.SPLITS for speaker in range(5) for item in range(32)]
    atomic_jsonl(prepared / "manifest.jsonl", values)
    atomic_jsonl(prepared / "references.jsonl", [{"item_id": r["item_id"], "text": "FAKE GOLD"} for r in values])
    test_path = tmp_path / "test.jsonl"
    atomic_jsonl(test_path, [_row("test", "test", "test-clean", tmp_path / "test.flac")])
    subset = tmp_path / "sample-ml-v2"
    prepare.select_subset(prepared, subset, per_speaker=30, seed=audit.SEED, test_manifest=test_path)
    _save(prepared / "verification.json", {"schema_version": "librispeech-dev-preparation-v1",
          "manifest_sha256": file_sha256(prepared / "manifest.jsonl"),
          "references_sha256": file_sha256(prepared / "references.jsonl"), "items": len(values)})
    paths = {"dev_manifest": subset / "manifest.jsonl", "dev_assignment": subset / "assignment.json",
             "dev_selection": subset / "selection.json", "test_manifest": test_path}
    for role in audit.ROLES:
        for kind in ("manifest", "references"):
            paths[role + "_" + kind] = subset / role / (kind + ".jsonl")
    manifests = {name: read_manifest(paths[name + "_manifest"]) for name in ("dev", "test", *audit.ROLES)}
    return paths, {"identity": {"manifest_sha256": file_sha256(paths["dev_manifest"]),
                               "expected_items": len(manifests["dev"])}}, manifests


def _reseal_assignment(paths, assignment):
    _save(paths["dev_assignment"], assignment)
    selection = _load(paths["dev_selection"])
    selection["assignment_sha256"] = file_sha256(paths["dev_assignment"])
    _save(paths["dev_selection"], selection)


def test_reconstructs_full_source_and_never_decodes_reference_bodies(bundle, monkeypatch):
    paths, run, manifests = bundle
    original_open = Path.open

    def guarded_open(path, mode="r", *args, **kwargs):
        if path.name == "references.jsonl":
            assert "b" in mode, "references must remain opaque bytes"
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    result = audit.audit_inputs(paths, run, manifests)
    assert result["source_items"] == 320
    assert result["selected_items"] == 300
    assert result["roles"]["train"]["items"] == 180
    assert result["roles"]["tune"]["items"] == 60
    assert result["roles"]["holdout"]["items"] == 60
    assert result["reference_bodies_decoded"] is False
    assert result == audit.audit_inputs(paths, run, manifests)
    json.dumps(result, allow_nan=False)


def test_rejects_dev_asr_manifest_mismatch(bundle):
    paths, run, manifests = bundle
    run["identity"]["manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="asr_manifest checksum"):
        audit.audit_inputs(paths, run, manifests)


def test_accepts_resolved_snapshot_with_distinct_bytes_and_records_both_identities(bundle, tmp_path):
    paths, run, manifests = bundle
    source = paths["test_manifest"]
    snapshot = tmp_path / "snapshot.jsonl"
    atomic_jsonl(snapshot, [{**row, "audio_path": Path(row["audio_path"]).name} for row in manifests["test"]])
    assert file_sha256(source) != file_sha256(snapshot)
    paths.update(test_source_manifest=source, test_manifest=snapshot)
    result = audit.audit_inputs(paths, run, manifests)
    assert result["test_manifests_metadata_equal"] is True
    for key, path in (("test_source_manifest", source), ("test_manifest", snapshot)):
        assert result["checked_files"][key] == {"path": str(path.resolve()), "sha256": file_sha256(path)}


@pytest.mark.parametrize("field", ["item_id", "speaker_id", "audio_sha256", "duration", "split",
                                  "language", "audio_path", "reference_id", "corpus_revision", "license"])
def test_rejects_any_source_snapshot_metadata_difference(bundle, tmp_path, field):
    paths, run, manifests = bundle
    source = paths["test_manifest"]
    changed = [dict(row) for row in manifests["test"]]
    changed[0][field] = 2.5 if field == "duration" else "0" * 64 if field == "audio_sha256" else "different"
    snapshot = tmp_path / "snapshot.jsonl"
    atomic_jsonl(snapshot, changed)
    paths.update(test_source_manifest=source, test_manifest=snapshot)
    manifests["test"] = changed
    with pytest.raises(ValueError, match="test source/snapshot manifest rows differ"):
        audit.audit_inputs(paths, run, manifests)


def test_rejects_asr_expected_count_mismatch(bundle):
    paths, run, manifests = bundle
    run["identity"]["expected_items"] -= 1
    with pytest.raises(ValueError, match="ASR expected item count"):
        audit.audit_inputs(paths, run, manifests)


@pytest.mark.parametrize("field", ["selected", "role", "within_speaker_item_rank", "item_hash"])
def test_rejects_resealed_assignment_that_disagrees_with_fixed_algorithm(bundle, field):
    paths, run, manifests = bundle
    assignment = _load(paths["dev_assignment"])
    entry = assignment["items"][0]
    entry[field] = {"selected": not entry["selected"], "role": "bogus",
                    "within_speaker_item_rank": 900, "item_hash": "0" * 64}[field]
    _reseal_assignment(paths, assignment)
    with pytest.raises(ValueError, match="assignment differs from reconstructed"):
        audit.audit_inputs(paths, run, manifests)


def test_rejects_stale_role_metadata_even_when_export_was_rehashed(bundle):
    paths, run, manifests = bundle
    manifests["train"][0]["speaker_id"] = "different-speaker"
    atomic_jsonl(paths["train_manifest"], manifests["train"])
    selection = _load(paths["dev_selection"])
    selection["exports"]["train"]["manifest_sha256"] = file_sha256(paths["train_manifest"])
    _save(paths["dev_selection"], selection)
    with pytest.raises(ValueError, match="train manifest rows differ"):
        audit.audit_inputs(paths, run, manifests)


def test_rejects_changed_reference_bytes_without_reading_them(bundle):
    paths, run, manifests = bundle
    paths["holdout_references"].write_bytes(b"not JSON, still opaque\n")
    with pytest.raises(ValueError, match="holdout_references checksum"):
        audit.audit_inputs(paths, run, manifests)


def test_rejects_supplied_manifest_not_matching_disk(bundle):
    paths, run, manifests = bundle
    manifests["dev"][0]["duration"] += 1
    with pytest.raises(ValueError, match="supplied dev manifest rows differ"):
        audit.audit_inputs(paths, run, manifests)


def test_rejects_test_overlap_in_an_unselected_development_recording(bundle):
    paths, run, manifests = bundle
    assignment = _load(paths["dev_assignment"])
    entry = next(r for r in assignment["items"] if not r["selected"])
    entry["audio_sha256"] = manifests["test"][0]["audio_sha256"]
    prepared_path = paths["dev_manifest"].parent.parent / "prepared/manifest.jsonl"
    original = [json.loads(line) for line in prepared_path.read_text().splitlines()]
    next(r for r in original if r["item_id"] == entry["item_id"])["audio_sha256"] = entry["audio_sha256"]
    atomic_jsonl(prepared_path, original)
    assignment["source_manifest_sha256"] = file_sha256(prepared_path)
    _reseal_assignment(paths, assignment)
    verification_path = prepared_path.parent / "verification.json"
    verification = _load(verification_path)
    verification["manifest_sha256"] = file_sha256(prepared_path)
    _save(verification_path, verification)
    with pytest.raises(ValueError, match="audio_sha256 overlap including unselected"):
        audit.audit_inputs(paths, run, manifests)


def test_rejects_altered_sampling_configuration(bundle):
    paths, run, manifests = bundle
    selection = _load(paths["dev_selection"])
    selection["per_speaker"] = 29
    _save(paths["dev_selection"], selection)
    with pytest.raises(ValueError, match="fixed sampling protocol"):
        audit.audit_inputs(paths, run, manifests)


def test_rejects_role_reference_path_that_is_not_the_export(bundle, tmp_path):
    paths, run, manifests = bundle
    other = tmp_path / "other-reference.jsonl"
    other.write_bytes(paths["holdout_references"].read_bytes())
    paths["holdout_references"] = other
    with pytest.raises(ValueError, match="holdout references path differs"):
        audit.audit_inputs(paths, run, manifests)


@pytest.fixture
def prior_study(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    names = ("scripts/evaluate_qc_ranking.py", "scripts/qc_ranking_metrics.py",
             "scripts/evaluate_speech_smoke.py", "speechprep/manifest.py", "speechprep/state.py",
             "speechqc/analysis.py")
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    config = tmp_path / "config.json"
    _save(config, {"fixed": True})
    protocol = tmp_path / "protocol.json"
    _save(protocol, {"config_sha256": file_sha256(config),
                    "source_sha256": {name: file_sha256(tmp_path / name) for name in names}})
    return {"v1_config": config, "v1_protocol": protocol}


def test_verifies_v1_config_and_every_reused_qc_scoring_source(prior_study):
    evidence = audit.verify_v1(prior_study)
    assert evidence["status"] == "passed"
    assert len(evidence["checked_files"]) == 7


def test_rejects_v1_source_change(prior_study):
    (audit.ROOT / "speechqc/analysis.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="speechqc/analysis.py checksum"):
        audit.verify_v1(prior_study)


def test_rejects_added_qc_source_absent_from_v1_freeze(prior_study):
    (audit.ROOT / "speechqc/new.py").write_text("# not frozen\n")
    with pytest.raises(ValueError, match="not frozen in v1"):
        audit.verify_v1(prior_study)


def test_rejects_v1_configuration_change(prior_study):
    _save(prior_study["v1_config"], {"fixed": False})
    with pytest.raises(ValueError, match="v1_config checksum"):
        audit.verify_v1(prior_study)
