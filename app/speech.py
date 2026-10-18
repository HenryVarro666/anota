"""Offline ASR import, registered audio, and append-only transcript corrections.

Only a small presentation whitelist is returned to annotators. In particular,
reference text, arbitrary metadata, local paths and model configuration stay out
of claim responses. ASR results and human revisions are separate records.
"""
import hashlib
import json
import math
from pathlib import Path

from speechqc.schema import validate_report

from .models import SpeechSubmitRequest


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_audio(path, expected_hash):
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("audio_path must be a regular file")
    with path.open("rb") as stream:
        header = stream.read(16)
    suffix = path.suffix.lower()
    valid = ((suffix == ".wav" and header[:4] == b"RIFF" and header[8:12] == b"WAVE")
             or (suffix == ".flac" and header[:4] == b"fLaC")
             or (suffix in (".ogg", ".opus") and header[:4] == b"OggS")
             or (suffix == ".mp3" and (header[:3] == b"ID3" or
                                      (len(header) > 1 and header[0] == 255 and header[1] & 224 == 224)))
             or (suffix in (".m4a", ".mp4") and header[4:8] == b"ftyp"))
    if not valid:
        raise ValueError("unsupported audio extension or invalid audio header")
    digest = _hash_file(path)
    if digest != expected_hash:
        raise ValueError("audio SHA256 mismatch")
    return path


def _finite(value, field, positive=False):
    number = float(value)
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise ValueError(f"invalid {field}")
    return number


def _confidence_fields(source, target, ranges):
    """Retain engine evidence, including explicit unavailable values, without inference."""
    for field, (low, high) in ranges.items():
        if field not in source:
            continue
        value = source[field]
        if value is None:
            target[field] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"invalid {field}")  # noqa: TRY004 - uniform invalid-manifest error
        try:
            value = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"invalid {field}") from exc
        if (not math.isfinite(value) or (low is not None and value < low)
                or (high is not None and value > high)):
            raise ValueError(f"invalid {field}")
        target[field] = value


