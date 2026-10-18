"""Render the README figure; SVG is dependency-free, PNG uses resvg-py."""

import json
from html import escape
from pathlib import Path

CONFIG = json.loads("{\"name\":\"ANOTA WORKBENCH\",\"eyebrow\":\"ANNOTATION SYSTEM / LOCAL EDITION\",\"title\":\"Quality, with a trace.\",\"subtitle\":\"Local model suggestions. Human decisions. Reproducible datasets.\",\"tagline\":\"A compact workbench for translation and interpretation QA.\",\"accent\":\"#007F78\",\"pale\":\"#DDF3EA\",\"steps\":[[\"01 / INGEST\",\"Synthetic tasks\",[\"JSONL + language profiles\",\"Batch-level visibility policy\",\"Golden answers stay hidden\"]],[\"02 / ASSIST\",\"Local model judge\",[\"LM Studio or Ollama\",\"Explicit model + bounded workers\",\"Invalid verdicts are rejected\"]],[\"03 / REVIEW\",\"Human decisions\",[\"Blind annotation in clean batches\",\"Disagreement-driven review\",\"Append-only decision history\"]],[\"04 / EXPORT\",\"Dataset snapshots\",[\"Filtered, versioned exports\",\"Canonical content hash\",\"Traceable annotation lineage\"]]],\"panels\":[[\"OFFLINE DEMO\",\"Ready without a GPU\",[\"30 synthetic tasks + a mock judge.\",\"Use probes to check the full workflow.\"]],[\"LOCAL INFERENCE\",\"Measured local pilot\",[\"One request at a time in the local profile.\",\"See the reproducible Qwen pilot above.\"]],[\"FAILURE CONTRACT\",\"Unavailable never means clean\",[\"Malformed model output cannot become no_error.\",\"Human annotation stays available.\"]]],\"footer\":\"IMPLEMENTED ARCHITECTURE  /  Measured results reported separately; synthetic data only.\",\"out\":\"docs/figures/local-architecture\"}")
ROOT = Path(__file__).resolve().parents[1]
parts = []


def text(x, y, value, size=22, color="#536272", weight=400, tracking=None):
    spacing = f' letter-spacing="{tracking}"' if tracking else ""
    parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
                 f'font-weight="{weight}"{spacing}>{escape(value)}</text>')


def rect(x, y, width, height, fill, stroke="none", radius=20):
    parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
                 f'rx="{radius}" fill="{fill}" stroke="{stroke}"/>')


def render():
    c = CONFIG
    parts.clear()
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="960" '
                 'viewBox="0 0 1600 960" role="img" aria-labelledby="title desc">'
                 f'<title id="title">{escape(c["name"])}</title>'
                 f'<desc id="desc">{escape(c["subtitle"])}</desc>'
                 '<defs><linearGradient id="paper" x2="1" y2="1">'
                 '<stop stop-color="#F7F9FC"/><stop offset="1" stop-color="#EFF3F7"/>'
                 '</linearGradient></defs><rect width="1600" height="960" fill="url(#paper)"/>'
                 '<g font-family="Segoe UI, Inter, Arial, sans-serif">')
    rect(0, 0, 1600, 9, c["accent"], radius=0)
    text(64, 66, c["name"], 20, "#172837", 700, "2")
    text(1536, 66, "", 18)
    parts.append(f'<text x="1536" y="66" text-anchor="end" font-size="16" '
                 f'fill="{c["accent"]}" font-weight="600" letter-spacing="1">'
                 f'{escape(c["eyebrow"])}</text>')
    text(60, 167, c["title"], 72, "#142638", 650)
    text(64, 221, c["subtitle"], 27, "#344C5F")
    text(64, 262, c["tagline"], 20, "#697988")
    for i, (label, title, lines) in enumerate(c["steps"]):
        x = 64 + i * 376
        rect(x, 323, 344, 272, "#FFFFFF", "#DCE4EB")
        rect(x + 24, 349, 44, 5, c["accent"], radius=2)
        text(x + 24, 393, label, 17, c["accent"], 700, "1.4")
        text(x + 24, 441, title, 28, "#172C3C", 650)
        for j, line in enumerate(lines):
            text(x + 24, 485 + j * 32, line, 18, "#5C6D7B")
        if i < 3:
            parts.append(f'<path d="M{x+352} 458h16m-6-6 6 6-6 6" '
                         f'fill="none" stroke="{c["accent"]}" stroke-width="2"/>')
    for i, (label, title, lines) in enumerate(c["panels"]):
        x = 64 + i * 501
        rect(x, 635, 470, 211, c["pale"] if i == 0 else "#E8EDF3", radius=18)
        text(x + 24, 675, label, 15, c["accent"], 700, "1.2")
        text(x + 24, 722, title, 24, "#193346", 650)
        for j, line in enumerate(lines):
            text(x + 24, 765 + j * 30, line, 18, "#536777")
    parts.append('<path d="M64 888h1472" stroke="#CED9E3"/>')
    text(64, 923, c["footer"], 17, "#647788")
    parts.append('</g></svg>')
    svg = "\n".join(parts) + "\n"
    output = ROOT / c["out"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".svg").write_text(svg, encoding="utf-8")
    try:
        import resvg_py
    except ImportError:
        print("SVG written. Optional PNG: pip install resvg-py, then rerun.")
    else:
        output.with_suffix(".png").write_bytes(resvg_py.svg_to_bytes(svg_string=svg))
        print(f"SVG and PNG written: {c['out']}")


if __name__ == "__main__":
    render()
