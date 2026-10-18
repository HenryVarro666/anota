"""Copy the reviewed model release into a five-file deployment bundle.

Usage: python scripts/package_speechrank_models.py DEST [--source OUTPUTS]
Never trains, modifies research outputs, or includes ASR/reference data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speechrank.runtime import DEFAULT_BUNDLE, _snapshot, bundle_manifest


def package(source: Path, destination: Path) -> None:
    _, blobs, _ = _snapshot(source)
    # Refuse an existing directory so deployment artifacts are never overwritten.
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in blobs.items():
        (destination / name).write_bytes(data)
    (destination / 'manifest.json').write_text(json.dumps(bundle_manifest(), sort_keys=True, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--source', type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    package(args.source, args.destination)
    print('Packaged reviewed SpeechRank bundle (models, cards, manifest).')
