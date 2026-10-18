# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# setup (Python 3.12 is the verified full-project baseline)
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints-runtime.txt

# run
.venv/bin/python run.py --demo          # throwaway demo DB, http://localhost:8420
.venv/bin/python run.py --db anota.db   # persistent DB
.venv/bin/python run.py --import-file corpus.jsonl --profile aqb --lang zh-en  # CLI import

# tests (offline core; optional dependency tests may skip; allow tens of seconds)
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_tasks.py::test_undo_reopens_last_submitted -v

# frontend has no build step; syntax-check JS after editing
node --check static/app.js
node --check static/review.js
node --check static/dash.js
node --check static/speech.js

# real LLM judge (any OpenAI-compatible endpoint; default mock needs nothing)
ANOTA_JUDGE=openai ANOTA_JUDGE_BASE_URL=http://localhost:8000/v1 .venv/bin/python run.py --demo

# docker
docker build -t anota . && docker run -p 8420:8420 anota --demo

# regenerate README screenshots (server must be running with --demo)
npm i puppeteer-core && node docs/make_screenshots.js
```

Windows PowerShell equivalents do not require activating the environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints-runtime.txt
.\.venv\Scripts\python.exe run.py --demo
.\.venv\Scripts\python.exe -m pytest -q
```

Use Python 3.12 for the full project. The core application targets Python ≥3.10,
but some speech preparation/evaluation scripts import `datetime.UTC` (≥3.11);
Python 3.10 full-feature compatibility is not established. ASR, audio analysis,
ML inference and figure tooling have separate optional requirements files.
Default Docker installs only the CPU web dependencies; model inference and
bundle deployment need the optional runtime described in `docs/audio-ml-review-queue.md`.

## Architecture

One FastAPI process (`app/main.py` = all routes + static mount) over stdlib `sqlite3`
(`app/db.py`: nine core tables, `schema_migrations`, and three speech tables —
13 application tables excluding SQLite internals; **one connection + one RLock,
no ORM — deliberate**). Frontend is four plain JS files served from `static/`;
`app.js` exposes `window.ANOTA` consumed by `review.js`, `dash.js` and `speech.js`. `docs/DESIGN.md` explains every design decision and the roadmap.

Data flow: `importer.py` (runs `lf.run_lfs` once at import → stored in `tasks.lf_flags`)
→ `tasks.py` claim/lease state machine → `annotations` → `quality.py` aggregation
→ `export.py` snapshots. Judge subsystem: `judge.py` (+ pooled runner in `main.py`),
`perturb.py` builds probe batches whose golden truth feeds `quality.judge_golden_calibration`.

The repository contains four cooperating modules: `app/` + `static/` (Anota),
`speechprep/` (offline ASR/manifests/cache), `speechqc/` (audio analysis and QC
priority), and `speechrank/` (optional ML ranking). Speech import/audio/revision
logic lives in `app/speech.py`; queue policy and ML/QC fallback live in
`app/speech_queue.py`. Shared runtime priority logic lives in `speechqc/priority.py`;
the web application must not import research scripts. ASR executes separately;
starting the web process must not load ASR/VAD models or require ML dependencies.

`docs/project-map.md` maps the six frozen experiments and related GitHub
repositories. Keep training, service deployment and engine benchmarking in their
existing repositories; cross-repository adapters are planned boundaries, not
implemented integrations.

## Invariants (violating these breaks the product's core claims)

1. **Batch policy is enforced server-side.** `with_policy()` in `main.py` handles
   translation policy; `speech.task_payload()` and `speech_queue` enforce speech
   hint/ranking policy: clean batches (`batches.show_suggestions=0`)
   must not contain `suggestions`/`lf_flags` keys at all. `tasks._task_payload` whitelists
   fields — never add a field to it (or to a claim/undo response) without deciding its
   policy. A test fails if a clean-batch claim response leaks suggestion fields.
2. **`golden_answers` and `is_golden` never reach annotator paths.** Golden truth is
   serialized only by explicit export with `include_golden=true`.
3. **`annotations` and `audit_log` are append-only.** No UPDATE/DELETE, ever. Corrections
   and undo-resubmits are new rows; reads take the latest row per (task, annotator)
   (`quality.latest_annotations`). Undo mutates only `assignments` (the mutable state
   machine). Deliberate consequence: a reaped (lease-expired) assignment is DELETEd, so the
   same annotator may claim that task again — a re-submit is a supersede, not a duplicate.
4. **Server-side validation is the source of truth** (`models.AnnotationPayload`:
   `no_error` exclusive + forces neutral; real error ≠ neutral; critical requires note).
   Client-side checks in `app.js`/`review.js` mirror these for UX only — change both sides.
5. **The judge is a first-pass filter.** `JudgeUnavailable` must never escape as a raw
   exception to callers; annotation flow works with the judge down. `MockJudge` must stay
   fully deterministic (sha256 of task id; no time/random).
6. **Speech revisions are append-only and policy applies to audio hints too.**
   Speech claims expose allowlisted fields. References, local paths, raw confidence
   arrays and ranking evidence must not leak into blind queues/claims. An invalid
   ML candidate makes all eligible hinted rows in the selected queue fall back
   to QC; do not mix ML densities and QC scores.
7. **Exports hash content only.** `sha256` over canonical JSON excludes export timestamps/
   version — same data must always produce the same hash.

## Conventions & gotchas

- Multi-step read-modify-write goes inside `with db.lock:` (see `tasks.py`, routing/judge
  handlers in `main.py`). The SQLite connection is shared across threads
  (`check_same_thread=False`); the lock is the only guard.
- Copied task ids use `::` suffixes (`t012::r3` routing copy, `t005::p2-nega0` probe item).
  Routing candidate queries exclude `id LIKE '%::%'` — keep it that way or probes/copies
  get re-routed.
- Aggregated final labels without a strict majority are flagged `unresolved` and must stay
  quarantined from κ and matrix statistics (`quality.final_label` consumers).
- LF lexicons are language-scoped per `lf.PROFILES` (`en-es`, `zh-en`); numeral word maps
  differ per language (Spanish `once` = 11, English `twice` = 2, zh compound 二十 → 20).
  The tests in `tests/test_lf.py` are the contract — when a lexicon test fails, fix the
  lexicon, not the test.
- `quality.cohen_kappa` is hand-written and pinned to textbook values in tests
  (0.4 on the 2×2; −1.0 on the weighted adjacent swap). Don't swap in a library.
- Judge run progress lives in `app.state.judge_runs` guarded by `progress_lock`; one run
  per batch at a time (409 otherwise).
- Demo data (`data/*.jsonl`) is synthetic and PHI-free by design; planted errors and arms
  are load-bearing for tests and the demo storyline (see `tests/test_importer.py` counts:
  30 tasks / 6 golden / 3 seed annotations) — edit with care.
- UI copy is English; the UI follows system light/dark with an override cycle
  (`localStorage: anota_theme = auto|light|dark`; annotator id under `anota_annotator`).

- Frozen `evals/runs/` evidence and hash-bound `speechrank/features.py` /
  `speechrank/models.py` must retain their original bytes. Respect `.gitattributes`
  LF rules when publishing and verify a clean checkout; changing source hashes
  to accommodate an accidental newline rewrite invalidates the provenance.
- Test counts in frozen reports describe their original checkpoint. Do not replace
  them with a later count or claim current CI/Docker success before those checks run.
- `speechprep/vendor/SOURCES.json` records user-provided reuse with no original
  license file found; do not invent a third-party license or attribution.
