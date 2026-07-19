"""API layer. The one non-negotiable: batch policy is enforced HERE, server-side —
a clean-batch claim response never even contains the suggestion fields."""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import export as exportmod
from . import importer, quality
from . import tasks as taskmod
from .db import Database
from .judge import JudgeUnavailable, get_judge, judge_health
from .models import SEVERITIES, AnnotationPayload, SubmitRequest

APP_DIR = Path(__file__).resolve().parent.parent


class ClaimRequest(BaseModel):
    annotator: str
    batch_id: int | None = None


class SkipRequest(BaseModel):
    annotator: str
    assignment_id: int
    reason: str = ""


class UndoRequest(BaseModel):
    annotator: str


class ReviewRequest(BaseModel):
    reviewer: str
    verdict: str                      # approved | overturned
    case_note: str = ""
    replacement: AnnotationPayload | None = None


class JudgeRunRequest(BaseModel):
    batch_id: int


class RoutingRequest(BaseModel):
    top_n: int = 10
    signal: str = "judge_confidence"  # judge_confidence | lf_conflict


class ExportRequest(BaseModel):
    batch_id: int | None = None
    include_golden: bool = False


def create_app(db_path=":memory:", demo=False, data_dir=None, export_dir=None):
    app = FastAPI(title="Anota Workbench")
    db = Database(db_path)
    app.state.db = db
    app.state.export_dir = export_dir or str(APP_DIR / "exports")
    gl_path = Path(data_dir or APP_DIR / "data") / "guideline.md"
    guideline = gl_path.read_text(encoding="utf-8") if gl_path.exists() else ""
    if demo:
        importer.import_demo(db, data_dir or APP_DIR / "data")

    def with_policy(payload):
        """Strip or attach machine signals according to batch policy."""
        if payload is None:
            return None
        if payload["batch"]["show_suggestions"]:
            jr = db.one("SELECT verdict, confidence, model, is_mock FROM judge_results "
                        "WHERE task_id=? ORDER BY id DESC LIMIT 1", (payload["task_id"],))
            payload["suggestions"] = {
                "lf": [f for f in payload["lf_flags"] if f["label"] == "ERROR"],
                "judge": ({**json.loads(jr["verdict"]), "confidence": jr["confidence"],
                           "model": jr["model"], "is_mock": bool(jr["is_mock"])} if jr else None)}
        payload.pop("lf_flags", None)
        return payload

    @app.post("/api/claim")
    def api_claim(req: ClaimRequest):
        c = taskmod.claim(db, req.annotator, req.batch_id)
        if c is None:
            bid = req.batch_id or (db.one("SELECT id FROM batches ORDER BY id LIMIT 1") or {}).get("id", 0)
            return {"task": None, "progress": taskmod.progress(db, req.annotator, bid) if bid else {"done": 0, "total": 0}}
        prog = taskmod.progress(db, req.annotator, c["batch_id"])
        return {"task": with_policy(c), "progress": prog}

    @app.post("/api/submit")
    def api_submit(req: SubmitRequest):
        try:
            ann_id = taskmod.submit(db, req.annotator, req.assignment_id, req)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"annotation_id": ann_id}

    @app.post("/api/skip")
    def api_skip(req: SkipRequest):
        try:
            taskmod.skip(db, req.annotator, req.assignment_id, req.reason)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/undo")
    def api_undo(req: UndoRequest):
        return {"task": with_policy(taskmod.undo(db, req.annotator))}

    @app.get("/api/batches")
    def api_batches():
        return [dict(b, n_tasks=db.one("SELECT COUNT(*) n FROM tasks WHERE batch_id=?",
                                       (b["id"],))["n"])
                for b in db.query("SELECT * FROM batches ORDER BY id")]

    def _disagreement(ann, judge_v, lf_errors):
        score = 0.0
        if judge_v:
            score += abs(SEVERITIES.index(ann["worst_severity"])
                         - SEVERITIES.index(judge_v["worst_severity"]))
            ha, ja = set(ann["error_types"]), set(judge_v["error_types"])
            union = ha | ja
            score += 2 * (1 - len(ha & ja) / len(union)) if union else 0
        score += 0.5 * len(lf_errors)
        return round(score, 2)

    @app.get("/api/review/queue")
    def api_review_queue():
        out = []
        for tr in db.query("SELECT DISTINCT task_id FROM annotations "
                           "WHERE annotator NOT LIKE 'reviewer:%'"):
            for ann in quality.latest_annotations(db, tr["task_id"]):
                if db.one("SELECT id FROM reviews WHERE reviewed_annotation_id=?", (ann["id"],)):
                    continue
                t = db.one("SELECT * FROM tasks WHERE id=?", (tr["task_id"],))
                jr = db.one("SELECT verdict, confidence, is_mock FROM judge_results "
                            "WHERE task_id=? ORDER BY id DESC LIMIT 1", (tr["task_id"],))
                judge_v = ({**json.loads(jr["verdict"]), "confidence": jr["confidence"],
                            "is_mock": bool(jr["is_mock"])} if jr else None)
                lf_errors = [f for f in json.loads(t["lf_flags"]) if f["label"] == "ERROR"]
                out.append({"task": {"id": t["id"], "source": t["source"],
                                     "hypothesis": t["hypothesis"], "reference": t["reference"]},
                            "annotation": ann, "judge": judge_v, "lf_errors": lf_errors,
                            "disagreement": _disagreement(ann, judge_v, lf_errors)})
        return sorted(out, key=lambda x: -x["disagreement"])

    @app.post("/api/review/{annotation_id}")
    def api_review(annotation_id: int, req: ReviewRequest):
        ann = db.one("SELECT * FROM annotations WHERE id=?", (annotation_id,))
        if ann is None:
            raise HTTPException(404, "annotation not found")
        if req.verdict not in ("approved", "overturned"):
            raise HTTPException(422, "verdict must be approved|overturned")
        replacement_id = None
        if req.verdict == "overturned":
            if req.replacement is None:
                raise HTTPException(422, "overturn requires a replacement annotation")
            p = req.replacement
            replacement_id = db.execute(
                "INSERT INTO annotations(task_id,annotator,error_types,worst_severity,adequacy,"
                "fluency,correction,note,guideline_version) VALUES(?,?,?,?,?,?,?,?,?)",
                (ann["task_id"], f"reviewer:{req.reviewer}", json.dumps(p.error_types),
                 p.worst_severity, p.adequacy, p.fluency, p.correction, p.note,
                 ann["guideline_version"]))
        review_id = db.execute(
            "INSERT INTO reviews(task_id,reviewed_annotation_id,reviewer,verdict,"
            "replacement_annotation_id,case_note) VALUES(?,?,?,?,?,?)",
            (ann["task_id"], annotation_id, req.reviewer, req.verdict,
             replacement_id, req.case_note))
        db.audit(f"reviewer:{req.reviewer}", "review", "annotation", annotation_id,
                 {"verdict": req.verdict, "case_note": req.case_note})
        return {"review_id": review_id}

    @app.get("/api/stats/overview")
    def api_overview():
        recent = [r["elapsed_ms"] for r in db.query(
            "SELECT elapsed_ms FROM annotations WHERE elapsed_ms>0 ORDER BY id DESC LIMIT 30")]
        return {"n_tasks": db.one("SELECT COUNT(*) n FROM tasks")["n"],
                "n_annotations": db.one("SELECT COUNT(*) n FROM annotations")["n"],
                "n_batches": db.one("SELECT COUNT(*) n FROM batches")["n"],
                "recent_elapsed_ms": list(reversed(recent)),
                "judge": judge_health(get_judge(guideline))}

    @app.get("/api/stats/matrix")
    def api_matrix():
        return quality.error_arm_matrix(db)

    @app.get("/api/stats/annotators")
    def api_annotators():
        return quality.annotator_stats(db)

    @app.get("/api/stats/agreement")
    def api_agreement():
        return {"pairwise": quality.pairwise_kappa(db),
                "judge_human": quality.judge_human_agreement(db)}

    @app.post("/api/judge/run")
    def api_judge_run(req: JudgeRunRequest):
        j = get_judge(guideline)
        n = 0
        for t in db.query("SELECT * FROM tasks WHERE batch_id=?", (req.batch_id,)):
            try:
                v = j.evaluate(t, json.loads(t["lf_flags"]))
            except JudgeUnavailable as e:
                raise HTTPException(503, f"judge unavailable: {e}")
            db.execute("INSERT INTO judge_results(task_id,verdict,confidence,model,is_mock) "
                       "VALUES(?,?,?,?,?)",
                       (t["id"], json.dumps({k: v[k] for k in ("error_types", "worst_severity",
                                                               "adequacy", "rationale")},
                                            ensure_ascii=False),
                        v["confidence"], j.model, int(j.is_mock)))
            n += 1
        db.audit("system", "judge_run", "batch", req.batch_id, {"n": n, "model": j.model})
        return {"n": n, "model": j.model, "is_mock": j.is_mock}

    @app.post("/api/routing/build")
    def api_routing(req: RoutingRequest):
        with db.lock:
            if req.signal == "judge_confidence":
                ranked = db.query("""
                    SELECT t.id, t.batch_id FROM tasks t JOIN (
                      SELECT task_id, confidence, MAX(id) FROM judge_results GROUP BY task_id
                    ) j ON j.task_id = t.id
                    WHERE t.id NOT LIKE '%::r%'
                    ORDER BY j.confidence ASC LIMIT ?""", (req.top_n,))
            elif req.signal == "lf_conflict":
                cands = [t for t in db.query("SELECT * FROM tasks WHERE id NOT LIKE '%::r%'")
                         if any(f["label"] == "ERROR" for f in json.loads(t["lf_flags"]))]
                cands.sort(key=lambda t: -sum(1 for f in json.loads(t["lf_flags"])
                                              if f["label"] == "ERROR"))
                ranked = cands[:req.top_n]
            else:
                raise HTTPException(422, "signal must be judge_confidence|lf_conflict")
            if not ranked:
                raise HTTPException(409, "no candidates — run the judge first")
            src_batch = db.one("SELECT * FROM batches WHERE id=?", (ranked[0]["batch_id"],))
            n_existing = db.one("SELECT COUNT(*) n FROM batches WHERE name LIKE 'routing-%'")["n"]
            name = f"routing-{n_existing + 1}"
            new_bid = db.execute(
                "INSERT INTO batches(name, show_suggestions, overlap, lang_profile, guideline_version)"
                " VALUES(?,1,1,?,?)", (name, src_batch["lang_profile"], src_batch["guideline_version"]))
            for r in ranked:
                t = db.one("SELECT * FROM tasks WHERE id=?", (r["id"],))
                new_id = f"{t['id']}::r{new_bid}"
                db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis,reference,metadata,"
                           "lf_flags) VALUES(?,?,?,?,?,?,?)",
                           (new_id, new_bid, t["source"], t["hypothesis"], t["reference"],
                            t["metadata"], t["lf_flags"]))
                jr = db.one("SELECT * FROM judge_results WHERE task_id=? ORDER BY id DESC LIMIT 1",
                            (t["id"],))
                if jr:
                    db.execute("INSERT INTO judge_results(task_id,verdict,confidence,model,is_mock)"
                               " VALUES(?,?,?,?,?)", (new_id, jr["verdict"], jr["confidence"],
                                                      jr["model"], jr["is_mock"]))
            db.audit("system", "routing_build", "batch", new_bid,
                     {"signal": req.signal, "n": len(ranked)})
            return {"batch_id": new_bid, "name": name, "n": len(ranked)}

    @app.post("/api/export")
    def api_export(req: ExportRequest):
        filters = {"batch_id": req.batch_id} if req.batch_id else {}
        return exportmod.export_snapshot(db, app.state.export_dir, filters, req.include_golden)

    @app.get("/api/guideline")
    def api_guideline():
        return {"text": guideline}

    @app.get("/api/health")
    def api_health():
        return {"status": "ok", "judge": judge_health(get_judge(guideline))}

    static_dir = APP_DIR / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app
