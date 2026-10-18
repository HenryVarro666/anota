"""Render the measured pilot and its disagreement table from committed raw attempts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.evaluation import summarize

INK, MUTED, TEAL, BLUE = "#182E40", "#647687", "#007F78", "#6174CF"
PAPER, LINE = "#F5F7FA", "#DDE5EC"


def fraction(metric):
    return f"{metric['numerator']}/{metric['denominator']}"


def percentage(metric):
    return f"{metric['rate']:.0%}" if metric["rate"] is not None else "n/a"


def render(run):
    records = [json.loads(line) for line in (run / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    dataset = ROOT / "evals/data/translation_probe_v1.jsonl"
    examples = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == manifest["identity"]["dataset_sha256"]
    summary = summarize(records, examples, manifest["identity"]["profile"]["execution"]["repeats"])
    if not summary["complete"] or len(summary["passes"]) != 2:
        raise ValueError("this two-pass figure requires a complete two-pass run")
    saved = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary == saved, "saved metrics do not match raw attempts"
    passes = [summary["passes"][str(i)] for i in (1, 2)]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12,
                         "svg.fonttype": "none", "svg.hashsalt": "anota-evaluation-v1", "axes.titleweight": "bold"})
    fig = plt.figure(figsize=(16, 10.5), dpi=140, facecolor=PAPER)
    fig.text(.055, .95, "ANOTA WORKBENCH", size=13, weight="bold", color=INK)
    fig.text(.945, .95, "JUDGE PILOT / " + manifest["started_at_utc"][:10], size=11, ha="right", color=TEAL)
    fig.text(.055, .875, "A local judge, measured.", size=38, weight="bold", color=INK)
    identity = manifest["identity"]
    artifact = identity["profile"].get("model_artifact", {})
    provenance = identity.get("provenance", {})
    subtitle = " · ".join([artifact.get("repository", identity["model"]).split("/")[-1],
                           artifact.get("quantization", artifact.get("precision", "precision unrecorded")),
                           provenance.get("gpu", "hardware unrecorded")])
    fig.text(.055, .833, subtitle, size=15, color=MUTED)
    valid = sum(p["valid_output"]["numerator"] for p in passes)
    cards = [
        ("VALID RESPONSES", f"{valid} / {summary['completed_attempts']}", "Warmup excluded"),
        ("EXACT ERROR TYPES", " / ".join(percentage(p["exact_error_types"]) for p in passes), "Pass 1 / Pass 2 · 20 examples each"),
        ("MEDIAN REQUEST TIME", f"{summary['pooled_timing']['p50']:.2f} s", f"P95 {summary['pooled_timing']['p95']:.2f} s · all timed requests"),
        ("REPEAT LABEL AGREEMENT", fraction(summary["repeat_label_agreement_valid_only"]), "Same types AND severity, valid pairs"),
    ]
    for i, (label, value, note) in enumerate(cards):
        x = .055 + i * .2275
        fig.add_artist(FancyBboxPatch((x, .664), .2075, .125, boxstyle="round,pad=0.008,rounding_size=0.012",
                                     transform=fig.transFigure, facecolor="white", edgecolor=LINE, zorder=0))
        fig.text(x + .011, .759, label, size=9, weight="bold", color=MUTED)
        fig.text(x + .011, .708, value, size=26, weight="bold", color=TEAL if i != 1 else INK)
        fig.text(x + .011, .681, note, size=8.4, color=MUTED)

    ax = fig.add_axes([.16, .225, .33, .33], facecolor=PAPER)
    types = ["negation_polarity", "number_unit", "terminology", "omission", "no_error"]
    labels = ["Negation", "Numbers / units", "Terminology", "Omission", "Clean controls"]
    for i, (p, color) in enumerate(zip(passes, (TEAL, BLUE))):
        ys = [n + (i - .5) * .30 for n in range(len(types))]
        values = [p["per_type_recall"][key]["rate"] for key in types]
        ax.barh(ys, values, height=.23, color=color, label=f"Pass {i+1}", zorder=3)
        for y, key, value in zip(ys, types, values):
            ax.text(value + .025, y, fraction(p["per_type_recall"][key]), va="center", size=10, color=color)
    ax.set_yticks(range(len(types)), labels, color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.18)
    ax.set_xticks([0, .25, .5, .75, 1], ["0", "25", "50", "75", "100%"], color=MUTED)
    ax.grid(axis="x", color=LINE, zorder=0)
    ax.tick_params(axis="both", length=0, pad=9)
    for spine in ax.spines.values(): spine.set_visible(False)
    ax.legend(loc="upper right", bbox_to_anchor=(1.06, 1.185), frameon=False, ncol=2, fontsize=10)
    fig.text(.055, .602, "01   CATEGORY RECALL", size=13, weight="bold", color=INK)
    fig.text(.055, .575, "4 reference items per category in each pass", size=10.5, color=MUTED)

    ax2 = fig.add_axes([.585, .225, .35, .33], facecolor=PAPER)
    for i, color in enumerate((TEAL, BLUE), 1):
        values = sorted(r["trace"]["elapsed_seconds"] for r in records if r["phase"] == "timed" and r["repeat"] == i)
        ax2.step([0] + values, [0] + [(j+1)/len(values) for j in range(len(values))], where="post",
                 color=color, lw=2.4, label=f"Pass {i}")
        ax2.scatter(values, [(j+1)/len(values) for j in range(len(values))], s=14, color=color, zorder=3)
    ax2.set_ylim(0, 1.05)
    ax2.set_xlim(left=max(0, min(r["trace"]["elapsed_seconds"] for r in records if r["phase"] == "timed") - 1))
    ax2.set_yticks([0, .25, .5, .75, 1], ["0", "25", "50", "75", "100%"], color=MUTED)
    ax2.set_xlabel("Client request time (seconds)", color=MUTED, labelpad=12)
    ax2.set_ylabel("Requests completed by this time", color=MUTED, labelpad=9)
    ax2.grid(color=LINE)
    ax2.tick_params(length=0, pad=8, colors=MUTED)
    for spine in ax2.spines.values(): spine.set_visible(False)
    fig.text(.555, .602, "02   LATENCY DISTRIBUTION", size=13, weight="bold", color=INK)
    fig.text(.555, .575, "Sequential · one warmup · server cache at defaults", size=10.5, color=MUTED)

    fig.text(.055, .13, f"{len(examples)} unique synthetic EN→ES examples · {summary['unique_parent_templates']} parent templates · 2 shuffled passes", size=12, weight="bold", color=INK)
    fig.text(.055, .098, "Reference labels are demo / perturbation labels, not expert-adjudicated clinical truth. Repeats are not independent samples.", size=10, color=MUTED)
    fig.text(.055, .071, "Category recall allows extra predicted tags; exact-type agreement does not. Timing includes prefill, generation and client parsing.", size=10, color=MUTED)
    fig.text(.055, .035, "SOURCE  evals/runs/" + run.name + "  ·  Raw requests, responses, hashes and limitations included", size=8.5, color=TEAL)
    out = ROOT / "docs/figures/local-qwen-evaluation"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out.with_suffix(".png"), facecolor=PAPER)
    fig.savefig(out.with_suffix(".svg"), facecolor=PAPER, metadata={"Date": None})
    plt.close(fig)

    lines = ["# Local Qwen pilot: observed results", "", "Generated from `attempts.jsonl`; no model-generated scores are substituted for reference labels.", "",
             "20 unique synthetic EN→ES examples, derived from 11 parent templates. Two passes measure repeatability, not 40 independent examples.", "",
             "| Metric | Pass 1 | Pass 2 |", "|---|---:|---:|"]
    for key, label in [("valid_output", "Valid output"), ("exact_error_types", "Exact error-type set"),
                       ("binary_accuracy", "Error vs clean agreement"), ("severity_accuracy", "Severity agreement"),
                       ("critical_severity_recall", "Critical severity recall"), ("clean_false_alarm", "False alarms on clean controls")]:
        lines.append("| " + label + " | " + " | ".join(f"{fraction(p[key])} ({percentage(p[key])})" for p in passes) + " |")
    for key, label in [("p50", "Request latency P50 (s)"), ("p95", "Request latency P95 (s)")]:
        lines.append(f"| {label} | " + " | ".join(f"{p['latency_seconds'][key]:.3f}" for p in passes) + " |")
    lines.append("| Effective output tokens/s | " + " | ".join(f"{p['effective_output_tokens_per_second']:.3f}" for p in passes) + " |")
    lines += ["", "Effective output tokens/s = reported completion tokens / request wall time, including prefill. This is not decoder throughput or streaming TTFT.", "",
              f"Label stability: **{fraction(summary['repeat_label_agreement_valid_only'])}** valid pairs match on both error types and severity.", "",
              f"Pooled P50: **{summary['pooled_timing']['p50']:.3f} s**; P95: **{summary['pooled_timing']['p95']:.3f} s**.", "",
              "## Every disagreement with the frozen references", "",
              "These are disagreements, not proof the model is always wrong: the reference labels are synthetic and require expert review. No label was changed after this run.", "",
              "| Pass | Example | Reference types / severity | Observed types / severity | Model rationale |", "|---|---|---|---|---|"]
    by_id = {e["id"]: e for e in examples}
    for row in records:
        if row["phase"] != "timed": continue
        gold, v = by_id[row["task_id"]]["gold"], row["verdict"]
        if v and set(gold["error_types"]) == set(v["error_types"]) and gold["worst_severity"] == v["worst_severity"]: continue
        gold_label = ", ".join(gold["error_types"]) + " / " + gold["worst_severity"]
        pred = ", ".join(v["error_types"]) + " / " + v["worst_severity"] if v else "UNAVAILABLE"
        rationale = (v["rationale"] if v else row["error"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row['repeat']} | `{row['task_id']}` | {gold_label} | {pred} | {rationale} |")
    lines += ["", "## Reproducibility and limits", "",
              "- [Manifest](manifest.json): actual model hash, upstream revision, runtime, request contract, dataset and code hashes.",
              "- [Raw attempts](attempts.jsonl): warmup and all timed requests, returned usage, raw response and elapsed time. Credentials are excluded.",
              "- [Metrics](summary.json): denominators, per-category recall and latency. Unavailable attempts count as failures in quality denominators.",
              "- One request at a time, temperature 0, 256 output tokens, JSON schema, `/no_think`, 4096 loaded context. No automatic retries.",
              "- Prefix caching uses the server default; repeated prompt prefixes may accelerate these warm requests. No cold-start, thermal, power or concurrent capacity benchmark.",
              "- EN→ES text only. No ASR, TTS, diarization, audio, low-resource languages, live PHI or expert clinical validation is included.",
              "- H100 BF16 and AWS profiles are unexecuted templates; hardware and precision comparisons need new runs.", ""]
    (run / "report.md").write_text("\n".join(lines), encoding="utf-8")
    paths = [run / name for name in ("manifest.json", "attempts.jsonl", "summary.json", "report.md")]
    if (run / "validation.json").exists():
        paths.append(run / "validation.json")
    # Archive exact executed bytes; normal source checkout may normalize CRLF/LF.
    sources = {"app/judge.py": "judge_code_sha256", "app/evaluation.py": "metrics_code_sha256",
               "scripts/evaluate_judge.py": "runner_code_sha256", "data/guideline.md": "guideline_sha256"}
    for relative, expected in sources.items():
        snapshot = run / "source-snapshot" / relative
        if not snapshot.exists():
            content = (ROOT / relative).read_bytes()
            if hashlib.sha256(content).hexdigest() != identity[expected]:
                raise ValueError(f"{relative} changed since the run; cannot archive it as executed")
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(content)
        assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == identity[expected]
        paths.append(snapshot)
    paths += [dataset, out.with_suffix(".png"), out.with_suffix(".svg")]
    hashes = {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    (run / "artifact-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    print(f"Figure, report and hashes written for {run.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    render(Path(parser.parse_args().run).resolve())
