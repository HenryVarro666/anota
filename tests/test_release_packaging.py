"""Execute release behavior from packaged sources and a fresh Windows-style checkout."""
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_cpu_image_sources_support_speech_review_without_developer_tree(tmp_path):
    # Mirror actual Docker COPY entries rather than inheriting the repository's
    # scripts, tests, evals or data caches through cwd/PYTHONPATH.
    for line in (ROOT / "Dockerfile").read_text().splitlines():
        fields = shlex.split(line)
        if not fields or fields[0] != "COPY":
            continue
        *sources, destination = fields[1:]
        destination = tmp_path / destination.removeprefix("./")
        for source in sources:
            path = ROOT / source
            if path.is_dir():
                shutil.copytree(path, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination / path.name)
    assert not any((tmp_path / name).exists() for name in ("scripts", "tests", "evals", "runtime"))
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-I", "-"], cwd=tmp_path, env=environment,
        check=False,
        input=(ROOT / "scripts/verify_release_image.py").read_text(encoding="utf-8"),
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Release check passed" in result.stdout


def test_autocrlf_checkout_preserves_reviewed_source_hashes_and_frozen_evidence(tmp_path):
    git = shutil.which("git")
    if not git:
        pytest.skip("Git executable is needed for the actual checkout regression")
    source = tmp_path / "source"
    source.mkdir()

    def run(*args, cwd=source):
        return subprocess.run([git, *args], cwd=cwd, check=True, capture_output=True, text=True)

    run("init", "--quiet")
    run("config", "core.autocrlf", "true")
    run("config", "core.safecrlf", "false")
    run("config", "core.hooksPath", str(source / "unused-hooks"))
    run("config", "user.name", "Release checkout test")
    run("config", "user.email", "release-check@example.invalid")
    shutil.copy2(ROOT / ".gitattributes", source / ".gitattributes")
    from speechrank.runtime import bundle_manifest
    hashes = bundle_manifest()["source_hashes"]
    for name in hashes:
        target = source / "speechrank" / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((ROOT / "speechrank" / name).read_bytes())
    evidence = {"evals/data/fixture.jsonl": b'{"frozen":true}\n',
                "evals/runs/frozen/source.py": b"# frozen LF evidence\n",
                "docs/figures/fixture.svg": b"<svg>\n</svg>\n"}
    for name, content in evidence.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    run("add", ".")
    run("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "checkout fixture")
    checkout = tmp_path / "checkout"
    run("clone", "--quiet", "--no-checkout", str(source), str(checkout))
    run("config", "core.autocrlf", "true", cwd=checkout)
    run("checkout", "--quiet", "--force", "HEAD", cwd=checkout)
    for name, digest in hashes.items():
        content = (checkout / "speechrank" / name).read_bytes()
        assert b"\r\n" not in content
        assert hashlib.sha256(content).hexdigest() == digest
    for name, content in evidence.items():
        assert (checkout / name).read_bytes() == content
