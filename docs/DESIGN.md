# Anota — Design Rationale & Industry Analysis

*Why this tool exists, why it is built the way it is, how it compares to the annotation-tool
landscape, and where it should (and should not) go next. Written 2026-07; market facts carry
that date.*

---

## 1. Positioning: what Anota is and is not

Anota is a **self-hosted, keyboard-first annotation workbench for translation and
interpretation quality assessment**, with the *quality-operations layer built into the data
model* rather than bolted on: batch-level suggestion policy, golden-set isolation, agreement
analytics, adjudication feedback, and versioned exports are first-class server-side concepts.

The core thesis:

> **Annotation *interfaces* are a commodity. Annotation *operations* are not.**
> Every mature platform can render a text pair and collect a label. What decides data quality
> in production is everything around that moment — whether the annotator saw a machine hint
> before forming a judgment, whether the golden set is truly blind, whether disagreement flows
> back into the guideline, whether an exported dataset can be cited and reproduced. Anota
> encodes those operational disciplines as *enforced system behavior*, not as team convention.

What Anota deliberately is **not**: a general-purpose labeling platform (no images, no audio
UI yet, no crowd workforce), not multi-tenant SaaS, and not production-hardened for hundreds
of concurrent annotators. It is an opinionated, small-surface reference implementation of
annotation quality ops — honest about its demo-grade scale, with a designed upgrade path
(§6, §7).

---

## 2. Why this design — decision by decision

### 2.1 Suggestion policy is a *data-model property*, enforced at the API boundary

The single most important design decision. Pre-labeling and machine suggestions measurably
anchor human annotators (automation bias); a golden set collected with hints on screen is
contaminated at birth, and every downstream measurement — judge calibration, annotator
accuracy, IAA — inherits the bias. Mainstream tools treat suggestion display as a per-project
UI toggle; the hint data still ships to the client and discipline depends on configuration
hygiene.

In Anota, `show_suggestions` is a column on `batches`, and the claim endpoint **strips the
fields from the response** for clean batches — judge verdicts and lint flags are not hidden
by CSS; they are absent from the wire. Golden-collection batches are pure by construction;
routing batches carry hints explicitly. The policy survives any frontend bug, browser
extension, or curious annotator opening DevTools.

### 2.2 Append-only annotations + full audit trail

Labels are never updated in place: a correction is a new row, latest-wins per
`(task, annotator)`, with reviewer overrides layered above. Every state transition (claim,
submit, skip, undo, lease expiry, review, export, import) writes an audit row.

Rationale: (a) **auditability** — in regulated domains (healthcare interpretation is
HIPAA-adjacent) "who labeled what, when, under which guideline version" is a hard
requirement, not a nice-to-have; (b) **research value** — supersede-not-overwrite preserves
re-annotation history, enabling intra-rater consistency studies and "which items get
re-judged" analysis for free; (c) **operational safety** — no destructive write path exists
to misuse.

### 2.3 Golden answers are server-side only

Honeypot items are indistinguishable from normal tasks in every annotator-facing payload:
golden truth lives in a separate table that no annotator-path endpoint serializes, and
`is_golden` itself never leaves the server except in explicitly requested exports. Many
platforms leak honeypot identity through metadata fields, distinct styling, or predictable
IDs — which lets annotators learn to detect gold tasks, silently invalidating per-annotator
accuracy tracking. Isolation-by-schema is stronger than isolation-by-convention.

### 2.4 The judge is a first-pass filter, never the final arbiter

LLM-as-judge is treated as an *uncalibrated instrument until proven otherwise*: its verdicts
are suggestions and routing signals, humans arbitrate, and the review tab shows judge and
human side by side with disagreement ranked first. Operationally, judge failure degrades
gracefully — if the endpoint is down, annotation continues untouched and the top bar says so.

Two implementation choices follow: a **deterministic MockJudge** (hash-seeded, derived from
lint signals) makes demos and CI runs reproducible offline; and the OpenAI-compatible client
means any self-hosted vLLM/llama.cpp/commercial endpoint plugs in with two environment
variables — important where translation content cannot leave a compliance boundary.

### 2.5 Programmatic lint at import time (weak-supervision as pre-QA)

