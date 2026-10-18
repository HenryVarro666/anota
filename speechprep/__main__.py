"""CLI entry point. All stdout is machine-readable JSON; progress stays on stderr."""

import argparse
import json
import sys

from .asr import resolve_model
from .pipeline import default_config, run_pipeline


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline, resumable ASR manifests")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("transcribe")
    run.add_argument("--manifest", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--state", required=True)
    run.add_argument("--model", default="base")
    run.add_argument("--revision", help="Immutable Hugging Face commit (recommended)")
    run.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    run.add_argument("--compute-type", default="int8")
    run.add_argument("--cpu-threads", type=int, default=4)
    run.add_argument("--beam-size", type=int, default=5)
    run.add_argument("--no-vad", action="store_true")
    run.add_argument("--limit", type=int, help="Maximum new attempts; resume without this flag")
    args = parser.parse_args(argv)
    try:
        model = resolve_model(args.model, args.revision)
        config = default_config(args.device, args.compute_type, args.beam_size,
                                args.cpu_threads, not args.no_vad)
        summary = run_pipeline(args.manifest, args.output, args.state, model, config, args.limit)
        print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
        return 1 if summary["failed"] else 0
    except Exception as exc:  # noqa: BLE001 -- CLI boundary emits a fatal record and nonzero exit
        print(json.dumps({"status": "fatal", "error": {"type": type(exc).__name__, "message": str(exc)}},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
