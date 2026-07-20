# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# setup (Python ≥3.10)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# run
.venv/bin/python run.py --demo          # throwaway demo DB, http://localhost:8420
.venv/bin/python run.py --db anota.db   # persistent DB
.venv/bin/python run.py --import-file corpus.jsonl --profile aqb --lang zh-en  # CLI import

# tests (fast, <1 s total)
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_tasks.py::test_undo_reopens_last_submitted -v

# frontend has no build step; syntax-check JS after editing
node --check static/app.js static/review.js static/dash.js

# real LLM judge (any OpenAI-compatible endpoint; default mock needs nothing)
ANOTA_JUDGE=openai ANOTA_JUDGE_BASE_URL=http://localhost:8000/v1 .venv/bin/python run.py --demo

# docker
docker build -t anota . && docker run -p 8420:8420 anota --demo

# regenerate README screenshots (server must be running with --demo)
npm i puppeteer-core && node docs/make_screenshots.js
```

## Architecture

One FastAPI process (`app/main.py` = all routes + static mount) over stdlib `sqlite3`
(`app/db.py`: 9-table DDL, **one connection + one RLock, no ORM — deliberate**). Frontend is
three plain JS files served from `static/`; `app.js` exposes `window.ANOTA` consumed by
`review.js`/`dash.js`. `docs/DESIGN.md` explains every design decision and the roadmap.

Data flow: `importer.py` (runs `lf.run_lfs` once at import → stored in `tasks.lf_flags`)
→ `tasks.py` claim/lease state machine → `annotations` → `quality.py` aggregation
→ `export.py` snapshots. Judge subsystem: `judge.py` (+ pooled runner in `main.py`),
`perturb.py` builds probe batches whose golden truth feeds `quality.judge_golden_calibration`.

## Invariants (violating these breaks the product's core claims)

1. **Batch policy is enforced server-side.** `with_policy()` in `main.py` is the only place
   annotator-facing task payloads are shaped: clean batches (`batches.show_suggestions=0`)
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
6. **Exports hash content only.** `sha256` over canonical JSON excludes export timestamps/
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
