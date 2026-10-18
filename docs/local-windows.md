# Local annotation and QA on Windows

This profile runs the existing Anota application with one judge request at a time,
a 120-second request timeout and an explicitly selected model. It does not install
GPU drivers, download weights, or change the AWS projects.

## Python and optional modules

Use **Python 3.12** for the full project; this is the locally verified version and
the configured CI target. The core annotation application targets Python ≥3.10,
but some speech preparation and experiment scripts use `datetime.UTC` (≥3.11).
Full speech/ML compatibility on Python 3.10 has not been verified.

The web application uses `requirements.txt` with `constraints-runtime.txt`.
Offline ASR, QC analysis and ML ranking use separate optional requirements:

| Task | Dependency file | Runs inside the web process? |
| --- | --- | --- |
| Core translation/speech review | `requirements.txt` | Yes; model-free baseline |
| Offline ASR preparation | `requirements-speech.txt` | No |
| Offline QC experiment/analysis | `requirements-audioqc-eval.txt` | No |
| Reviewed ML queue inference | `requirements-speechrank-runtime.txt` | Optional, only when ranking is requested |
| Full ML research tooling | `requirements-speechrank.txt` | No training in the web process |

See the [project map](project-map.md) for the four modules and repository boundaries.

## Start the offline application

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints-runtime.txt
.\.venv\Scripts\python.exe scripts/run_local.py
```

Open `http://127.0.0.1:8420`. The default is a temporary database containing the
original 30 synthetic tasks and mock suggestions. It is removed on graceful exit.
Use `--db anota-local.db` for an initially empty persistent workspace and the existing
`run.py --import-file` workflow to import a batch. Export demo results before exiting.

## Connect a local model

Enable the server in LM Studio, load a chat model and copy its exact ID from
`GET http://127.0.0.1:1234/v1/models`. Then:

```powershell
.\.venv\Scripts\python.exe scripts/run_local.py --provider lmstudio --model "YOUR-EXACT-MODEL-ID" --check
.\.venv\Scripts\python.exe scripts/run_local.py --provider lmstudio --model "YOUR-EXACT-MODEL-ID"
```

To use the **exact measured Qwen configuration**, load Qwen3-14B Q4_K_M with
identifier `anota-qwen3-14b-q4km`, context 4096 and one parallel slot, then run:

```powershell
.\.venv\Scripts\python.exe scripts/run_local.py --judge-profile evals/profiles/local-lmstudio.json --check
.\.venv\Scripts\python.exe scripts/run_local.py --judge-profile evals/profiles/local-lmstudio.json
```

This applies the same prompt suffix, JSON schema and 256-token output budget as
the [real evaluation](../evals/README.md). Demo seed verdicts remain labeled mock
until you run the real judge on a batch; connecting a model does not relabel them.

For an already running Ollama server, use `--provider ollama` and its exact model
tag. Discovery uses port 11434. Only one server is needed. A passing `--check`
confirms model discovery, not generation, output validity or judge quality.

On an HX 370 / Radeon 890M, start with one quantized 8–14B class text model,
one request and a short context. Compare quality before trying a larger model.
The 48GB GPU reservation changes capacity; it does not establish throughput.
Anota itself runs on the CPU. Acceleration belongs to the separate model server.

The launcher overrides the judge worker count, timeout and sample count only in its
own process. To tune those individually, launch the original `run.py` with
`ANOTA_JUDGE_WORKERS`, `ANOTA_JUDGE_TIMEOUT` and `ANOTA_JUDGE_SAMPLES` set yourself.
The worker budget is shared across batches within one application process.
The timeout is an HTTP I/O timeout, not a hard deadline for an entire batch.

## Measure quality before using suggestions

1. Build a synthetic probe from the demo batch.
2. Run the real judge on that probe; demo seed results remain marked as mock.
3. Inspect golden agreement, critical-error recall and false alarms. A tiny probe
   validates plumbing; it is not a deployment-quality evaluation dataset.
4. Keep human annotation blinded in clean batches and route disagreements to review.
5. Export a content-hashed snapshot. Save the model ID, weight revision/hash,
   quantization, prompt/rubric revision, context, server version and hardware next to it.

Malformed labels, contradictory labels and invalid scores now fail the judge request.
They are never silently converted to `no_error`. An HTTP 503 or an unavailable judge
leaves manual annotation usable and does not overwrite previous judgments.
The health endpoint checks authenticated HTTP reachability; it is not a generation test.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pip install pytest==9.1.1
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
node --check static/app.js
node --check static/review.js
node --check static/dash.js
node --check static/speech.js
git diff --check
```

Run each JavaScript check separately: `node --check` checks one entry point per
invocation. The full regression suite takes tens of seconds on the local machine;
optional dependency tests can skip in a core-only environment. Real ASR, model
loading and Docker require their own checks.

The tests use synthetic data and provider stubs. No local-model latency or quality
claim follows from their passing. Separately, the committed
[LM Studio pilot](../evals/runs/2026-10-17-lmstudio-qwen3-14b-q4km/report.md)
contains real requests, responses, quality scores and timings.

References: [LM Studio server](https://lmstudio.ai/docs/developer/core/server),
[Ollama compatibility](https://docs.ollama.com/openai).

## GitHub development workflow

Install Git for Windows and GitHub CLI, then authenticate from PowerShell:

```powershell
git --version
gh --version
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
```

Open this repository root in Codex and GitHub Desktop (the `anota` folder, not
its parent). Keep changes on a feature branch; inspect `git status` and the diff,
run the relevant tests, and commit files by task before pushing. A successful
local test run does not establish remote Actions or Docker success; inspect
the pushed commit's checks on GitHub. Repository data, caches and `.env` files
belong in their ignored local locations.
