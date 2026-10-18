import hashlib
import io
import json
import tarfile

import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")

from speechprep import prepare_librispeech as preparation
from speechprep.manifest import read_manifest


def _flac(rate=16000, channels=1):
    stream = io.BytesIO()
    samples = np.tile(np.linspace(-0.2, 0.2, 1600, dtype=np.float32)[:, None], (1, channels))
    sf.write(stream, samples, rate, format="FLAC", subtype="PCM_16")
    return stream.getvalue()


def _fixtures(tmp_path, monkeypatch, *, missing_reference=False, unsafe=None, rate=16000, channels=1):
    archives = tmp_path / "archives"
    archives.mkdir()
    specs = {}
    original = _flac(rate, channels)
    for split in ("test-clean", "test-other"):
        path = archives / f"{split}.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            entries = [("LibriSpeech/LICENSE.TXT", b"Creative Commons Attribution 4.0\n"),
                       (f"LibriSpeech/{split}/1/2/1-2-0001.flac", original)]
            if not missing_reference or split != "test-other":
                entries.append((f"LibriSpeech/{split}/1/2/1-2.trans.txt", b"1-2-0001 A REFERENCE SENTENCE\n"))
            for name, content in entries:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            if unsafe and split == "test-other":
                info = tarfile.TarInfo(unsafe)
                if unsafe.endswith("link"):
                    info.type = tarfile.SYMTYPE
                    info.linkname = "../../outside"
                    tar.addfile(info)
                else:
                    info.size = 1
                    tar.addfile(info, io.BytesIO(b"x"))
        content = path.read_bytes()
        specs[split] = {"filename": path.name, "bytes": len(content),
                        "md5": hashlib.md5(content).hexdigest(), "items": 1}
    (archives / "md5sum.official.txt").write_text(
        "".join(f"{spec['md5']}  {spec['filename']}\n" for spec in specs.values()), encoding="utf-8")
    monkeypatch.setattr(preparation, "ARCHIVE_SPECS", specs)
    return archives, tmp_path / "prepared", original


def test_all_items_preserve_flac_and_isolate_references_and_repeat(tmp_path, monkeypatch):
    archives, output, original = _fixtures(tmp_path, monkeypatch)
    result = preparation.prepare(archives, output)
    assert result["items"] == 2
    assert set(result["splits"]) == {"test-clean", "test-other"}
    manifest_bytes = (output / "manifest.jsonl").read_bytes()
    assert b"A REFERENCE SENTENCE" not in manifest_bytes
    rows = read_manifest(output / "manifest.jsonl")
    references = [json.loads(line) for line in (output / "references.jsonl").read_text().splitlines()]
    assert {r["reference_id"] for r in references} == {r["reference_id"] for r in rows}
    assert len({r["item_id"] for r in rows}) == 2
    for row in rows:
        assert (output / row["audio_path"]).read_bytes() == original
        assert row["sample_rate"] == 16000 and row["channels"] == 1
        assert row["duration"] == pytest.approx(0.1)
    for split, summary in result["splits"].items():
        assert summary["items"] == summary["speakers"] == summary["chapters"] == 1
        assert len(read_manifest(output / f"{split}.manifest.jsonl")) == 1
        assert (output / f"source_metadata/{split}/LICENSE.TXT").is_file()
        assert summary["source_archive"]["sha256"] == hashlib.sha256((archives / f"{split}.tar.gz").read_bytes()).hexdigest()
    assert preparation.prepare(archives, output) == result
    assert (output / "manifest.jsonl").read_bytes() == manifest_bytes


@pytest.mark.parametrize("unsafe", [".", "../outside", "/outside", "LibriSpeech/../outside", "LibriSpeech/link"])
def test_rejects_traversal_and_links_without_publishing_manifest(tmp_path, monkeypatch, unsafe):
    archives, output, _ = _fixtures(tmp_path, monkeypatch, unsafe=unsafe)
    with pytest.raises(ValueError, match="Unsafe archive|links and special"):
        preparation.prepare(archives, output)
    assert not (tmp_path / "outside").exists()
    assert not (output / "manifest.jsonl").exists()


def test_missing_reference_is_fatal_not_skipped(tmp_path, monkeypatch):
    archives, output, _ = _fixtures(tmp_path, monkeypatch, missing_reference=True)
    with pytest.raises(ValueError, match="Audio/reference mismatch"):
        preparation.prepare(archives, output)
    assert not (output / "manifest.jsonl").exists()
    assert not (output / "test-clean.manifest.jsonl").exists()


def test_checksum_mismatch_stops_before_extraction(tmp_path, monkeypatch):
    archives, output, _ = _fixtures(tmp_path, monkeypatch)
    preparation.ARCHIVE_SPECS["test-other"]["md5"] = "0" * 32
    with pytest.raises(ValueError, match="MD5 mismatch"):
        preparation.prepare(archives, output)
    assert not output.exists()


@pytest.mark.parametrize("rate,channels", [(8000, 1), (16000, 2)])
def test_decodes_and_rejects_wrong_audio_contract(tmp_path, monkeypatch, rate, channels):
    archives, output, _ = _fixtures(tmp_path, monkeypatch, rate=rate, channels=channels)
    with pytest.raises(ValueError, match="mono 16 kHz"):
        preparation.prepare(archives, output)
    assert not (output / "manifest.jsonl").exists()


def test_changed_prepared_audio_is_not_silently_reused(tmp_path, monkeypatch):
    archives, output, _ = _fixtures(tmp_path, monkeypatch)
    preparation.prepare(archives, output)
    path = output / "audio/test-clean/1/2/1-2-0001.flac"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs from verified archive"):
        preparation.prepare(archives, output)