def _normalise_row(row, manifest_dir):
    if row.get("schema_version") != "speech-v1":
        raise ValueError("schema_version must be speech-v1")
    if row.get("language") not in ("en", "zh"):
        raise ValueError("language must be en or zh")
    for key in ("item_id", "audio_path", "corpus", "corpus_revision", "split", "source_id",
                "license", "cache_key", "run_id"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"{key} is required")
    sha = row.get("audio_sha256", "")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("audio_sha256 must be a lowercase SHA256 digest")
    path = Path(row["audio_path"])
    if not path.is_absolute():
        path = manifest_dir / path
    path = _validate_audio(path, sha)
    duration = _finite(row.get("audio_duration_s"), "audio_duration_s", positive=True)
    for key in ("sample_rate", "channels"):
        if isinstance(row.get(key), bool) or not isinstance(row.get(key), int) or row[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if not isinstance(row.get("asr_text"), str):
        raise TypeError("asr_text must be a string, including an explicit empty string")
    model = row.get("model")
    if not isinstance(model, dict) or any(not isinstance(model.get(key), str) or not model[key]
                                          for key in ("name", "revision", "device", "compute_type")):
        raise ValueError("model requires name, revision, device and compute_type")
    if not isinstance(row.get("config"), dict):
        raise TypeError("config must be an object")
    segments = []
    for segment in row.get("segments", []):
        start = _finite(segment["start"], "segment start")
        end = _finite(segment["end"], "segment end")
        if end < start or end > duration + 0.05 or not isinstance(segment.get("text"), str):
            raise ValueError("invalid ASR segment boundaries or text")
        normalised = {"start": start, "end": end, "text": segment["text"]}
        _confidence_fields(segment, normalised, {
            "avg_logprob": (None, 0), "no_speech_prob": (0, 1),
            "compression_ratio": (0, None), "temperature": (0, None)})
        segments.append(normalised)
    provenance = {key: row.get(key) for key in (
        "schema_version", "item_id", "parent_id", "language", "audio_sha256", "corpus",
        "corpus_revision", "split", "source_id", "reference_id", "license", "status",
        "asr_text", "sample_rate", "channels", "config", "cache_key", "run_id", "cache_hit",
        "language_config", "source_audio_filename", "speaker_id", "speaker_id_status", "error")}
    provenance.update(audio_path=str(path), audio_duration_s=duration, segments=segments,
                      model={key: model[key] for key in ("name", "revision", "device", "compute_type")},
                      processing_ms=_finite(row.get("processing_ms", 0), "processing_ms"),
                      inference_run_id=row.get("inference_run_id", row["run_id"]),
                      inference_processing_ms=_finite(row.get("inference_processing_ms", row.get("processing_ms", 0)),
                                                      "inference_processing_ms"))
    if model.get("repo_id") is not None:
        if not isinstance(model["repo_id"], str):
            raise ValueError("model repo_id must be a string")
        provenance["model"]["repo_id"] = model["repo_id"]
    if not isinstance(provenance["inference_run_id"], str) or not provenance["inference_run_id"]:
        raise ValueError("inference_run_id must be a nonempty string")
    if "words" in row:
        words = []
        for word in row["words"]:
            start = _finite(word["start"], "word start")
            end = _finite(word["end"], "word end")
            if end < start or end > duration + 0.05 or not isinstance(word.get("w"), str):
                raise ValueError("invalid ASR word boundaries or text")
            normalised = {"start": start, "end": end, "w": word["w"]}
            _confidence_fields(word, normalised, {"probability": (0, 1)})
            words.append(normalised)
        provenance["words"] = words
    if row.get("detected_language") is not None:
        if not isinstance(row["detected_language"], str):
            raise ValueError("detected_language must be a string")
        provenance["detected_language"] = row["detected_language"]
    if row.get("language_probability") is not None:
        probability = _finite(row["language_probability"], "language_probability")
        if probability > 1:
            raise ValueError("language_probability must be <= 1")
        provenance["language_probability"] = probability
    if "audio_analysis" in row:
        provenance["audio_analysis"] = validate_report(
            row["audio_analysis"], audio_sha256=sha, transcript=provenance, duration_s=duration)
        report = provenance["audio_analysis"]
        if report["sample_rate"] != row["sample_rate"] or report["channels"] != row["channels"]:
            raise ValueError("audio analysis metadata does not match the speech manifest")
    if "audio_analysis_error" in row:
        failure = row["audio_analysis_error"]
        if "audio_analysis" in row:
            raise ValueError("audio analysis cannot contain both a report and a failure")
        if (not isinstance(failure, dict) or set(failure) != {"type", "message"}
                or not isinstance(failure["type"], str) or not 1 <= len(failure["type"]) <= 100
                or not isinstance(failure["message"], str) or len(failure["message"]) > 1000):
            raise ValueError("invalid audio analysis failure record")
        # Failure provenance survives import/export; raw offline diagnostics are
        # never forwarded to the annotator (may contain a local filesystem path).
        provenance["audio_analysis_error"] = dict(failure)
    # JSON encoding rejects invalid config numbers and unsupported objects early.
    json.dumps(provenance, allow_nan=False)
    return provenance


def import_speech_manifest(db, path, batch_name, actor="cli", show_suggestions=None):
    """Import succeeded ASR rows; invalid/failed rows are explicit denominator counts.

    Identity excludes operational run/cache-hit fields, so re-importing identical
    content is idempotent. Changed audio, configuration or ASR content gets a new
    immutable task, even if a caller accidentally reuses its cache_key.
    """
    path = Path(path).resolve(strict=True)
    if not batch_name.strip():
        raise ValueError("batch_name is required")
    if show_suggestions is not None and not isinstance(show_suggestions, bool):
        raise ValueError("show_suggestions must be a boolean or None")
    with db.lock:
        existing = db.one("SELECT id,show_suggestions FROM batches WHERE name=?", (batch_name,))
        if existing:
            bid = existing["id"]
            if db.one("SELECT 1 FROM tasks WHERE batch_id=? AND task_type!='speech'", (bid,)):
                raise ValueError("cannot import speech into a translation batch")
            if show_suggestions is not None and bool(existing["show_suggestions"]) != show_suggestions:
                raise ValueError("existing batch hint policy differs; use a new batch for a different review condition")
        else:
            bid = db.execute("INSERT INTO batches(name,lang_profile,guideline_version,show_suggestions) "
                             "VALUES(?,'en-zh-speech','speech-v1',?)",
                             (batch_name, int(bool(show_suggestions))))
        result = {"batch_id": bid, "n": 0, "skipped": 0, "failed": 0, "invalid": 0, "errors": []}
        for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise TypeError("manifest row must be an object")
                if row.get("status") != "succeeded":
                    result["failed"] += 1
                    result["errors"].append({"line": line_no, "item_id": row.get("item_id"),
                                              "error": row.get("error") or "ASR not succeeded"})
                    continue
                provenance = _normalise_row(row, path.parent)
                stable = {key: value for key, value in provenance.items()
                          if key not in ("audio_path", "run_id", "cache_hit", "processing_ms",
                                         "inference_run_id", "inference_processing_ms")}
                identity = {"batch_id": bid, "speech": stable}
                task_id = "speech-" + hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
                if db.one("SELECT 1 FROM tasks WHERE id=?", (task_id,)):
                    result["skipped"] += 1
                    continue
                audio_id = "audio-" + provenance["audio_sha256"]
                with db.conn:
                    db.conn.execute("INSERT OR IGNORE INTO audio_assets"
                                    "(id,path,sha256,duration_s,sample_rate,channels) VALUES(?,?,?,?,?,?)",
                                    (audio_id, provenance["audio_path"], provenance["audio_sha256"],
                                     provenance["audio_duration_s"], provenance["sample_rate"], provenance["channels"]))
                    db.conn.execute("INSERT INTO tasks(id,batch_id,source,hypothesis,task_type) "
                                    "VALUES(?,?,?,?,'speech')",
                                    (task_id, bid, f"{provenance['language']} · {provenance['source_id']}",
                                     provenance["asr_text"]))
                    db.conn.execute("INSERT INTO speech_tasks(task_id,audio_id,provenance) VALUES(?,?,?)",
                                    (task_id, audio_id, _json(provenance)))
                result["n"] += 1
            except (ValueError, TypeError, KeyError, OSError) as exc:
                result["invalid"] += 1
                result["errors"].append({"line": line_no, "error": str(exc)})
        db.audit(actor, "import_speech", "batch", bid, {"manifest": str(path), **result})
        return result


def audio_asset(db, audio_id):
    asset = db.one("SELECT * FROM audio_assets WHERE id=?", (audio_id,))
    if asset is None:
        raise LookupError("audio asset not found")
    # Prevent stale registry entries from serving replaced/symlinked files.
    path = _validate_audio(Path(asset["path"]), asset["sha256"])
    if str(path) != asset["path"]:
        raise ValueError("registered audio path changed")
    return asset


def _annotation(row):
    if row is None:
        return None
    result = dict(row)
    result["uncertain_spans"] = json.loads(result["uncertain_spans"])
    result["review_context"] = json.loads(result.get("review_context", "{}"))
    return result


def task_payload(db, assignment_id, task, batch):
    record = db.one("SELECT * FROM speech_tasks WHERE task_id=?", (task["id"],))
    provenance = json.loads(record["provenance"])
    assignment = db.one("SELECT annotator FROM assignments WHERE id=? AND task_id=?",
                        (assignment_id, task["id"]))
    # An undone assignment may expire and be replaced with a fresh assignment
    # ID. Recover this annotator's own latest revision across that lease change,
    # while keeping other annotators' corrections blind.
    latest = (db.one("SELECT * FROM speech_annotations WHERE task_id=? AND annotator=? "
                     "ORDER BY id DESC LIMIT 1", (task["id"], assignment["annotator"]))
              if assignment else None)
    # Explicit whitelist: do not forward metadata, reference text or provenance.
    payload = {"assignment_id": assignment_id, "task_id": task["id"], "batch_id": task["batch_id"],
            "task_type": "speech", "source": task["source"], "hypothesis": task["hypothesis"],
            "batch": batch, "speech": {
                "audio_id": record["audio_id"], "audio_url": "/api/audio/" + record["audio_id"],
                "language": provenance["language"], "duration_s": provenance["audio_duration_s"],
                "segments": [{key: segment[key] for key in ("start", "end", "text")}
                             for segment in provenance["segments"]],
                "asr_text": provenance["asr_text"],
                "words": [{key: word[key] for key in ("start", "end", "w")}
                          for word in provenance.get("words", [])],
                "latest_annotation": _annotation(latest)}}
    report = provenance.get("audio_analysis")
    if report:
        # Objective waveform is usable in both review conditions. Inferences,
        # diagnostic metrics and even issue counts stay out of clean responses.
        payload["speech"]["waveform"] = report["waveform"]
        if batch["show_suggestions"]:
            payload["speech"]["audio_analysis"] = {key: report[key] for key in (
                "analysis_id", "analyzer_version", "status", "metrics", "vad",
                "coverage", "issues", "warnings")}
    return payload


def submit_speech(db, annotator, assignment_id, payload):
    if not isinstance(payload, SpeechSubmitRequest):
        payload = SpeechSubmitRequest.model_validate(payload)
    if payload.annotator != annotator or payload.assignment_id != assignment_id:
        raise ValueError("submission identity does not match assignment")
    with db.lock, db.conn:
        assignment = db.one("SELECT * FROM assignments WHERE id=?", (assignment_id,))
        if assignment is None or assignment["annotator"] != annotator:
            raise LookupError("assignment not found for this annotator")
        if assignment["status"] != "assigned":
            raise ValueError(f"assignment is '{assignment['status']}', not open")
        task = db.one("SELECT t.task_type,b.guideline_version,b.show_suggestions FROM tasks t JOIN batches b "
                      "ON b.id=t.batch_id WHERE t.id=?", (assignment["task_id"],))
        if task["task_type"] != "speech":
            raise ValueError("translation task requires the translation submission endpoint")
        record = db.one("SELECT provenance FROM speech_tasks WHERE task_id=?", (assignment["task_id"],))
        provenance = json.loads(record["provenance"])
        duration = provenance["audio_duration_s"]
        if any(span.end > duration + 0.001 for span in payload.uncertain_spans):
            raise ValueError("uncertain span exceeds audio duration")
        report = provenance.get("audio_analysis")
        hints_available = bool(report and task["show_suggestions"])
        analysis_id = report["analysis_id"] if hints_available else None
        context = payload.review_context
        if context is not None:
            if context.analysis_id != analysis_id or context.hints_exposed != hints_available:
                raise ValueError("review context does not match task analysis or batch policy")
            allowed = {issue["id"] for issue in report["issues"]} if hints_available else set()
            if set(context.played_issue_ids) - allowed:
                raise ValueError("review context contains unknown or hidden issue ids")
        review_context = {
            "schema_version": "speech-review-v1", "analysis_id": analysis_id,
            "hints_available": hints_available,
            "hints_exposed": context.hints_exposed if context is not None else None,
            "played_issue_ids": sorted(context.played_issue_ids) if context is not None else [],
            "telemetry_status": "reported" if context is not None else "not_reported",
        }
        cursor = db.conn.execute(
            "INSERT INTO speech_annotations(task_id,assignment_id,annotator,corrected_transcript,"
            "uncertain_spans,note,elapsed_ms,active_ms,guideline_version,review_context) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (assignment["task_id"], assignment_id, annotator, payload.corrected_transcript,
             _json([span.model_dump() for span in payload.uncertain_spans]), payload.note,
             payload.elapsed_ms, payload.active_ms, task["guideline_version"], _json(review_context)))
        annotation_id = cursor.lastrowid
        db.conn.execute("UPDATE assignments SET status='submitted' WHERE id=?", (assignment_id,))
        db.conn.execute("INSERT INTO audit_log(actor,action,entity_type,entity_id,payload) "
                        "VALUES(?,'submit_speech','speech_annotation',?,?)",
                        (annotator, str(annotation_id), _json({"task_id": assignment["task_id"],
                                                              "assignment_id": assignment_id,
                                                              "review_context": review_context})))
        return annotation_id


def snapshot_item(db, task):
    record = db.one("SELECT * FROM speech_tasks WHERE task_id=?", (task["id"],))
    provenance = json.loads(record["provenance"])
    annotations = [_annotation(row) for row in db.query(
        "SELECT * FROM speech_annotations WHERE task_id=? ORDER BY id", (task["id"],))]
    active = db.query(
        "SELECT s.* FROM speech_annotations s JOIN assignments a ON a.id=s.assignment_id "
        "WHERE s.task_id=? AND a.status='submitted' AND s.id=(SELECT MAX(s2.id) "
        "FROM speech_annotations s2 WHERE s2.assignment_id=s.assignment_id) ORDER BY s.id",
        (task["id"],))
    finals = [_annotation(row) for row in active]
    return {"task": {"id": task["id"], "task_type": "speech", "audio_id": record["audio_id"],
                     "source": task["source"], "asr_text": provenance["asr_text"],
                     "provenance": provenance},
            "annotations": annotations, "final_annotations": finals,
            "final_label": finals[0] if len(finals) == 1 else None,
            "unresolved": len(finals) > 1 and len({a["corrected_transcript"] for a in finals}) > 1}
