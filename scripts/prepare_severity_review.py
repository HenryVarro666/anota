"""Create a separate reviewer packet with v3 rules but no gold, strata or model outputs."""
import argparse
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="a new directory; never overwrites a review round")
    args = parser.parse_args()
    out = Path(args.out)
    if out.exists():
        parser.error("review directory already exists; select a new round path")
    rows = []
    for file in ("severity_holdout_v3.jsonl", "severity_disputed_v3.jsonl"):
        rows += [json.loads(s) for s in (ROOT / "evals/data" / file).read_text(encoding="utf-8").splitlines() if s.strip()]
    random.Random(31017).shuffle(rows)
    packet, private = out / "reviewer-packet", out / "coordinator-only"
    packet.mkdir(parents=True)
    private.mkdir()
    inputs, template, mapping = [], [], []
    for e in rows:
        id_ = "review-" + hashlib.sha256(("severity-v3:" + e["source"]).encode()).hexdigest()[:12]
        inputs.append({"id": id_, "source": e["source"], "hypothesis": e["hypothesis"]})
        template.append({"id": id_, "reviewer": "", "rubric_version": "severity-v3", "error_types": None,
                         "worst_severity": None, "rationale": "", "needs_adjudication": None})
        mapping.append({"review_id": id_, "original_id": e["id"], "reference_status": e["reference_status"]})
    for path, data in ((packet / "inputs.jsonl", inputs), (packet / "labels-template.jsonl", template), (private / "mapping.jsonl", mapping)):
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in data), encoding="utf-8")
    (packet / "rubric.md").write_bytes((ROOT / "data/guidelines/severity-v3.md").read_bytes())
    (packet / "README.md").write_text(
        "# Independent severity review\n\n"
        "Use inputs.jsonl and rubric.md. Copy labels-template.jsonl for each reviewer, fill the fields independently, and record your reviewer ID. "
        "Use needs_adjudication for missing-context or rule-boundary questions. Do not consult model results, provisional references, other reviewers, or the public result pages before submitting.\n\n"
        "The packet has 28 shuffled synthetic EN/ES examples with opaque IDs. It contains no gold, model responses or primary/disputed split indicators. "
        "A coordinator must distribute only this reviewer-packet directory; keep the mapping separate. Prior exposure to the reference labels or model results makes the first pass unblinded. "
        "After independent submissions, record adjudication separately instead of overwriting original answers. No human labels have been collected by this script.\n",
        encoding="utf-8")
    print(json.dumps({"reviewer_packet": str(packet), "items": len(inputs), "gold_in_packet": False,
                      "model_outputs_in_packet": False, "coordinator_mapping": str(private)}))


if __name__ == "__main__":
    main()