Four labeling functions (negation dropped, number mismatch with language-scoped numeral
normalization, untranslated fragments, length-ratio outliers) run once at import and attach
to tasks as evidence-carrying flags. They are Snorkel-style weak signals used *as lint, not
as labels* — surfaced to reviewers and routing, hidden from clean-batch annotators (per 2.1).
Cheap deterministic checks catch a meaningful share of critical translation errors (dosage
numbers, negation) before any human or LLM time is spent.

### 2.6 Keyboard-first, single-decision flow

Borrowed from Prodigy's central insight: annotation throughput is dominated by interaction
cost, and mouse-driven form UIs tax every record. Anota's happy path for a clean record is
two keystrokes (`0`, `Space`); every control has a key; per-record elapsed time is recorded
and surfaced as a first-class ops metric (it also prices future work: cost-per-label comes
from this number).

### 2.7 Three runtime dependencies, SQLite, no ORM — on purpose

The stack is FastAPI + uvicorn + httpx over stdlib `sqlite3`, a static no-build frontend, one
process. This is a considered position, not naivety: the correctness-critical part of an
annotation backend is **task-distribution semantics** (atomic claim, lease expiry, overlap
accounting) — which lives in explicit SQL under one lock and is directly testable — not
object mapping. The payoff is operational: cold start in seconds on any machine, trivially
auditable behavior, and a deployment story (§5) that most competitors structurally cannot
match.

### 2.8 Deterministic, versioned exports

