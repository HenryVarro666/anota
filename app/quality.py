"""Quality math: hand-written Cohen's kappa (tested against textbook values),
golden scoring, final-label aggregation, dashboard aggregates. No sklearn on purpose."""
import json
from statistics import median

from .models import ERROR_TYPES, SEVERITIES


def cohen_kappa(a, b, categories, weights=None):
    assert len(a) == len(b) and a, "need equal non-empty lists"
    idx = {c: i for i, c in enumerate(categories)}
    k, n = len(categories), len(a)
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[idx[x]][idx[y]] += 1.0 / n
    pa = [sum(row) for row in obs]
    pb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    if weights == "linear":
        w = [[abs(i - j) / (k - 1) for j in range(k)] for i in range(k)]
    else:
        w = [[0.0 if i == j else 1.0 for j in range(k)] for i in range(k)]
    po_w = sum(w[i][j] * obs[i][j] for i in range(k) for j in range(k))
    pe_w = sum(w[i][j] * pa[i] * pb[j] for i in range(k) for j in range(k))
    if pe_w == 0:
        return 1.0
    return 1.0 - po_w / pe_w


def golden_score(answer, ann):
    types_match = set(answer["error_types"]) == set(ann["error_types"])
    severity_match = answer["worst_severity"] == ann["worst_severity"]
    return {"passed": types_match and severity_match,
            "types_match": types_match, "severity_match": severity_match}


def _parse(ann_row):
    d = dict(ann_row)
    d["error_types"] = json.loads(d["error_types"])
    return d


def latest_annotations(db, task_id):
    rows = db.query(
        "SELECT * FROM annotations WHERE task_id=? AND annotator NOT LIKE 'reviewer:%' "
        "ORDER BY id", (task_id,))
    latest = {}
    for r in rows:
        latest[r["annotator"]] = r
    return [_parse(r) for r in latest.values()]


