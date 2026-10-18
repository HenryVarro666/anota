"""Publish frozen evidence intact; never regenerate an experiment in a test."""
import hashlib
import json

import pytest

from scripts.verify_research_artifacts import verify


def fixture(root):
    run = root / "evals/runs/study"
    run.mkdir(parents=True)
    evidence = run / "evidence.jsonl"
    evidence.write_bytes(b'{"result": 1}\n')
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    (run / "artifact-hashes.json").write_text(json.dumps({"evidence.jsonl": digest}), encoding="utf-8")
    (run / "frozen-hashes.json").write_text(
        json.dumps({"evals/runs/study/evidence.jsonl": digest}), encoding="utf-8")
    figure = root / "docs/figures/result.svg"
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b"<svg/>\n")
    (run / "artifact-sha256.json").write_text(
        json.dumps({"docs/figures/result.svg": hashlib.sha256(figure.read_bytes()).hexdigest()}), encoding="utf-8")
    return run, evidence


def test_verifies_both_historical_path_layouts_and_rejects_tampering(tmp_path):
    _, evidence = fixture(tmp_path)
    assert verify(tmp_path) == {"manifests": 3, "hashes": 3}
    evidence.write_bytes(b'{"result": 2}\n')
    with pytest.raises(ValueError, match="hash mismatch"):
        verify(tmp_path)
    evidence.unlink()
    with pytest.raises(ValueError, match="missing research artifact"):
        verify(tmp_path)


def test_empty_evidence_and_escaping_paths_cannot_pass(tmp_path):
    with pytest.raises(ValueError, match="no frozen artifact manifests"):
        verify(tmp_path)
    run, _ = fixture(tmp_path)
    (run / "artifact-hashes.json").write_text(
        json.dumps({"../../../../outside": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes repository"):
        verify(tmp_path)
