"""Launch the existing workbench with a bounded local judge profile."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def configure_profile(path):
    """Apply the same request contract used by the standalone evaluation runner."""
    profile = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    base = os.environ.get(profile.get("base_url_env", ""), profile.get("base_url"))
    model = os.environ.get(profile.get("model_env", ""), profile.get("model"))
    if not base or not model:
        raise ValueError("profile endpoint/model environment variables must be set")
    request = profile["request"]
    settings = {
        "ANOTA_JUDGE": "openai", "ANOTA_JUDGE_BASE_URL": base, "ANOTA_JUDGE_MODEL": model,
        "ANOTA_JUDGE_WORKERS": str(profile["execution"]["concurrency"]),
        "ANOTA_JUDGE_TIMEOUT": str(request["timeout_seconds"]), "ANOTA_JUDGE_SAMPLES": "1",
        "ANOTA_JUDGE_MAX_TOKENS": str(request["max_tokens"]),
        "ANOTA_JUDGE_JSON_SCHEMA": "1" if request["structured_output"] else "0",
        "ANOTA_JUDGE_PROMPT_SUFFIX": request["prompt_suffix"],
    }
    os.environ.update(settings)
    return base, model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["mock", "lmstudio", "ollama"])
    parser.add_argument("--judge-profile", help="evaluation profile JSON; replaces --provider and --model")
    parser.add_argument("--model", help="exact chat-model ID shown by the local server")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--db", help="persistent DB path; omit for a fresh synthetic demo")
    parser.add_argument("--check", action="store_true", help="check provider without starting the UI")
    args = parser.parse_args()
    if args.judge_profile and (args.provider or args.model):
        parser.error("--judge-profile cannot be combined with --provider or --model")
    args.provider = args.provider or "mock"
    if not args.judge_profile and args.provider != "mock" and not args.model:
        parser.error("--model is required for a real provider; embedding models cannot judge")
    os.environ["ANOTA_JUDGE"] = "mock" if args.provider == "mock" else "openai"
    os.environ["ANOTA_JUDGE_WORKERS"] = "1"
    os.environ["ANOTA_JUDGE_TIMEOUT"] = "120"
    os.environ["ANOTA_JUDGE_SAMPLES"] = "1"
    if args.model:
        os.environ["ANOTA_JUDGE_MODEL"] = args.model
    if args.judge_profile:
        try:
            _, args.model = configure_profile(args.judge_profile)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(f"invalid judge profile: {exc}")
        args.provider = "profile"
    elif args.provider != "mock":
        port = 1234 if args.provider == "lmstudio" else 11434
        os.environ["ANOTA_JUDGE_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    if args.provider != "mock":
        import httpx
        try:
            response = httpx.get(
                f"{os.environ['ANOTA_JUDGE_BASE_URL']}/models", timeout=5,
                headers={"Authorization": "Bearer " + os.environ.get("ANOTA_JUDGE_API_KEY", "EMPTY")})
            response.raise_for_status()
            ids = [item["id"] for item in response.json()["data"]]
            if args.model not in ids:
                parser.error(f"model {args.model!r} is not listed by the server; available: {ids}")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            parser.exit(2, f"Local provider is unavailable: {exc}\nStart the model server first.\n")
    if args.check:
        print(f"Provider check passed: {args.provider}; model={args.model or 'mock-judge'}")
        print("This checks discovery only; run the synthetic probe to measure judge quality.")
        return
    import tempfile
    import uvicorn
    from app.main import create_app
    if args.db:
        uvicorn.run(create_app(db_path=args.db), host="127.0.0.1", port=args.port)
    else:
        with tempfile.TemporaryDirectory(prefix="anota-local-") as directory:
            app = create_app(db_path=str(Path(directory) / "demo.db"), demo=True,
                             export_dir=str(Path(directory) / "exports"))
            try:
                uvicorn.run(app, host="127.0.0.1", port=args.port)
            finally:
                app.state.db.conn.close()


if __name__ == "__main__":
    main()
