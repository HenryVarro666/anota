import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workbench_profile_matches_evaluation_request(monkeypatch):
    import json
    from scripts.run_local import configure_profile
    from app.judge import get_judge
    for key in ("ANOTA_JUDGE", "ANOTA_JUDGE_BASE_URL", "ANOTA_JUDGE_MODEL", "ANOTA_JUDGE_WORKERS",
                "ANOTA_JUDGE_TIMEOUT", "ANOTA_JUDGE_SAMPLES", "ANOTA_JUDGE_MAX_TOKENS",
                "ANOTA_JUDGE_JSON_SCHEMA", "ANOTA_JUDGE_PROMPT_SUFFIX"):
        monkeypatch.delenv(key, raising=False)
    path = ROOT / "evals/profiles/local-lmstudio.json"
    profile = json.loads(path.read_text())
    base, model = configure_profile(path)
    judge = get_judge()
    assert (judge.base_url, judge.model) == (base, model)
    assert judge.max_tokens == profile["request"]["max_tokens"]
    assert judge.structured_output is True
    assert judge.prompt_suffix == profile["request"]["prompt_suffix"]
    assert judge.n_samples == 1  # experiment repeats are NOT in-request self-consistency


def test_conflicting_profile_options_rejected_before_network():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/run_local.py"),
                             "--judge-profile", "missing.json", "--provider", "mock", "--check"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and "cannot be combined" in result.stderr


def test_mock_preflight_needs_no_model_server():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/run_local.py"), "--check"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "mock-judge" in result.stdout and "discovery only" in result.stdout


def test_real_provider_requires_explicit_model_before_network():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/run_local.py"),
                             "--provider", "lmstudio", "--check"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and "--model is required" in result.stderr
