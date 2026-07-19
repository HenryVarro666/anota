# PropioQA Workbench

A self-hosted, keyboard-first translation-QA annotation workbench.
FastAPI + SQLite + vanilla JS — no ORM, no build chain, three runtime deps.

**Demo-grade by design, with production notes inline. All bundled data is synthetic
(EN→ES medication instructions written for this demo). No PHI anywhere.**

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --demo        # → http://localhost:8420
```

`--demo` boots a throwaway DB with 30 synthetic tasks (12 planted errors), a golden
set, a second annotator's labels, and pre-computed mock-judge verdicts.

## What it embodies (one lesson per platform)

| Lesson from | Mechanism here |
|---|---|
| Prodigy | keyboard-first flow, per-record timing |
| Argilla | suggestions vs responses; task distribution with lease |
| Snorkel | labeling functions as import-time lint |
| Labelbox | consensus (overlap) + benchmark (golden) quality modes |
| Scale | reviewer workflow, QA rubric as single source of truth |
| Humanloop | judge/eval loop feeding human feedback back to data |

The one non-negotiable design rule: **batch policy is enforced server-side** —
a golden-collection batch's claim response never even contains judge/LF fields
(anchoring discipline), while routing batches carry them explicitly.

## Real judge

```bash
PROPIOQA_JUDGE=openai PROPIOQA_JUDGE_BASE_URL=http://localhost:8000/v1 \
  .venv/bin/python run.py --demo
```
Any OpenAI-compatible endpoint works. If the judge is down, annotation is untouched —
the judge is a first-pass filter, never the final arbiter.

## 5-minute demo script

1. **Annotate** (2 min): start as `chao`, label t001 with `0 Space` (clean),
   t002 by hand — negation dropped, `g v→critical, 2, ⇧4, x note, Space`.
   Point out: no machine hints on screen, and why (anchoring).
2. **Dashboard** (1 min): error×arm matrix — low-latency arm degrades to
   omission/negation errors; golden accuracy per annotator; κ panel.
3. **Routing loop** (2 min): Build routing batch from lowest judge confidence →
   claim from it as `chao` → suggestion chips (MOCK-labeled) now visible →
   Review tab → overturn one with a case note that feeds the guideline.

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Production upgrade path (deliberately not built)

SSO/RBAC instead of self-reported ids · Postgres + row locks at >10 annotators ·
SSE instead of 5s polling · object storage for audio · honeypot rotation scheduling ·
per-language golden sets · Argilla/Label Studio as the UI layer with this API as
the quality/routing brain.
