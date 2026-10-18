import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts import prepare_librispeech_dev as dev


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _subset_source(tmp_path):
    prepared = tmp_path / "prepared"
    rows, refs = [], []
    for split in ("dev-clean", "dev-other"):
        for speaker in range(10):
            speaker_id = f"{split}-{speaker}"
            for item in range(3):
                item_id = f"{speaker_id}-{item}"
                rows.append({"item_id": item_id, "split": split, "speaker_id": speaker_id,
                             "audio_sha256": hashlib.sha256(item_id.encode()).hexdigest(),
                             "audio_path": f"audio/{item_id}.flac", "duration": item + 1,
                             "reference_id": f"ref:{item_id}"})
                refs.append({"item_id": item_id, "text": f"SECRET GOLD {item_id}"})
    _jsonl(prepared / "manifest.jsonl", rows)
    _jsonl(prepared / "references.jsonl", refs)
    test = tmp_path / "test.manifest.jsonl"
    _jsonl(test, [{"item_id": "test", "speaker_id": "test", "audio_sha256": "0" * 64}])
    return prepared, test, rows, refs


def test_fixed_partition_and_selection_before_any_reference_join(tmp_path, monkeypatch):
    prepared, test, rows, _ = _subset_source(tmp_path)
    output = tmp_path / "subset"
    original_read = dev._read_jsonl

    def guarded_read(path):
        if Path(path).name == "references.jsonl":
            assert (output / "assignment.json").is_file()
            assert not (output / "manifest.jsonl").exists()
        return original_read(path)

    monkeypatch.setattr(dev, "_read_jsonl", guarded_read)
    result = dev.select_subset(prepared, output, per_speaker=2, seed="audio-qc-ml-v2", test_manifest=test)
    assignment = json.loads((output / "assignment.json").read_text())
    assert assignment["source_items"] == 60 and assignment["selected_items"] == 40
    assert assignment["created_before_reference_join"] is True
    assert assignment["labels_used_for_selection"] is False
    assert len(assignment["items"]) == len(rows)
    assert result["exports"]["train"]["items"] == 24
    assert result["exports"]["tune"]["items"] == 8
    assert result["exports"]["holdout"]["items"] == 8
    for split in ("dev-clean", "dev-other"):
        speakers = sorted({r["speaker_id"] for r in rows if r["split"] == split},
                          key=lambda speaker: hashlib.sha256(f"audio-qc-ml-v2:speaker:{speaker}".encode()).hexdigest())
        assert assignment["speaker_partitions"][split] == {
            "train": speakers[:6], "tune": speakers[6:8], "holdout": speakers[8:]}
    for group in assignment["groups"]:
        candidates = sorted((r for r in rows if r["speaker_id"] == group["speaker_id"]),
                            key=lambda row: hashlib.sha256(f"audio-qc-ml-v2:item:{row['item_id']}".encode()).hexdigest())
        chosen = {r["item_id"] for r in assignment["items"] if r["speaker_id"] == group["speaker_id"] and r["selected"]}
        assert chosen == {r["item_id"] for r in candidates[:2]}
    for role in ("train", "tune", "holdout"):
        data = (output / role / "manifest.jsonl").read_text()
        assert "SECRET GOLD" not in data and '"text"' not in data
        ids = [json.loads(line)["item_id"] for line in data.splitlines()]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)


def test_changed_gold_cannot_change_partition_or_selection(tmp_path):
    prepared, test, _, refs = _subset_source(tmp_path)
    left, right = tmp_path / "left", tmp_path / "right"
    dev.select_subset(prepared, left, per_speaker=2, seed="audio-qc-ml-v2", test_manifest=test)
    for row in refs:
        row["text"] = "TOTALLY DIFFERENT ANSWER"
    _jsonl(prepared / "references.jsonl", list(reversed(refs)))
    dev.select_subset(prepared, right, per_speaker=2, seed="audio-qc-ml-v2", test_manifest=test)
    lvalue = json.loads((left / "assignment.json").read_text())
    rvalue = json.loads((right / "assignment.json").read_text())
    assert lvalue["items"] == rvalue["items"]
    assert lvalue["speaker_partitions"] == rvalue["speaker_partitions"]
    assert (left / "manifest.jsonl").read_bytes() == (right / "manifest.jsonl").read_bytes()


@pytest.mark.parametrize("key", ["speaker_id", "item_id", "audio_sha256"])
def test_rejects_test_overlap_before_any_export(tmp_path, key):
    prepared, test, rows, _ = _subset_source(tmp_path)
    test_row = {"speaker_id": "test", "item_id": "test", "audio_sha256": "0" * 64}
    test_row[key] = rows[0][key]
    _jsonl(test, [test_row])
    output = tmp_path / "subset"
    with pytest.raises(ValueError, match=f"Development/test {key} overlap"):
        dev.select_subset(prepared, output, per_speaker=2, seed="audio-qc-ml-v2", test_manifest=test)
    assert not output.exists()


def test_rejects_same_speaker_in_multiple_official_splits(tmp_path):
    prepared, test, rows, _ = _subset_source(tmp_path)
    rows[-1]["speaker_id"] = rows[0]["speaker_id"]
    _jsonl(prepared / "manifest.jsonl", rows)
    with pytest.raises(ValueError, match="multiple official"):
        dev.select_subset(prepared, tmp_path / "subset", per_speaker=2, seed="audio-qc-ml-v2", test_manifest=test)


