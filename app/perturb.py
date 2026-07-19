"""Synthetic hard-negative probe: perturb clean translations with KNOWN error truth.

A probe batch answers "which error types does the judge actually catch?" for free —
the injected error carries its own ground truth, so probe items feed the judge-vs-golden
calibration card (per-type recall) without any human labeling.
"""
import json
import re

from .lf import run_lfs

TERM_SWAPS = {
    "medicamento": "suplemento",
    "medicamentos": "suplementos",
    "antibiótico": "antihistamínico",
    "tableta": "cucharada",
    "tabletas": "cucharadas",
    "fiebre": "tos",
    "presión arterial": "frecuencia cardíaca",
    "azúcar": "sodio",
}


def perturb_negation(hyp):
    """Flip polarity by injecting a leading negation."""
    return "No " + hyp[0].lower() + hyp[1:]


def perturb_number(hyp):
    """Multiply the first number by 10 (5 -> 50). None if no number present."""
    m = re.search(r"\d+(?:\.\d+)?", hyp)
    if not m:
        return None
    return hyp[:m.start()] + m.group(0).replace(".", "") + "0" + hyp[m.end():]


def perturb_terminology(hyp):
    """Swap one domain term for a plausible-but-wrong neighbour. None if no term hit."""
    for src, dst in TERM_SWAPS.items():
        if src in hyp:
            return hyp.replace(src, dst, 1)
    return None


def perturb_omission(hyp):
    """Drop the trailing clause (after the last comma) or the last three words."""
    if "," in hyp:
        return hyp.rsplit(",", 1)[0].rstrip() + "."
    words = hyp.rstrip(".").split()
    if len(words) <= 5:
        return None
    return " ".join(words[:-3]) + "."


PERTURBATIONS = {
    "negation_polarity": (perturb_negation, "critical", 2),
    "number_unit": (perturb_number, "critical", 2),
    "terminology": (perturb_terminology, "critical", 2),
    "omission": (perturb_omission, "major", 3),
}


def _clean_candidates(db, source_batch_id):
    """Tasks in the source batch that look genuinely clean: no lint ERROR flags and
    not registered as golden-error items."""
    out = []
    for t in db.query("SELECT * FROM tasks WHERE batch_id=? ORDER BY rowid",
                      (source_batch_id,)):
        if "::" in t["id"]:
            continue
        if any(f["label"] == "ERROR" for f in json.loads(t["lf_flags"])):
            continue
        g = db.one("SELECT answer FROM golden_answers WHERE task_id=?", (t["id"],))
        if g and json.loads(g["answer"])["error_types"] != ["no_error"]:
            continue
        out.append(t)
    return out


def build_probe(db, source_batch_id, per_type=3, actor="system"):
    """Create a probe batch: per_type perturbed items per error type + per_type clean
    controls, all with golden truth registered server-side."""
    src_batch = db.one("SELECT * FROM batches WHERE id=?", (source_batch_id,))
    if src_batch is None:
        raise LookupError(f"batch {source_batch_id} not found")
    cands = _clean_candidates(db, source_batch_id)
    if not cands:
        raise ValueError("no clean candidate tasks in the source batch")
    n_probe = db.one("SELECT COUNT(*) n FROM batches WHERE name LIKE 'probe-%'")["n"]
    name = f"probe-{n_probe + 1}"
    bid = db.execute(
        "INSERT INTO batches(name, show_suggestions, overlap, lang_profile,"
        " guideline_version) VALUES(?,0,1,?,?)",
        (name, src_batch["lang_profile"], src_batch["guideline_version"]))

    def insert(task, hyp, suffix, answer):
        new_id = f"{task['id']}::p{bid}-{suffix}"
        flags = run_lfs(task["source"], hyp, src_batch["lang_profile"])
        db.execute(
            "INSERT INTO tasks(id,batch_id,source,hypothesis,reference,metadata,lf_flags,"
            "is_golden) VALUES(?,?,?,?,?,?,?,1)",
            (new_id, bid, task["source"], hyp, task["reference"], task["metadata"],
             json.dumps(flags, ensure_ascii=False)))
        db.execute("INSERT INTO golden_answers(task_id, answer) VALUES(?,?)",
                   (new_id, json.dumps(answer, ensure_ascii=False)))

    counts = {}
    for etype, (fn, severity, adequacy) in PERTURBATIONS.items():
        made = 0
        for task in cands:
            if made >= per_type:
                break
            hyp = fn(task["hypothesis"])
            if hyp is None or hyp == task["hypothesis"]:
                continue
            insert(task, hyp, etype.split("_")[0][:4] + str(made), {
                "error_types": [etype], "worst_severity": severity, "adequacy": adequacy})
            made += 1
        counts[etype] = made
    for i, task in enumerate(cands[:per_type]):
        insert(task, task["hypothesis"], f"clean{i}",
               {"error_types": ["no_error"], "worst_severity": "neutral", "adequacy": 5})
    counts["clean"] = min(per_type, len(cands))
    total = sum(counts.values())
    db.audit(actor, "probe_build", "batch", bid, {"name": name, "counts": counts})
    return {"batch_id": bid, "name": name, "n": total, "counts": counts}
