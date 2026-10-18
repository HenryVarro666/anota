"""Render experiment 03 from complete raw evidence, preserving earlier experiments."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.comparison import correct
from app.severity_study import severity_summary
from scripts.run_severity_study import read_rows
from scripts.render_comparison import COLORS, INK, MUTED, PAPER, LINE, fraction, percent, labels, clean, error_map


def mean(summary, arm, metric):
    return sum(summary["arms"][arm]["passes"][str(p)][metric]["rate"] for p in (1, 2)) / 2


def save(fig, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path.with_suffix(".png"), facecolor=PAPER, dpi=150)
    fig.savefig(path.with_suffix(".svg"), facecolor=PAPER, metadata={"Date": None})
    plt.close(fig)


def policy_figure(run):
    fig = plt.figure(figsize=(16, 9), facecolor=PAPER)
    fig.text(.06, .94, "ANOTA / SEVERITY-V3", size=13, weight="bold", color=COLORS["candidate"])
    fig.text(.06, .858, "Severity is an ordered decision.", size=34, weight="bold", color=INK)
    fig.text(.06, .807, "Start at the top. Use the first applicable level. An error category is not a severity trigger.", size=13, color=MUTED)
    specs = [
        ("01", "CRITICAL", "Changes clinical action or its authorization", "Includes a missing prerequisite, exception, restriction or stopping condition.", "#F8E4DD", "#A54C32"),
        ("02", "MAJOR", "Changes meaning without the critical criterion", "Includes altered administrative steps, documents, locations, times and commitments.", "#F6EEDB", "#86662C"),
        ("03", "MINOR", "A real form error; all relevant meaning stays intact", "Grammar, spelling or punctuation alone; no changed proposition or condition.", "#E6EBF7", "#4A61A8"),
        ("04", "NEUTRAL", "Correct translation or acceptable paraphrase", "Use no_error exclusively. Do not penalize a valid wording choice.", "#DFEFE8", "#237A62"),
    ]
    for i, (number, title, criterion, detail, background, color) in enumerate(specs):
        y = .63 - i * .155
        fig.add_artist(FancyBboxPatch((.06, y), .88, .125, boxstyle="round,pad=0.007,rounding_size=0.012", transform=fig.transFigure, facecolor=background, edgecolor=LINE))
        fig.text(.078, y + .077, number, size=15, weight="bold", color=color)
        fig.text(.12, y + .073, title, size=18, weight="bold", color=color)
        fig.text(.29, y + .077, criterion, size=15, weight="bold", color=INK)
        fig.text(.29, y + .032, detail, size=11, color=MUTED)
    fig.text(.06, .09, "A short omission can be critical. Fluent wording does not reduce the consequence of a missing clinical condition.", size=12, weight="bold", color=INK)
    fig.text(.06, .046, "Unstated context → separate adjudication. Provisional research policy; independent bilingual/domain review is pending.", size=11, color=MUTED)
    save(fig, run / "figures/severity-policy")


def overview(summary, rows, run):
    identity = json.loads((run / "manifest.json").read_text(encoding="utf-8"))["identity"]
    artifact = identity["profiles"]["baseline"].get("model_artifact", {})
    model_name = artifact.get("repository", identity["profiles"]["baseline"]["model"]).split("/")[-1].removesuffix("-GGUF")
    model_name += " " + artifact.get("quantization", "")
    device = identity["provenance"].get("gpu", "device not reported").replace("AMD Radeon(TM) ", "Radeon ").replace(" Graphics", "")
    primary_count = summary["primary_unique_items"]
    first = summary["arms"]["baseline"]["passes"]["1"]
    fig = plt.figure(figsize=(16, 12), facecolor=PAPER)
    fig.text(.055, .953, "ANOTA / EXPERIMENT 03", size=13, weight="bold", color=INK)
    fig.text(.945, .953, "ONE AUTHORITATIVE RUBRIC", size=11, ha="right", color=COLORS["candidate"])
    fig.text(.055, .885, "Conditions are part of the action.", size=34, weight="bold", color=INK)
    fig.text(.055, .843, f"{model_name.strip()} · {device} · {primary_count} new source parents · 2 counterbalanced passes", size=13, color=MUTED)
    cards = [(f"CRITICAL RECALL / {first['critical_severity_recall']['denominator']} CASES", "critical_severity_recall"),
             (f"CONDITION RECALL / {first['condition_critical_recall']['denominator']} CASES", "condition_critical_recall"),
             (f"SEVERITY AGREEMENT / {primary_count} CASES", "severity_accuracy")]
    for i, (title, metric) in enumerate(cards):
        x = .055 + i * .2275
        fig.add_artist(FancyBboxPatch((x, .691), .2075, .106, boxstyle="round,pad=0.008,rounding_size=0.012", transform=fig.transFigure, facecolor="white", edgecolor=LINE))
        fig.text(x + .009, .77, title, size=8.5, weight="bold", color=MUTED)
        fig.text(x + .009, .726, f"{mean(summary, 'baseline', metric):.1%} → {mean(summary, 'candidate', metric):.1%}", size=22, weight="bold", color=INK)
        fig.text(x + .009, .704, "Baseline → candidate · mean of 2 passes", size=8.3, color=MUTED)
    x = .7375
    passed = summary["all_pilot_gates_pass"]
    fig.add_artist(FancyBboxPatch((x, .691), .2075, .106, boxstyle="round,pad=0.008,rounding_size=0.012", transform=fig.transFigure, facecolor="#E2F2EC" if passed else "#FAEBDD", edgecolor=LINE))
    fig.text(x + .009, .77, "PREDECLARED PILOT GATES", size=8.5, weight="bold", color=MUTED)
    fig.text(x + .009, .726, "PASS" if passed else "HOLD", size=26, weight="bold", color=COLORS["candidate"] if passed else "#AF6129")
    fig.text(x + .009, .704, "No automatic default change", size=8.3, color=MUTED)

    fig.text(.055, .635, "01   DETECTION, LABELS AND OVERGRADING", size=12, color=INK, weight="bold")
    fig.text(.055, .608, "Bars: two-pass mean. Dots: each pass, not confidence intervals.", size=10, color=MUTED)
    ax = fig.add_axes([.18, .285, .32, .28], facecolor=PAPER)
    metrics = [("binary_accuracy", "Error / clean"), ("exact_error_types", "Exact categories"),
               ("severity_accuracy", "Severity"), ("critical_precision", "Critical precision")]
    for j, (arm, color) in enumerate(COLORS.items()):
        for i, (metric, _) in enumerate(metrics):
            values = [summary["arms"][arm]["passes"][str(p)][metric]["rate"] for p in (1, 2)]
            y = i + (j - .5) * .31
            if any(value is None for value in values):
                ax.text(.025, y, "n/a", size=9, color=color, va="center")
                continue
            avg = sum(values) / 2
            ax.barh(y, avg, height=.245, color=color, alpha=.85, zorder=2)
            ax.scatter(values, [y, y], s=22, color=color, edgecolor="white", linewidth=.9, zorder=4)
            ax.text(max(values) + .025, y, f"{avg:.1%}", size=9, color=color, va="center")
    ax.set_yticks(range(len(metrics)), [label for _, label in metrics], color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.21)
    ax.set_xticks([0, .25, .5, .75, 1], ["0", "25", "50", "75", "100%"], color=MUTED)
    ax.tick_params(length=0, pad=9)
    ax.grid(axis="x", color=LINE, zorder=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.legend(handles=[Patch(color=c, label=a.title()) for a, c in COLORS.items()], loc="upper left", bbox_to_anchor=(.055, .596), ncol=2, frameon=False, fontsize=9)

    fig.text(.555, .635, "02   END-TO-END REQUEST TIME", size=12, color=INK, weight="bold")
    load_confirmed = (run / "validation/user-load-context.json").exists()
    timing_caption = "Concurrent computation confirmed; uncontrolled desktop load" if load_confirmed else "Primary requests only; warmups and disputed cases excluded"
    fig.text(.555, .608, timing_caption, size=10, color=MUTED)
    ax2 = fig.add_axes([.59, .285, .345, .28], facecolor=PAPER)
    for arm, color in COLORS.items():
        values = sorted(r["trace"]["elapsed_seconds"] for r in rows if r["phase"] == "holdout" and r["arm"] == arm)
        ax2.step([0] + values, [0] + [(i + 1) / len(values) for i in range(len(values))], where="post", color=color, lw=2.4)
    ax2.set_ylim(0, 1.04)
    ax2.set_xlim(left=0)
    ax2.set_yticks([0, .25, .5, .75, 1], ["0", "25", "50", "75", "100%"])
    ax2.set_xlabel("Request wall time (seconds)", color=MUTED, labelpad=5)
    ax2.set_ylabel("Cumulative share", color=MUTED, labelpad=10)
    ax2.tick_params(length=0, pad=8, colors=MUTED)
    ax2.grid(color=LINE)
    for spine in ax2.spines.values():
        spine.set_visible(False)
    b, c = [summary["arms"][a]["pooled_timing"] for a in COLORS]
    fig.text(.555, .216, f"P50   {b['p50']:.2f}s → {c['p50']:.2f}s      P95   {b['p95']:.2f}s → {c['p95']:.2f}s", size=11, color=INK)
    category_delta = mean(summary, "candidate", "exact_error_types") - mean(summary, "baseline", "exact_error_types")
    if category_delta < 0:
        fig.text(.055, .216, f"Exact-category agreement falls {-100 * category_delta:.1f} percentage points.", size=11, weight="bold", color="#A54C32")
    rates = [" · ".join(fraction(summary["arms"][a]["passes"][str(p)]["noncritical_false_critical"]) for p in (1, 2)) for a in COLORS]
    fig.text(.055, .17, f"False critical on major/minor cases, pass 1 · 2:     Baseline {rates[0]}     Candidate {rates[1]}", size=12, weight="bold", color=INK)
    fig.text(.055, .128, f"{primary_count} primary examples ≠ {primary_count * 4} independent cases. {summary['disputed_unique_items']} disputed references were separated before inference.", size=11, color=MUTED)
    fig.text(.055, .097, "Synthetic same-agent reference labels; no independent expert adjudication. All primary failures and outliers remain included.", size=10, color=MUTED)
    fig.text(.055, .069, "Same weights/settings; only the system rubric changes. Timing is an observation under load, not isolated hardware performance.", size=10, color=MUTED)
    fig.text(.055, .033, "SOURCE  evals/runs/" + run.name + "  ·  Frozen inputs, complete traces, separate disputed-case record", size=8.8, color=COLORS["candidate"])
    save(fig, run / "figures/severity-comparison")


def write_report(run, summary, rows, primary, disputed, regression, identity):
    def line_metrics():
        cols = [summary["arms"][arm]["passes"][str(p)] for p in (1, 2) for arm in COLORS]
        output = ["| Metric | Baseline 1 | Candidate 1 | Baseline 2 | Candidate 2 |", "|---|---:|---:|---:|---:|"]
        for field, name in [("valid_output", "Valid output"), ("binary_accuracy", "Error / clean agreement"),
                            ("exact_error_types", "Exact category set"), ("severity_accuracy", "Severity agreement"),
                            ("critical_severity_recall", "Critical recall"), ("condition_critical_recall", "Clinical-condition critical recall"),
                            ("critical_precision", "Critical precision"), ("noncritical_false_critical", "Critical predictions on major/minor errors"),
                            ("clean_false_alarm", "False alarms on clean controls")]:
            output.append("| " + name + " | " + " | ".join(fraction(c[field]) + " (" + percent(c[field]) + ")" for c in cols) + " |")
        for q in ("p50", "p95"):
            output.append("| Request " + q.upper() + " (s) | " + " | ".join(f"{c['latency_seconds'][q]:.3f}" for c in cols) + " |")
        return output
    decision = "PASS: all predeclared pilot gates passed in both primary passes." if summary["all_pilot_gates_pass"] else "HOLD: at least one predeclared pilot gate failed."
    lines = ["# Experiment 03 — unified severity policy", "", "![Severity comparison](figures/severity-comparison.png)", "",
             "**" + decision + "** No automatic default change. Independent bilingual/domain review remains pending.", "",
             "The baseline retains rubric v1. The candidate uses one ordered severity-v3 system rubric; weights, endpoint, schema, output limit, temperature and user suffix are identical. This tests the complete policy revision, not instruction priority or quantization in isolation.", "",
             "## Primary results", "", "24 new synthetic source parents, two counterbalanced passes: 12 critical (six clinical-condition cases), six major, two minor and four clean. Reference labels are provisional and authored by the same agent as the rule before inference.", ""] + line_metrics()
    lines += ["", "Every denominator counts observed primary attempts, including failed outputs. Repeats are dependent; 96 calls are not 96 independent examples. Disputed and historical cases never enter this table.", "",
              "## Predeclared gates", "", "| Gate | Pass 1 | Pass 2 |", "|---|---|---|"]
    for gate in summary["gates"]["1"]:
        lines.append("| " + gate.replace("_", " ") + " | " + " | ".join("PASS" if summary["gates"][str(p)][gate] else "FAIL" for p in (1, 2)) + " |")
    lines += ["", "## Paired wins and losses", "", "Each comparison pairs the same source under the two policies. Failed outputs count as incorrect. Ties include both-correct and both-incorrect cases.", "",
              "| Pass | Endpoint | Candidate wins | Candidate losses | Ties |", "|---|---|---:|---:|---:|"]
    for p in ("1", "2"):
        for endpoint, counts in summary["paired"][p].items():
            lines.append(f"| {p} | {endpoint} | {counts['candidate_wins']} | {counts['candidate_losses']} | {counts['ties']} |")
    lines += ["", "## The single severity policy", "", "![Ordered severity policy](figures/severity-policy.png)", "",
              "[Executed rule](source-snapshot/data/guidelines/severity-v3.md). The source-stated prerequisite or stopping condition is part of the clinical action. Administrative meaning changes remain major; form-only faults remain minor. This is an operational research policy, not full MQM or clinical certification.", "",
              "## Complete primary predictions", "", "![Case map](figures/case-map.png)", "",
              "| ID | Reference | Baseline 1 | Candidate 1 | Baseline 2 | Candidate 2 |", "|---|---|---|---|---|---|"]
    index = {(r["task_id"], r["arm"], r["repeat"]): r for r in rows if r["phase"] == "holdout"}
    for e in primary:
        lines.append("| " + e["id"] + " | " + labels(e["gold"]) + " | " + " | ".join(clean(labels(index[(e["id"], arm, p)]["verdict"])) for p in (1, 2) for arm in COLORS) + " |")
    lines += ["", "## Every critical undergrade or unavailable critical output", ""]
    found = False
    for e in primary:
        if e["gold"]["worst_severity"] != "critical":
            continue
        failures = [index[(e["id"], arm, p)] for p in (1, 2) for arm in COLORS
                    if not index[(e["id"], arm, p)]["verdict"] or index[(e["id"], arm, p)]["verdict"]["worst_severity"] != "critical"]
        if not failures:
            continue
        found = True
        lines += ["### " + e["id"], "", "- Source: " + e["source"], "- Hypothesis: " + e["hypothesis"], "- Provisional reference rationale: " + e["reference_rationale"]]
        for r in failures:
            v = r["verdict"]
            lines.append(f"- {r['arm']} / pass {r['repeat']}: {labels(v)}. " + (v["rationale"] if v else str(r["error"])))
        lines.append("")
    if not found:
        lines += ["Neither arm undergraded or failed a critical primary item in this run. This does not establish zero risk on unseen data.", ""]
    lines += ["## Candidate regressions on any primary endpoint", "", "Includes losing a correct binary, exact-category or severity decision, even if another endpoint improves.", ""]
    count = 0
    for p in (1, 2):
        for e in primary:
            b, c = [index[(e["id"], a, p)]["verdict"] for a in COLORS]
            lost = [field for field in ("binary", "types", "severity") if correct(b, e["gold"], field) and not correct(c, e["gold"], field)]
            if not lost:
                continue
            count += 1
            lines += [f"### {e['id']} / pass {p}: loses {', '.join(lost)}", "", "- Source: " + e["source"], "- Hypothesis: " + e["hypothesis"],
                      "- Reference: " + labels(e["gold"]) + ". " + e["reference_rationale"], "- Baseline: " + labels(b) + ". " + (b["rationale"] if b else "Unavailable"),
                      "- Candidate: " + labels(c) + ". " + (c["rationale"] if c else "Unavailable"), ""]
    if not count:
        lines += ["No candidate losses on these endpoints in this constructed primary set. This is descriptive, not an equivalence test.", ""]
    lines += ["## Disputed references — descriptive, never scored", "", "These four cases were separated before model calls and have no single gold field. Plausible severities below are review alternatives, not model-validated answers. Their raw calls and rationales are retained.", ""]
    for e in disputed:
        lines += ["### " + e["id"], "", "- Source: " + e["source"], "- Hypothesis: " + e["hypothesis"],
                  "- Review alternatives: " + " / ".join(e["plausible_severities"]) + ". " + e["reason"]]
        for arm in COLORS:
            r = next(r for r in rows if r["phase"] == "disputed" and r["task_id"] == e["id"] and r["arm"] == arm)
            v = r["verdict"]
            lines.append("- " + arm.title() + ": " + labels(v) + ". " + (v["rationale"] if v else str(r["error"])))
        lines.append("")
    previous = read_rows(ROOT / "evals/runs/2026-10-17-paired-rubric-v2/attempts.jsonl")
    lines += ["## Known regressions — candidate follow-up only", "", "These cases helped design the rule; they are not held-out evidence. Original labels remain unchanged, including disputed ones. The old candidate output is historical rather than a contemporaneous arm.", "",
              "| ID | Historical reference | Previous candidate | Severity-v3 candidate |", "|---|---|---|---|"]
    for e in regression:
        old = next(r for r in previous if r["task_id"] == e["id"] and r["arm"] == "candidate" and r["repeat"] == 1)
        current = next(r for r in rows if r["phase"] == "development" and r["task_id"] == e["id"])
        lines.append(f"| {e['id']} | {labels(e['gold'])} | {labels(old['verdict'])} | {labels(current['verdict'])} |")
        # Full rationale follows below to keep the table compact.
    lines.append("")
    for r in rows:
        if r["phase"] == "development":
            lines.append("- `" + r["task_id"] + "`: " + (r["verdict"]["rationale"] if r["verdict"] else str(r["error"])))
    lines += ["", "## Latency and output workload", "", "| Arm | Mean input tokens | Mean output tokens | Request P50 | Request P95 | Label repeatability |", "|---|---:|---:|---:|---:|---:|"]
    for arm in COLORS:
        selected = [r for r in rows if r["phase"] == "holdout" and r["arm"] == arm]
        usage = [(r["trace"]["response"] or {}).get("usage", {}) for r in selected]
        means = []
        for field in ("prompt_tokens", "completion_tokens"):
            values = [u.get(field) for u in usage]
            means.append(f"{sum(values) / len(values):.1f}" if values and all(type(v) is int for v in values) else "unavailable")
        m = summary["arms"][arm]
        lines.append(f"| {arm} | {means[0]} | {means[1]} | {m['pooled_timing']['p50']:.3f} s | {m['pooled_timing']['p95']:.3f} s | {fraction(m['repeat_label_agreement'])} |")
    lines += ["", "Latency is client wall time including prefill, generation and validation. All primary outliers stay included. Prefix caching follows server defaults; host load, power and thermals are uncontrolled. Repeatability covers exact type set and severity on valid repeated pairs, not independent samples.", "",
              "## Reproducibility and limits", "",
              "- [Manifest](manifest.json), [raw attempts](attempts.jsonl), [summary](summary.json), [validation](validation.json), [predeclared protocol](source-snapshot/evals/severity-v3-protocol.md).",
              "- [Frozen dispute register](source-snapshot/evals/reviews/disputed-labels-v3.md), [primary fixture](source-snapshot/evals/data/severity_holdout_v3.jsonl), [disputed fixture](source-snapshot/evals/data/severity_disputed_v3.jsonl).",
              "- Both previous rounds and their labels are hash-checked and retained. New metrics must not be compared numerically across different test sets as if they were matched observations.",
              "- The same agent authored references and rule; the apparent gain may partly measure adherence to that policy. No independent reference adjudication or natural-call validation is claimed.",
              "- Label agreement does not score rationale correctness. Explanations and confidence values require separate review; a correct label can accompany an incorrect grammatical or domain claim.",
              "- No model/precision comparison, H100 run, AWS deployment or production capacity test occurred. Existing v1 annotation batches keep their original guideline and the workbench default stays unchanged.",
              "- Source snapshots are pre-inference evidence. `analysis-snapshot/` contains post-run plotting and verification sources; it is not predeclared inference code.", ""]
    if (run / "validation/resource-observation.json").exists():
        lines += ["## Host-load observation after latency drift", "",
                  "A later part of the run produced markedly longer requests. The [single CPU/RAM observation](validation/resource-observation.json) was collected after noticing that drift; it is not a time series, an isolated-load condition, or proof of the cause. No workload was stopped, no completed request was replayed, and no latency outlier was removed. Per-pass latency is reported above. Treat the latency gate as descriptive of this desktop session; a dedicated idle-host run is still needed for performance or capacity conclusions.", ""]
    if (run / "validation/user-load-context.json").exists():
        lines += ["The user [confirmed concurrent computation](validation/user-load-context.json) while the study was running. The workload type and its start/end were not recorded. Do not assume the first pass was idle, attribute individual slow requests to a specific application, or interpret the latency gate as an isolated prompt-speed comparison. This context was recorded after observing drift; the frozen protocol and every observed request remain unchanged.", ""]
    if (run / "interpretation.md").exists():
        lines += ["[Post-run interpretation and remaining adjudication questions](interpretation.md).", ""]
    if (run / "validation/workspace-drift.json").exists():
        lines += ["## Shared workspace and recovery validation", "",
                  "[Workspace drift record](validation/workspace-drift.json) distinguishes the frozen inference snapshot from concurrent speech-module edits. The live `app/models.py` acquired speech-review fields after the evaluation process had imported its modules; the judge constants and annotation schema are AST-equivalent. Actual request bodies are checked against the frozen contract. The existing edits are preserved. Recovery checks use a reconstructed frozen checkout, never overwrite the live workspace, and replay no inference calls.", ""]
        lines += ["The selected source snapshot is not a complete standalone checkout. The first isolated reconstruction [failed on an absent import](validation/recovery-initial-failure.json); supplying the unchanged Git `app/lf.py` dependency allowed all [recovery probes](validation/recovery-check.json) to pass. Its source and commit are recorded separately, not retroactively added to the frozen manifest. The LF mapping is used by the mock judge, not the real-model evaluation path.", ""]
    if (run / "validation/current-workspace-tests.json").exists():
        current = json.loads((run / "validation/current-workspace-tests.json").read_text(encoding="utf-8"))
        lines += [f"The frozen checkpoint passed 173 Windows tests. The later [shared-workspace check](validation/current-workspace-tests.json) ran {current['counts']['tests']} tests with {current['counts']['failures']} failures and {current['counts']['errors']} errors; application files were unchanged during that test command. These CPU checks do not validate H100/AWS serving. Its start/end are also recorded as an additional local workload during evaluation.", ""]
    (run / "report.md").write_text("\n".join(lines), encoding="utf-8")


def update_readme(run, summary, rows):
    relative = run.relative_to(ROOT).as_posix()
    passed = summary["all_pilot_gates_pass"]
    category_delta = mean(summary, "candidate", "exact_error_types") - mean(summary, "baseline", "exact_error_types")
    category_note = (
        f"**Category tradeoff:** mean exact type-set agreement falls by {-100 * category_delta:.1f} percentage points. Passing the severity pilot gates would not establish full taxonomy quality."
        if category_delta < 0 else "Exact category agreement is reported separately from severity; pilot gates do not establish production quality."
    )
    historical = next((r for r in rows if r["phase"] == "development" and r["task_id"] == "h19"), None)
    historical_note = ""
    if historical:
        previous = read_rows(ROOT / "evals/runs/2026-10-17-paired-rubric-v2/attempts.jsonl")
        old = next(r for r in previous if r["task_id"] == "h19" and r["arm"] == "candidate" and r["repeat"] == 1)
        historical_note = (f"**Known critical regression, h19:** previous candidate `{labels(old['verdict'])}` → severity-v3 `{labels(historical['verdict'])}`. "
                           "This missing clinical prerequisite was used to design the rule. Its single follow-up is development evidence, not a held-out gain; the original critical reference is unchanged.")
    lines = ["<!-- SEVERITY_V3_START -->", "## Experiment 03: one severity rule, a new test set", "",
             f"![Unified severity comparison]({relative}/figures/severity-comparison.png)", "",
             "**24 new primary texts · four predeclared disputed texts · two counterbalanced primary passes.**",
             "The candidate replaces the system rubric with one ordered policy: changed clinical action or its prerequisite → critical; other meaning change → major; form-only error → minor; correct translation → neutral. The baseline retains the original rubric. All other request settings are identical.", "",
             "| Metric | Baseline: passes 1 · 2 | Candidate: passes 1 · 2 |", "|---|---:|---:|"]
    for field, name in [("valid_output", "Valid output"), ("critical_severity_recall", "Critical recall"),
                        ("condition_critical_recall", "Clinical-condition critical recall"), ("severity_accuracy", "Severity agreement"),
                        ("exact_error_types", "Exact category set"), ("noncritical_false_critical", "False critical on major/minor errors"),
                        ("clean_false_alarm", "False alarms on clean controls")]:
        values = [" · ".join(fraction(summary["arms"][a]["passes"][str(p)][field]) for p in (1, 2)) for a in COLORS]
        lines.append("| " + name + " | " + " | ".join(values) + " |")
    times = [summary["arms"][a]["pooled_timing"] for a in COLORS]
    lines += ["| Pooled request P50 / P95 | " + " | ".join(f"{t['p50']:.2f} / {t['p95']:.2f} s" for t in times) + " |", "",
              "**" + ("PASS: all predeclared pilot gates passed in both passes." if passed else "HOLD: the candidate did not pass every predeclared gate.") + "** The default is unchanged; independent bilingual/domain reference review is still needed.", "",
              category_note, "",
              historical_note, "",
              "The 96 primary calls represent 24 synthetic source parents. Reference labels and the rubric were authored by the same agent before inference. Four disputed cases have no single gold and are reported separately, never silently removed after seeing predictions. Previous rounds and their labels remain intact; their different datasets are not a direct trend line.", "",
              f"[Full report and every undergrade]({relative}/report.md) · [Raw evidence]({relative}/attempts.jsonl) · [Unified rule](data/guidelines/severity-v3.md) · [Dispute register](evals/reviews/disputed-labels-v3.md) · [Protocol](evals/severity-v3-protocol.md)", "",
              f"<details><summary>Ordered severity rule, complete case map and editable figures</summary>", "",
              f"![Severity policy]({relative}/figures/severity-policy.png)", "", f"![Primary case map]({relative}/figures/case-map.png)", "",
              f"[Comparison SVG]({relative}/figures/severity-comparison.svg) · [Policy SVG]({relative}/figures/severity-policy.svg) · [Case-map SVG]({relative}/figures/case-map.svg)", "", "</details>", "",
              f"[Validation and scope]({relative}/validation.json). Windows CPU checks are recorded separately from actual model calls. H100, AWS, Docker and cross-platform CI are not measured by this experiment.", "", "<!-- SEVERITY_V3_END -->"]
    notes = []
    if (run / "validation/user-load-context.json").exists():
        notes += [f"**Timing context:** the user [confirmed concurrent computation]({relative}/validation/user-load-context.json). Workload duration and intensity were not logged. All outliers remain included; the latency gate is descriptive and does not establish isolated prompt or 890M performance.", ""]
    if (run / "interpretation.md").exists():
        notes += [f"[Interpretation, historical regressions and remaining label questions]({relative}/interpretation.md).", ""]
    lines[-1:-1] = notes
    path = ROOT / "README.md"
    content = path.read_text(encoding="utf-8")
    start, end = "<!-- SEVERITY_V3_START -->", "<!-- SEVERITY_V3_END -->"
    block = "\n".join(lines)
    if start in content:
        a, b = content.index(start), content.index(end) + len(end)
        content = content[:a] + block + content[b:]
    else:
        content = content.replace("<!-- COMPARISON_V2_START -->", block + "\n\n<!-- COMPARISON_V2_START -->", 1)
    path.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    run = Path(args.run).resolve()
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    identity, snapshot = manifest["identity"], run / "source-snapshot"
    for relative, expected in identity["files"].items():
        if hashlib.sha256((snapshot / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen input mismatch: " + relative)
    spec = identity["spec"]
    primary, disputed, regression = [read_rows(snapshot / spec[k]) for k in ("holdout", "disputed", "regression")]
    rows = read_rows(run / "attempts.jsonl")
    summary = severity_summary(rows, primary, disputed, regression, spec["gates"])
    if not summary["complete"] or manifest["status"] != "complete":
        raise ValueError("Never render an incomplete study as final evidence")
    if summary != json.loads((run / "summary.json").read_text(encoding="utf-8")):
        raise ValueError("Raw metrics differ from saved summary")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12, "svg.fonttype": "none", "svg.hashsalt": "anota-severity-v3"})
    overview(summary, rows, run)
    policy_figure(run)
    error_map(primary, rows, run)
    write_report(run, summary, rows, primary, disputed, regression, identity)
    update_readme(run, summary, rows)
    for relative in ("scripts/render_severity_study.py", "scripts/render_comparison.py", "scripts/verify_severity_study.py"):
        target = run / "analysis-snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    files = [p for p in run.rglob("*") if p.is_file() and p.name != "artifact-sha256.json" and "__pycache__" not in p.parts]
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    (run / "artifact-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    print("Rendered comparison, policy, complete case map, report, README and hashes.")


if __name__ == "__main__":
    main()