def test_rejects_duplicate_audio_across_roles_even_when_unselected(tmp_path):
    prepared, test, rows, _ = _subset_source(tmp_path)
    dev.select_subset(prepared, tmp_path / "first", per_speaker=1, seed="audio-qc-ml-v2", test_manifest=test)
    assignments = json.loads((tmp_path / "first/assignment.json").read_text())["items"]
    left = next(r for r in assignments if r["role"] == "train" and not r["selected"])
    right = next(r for r in assignments if r["role"] == "holdout" and not r["selected"])
    for row in rows:
        if row["item_id"] == right["item_id"]:
            row["audio_sha256"] = left["audio_sha256"]
    _jsonl(prepared / "manifest.jsonl", rows)
    with pytest.raises(ValueError, match="train/holdout audio_sha256 overlap"):
        dev.select_subset(prepared, tmp_path / "second", per_speaker=1, seed="audio-qc-ml-v2", test_manifest=test)


def test_refuses_overwrite_of_frozen_assignment(tmp_path):
    prepared, test, _, _ = _subset_source(tmp_path)
    output = tmp_path / "subset"
    dev.select_subset(prepared, output, per_speaker=2, seed="audio-qc-ml-v2", test_manifest=test)
    saved = (output / "assignment.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        dev.select_subset(prepared, output, per_speaker=3, seed="other", test_manifest=test)
    assert (output / "assignment.json").read_bytes() == saved


def test_missing_reference_leaves_assignment_but_no_model_manifest(tmp_path):
    prepared, test, _, refs = _subset_source(tmp_path)
    _jsonl(prepared / "references.jsonl", refs[:-1])
    output = tmp_path / "subset"
    with pytest.raises(ValueError, match="manifest/reference IDs differ"):
        dev.select_subset(prepared, output, per_speaker=2, seed="audio-qc-ml-v2", test_manifest=test)
    assert (output / "assignment.json").is_file()
    assert not (output / "manifest.jsonl").exists()


def test_prepare_preserves_source_bytes_and_uses_only_dev_revision(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    wave = io.BytesIO()
    sf.write(wave, np.linspace(-0.2, 0.2, 1600, dtype=np.float32), 16000, format="FLAC", subtype="PCM_16")
    original = wave.getvalue()
    archives = tmp_path / "archives"
    archives.mkdir()
    specs = {}
    for split in ("dev-clean", "dev-other"):
        path = archives / f"{split}.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, content in (("LibriSpeech/LICENSE.TXT", b"CC BY 4.0\n"),
                                  (f"LibriSpeech/{split}/1/2/1-2-0001.flac", original),
                                  (f"LibriSpeech/{split}/1/2/1-2.trans.txt", b"1-2-0001 SECRET REFERENCE\n")):
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        content = path.read_bytes()
        specs[split] = {"filename": path.name, "bytes": len(content), "items": 1,
                        "md5": hashlib.md5(content).hexdigest()}
    monkeypatch.setattr(dev, "ARCHIVE_SPECS", specs)
    (archives / "md5sum.official.txt").write_text("".join(f"{v['md5']}  {v['filename']}\n" for v in specs.values()))
    output = tmp_path / "prepared"
    result = dev.prepare(archives, output)
    assert result["items"] == 2
    assert result["corpus_revision"] == "openslr-12-official-dev-v1"
    rows = dev._read_jsonl(output / "manifest.jsonl")
    references = dev._read_jsonl(output / "references.jsonl")
    assert b"SECRET REFERENCE" not in (output / "manifest.jsonl").read_bytes()
    assert all(r["corpus_revision"] == dev.REVISION for r in rows + references)
    assert all((output / r["audio_path"]).read_bytes() == original for r in rows)
    assert dev.prepare(archives, output) == result


@pytest.mark.parametrize("valid_range", [True, False])
def test_range_download_checks_content_range_and_final_official_digest(tmp_path, monkeypatch, valid_range):
    content = b"verified official archive bytes" * 100
    spec = {"filename": "dev-clean.tar.gz", "bytes": len(content), "items": 1,
            "md5": hashlib.md5(content).hexdigest()}

    class Response(io.BytesIO):
        status = 206
        url = "https://openslr.elda.org/resources/12/dev-clean.tar.gz"

        def __init__(self, data):
            super().__init__(data)
            self.headers = {"Content-Range": f"bytes 0-{len(content)-1}/{len(content)}" if valid_range else "bytes 1-2/3"}

    monkeypatch.setattr(dev.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(content))
    if valid_range:
        result = dev._range_archive(tmp_path, "dev-clean", spec, {"attempts": []})
        assert result["status"] == "verified"
        assert result["archive"]["md5"] == spec["md5"]
        assert (tmp_path / spec["filename"]).read_bytes() == content
        assert result["ranges"][0]["attempts"][0]["http_status"] == 206
    else:
        with pytest.raises(RuntimeError, match="All official mirrors failed"):
            dev._range_archive(tmp_path, "dev-clean", spec, {"attempts": []})
        assert not (tmp_path / spec["filename"]).exists()
        log = json.loads((tmp_path / "range-parts/dev-clean/0000.json").read_text())
        assert len(log["attempts"]) == 3
        assert all(row["status"] == "failed" for row in log["attempts"])
