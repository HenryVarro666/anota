"""Check frozen research artifact bytes without inference or local datasets.

``artifact-sha256`` manifests use repository paths. Other historical manifests
use paths relative to themselves, or prefix repository paths with ``evals/``.
This checks integrity only; the study-specific verifiers also recompute metrics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAMES = frozenset({"artifact-sha256.json", "artifact-hashes.json", "frozen-hashes.json"})


def verify(root: Path = ROOT) -> dict[str, int]:
    root = root.resolve()
    manifests = sorted(
        path for path in (root / "evals/runs").rglob("*.json")
        if path.name in MANIFEST_NAMES and "source-snapshot" not in path.parts
    )
    if not manifests:
        raise ValueError("no frozen artifact manifests found")
    count = 0
    for manifest in manifests:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(entries, dict) or not entries:
            raise ValueError(f"empty or invalid artifact manifest: {manifest.relative_to(root)}")
        for relative, expected in entries.items():
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise TypeError("artifact manifest must map paths to SHA-256 strings")
            relative_path = PurePosixPath(relative)
            repository_paths = manifest.name == "artifact-sha256.json" or relative_path.parts[:1] == ("evals",)
            base = root if repository_paths else manifest.parent
            target = (base / relative).resolve()
            if relative_path.is_absolute() or not target.is_relative_to(root):
                raise ValueError(f"artifact path escapes repository: {relative}")
            if not target.is_file():
                raise ValueError(f"missing research artifact: {relative}")
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"research artifact hash mismatch: {relative}")
            count += 1
    return {"manifests": len(manifests), "hashes": count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    result = verify(parser.parse_args().root)
    print(f"Verified {result['hashes']} frozen artifact hashes in {result['manifests']} manifests.")