Exports are canonical-JSON snapshots hashed with sha256; identical data always hashes
identically; each export registers `dataset@vN` with its filter and guideline version.
Downstream training or evaluation cites an immutable version, never "the latest file" — the
lineage discipline that data-centric ML teams need for failure forensics ("which batch, which
rubric, whose labels").

---

## 3. Against the existing landscape

### 3.1 Feature-position matrix (2026-07)

| Capability | **Anota** | Label Studio (OSS) | Argilla | Prodigy | doccano | Labelbox / Scale |
|---|---|---|---|---|---|---|
| Self-hosted, data never leaves | ✅ 1 process | ✅ (heavier) | ✅ (5 containers) | ✅ (local) | ✅ | ❌ SaaS (BAA = enterprise $) |
| Suggestion/anchoring policy **enforced server-side per batch** | ✅ core design | ⚠️ per-project UI toggle | ⚠️ suggestions global | ⚠️ recipe-dependent | ❌ | ⚠️ config-level |
| Golden/honeypot isolation by schema | ✅ | ⚠️ enterprise feature | ❌ manual | ❌ manual | ❌ | ✅ (benchmark) |
| Built-in agreement math (Cohen's κ, weighted) | ✅ | ⚠️ enterprise | ❌ export & DIY | ❌ DIY | ❌ | ✅ (consensus) |
| LLM-judge loop w/ confidence routing to humans | ✅ closed loop | ⚠️ ML backend plumbing | ⚠️ partial | ⚠️ custom recipe | ❌ | ⚠️ services |
| Domain analytics (error type × latency regime) | ✅ unique | ❌ | ❌ | ❌ | ❌ | ❌ |
| Deterministic versioned exports (content-hash) | ✅ | ❌ | ❌ | ❌ | ❌ | ⚠️ |
| Audio / image / video annotation | ❌ (roadmap: audio) | ✅ | ⚠️ | ✅ | ❌ | ✅ |
| Multi-tenant / workforce management | ❌ | ⚠️ | ❌ | ❌ | ❌ | ✅ |
| License / cost | MIT-able, free | OSS + paid tiers | OSS (maintenance mode) | ~$390/seat | OSS | $$$ |

⚠️ = possible but requires configuration, plugins, paid tier, or custom code.

### 3.2 The gap Anota actually targets

Three adjacent tool families exist, and none covers this niche:

1. **Generic annotation platforms** (Label Studio, Labelbox, doccano): breadth-first — many
   modalities, generic quality features, quality *ops* left to the team. None knows what a
   latency arm, an MQM severity weight, or a rendition is.
2. **Translation QA tooling** (Xbench, Verifika, QA checkers inside CAT/TMS suites): the
   localization industry's incumbent QA layer is *rule-based batch checking* of terminology
   and consistency — not human annotation collection, not golden sets, not judge calibration.
   MQM scorecards mostly live in spreadsheets.
3. **LLM observability queues** (Langfuse, LangSmith annotation queues): trace-centric —
   built to grade LLM app traces, not to run a corpus-annotation operation with overlap,
   honeypots, and IAA.

Anota sits at the intersection: **corpus-level human QA for translation/interpretation with
the ops discipline of an RLHF data pipeline**. The one genuinely novel analytic — the
**error-type × latency-arm matrix** — exists because the tool assumes its corpus comes from
*simultaneous* translation systems, where the operative business question is "what error
types does low latency buy you?" No horizontal tool ships that view.

### 3.3 What incumbents do better (honest)

Label Studio has modality breadth, a plugin ecosystem, and active maintenance. Prodigy has a
mature recipe ecosystem and active-learning loops. Labelbox/Scale bring workforce,
SLAs, and compliance paperwork. Argilla (despite maintenance mode) has Hugging Face-ecosystem
integration Anota lacks. Anota's bet is narrow depth over breadth — where the six comparison
columns above are the requirements, it wins; where modality breadth or workforce is the
requirement, it should not compete.

---

## 4. Engineering optimizations worth naming

- **Throughput**: 2-keystroke clean-record path; auto-defaults on `no_error`; batch-targeted
  claiming from the dashboard; per-record ms telemetry.
- **Integrity**: policy enforcement at the response-shape level (2.1); client-side validation
  mirrors server rules so annotators never round-trip to learn a rule; submit reentrancy
  guards; lease-based distribution with audited reaping.
- **Correctness under review**: hand-written Cohen's κ (plain + linear-weighted) pinned to
  textbook values in tests; language-scoped numeral lexicons (Spanish *once* = 11, not
  English "once" — a real bug class caught in adversarial review); brace-safe JSON extraction
  from judge output; aggregate labels without consensus are quarantined (`unresolved`) rather
  than silently coerced — they never pollute κ or the matrix.
- **Reproducibility**: MockJudge determinism; content-hashed exports; append-only history.
- **83 tests** across the state machine, lint functions, agreement math, policy leaks
  (a test asserts clean-batch responses contain no suggestion keys), and export determinism.

---

## 5. Deployment analysis

### 5.1 Why deployment is a structural advantage

The runtime is one Python process, three pip dependencies, a SQLite file, and static assets.
Compare the self-hosted competition: Argilla's stack is five containers (server, worker,
PostgreSQL, Elasticsearch, Redis); Langfuse v3 is six (web, worker, Postgres, ClickHouse,
Redis, MinIO); Label Studio wants Postgres + Redis at any real scale. Anota cold-starts in
seconds on a laptop, a VPS, or an air-gapped VM — **for compliance-sensitive language data
(medical interpretation is the motivating case), "runs entirely inside the boundary with
nothing else to operate" is a feature most alternatives cannot offer at this weight.**

### 5.2 Recommended deployment ladder

| Stage | Setup | When |
|---|---|---|
| Solo / evaluation | `python run.py --demo` in a venv | first 10 minutes |
| Real corpus, 1–3 annotators | venv + `--db anota.db`, launchd/systemd unit | pilot projects |
| Team pilot | **Docker** (shipped: `docker build -t anota . && docker run -p 8420:8420 -v anota-data:/data anota`), behind a TLS reverse proxy (Caddy/nginx) with SSO/basic-auth at the proxy | 3–10 annotators, VPC/on-prem |
| Beyond | Postgres backend + SSE + real RBAC (roadmap §6) on ECS/Fargate/K8s | >10 concurrent, org-wide |

Docker **is** worth shipping (and is shipped): image ≈ base `python:3.12-slim` + ~10 MB; it
buys environment reproducibility and the VPC deployment story. Docker-*compose* is
deliberately **not** needed — there are no sidecar services, and that absence is the point.

### 5.3 Platform variants: what is and is not worth building

- **Native desktop (Electron/Tauri)**: not worth it. The browser is the UI; wrapping it adds
  ~200 MB and an update channel for zero capability gain.
- **Single-binary (PyInstaller/shiv)**: cheap nice-to-have for "download and double-click"
  evaluation on annotator laptops; low priority.
- **PyPI package (`pipx install anota`)**: worth doing early — it is the natural distribution
  for the target user (technical data/QA leads).
- **Cross-OS**: already OS-agnostic (pure Python + web; zero native deps).
- **Managed cloud/SaaS**: contradicts the compliance thesis; only meaningful with a
  multi-tenant rewrite. Not a near-term direction.

### 5.4 Honest scale ceiling

SQLite behind one process lock is comfortable to roughly ten concurrent annotators and
low-hundreds-of-thousands of records; dashboard queries are N+1 in places (fine at this
scale, profiled as the first thing to fix beyond it). The ceiling is documented, not hidden,
and the upgrade path (Postgres + row locks, SSE instead of polling) does not require
re-architecting the domain model.

---

## 6. Roadmap

**Near term (weeks)**
1. Reverse-proxy SSO header → real annotator/reviewer identity (drop self-reported IDs).
2. `pipx` packaging; publish the Docker image.
3. Hugging Face `datasets` export target alongside JSONL snapshots.
4. Span-level error marking (ESA-style, per WMT24 practice) as an optional annotation layer.
5. Guideline versioning UI: case notes from adjudication auto-append to a draft next version.

**Mid term (months)**
6. **Audio rendition playback** — turn-level audio alongside transcript for interpretation
   QA. This is the single highest-value feature for the LSP niche and the moment Anota stops
   being text-only.
7. Honeypot rotation scheduling and per-annotator calibration batches (Scale-style
   onboarding flows, self-hosted).
8. IAA drill-down: per-label confusion matrices, per-pair disagreement heatmaps — pointing
   at which *guideline clause* needs work.
9. Continuous uncertainty routing (recurring batches by judge-confidence decile) rather than
   one-shot routing builds.
10. Second automated signal: xCOMET / COMET-QE scores next to the LLM judge, with the same
    calibrate-against-golden discipline.

**Long term**
11. Postgres backend + SSE; multi-project workspaces; plugin registry for labeling functions;
    read-only analytics API for BI tools.

**Non-goals** (stated to stay honest): crowd marketplace, generic CV annotation,
prompt-management platform, multi-tenant SaaS.

---

## 7. Industry lens: economics and realistic prospects

**Market context (2026).** The data-annotation tooling market is in a post-LLM
reconfiguration: value migrated from raw labeling throughput to *quality operations* —
RLHF/preference data, evaluation sets, LLM-judge calibration. Meanwhile the incumbent
managed-labeling giant lost neutrality (Meta's 2025 stake in Scale AI pushed frontier labs
toward Surge/Mercor and toward *bringing quality ops in-house*), and two popular open tools
in this exact niche lost momentum (Argilla in maintenance mode after the Hugging Face
acquisition; Humanloop shut down after the Anthropic acqui-hire). In-house, self-hosted
quality tooling is structurally more attractive in 2026 than it was in 2023.

**Who would actually use this.** (a) LSP quality teams that today run MQM scorecards in
spreadsheets next to a rule-based QA checker; (b) MT/speech-translation teams building
evaluation sets and judge-calibration data; (c) applied-research groups doing human evals who
need overlap/golden/IAA discipline without operating a five-container platform.

**Build-vs-buy economics.** For a 2–10 annotator quality operation, the realistic
alternatives are: enterprise SaaS (five-figure annual + BAA negotiation + data leaves the
boundary), self-hosting a heavyweight OSS platform (real DevOps cost, quality ops still DIY),
or spreadsheets (the actual incumbent, with zero enforcement). A one-process tool that
encodes the ops discipline occupies a genuinely empty quadrant: *low operational cost, high
process rigor*.

**The moat, honestly.** The code is replicable in weeks by any competent team — the durable
value is (a) the *encoded workflow discipline* (anchoring policy, golden blindness,
adjudication feedback as schema, not policy docs), (b) the domain analytics (latency-regime
error economics for simultaneous translation), and (c) whatever corpus/guideline/judgment
assets accumulate inside an instance. That argues for an open-source positioning if it were
productized: distribution and domain credibility, with revenue (if ever) in hosted
compliance, calibration content, and integrations — the standard OSS-tooling play.

**Risks.** Single-maintainer bus factor; demo-grade concurrency ceiling (documented);
LLM-judge dependency shifting under fast-moving model quality; and the perennial risk of
horizontal platforms adding "good-enough" versions of the differentiators. The counter to
all four is the same: stay narrow, stay light, keep the ops discipline the product.
