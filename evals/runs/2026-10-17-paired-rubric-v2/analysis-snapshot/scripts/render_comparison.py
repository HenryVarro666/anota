"""Render the paired study from raw evidence; preserve all original baseline assets."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Patch
from matplotlib.colors import ListedColormap

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.comparison import comparison_summary

INK, MUTED, PAPER, LINE = "#182E40", "#647687", "#F5F7FA", "#DDE5EC"
COLORS = {"baseline": "#6174CF", "candidate": "#007F78"}
TYPE_NAMES = {"no_error": "Clean", "negation_polarity": "Polarity", "number_unit": "Number",
              "terminology": "Term", "omission": "Omission", "mistranslation": "Meaning",
              "addition": "Addition", "grammar": "Grammar", "punctuation": "Punctuation"}


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def fraction(value):
    return f"{value['numerator']}/{value['denominator']}"


def percent(value):
    return f"{value['rate']:.1%}" if value["rate"] is not None else "n/a"


def clean(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def labels(verdict):
    if verdict is None: return "UNAVAILABLE"
    return ", ".join(verdict["error_types"]) + " / " + verdict["worst_severity"]


def save_figure(fig, run, stem):
    out = run / "figures" / stem
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out.with_suffix(".png"), facecolor=PAPER, dpi=150)
    fig.savefig(out.with_suffix(".svg"), facecolor=PAPER, metadata={"Date": None})
    plt.close(fig)
    return [out.with_suffix(".png"), out.with_suffix(".svg")]


def mean_metric(summary, arm, metric):
    return sum(summary["arms"][arm]["passes"][str(p)][metric]["rate"] for p in (1, 2)) / 2


def overview(summary, records, run):
    fig = plt.figure(figsize=(16, 11), facecolor=PAPER)
    fig.text(.055, .953, "ANOTA / EXPERIMENT 02", size=13, weight="bold", color=INK)
    fig.text(.945, .953, "PAIRED PROMPT COMPARISON", size=11, ha="right", color=COLORS["candidate"])
    fig.text(.055, .88, "One model. Two judging policies.", size=34, weight="bold", color=INK)
    fig.text(.055, .838, "Qwen3-14B Q4_K_M · Radeon 890M · 24 novel source parents · 2 counterbalanced passes", size=14, color=MUTED)
    card_specs = [("EXACT CATEGORY AGREEMENT", "exact_error_types"), ("SEVERITY AGREEMENT", "severity_accuracy"),
                  ("CRITICAL SEVERITY RECALL", "critical_severity_recall")]
    for i, (title, metric) in enumerate(card_specs):
        x = .055 + i * .2275
        fig.add_artist(FancyBboxPatch((x, .677), .2075, .115, boxstyle="round,pad=0.008,rounding_size=0.012",
                                     transform=fig.transFigure, facecolor="white", edgecolor=LINE, zorder=0))
        fig.text(x+.009, .763, title, size=8.7, color=MUTED, weight="bold")
        a, b = [mean_metric(summary, arm, metric) for arm in COLORS]
        fig.text(x+.009, .717, f"{a:.1%} → {b:.1%}", size=22, color=INK, weight="bold")
        fig.text(x+.009, .691, "Baseline → candidate · mean of 2 passes", size=8.3, color=MUTED)
    x = .055 + 3 * .2275
    passed = summary["all_pilot_gates_pass"]
    fig.add_artist(FancyBboxPatch((x, .677), .2075, .115, boxstyle="round,pad=0.008,rounding_size=0.012",
                                 transform=fig.transFigure, facecolor="#E2F2EC" if passed else "#FAEBDD", edgecolor=LINE, zorder=0))
    fig.text(x+.009, .763, "PREDECLARED PILOT GATES", size=8.7, color=MUTED, weight="bold")
    fig.text(x+.009, .717, "PASS" if passed else "HOLD", size=26, weight="bold", color=COLORS["candidate"] if passed else "#AF6129")
    fig.text(x+.009, .691, "No automatic production promotion", size=8.3, color=MUTED)

    fig.text(.055, .616, "01   QUALITY, WITH REPEATS VISIBLE", size=12, color=INK, weight="bold")
    fig.text(.055, .586, "Bars: mean of two passes. Dots: individual passes, not confidence intervals.", size=9.4, color=MUTED)
    ax = fig.add_axes([.178, .235, .32, .315], facecolor=PAPER)
    metrics = [("binary_accuracy", "Error / clean"), ("exact_error_types", "Exact categories"),
               ("severity_accuracy", "Severity"), ("critical_severity_recall", "Critical recall")]
    for j, (arm, color) in enumerate(COLORS.items()):
        for i, (metric, _) in enumerate(metrics):
            y = i + (j-.5)*.31
            values = [summary["arms"][arm]["passes"][str(p)][metric]["rate"] for p in (1, 2)]
            avg = sum(values)/2
            ax.barh(y, avg, height=.245, color=color, alpha=.85, zorder=2)
            ax.scatter(values, [y, y], marker="o", s=22, color=color, edgecolor="white", linewidth=.9, zorder=4)
            ax.text(max(values)+.025, y, f"{avg:.1%}", va="center", size=9, color=color)
    ax.set_yticks(range(len(metrics)), [label for _, label in metrics], color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.21)
    ax.set_xticks([0, .25, .5, .75, 1], ["0", "25", "50", "75", "100%"], color=MUTED)
    ax.tick_params(length=0, pad=9)
    ax.grid(axis="x", color=LINE, zorder=0)
    for spine in ax.spines.values(): spine.set_visible(False)
    fig.legend(handles=[Patch(color=c, label=arm.title()) for arm,c in COLORS.items()],
               loc="upper left", bbox_to_anchor=(.055,.575), frameon=False, ncol=2, fontsize=9)

    fig.text(.555, .616, "02   END-TO-END REQUEST TIME", size=12, color=INK, weight="bold")
    fig.text(.555, .586, "Warmups + development excluded; all holdout outliers retained", size=9.4, color=MUTED)
    ax2 = fig.add_axes([.59, .235, .345, .315], facecolor=PAPER)
    for arm, color in COLORS.items():
        values = sorted(r["trace"]["elapsed_seconds"] for r in records if r["phase"] == "holdout" and r["arm"] == arm)
        ax2.step([0]+values, [0]+[(i+1)/len(values) for i in range(len(values))], where="post", color=color, lw=2.4)
    ax2.set_ylim(0,1.04)
    ax2.set_xlim(left=0)
    ax2.set_yticks([0,.25,.5,.75,1], ["0", "25", "50", "75", "100%"])
    ax2.set_xlabel("Request wall time (seconds)", color=MUTED, labelpad=4)
    ax2.tick_params(length=0, pad=8, colors=MUTED)
    ax2.grid(color=LINE)
    for spine in ax2.spines.values(): spine.set_visible(False)
    b, c = [summary["arms"][arm]["pooled_timing"] for arm in COLORS]
    fig.text(.555,.163,f"P50   {b['p50']:.2f}s → {c['p50']:.2f}s      P95   {b['p95']:.2f}s → {c['p95']:.2f}s",size=11,color=INK)
    fig.text(.055,.125,"96 holdout requests ≠ 96 independent examples. The source-parent count is 24.",size=12,weight="bold",color=INK)
    fig.text(.055,.093,"Provisional synthetic labels authored before inference by the same agent; independent bilingual expert adjudication is still needed.",size=10,color=MUTED)
    fig.text(.055,.068,"Same model, context, schema and token budget. Candidate changes prompt/rubric clarification; no weight or quantization attribution.",size=10,color=MUTED)
    fig.text(.055,.029,"SOURCE  evals/runs/"+run.name+"  ·  Frozen order, complete raw traces and every regression included",size=8.8,color=COLORS["candidate"])
    return save_figure(fig, run, "comparison")


def error_map(examples, records, run):
    rows = [r for r in records if r["phase"] == "holdout"]
    index = {(r["task_id"],r["arm"],r["repeat"]):r for r in rows}
    columns = [("baseline",1),("baseline",2),("candidate",1),("candidate",2)]
    colors = ["#CDE9DD", "#DCE1F7", "#F5DBBA", "#EABFD0", "#667788"]
    names = ["Match", "Type only", "Severity only", "Both", "Unavailable"]
    matrix = []
    for e in examples:
        row=[]
        for arm,p in columns:
            v=index[(e["id"],arm,p)]["verdict"]
            if v is None: code=4
            else:
                t=set(v["error_types"])!=set(e["gold"]["error_types"])
                s=v["worst_severity"]!=e["gold"]["worst_severity"]
                code=int(t)+2*int(s)
            row.append(code)
        matrix.append(row)
    fig=plt.figure(figsize=(14,14),facecolor=PAPER)
    fig.text(.06,.953,"ANOTA / ITEM-LEVEL EVIDENCE",size=13,color=INK,weight="bold")
    fig.text(.06,.9,"Every case stays in view.",size=31,color=INK,weight="bold")
    fig.text(.06,.862,"Disagreements with the frozen reference. Green = both category and severity match.",size=12,color=MUTED)
    ax=fig.add_axes([.35,.15,.59,.655])
    ax.imshow(matrix,cmap=ListedColormap(colors),vmin=-.5,vmax=4.5,aspect="auto",interpolation="nearest")
    ax.set_xticks(range(4),["Baseline · 1","Baseline · 2","Candidate · 1","Candidate · 2"],color=INK)
    ax.xaxis.tick_top()
    ax.set_yticks(range(len(examples)),[e["id"]+"  ·  "+TYPE_NAMES[e["gold"]["error_types"][0]]+" / "+e["gold"]["worst_severity"] for e in examples],color=INK)
    ax.tick_params(length=0,pad=10,labelsize=10)
    ax.set_xticks([i-.5 for i in range(5)],minor=True)
    ax.set_yticks([i-.5 for i in range(len(examples)+1)],minor=True)
    ax.grid(which="minor",color=PAPER,linewidth=3)
    ax.tick_params(which="minor",length=0)
    for i,row in enumerate(matrix):
        for j,code in enumerate(row): ax.text(j,i,names[code],ha="center",va="center",size=9,color="white" if code==4 else INK)
    for spine in ax.spines.values(): spine.set_visible(False)
    fig.legend(handles=[Patch(color=c,label=n) for c,n in zip(colors,names)],loc="lower center",bbox_to_anchor=(.5,.09),frameon=False,ncol=5,fontsize=10)
    fig.text(.06,.06,"Type-only disagreement may reflect redundant tagging; severity-only disagreement may reflect the rubric or a reference ambiguity.",size=9.4,color=MUTED)
    fig.text(.06,.035,"No failed case or reference label was removed after seeing predictions. See report.md for text, predictions and rationales.",size=9.4,color=MUTED)
    return save_figure(fig,run,"case-map")


def write_report(run, summary, holdout, development, records):
    lines=["# Paired Qwen rubric experiment", "", "![Paired quality and latency](figures/comparison.png)", "", "Two prompts, identical Qwen3-14B Q4_K_M weights and runtime. 24 new synthetic source parents; two passes.","",
           "The reference labels are provisional and agent-authored before inference. They are not independently expert-adjudicated. This study measures adherence to an explicit label policy, not clinical certification.","",
           "## Holdout results", "", "| Metric | Baseline 1 | Candidate 1 | Baseline 2 | Candidate 2 |", "|---|---:|---:|---:|---:|"]
    cols=[summary["arms"][arm]["passes"][str(p)] for p in (1,2) for arm in COLORS]
    for field,title in [("valid_output","Valid output"),("binary_accuracy","Error/clean agreement"),("exact_error_types","Exact type set"),
                        ("severity_accuracy","Severity agreement"),("critical_severity_recall","Critical severity recall"),("clean_false_alarm","False alarms on clean controls")]:
        lines.append("| "+title+" | "+" | ".join(f"{fraction(c[field])} ({percent(c[field])})" for c in cols)+" |")
    for key in ("p50","p95"):
        lines.append("| Request "+key.upper()+" (s) | "+" | ".join(f"{c['latency_seconds'][key]:.3f}" for c in cols)+" |")
    lines += ["", "Each column has 24 examples, not 48 independent observations. Binary detection and exact taxonomy are different endpoints.","",
              "## Predeclared gates", "", "**"+("All pilot gates passed." if summary["all_pilot_gates_pass"] else "HOLD: at least one pilot gate failed. Keep the baseline default.")+"**", "",
              "| Gate | Pass 1 | Pass 2 |", "|---|---|---|"]
    for key in summary["gates"]["1"]:
        lines.append("| "+key.replace("_"," ")+" | "+" | ".join("PASS" if summary["gates"][str(p)][key] else "FAIL" for p in (1,2))+" |")
    lines += ["", "These are small-pilot gates, not production acceptance. No automatic deployment or model switch occurred.","",
              "## Paired wins and losses", "", "Each comparison is the same source item under both prompts; unavailable outputs count as incorrect.","",
              "| Pass | Endpoint | Candidate wins | Candidate losses | Ties |", "|---|---|---:|---:|---:|"]
    for p in ("1","2"):
        for field,c in summary["paired"][p].items():
            lines.append(f"| {p} | {field} | {c['candidate_wins']} | {c['candidate_losses']} | {c['ties']} |")
    lines += ["", "## Timing workload", "", "| Arm | Mean prompt tokens | Mean completion tokens | Pooled request P50 | Pooled request P95 |", "|---|---:|---:|---:|---:|"]
    for arm in COLORS:
        selected=[r for r in records if r["phase"]=="holdout" and r["arm"]==arm]
        usage=[r["trace"]["response"].get("usage",{}) for r in selected if r["trace"]["response"]]
        means=[]
        for field in ("prompt_tokens","completion_tokens"):
            values=[u.get(field) for u in usage]
            means.append(f"{sum(values)/len(values):.1f}" if values and all(type(v) is int for v in values) else "unavailable")
        timing=summary["arms"][arm]["pooled_timing"]
        lines.append(f"| {arm} | {means[0]} | {means[1]} | {timing['p50']:.3f} s | {timing['p95']:.3f} s |")
    lines += ["", "The candidate changes both instruction content and input length. This is not an equal-token-budget prompt ablation, despite equal maximum output tokens. Pooled timing describes requests, not independent accuracy samples."]
    lines += ["", "Label repeatability (both exact types and severity, valid pairs): " + "; ".join(
        arm + " " + fraction(summary["arms"][arm]["repeat_label_agreement"]) for arm in COLORS) + ".", "",
        "## Severity direction (exploratory diagnostic)", "",
        "These post-run diagnostics use the unchanged provisional labels; they were not additional promotion gates.", "",
        "| Pass | Arm | Critical predictions on noncritical error items | Noncritical predictions on critical items |", "|---|---|---:|---:|"]
    gold_by_id={e["id"]:e["gold"] for e in holdout}
    for p in (1,2):
        for arm in COLORS:
            selected=[r for r in records if r["phase"]=="holdout" and r["repeat"]==p and r["arm"]==arm]
            noncritical=[r for r in selected if gold_by_id[r["task_id"]]["worst_severity"] not in ("neutral","critical")]
            critical=[r for r in selected if gold_by_id[r["task_id"]]["worst_severity"]=="critical"]
            over=sum(r["verdict"] is not None and r["verdict"]["worst_severity"]=="critical" for r in noncritical)
            under=sum(r["verdict"] is not None and r["verdict"]["worst_severity"]!="critical" for r in critical)
            lines.append(f"| {p} | {arm} | {over}/{len(noncritical)} | {under}/{len(critical)} |")
    lines += ["", "Unavailable outputs are reported in validity and critical recall, not counted as a specific severity prediction here. Clean false alarms in the main table are a different measure from overgrading an already erroneous translation."]
    lines += ["", "No significance test treats repeated outputs as independent samples. This small, purpose-built set is descriptive.","",
              "## Complete holdout predictions", "", "![Every item and both passes](figures/case-map.png)", "", "Source and reference rationales are in [the frozen fixture](source-snapshot/evals/data/holdout_v2.jsonl).", "",
              "| Example | Reference | Baseline 1 | Candidate 1 | Baseline 2 | Candidate 2 |", "|---|---|---|---|---|---|"]
    indexed={(r["task_id"],r["arm"],r["repeat"]):r for r in records if r["phase"]=="holdout"}
    for e in holdout:
        lines.append("| `"+e["id"]+"` | "+labels(e["gold"])+" | "+" | ".join(clean(labels(indexed[(e["id"],arm,p)]["verdict"])) for p in (1,2) for arm in COLORS)+" |")
    lines += ["", "## Every candidate regression against the baseline", "", "This includes any item that loses a correct type set, severity, or binary decision, even if another endpoint improves.", ""]
    from app.comparison import correct
    for p in (1,2):
        for e in holdout:
            b,c=[indexed[(e["id"],arm,p)]["verdict"] for arm in COLORS]
            lost=[f for f in ("binary","types","severity") if correct(b,e["gold"],f) and not correct(c,e["gold"],f)]
            if not lost: continue
            lines += [f"### {e['id']} / pass {p}: loses {', '.join(lost)}", "", f"- Source: {e['source']}",f"- Hypothesis: {e['hypothesis']}",
                      f"- Reference: {labels(e['gold'])}. Rationale: {e['reference_rationale']}",
                      f"- Baseline: {labels(b)}. {b['rationale'] if b else 'Unavailable.'}",
                      f"- Candidate: {labels(c)}. {c['rationale'] if c else 'Unavailable.'}", ""]
    lines += ["## Development follow-up (exploratory)", "", "This revisits the old 20 texts after the new holdout. Original labels are unchanged. The baseline is historical, so this is not the paired holdout result.","",
              "| Metric | Original baseline pass 1 | Original baseline pass 2 | Candidate follow-up |", "|---|---:|---:|---:|"]
    old=json.loads((ROOT/"evals/runs/2026-10-17-lmstudio-qwen3-14b-q4km/summary.json").read_text())
    for field in ("binary_accuracy","exact_error_types","severity_accuracy","critical_severity_recall"):
        values=[old["passes"][str(p)][field] for p in (1,2)]+[summary["development_candidate"][field]]
        lines.append("| "+field+" | "+" | ".join(fraction(v)+" ("+percent(v)+")" for v in values)+" |")
    lines += ["", "The two original semantic misses:", ""]
    for id_ in ("t003::p2-omis1","t008::p2-term2"):
        row=next(r for r in records if r["phase"]=="development" and r["task_id"]==id_)
        v=row["verdict"]
        lines.append(f"- `{id_}` → {labels(v)}. {v['rationale'] if v else row['error']}")
    lines += ["", "## Evidence and interpretation limits", "",
              "- [Manifest](manifest.json): frozen inputs, full request order, model hash and runtime. Source snapshots retain executed bytes.",
              "- [Raw requests/responses](attempts.jsonl), [machine-readable summary](summary.json), [protocol](../../comparison-v2.md), [development label audit](../../reviews/pilot-v1-audit.md).",
              "- [Post-run interpretation](../../reviews/paired-v2-interpretation.md) separates semantic detection, category convention, severity and reference uncertainty. Post-run analysis sources are retained separately in `analysis-snapshot/`; they were not predeclared model inputs.",
              "- Every arm uses the same model and quantization; this experiment cannot attribute residual errors to Q4 or model weights.",
              "- Strict-category gains can come from enforcing a narrower annotation policy. They are not automatically semantic gains.",
              "- Timing includes prefill and completion, defaults to server prefix caching, retains outliers, and has no thermal/power isolation.",
              "- All clinical phrases are invented evaluation examples, not patient data or clinical instructions for use.",
              "- Neither H100 nor AWS has been run. Candidate remains opt-in; preserve this holdout before designing another experiment.", ""]
    path=run/"report.md"
    path.write_text("\n".join(lines),encoding="utf-8")
    return path


def update_readme(summary, run):
    validation_path = run / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.exists() else {}
    checkpoint = validation.get("frozen_evaluation_checkpoint", {})
    validation_text = (
        f"Validation at the frozen evaluation checkpoint: **{checkpoint['tests_passed']} Windows tests passed**. "
        f"See [validation evidence]({run.relative_to(ROOT).as_posix()}/validation.json) for scope and later checks."
        if checkpoint.get("tests_passed") else "No test-validation evidence has been recorded for this run yet."
    )
    relative=run.relative_to(ROOT).as_posix()
    lines=["<!-- COMPARISON_V2_START -->", "## Experiment 02: frozen holdout comparison", "",
           f"![Paired rubric comparison]({relative}/figures/comparison.png)", "",
           "**24 new EN→ES source parents · two counterbalanced passes · identical Qwen3-14B Q4_K_M weights.**",
           "The candidate changes only the prompt suffix: proposition checks, minimal specific labels and consequence-based severity.",
           "Both arms see the same items; original baseline artifacts and labels remain intact.", "",
           "| Metric | Baseline: passes 1 / 2 | Candidate: passes 1 / 2 |", "|---|---:|---:|"]
    for field,title in [("valid_output","Valid responses"),("binary_accuracy","Error / clean agreement"),
                        ("exact_error_types","Exact type-set agreement"),("severity_accuracy","Severity agreement"),
                        ("critical_severity_recall","Critical severity recall"),("clean_false_alarm","Clean false alarms")]:
        entries=[" · ".join(fraction(summary["arms"][arm]["passes"][str(p)][field]) for p in (1,2)) for arm in COLORS]
        lines.append("| "+title+" | "+" | ".join(entries)+" |")
    times=[summary["arms"][arm]["pooled_timing"] for arm in COLORS]
    lines.append("| Pooled request P50 / P95 | "+" | ".join(f"{t['p50']:.2f} / {t['p95']:.2f} s" for t in times)+" |")
    decision=("The candidate passed the small-pilot gates; it remains opt-in pending independent expert review."
              if summary["all_pilot_gates_pass"] else "**HOLD: the candidate did not pass all predeclared gates. The baseline remains the default.**")
    lines += ["",decision,"",
              "The exploratory revisit detects both earlier misses (a missing time span and medication→supplement), but still grades both minor. On the new holdout, a missing clinical-action prerequisite is downgraded from critical to major in both candidate passes.","",
              "Exact-label gains may come from a clearer annotation convention, not improved semantic detection. Severity references are provisional.",
              "The same agent authored the new texts, reference labels and candidate before inference; independent bilingual adjudication is still needed.",
              "The 96 holdout calls represent **24 source parents**, not 96 independent examples. All regressions and latency outliers are retained.","",
              f"[Full report, paired wins/losses and regressions]({relative}/report.md) · [raw attempts]({relative}/attempts.jsonl) · [frozen protocol](evals/comparison-v2.md) · [label diagnosis](evals/reviews/pilot-v1-audit.md) · [interpretation and next experiment](evals/reviews/paired-v2-interpretation.md)","",
              "A [two-reviewer blind batch](evals/reviews/blind-review.md) can be prepared without provisional gold or model suggestions.",
              "This prompt experiment leaves the system rubric and default model profile unchanged; candidate selection is explicit.","",
              validation_text + " Linux/ARM64 CI, Docker, H100 and AWS remain unexecuted here.","",
              "<details><summary>Item-by-item comparison and editable figures</summary>","",
              f"![Complete case map]({relative}/figures/case-map.png)","",
              f"[Overview SVG]({relative}/figures/comparison.svg) · [Case-map SVG]({relative}/figures/case-map.svg)","",
              "</details>", "", "<!-- COMPARISON_V2_END -->"]
    path=ROOT/"README.md"
    content=path.read_text(encoding="utf-8")
    start,end="<!-- COMPARISON_V2_START -->","<!-- COMPARISON_V2_END -->"
    block="\n".join(lines)
    if start in content:
        a,b=content.index(start),content.index(end)+len(end)
        content=content[:a]+block+content[b:]
    else:
        content=content.replace("## Quick start",block+"\n\n## Quick start",1)
    path.write_text(content,encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",required=True)
    args=parser.parse_args()
    run=Path(args.run).resolve()
    manifest=json.loads((run/"manifest.json").read_text(encoding="utf-8"))
    for relative,expected in manifest["identity"]["files"].items():
        if hashlib.sha256((run/"source-snapshot"/relative).read_bytes()).hexdigest()!=expected:
            raise ValueError("Frozen source mismatch: "+relative)
    holdout=read_rows(run/"source-snapshot/evals/data/holdout_v2.jsonl")
    development=read_rows(run/"source-snapshot/evals/data/translation_probe_v1.jsonl")
    records=read_rows(run/"attempts.jsonl")
    summary=comparison_summary(records,holdout,development)
    if manifest["status"]!="complete" or not summary["complete"]:
        raise ValueError("Do not render an incomplete study as final results")
    if summary!=json.loads((run/"summary.json").read_text(encoding="utf-8")):
        raise ValueError("Raw attempts do not reproduce saved metrics")
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":12,"svg.fonttype":"none","svg.hashsalt":"anota-comparison-v2"})
    artifacts=overview(summary,records,run)+error_map(holdout,records,run)
    artifacts.append(write_report(run,summary,holdout,development,records))
    update_readme(summary,run)
    for relative in ("scripts/render_comparison.py", "scripts/verify_comparison.py",
                     "evals/reviews/paired-v2-interpretation.md"):
        snapshot = run / "analysis-snapshot" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes((ROOT / relative).read_bytes())
    artifacts += [run/name for name in ("manifest.json","summary.json","attempts.jsonl")]
    artifacts += list((run/"source-snapshot").rglob("*"))
    artifacts += list((run/"analysis-snapshot").rglob("*"))
    artifacts += list((run/"validation").rglob("*"))
    if (run/"validation.json").exists(): artifacts.append(run/"validation.json")
    hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts if p.is_file()}
    (run/"artifact-sha256.json").write_text(json.dumps(hashes,indent=2)+"\n",encoding="utf-8")
    print("Rendered overview, complete case map, report and artifact hashes.")


if __name__=="__main__": main()