def final_label(db, task_id):
    rev = db.one("SELECT * FROM reviews WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,))
    if rev and rev["verdict"] == "overturned" and rev["replacement_annotation_id"]:
        rep = _parse(db.one("SELECT * FROM annotations WHERE id=?",
                            (rev["replacement_annotation_id"],)))
        return {**_core(rep), "source_kind": "review", "unresolved": False}
    anns = latest_annotations(db, task_id)
    if not anns:
        return None
    if len(anns) == 1:
        return {**_core(anns[0]), "source_kind": "single", "unresolved": False}
    n = len(anns)
    counts = {}
    for a in anns:
        for e in a["error_types"]:
            counts[e] = counts.get(e, 0) + 1
    maj = [e for e in ERROR_TYPES if counts.get(e, 0) * 2 > n]
    unresolved = not maj
    sev_counts = {}
    for a in anns:
        sev_counts[a["worst_severity"]] = sev_counts.get(a["worst_severity"], 0) + 1
    top = max(sev_counts.values())
    tied = [s for s in SEVERITIES if sev_counts.get(s, 0) == top]
    severity = tied[-1]  # tie -> stricter (SEVERITIES is ordered mild->severe)
    newest = max(anns, key=lambda a: a["id"])
    return {"error_types": maj or ["no_error"], "worst_severity": severity,
            "adequacy": int(median(a["adequacy"] for a in anns)),
            "fluency": int(median(a["fluency"] for a in anns)),
            "correction": newest["correction"], "note": newest["note"],
            "source_kind": "aggregate", "unresolved": unresolved}


def _core(a):
    return {k: a[k] for k in
            ("error_types", "worst_severity", "adequacy", "fluency", "correction", "note")}


def annotator_stats(db):
    out = []
    for r in db.query("SELECT DISTINCT annotator FROM annotations "
                      "WHERE annotator NOT LIKE 'reviewer:%'"):
        who = r["annotator"]
        rows = db.query("SELECT * FROM annotations WHERE annotator=? ORDER BY id", (who,))
        latest = {}
        for a in rows:
            latest[a["task_id"]] = a
        golden_total = golden_passed = 0
        for task_id, a in latest.items():
            g = db.one("SELECT answer FROM golden_answers WHERE task_id=?", (task_id,))
            if g:
                golden_total += 1
                if golden_score(json.loads(g["answer"]), _parse(a))["passed"]:
                    golden_passed += 1
        n = len(latest)
        avg_ms = sum(a["elapsed_ms"] for a in latest.values()) / n if n else 0
        out.append({"annotator": who, "n_submitted": n, "avg_elapsed_ms": round(avg_ms),
                    "golden_total": golden_total, "golden_passed": golden_passed})
    return sorted(out, key=lambda s: -s["n_submitted"])


def _shared_labels(db, a, b):
    rows_a = {r["task_id"]: _parse(r) for r in db.query(
        "SELECT * FROM annotations WHERE annotator=? ORDER BY id", (a,))}
    rows_b = {r["task_id"]: _parse(r) for r in db.query(
        "SELECT * FROM annotations WHERE annotator=? ORDER BY id", (b,))}
    shared = sorted(set(rows_a) & set(rows_b))
    return [rows_a[t] for t in shared], [rows_b[t] for t in shared]


def _binary(ann):
    return "error" if ann["error_types"] != ["no_error"] else "clean"


def pairwise_kappa(db, min_shared=3):
    annotators = sorted(r["annotator"] for r in db.query(
        "SELECT DISTINCT annotator FROM annotations WHERE annotator NOT LIKE 'reviewer:%'"))
    out = []
    for i in range(len(annotators)):
        for j in range(i + 1, len(annotators)):
            xa, xb = _shared_labels(db, annotators[i], annotators[j])
            if len(xa) < min_shared:
                continue
            out.append({
                "a": annotators[i], "b": annotators[j], "n": len(xa),
                "kappa_severity": round(cohen_kappa(
                    [x["worst_severity"] for x in xa], [x["worst_severity"] for x in xb],
                    SEVERITIES, weights="linear"), 3),
                "kappa_binary": round(cohen_kappa(
                    [_binary(x) for x in xa], [_binary(x) for x in xb],
                    ["error", "clean"]), 3)})
    return out


def judge_human_agreement(db):
    pairs = []
    for t in db.query("SELECT id FROM tasks"):
        fl = final_label(db, t["id"])
        jr = db.one("SELECT verdict FROM judge_results WHERE task_id=? ORDER BY id DESC LIMIT 1",
                    (t["id"],))
        if fl and jr:
            pairs.append((fl, json.loads(jr["verdict"])))
    if not pairs:
        return None
    return {"n": len(pairs),
            "kappa_binary": round(cohen_kappa(
                [_binary(h) for h, _ in pairs], [_binary(j) for _, j in pairs],
                ["error", "clean"]), 3),
            "kappa_severity": round(cohen_kappa(
                [h["worst_severity"] for h, _ in pairs],
                [j["worst_severity"] for _, j in pairs], SEVERITIES, weights="linear"), 3)}


def error_arm_matrix(db):
    per_arm, sources = {}, {"human": 0, "judge": 0}
    for t in db.query("SELECT id, metadata FROM tasks"):
        fl = final_label(db, t["id"])
        if fl is not None:
            sources["human"] += 1
        else:
            jr = db.one("SELECT verdict FROM judge_results WHERE task_id=? "
                        "ORDER BY id DESC LIMIT 1", (t["id"],))
            if jr is None:
                continue
            fl = json.loads(jr["verdict"])
            sources["judge"] += 1
        arm = json.loads(t["metadata"]).get("arm", "unknown")
        per_arm.setdefault(arm, []).append(fl)
    etypes = [e for e in ERROR_TYPES if e != "no_error"]
    cells = {arm: {e: round(sum(1 for fl in fls if e in fl["error_types"]) / len(fls), 3)
                   for e in etypes}
             for arm, fls in per_arm.items()}
    return {"arms": sorted(per_arm), "error_types": etypes, "cells": cells,
            "n": {arm: len(fls) for arm, fls in per_arm.items()}, "sources": sources}
