"""python -m speechqc analyze --manifest asr.jsonl --output qc.jsonl --cache-dir cache"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cache import analyze_cached, atomic_jsonl
from .schema import canonical_json, file_sha256, resolve_config


def analyze_manifest(manifest, output, cache_dir, config=None):
    """Enrich manifests without changing ASR fields; return a run count summary.

    Per-item failures are explicit `audio_analysis_error` records and do not stop
    other items. Malformed JSON/manifest structure and unsafe output paths fail
    before any output is replaced. A partial run is a nonzero CLI exit.
    """
    manifest = Path(manifest).resolve(strict=True)
    output = Path(output).resolve()
    config = resolve_config(config)
    if manifest == output:
        raise ValueError("output must differ from input manifest")
    rows = []
    for number, line in enumerate(manifest.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("schema_version") != "speech-v1":
            raise ValueError(f"manifest line {number}: expected speech-v1 object")
        if not isinstance(row.get("audio_path"), str) or not row["audio_path"]:
            raise ValueError(f"manifest line {number}: audio_path is required")
        path = (manifest.parent / row["audio_path"]).resolve()
        if path == output:
            raise ValueError("output collides with source audio")
        row["audio_path"] = str(path)
        rows.append(row)
    counts = {"total": len(rows), "complete": 0, "partial": 0, "failed": 0}
    for row in rows:
        row.pop("audio_analysis", None)
        row.pop("audio_analysis_error", None)
        try:
            actual_hash = file_sha256(row["audio_path"])
            if row.get("audio_sha256") != actual_hash:
                raise ValueError("manifest audio_sha256 mismatch")
            if row.get("status") not in (None, "succeeded"):
                raise ValueError("ASR row did not succeed; analyze a succeeded speech-v1 row")
            report = analyze_cached(row["audio_path"], row, cache_dir, config)
            row["audio_analysis"] = report
            counts[report["status"]] += 1
        except Exception as exc:
            counts["failed"] += 1
            # Keep actionable but bounded diagnostics; no exception traceback.
            row["audio_analysis_error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    atomic_jsonl(output, rows)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent offline audio quality and temporal coverage checks")
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="Enrich existing speech-v1 ASR manifest; never run ASR")
    analyze.add_argument("--manifest", required=True)
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--cache-dir", required=True)
    analyze.add_argument("--config", help="JSON file containing a subset of documented config keys")
    args = parser.parse_args(argv)
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8-sig")) if args.config else None
        summary = analyze_manifest(args.manifest, args.output, args.cache_dir, config)
    except (OSError, ValueError, TypeError) as exc:
        print(f"speechqc: {exc}", file=sys.stderr)
        return 2
    print(canonical_json(summary))
    return 1 if summary["partial"] or summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
