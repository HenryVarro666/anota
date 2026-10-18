"""Score offline speech results against a separate reference store.

The input manifest supplies the denominator, including missing and failed results.
This script calls no model and imports no speech/model dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NORMALIZATION = {
    "version": "anota-speech-smoke-normalization-v1",
    "unicode": "NFKC then casefold",
    "punctuation": "remove Unicode P categories; preserve %, per-mille, and decimal/grouping separators between digits",
    "english": "punctuation becomes a word boundary except apostrophes, which are removed; whitespace-token WER",
    "chinese": "remove punctuation and whitespace; Unicode-codepoint CER",
    "preserved": "digits, letters, unit and mathematical symbols, negation words; no number-word rewriting or semantic correction",
}

SMOKE_PURPOSE = "Engineering smoke evaluation of 20 deterministic train recordings; not a frozen quality benchmark"
COMMON_LIMITATIONS = [
    "Read speech does not establish medical terminology, telephone audio, or clinical safety performance",
    "Source references are not independent human corrections made in Anota",
    "WER/CER do not measure clinical meaning, number accuracy, unit accuracy, or negation accuracy separately",
]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(value.get("item_id"), str):
                raise TypeError(f"{path}:{number}: expected object with string item_id")
            rows.append(value)
    ids = [row["item_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate item_id in {path}")
    return rows


def normalize(text: str, language: str) -> str:
    if language not in {"en", "zh"}:
        raise ValueError(f"Unsupported smoke-test language: {language}")
    text = unicodedata.normalize("NFKC", text).casefold()
    output = []
    for index, character in enumerate(text):
        if character in "'’ʼ":
            continue
        numeric_separator = (
            character in ".,"
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        )
        if (
            unicodedata.category(character).startswith("P")
            and character not in "%‰"
            and not numeric_separator
        ):
            if language == "en":
                output.append(" ")
        else:
            output.append(character)
    normalized = "".join(output)
    return " ".join(normalized.split()) if language == "en" else "".join(normalized.split())


def edit_counts(reference: list[str], hypothesis: list[str]) -> dict[str, int]:
    """Unit-cost Levenshtein, with deterministic substitution/delete/insert ties."""
    previous = [(index, 0, 0, index) for index in range(len(hypothesis) + 1)]
    for reference_index, reference_token in enumerate(reference, 1):
        current = [(reference_index, 0, reference_index, 0)]
        for hypothesis_index, hypothesis_token in enumerate(hypothesis, 1):
            if reference_token == hypothesis_token:
                current.append(previous[hypothesis_index - 1])
                continue
            substitution = previous[hypothesis_index - 1]
            deletion = previous[hypothesis_index]
            insertion = current[hypothesis_index - 1]
            candidates = [
                (substitution[0] + 1, substitution[1] + 1, substitution[2], substitution[3]),
                (deletion[0] + 1, deletion[1], deletion[2] + 1, deletion[3]),
                (insertion[0] + 1, insertion[1], insertion[2], insertion[3] + 1),
            ]
            current.append(min(candidates, key=lambda value: value[0]))
        previous = current
    errors, substitutions, deletions, insertions = previous[-1]
    return {
        "reference_units": len(reference),
        "hypothesis_units": len(hypothesis),
        "errors": errors,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
    }


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _timing_summary(values: list[float]) -> dict[str, Any]:
    return {
        "items_with_timing": len(values),
        "sum_ms": sum(values),
        "p50_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
        "percentile_method": "linear interpolation, position=(n-1)*q",
    }


def _quality_metric(language: str) -> dict[str, Any]:
    return {
        "metric": "WER" if language == "en" else "CER",
        "items": 0, "succeeded": 0, "missing": 0, "failed_or_invalid": 0,
        "empty_successful_predictions": 0,
        "reference_units": 0, "hypothesis_units": 0, "errors": 0,
        "substitutions": 0, "deletions": 0, "insertions": 0,
    }


def _accumulate_quality(
    metric: dict[str, Any], counts: dict[str, int], *,
    succeeded: bool, missing: bool, empty_prediction: bool,
) -> None:
    metric["items"] += 1
    metric["succeeded"] += int(succeeded)
    metric["missing"] += int(missing)
    metric["failed_or_invalid"] += int(not succeeded and not missing)
    metric["empty_successful_predictions"] += int(succeeded and empty_prediction)
    for field, value in counts.items():
        metric[field] += value


def _finalize_quality(metrics: dict[str, Any]) -> None:
    for metric in metrics.values():
        metric["corpus_error_rate"] = metric["errors"] / metric["reference_units"] if metric["reference_units"] else None
        metric["success_coverage"] = metric["succeeded"] / metric["items"]
        metric["aggregation"] = "sum(S+D+I)/sum(reference units), including empty hypotheses for failed/missing valid clips"


def evaluate(
    results_path: Path, references_path: Path, manifest_path: Path, *,
    evaluation_kind: str = "smoke", purpose: str | None = None,
    dataset_description: str | None = None,
) -> dict[str, Any]:
    if evaluation_kind not in {"smoke", "benchmark"}:
        raise ValueError("evaluation_kind must be smoke or benchmark")
    manifest = _read(manifest_path)
    if not manifest:
        raise ValueError("Empty manifest cannot supply an evaluation denominator")
    references = {row["item_id"]: row for row in _read(references_path)}
    results = {row["item_id"]: row for row in _read(results_path)}
    expected_ids = {row["item_id"] for row in manifest}
    if set(results) - expected_ids:
        raise ValueError("Results contain item IDs outside the supplied manifest")
    if expected_ids - set(references):
        raise ValueError("Every manifest item must have a reference; missing references cannot be dropped")

    items: list[dict[str, Any]] = []
    language_metrics: dict[str, Any] = {}
    by_source_split: dict[str, Any] = {}
    original_ms, cache_ms, current_ms, uncached_ms, failed_ms = [], [], [], [], []
    timed_audio_seconds = 0.0
    paired_original_ms = 0.0
    all_audio_seconds = 0.0
    audio_duration_items = 0
    models: dict[str, Any] = {}
    for source in manifest:
        item_id, language = source["item_id"], source["language"]
        reference = references[item_id]
        if reference.get("language") != language or reference.get("reference_id") != source.get("reference_id"):
            raise ValueError(f"Reference identity/language mismatch: {item_id}")
        if not isinstance(reference.get("text"), str):
            raise TypeError(f"Reference text must be a string: {item_id}")
        result = results.get(item_id)
        if result is not None and (
            result.get("language") != language
            or result.get("audio_sha256") != source.get("audio_sha256")
        ):
            raise ValueError(f"Result audio identity/language mismatch: {item_id}")
        status = result.get("status", "invalid_result") if result else "missing"
        succeeded = status == "succeeded" and isinstance(result.get("asr_text"), str)
        if status == "succeeded" and not succeeded:
            status = "invalid_result"
        # All source files were validated as real, nonempty speech at preparation.
        # Failed or absent predictions enter the main metric as empty hypotheses.
        hypothesis = result["asr_text"] if succeeded else ""
        normalized_reference = normalize(reference["text"], language)
        normalized_hypothesis = normalize(hypothesis, language)
        reference_units = normalized_reference.split() if language == "en" else list(normalized_reference)
        hypothesis_units = normalized_hypothesis.split() if language == "en" else list(normalized_hypothesis)
        counts = edit_counts(reference_units, hypothesis_units)
        duration = _positive_number(source.get("duration"))
        if duration is None and result:
            duration = _positive_number(result.get("audio_duration_s"))
        if duration is not None:
            all_audio_seconds += duration
            audio_duration_items += 1
        split = source.get("split")
        split = split if isinstance(split, str) and split.strip() else "unspecified"
        group = by_source_split.setdefault(split, {
            "coverage": {
                "expected_valid_audio_items": 0, "result_rows": 0, "succeeded": 0,
                "missing": 0, "failed_or_invalid": 0, "cache_hits": 0,
            },
            "audio": {"total_seconds": 0.0, "items_with_duration": 0},
            "quality_by_language": {},
        })
        metric = language_metrics.setdefault(language, _quality_metric(language))
        split_metric = group["quality_by_language"].setdefault(language, _quality_metric(language))
        for aggregate in (metric, split_metric):
            _accumulate_quality(
                aggregate, counts, succeeded=succeeded, missing=result is None,
                empty_prediction=not normalized_hypothesis,
            )
        coverage = group["coverage"]
        coverage["expected_valid_audio_items"] += 1
        coverage["result_rows"] += int(result is not None)
        coverage["succeeded"] += int(succeeded)
        coverage["missing"] += int(result is None)
        coverage["failed_or_invalid"] += int(result is not None and not succeeded)
        coverage["cache_hits"] += int(bool(result and succeeded and result.get("cache_hit")))
        if duration is not None:
            group["audio"]["total_seconds"] += duration
            group["audio"]["items_with_duration"] += 1
        item = {
            "item_id": item_id, "language": language, "status": status, "source_split": split,
            "metric": metric["metric"], **counts,
            "error_rate": counts["errors"] / counts["reference_units"] if counts["reference_units"] else None,
            "empty_reference": not reference_units,
            "normalized_reference": normalized_reference,
            "normalized_hypothesis": normalized_hypothesis,
            "audio_duration_s": duration,
            "cache_hit": bool(result and succeeded and result.get("cache_hit")),
            "cache_key": result.get("cache_key") if result else None,
            "inference_run_id": result.get("inference_run_id") if result else None,
        }
        if result:
            processing = _nonnegative_number(result.get("processing_ms"))
            inference = _nonnegative_number(result.get("inference_processing_ms"))
            if processing is not None:
                current_ms.append(processing)
                if not succeeded:
                    failed_ms.append(processing)
                elif result.get("cache_hit"):
                    cache_ms.append(processing)
                else:
                    uncached_ms.append(processing)
            if succeeded and inference is not None:
                original_ms.append(inference)
                if duration is not None:
                    timed_audio_seconds += duration
                    paired_original_ms += inference
            item["processing_ms_current_run"] = processing
            item["inference_processing_ms_original"] = inference
            item["error"] = result.get("error")
            if result.get("model"):
                models[json.dumps(result["model"], sort_keys=True)] = result["model"]
        items.append(item)

    _finalize_quality(language_metrics)
    for group in by_source_split.values():
        _finalize_quality(group["quality_by_language"])
        coverage = group["coverage"]
        coverage["success_rate"] = coverage["succeeded"] / coverage["expected_valid_audio_items"]
    total = len(manifest)
    succeeded_count = sum(metric["succeeded"] for metric in language_metrics.values())
    return {
        "schema_version": "anota-speech-smoke-evaluation-v1",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "evaluation_kind": evaluation_kind,
        "purpose": purpose if purpose is not None else (
            SMOKE_PURPOSE if evaluation_kind == "smoke" else
            "Offline ASR benchmark of every valid recording in the supplied manifest"
        ),
        "dataset_description": dataset_description,
        "inputs": {name: {"path": str(path.resolve()), "sha256": _sha(path)} for name, path in (
            ("manifest", manifest_path), ("results", results_path), ("references", references_path))},
        "reference_policy": "References are joined only in this offline evaluation; no inference calls are made",
        "normalization": NORMALIZATION,
        "models": list(models.values()),
        "coverage": {
            "expected_valid_audio_items": total,
            "result_rows": len(results), "succeeded": succeeded_count,
            "success_rate": succeeded_count / total,
            "missing": total - len(results),
            "failed_or_invalid": len(results) - succeeded_count,
            "cache_hits": sum(item["cache_hit"] for item in items),
        },
        "audio": {"total_seconds": all_audio_seconds, "items_with_duration": audio_duration_items},
        "quality_by_language": language_metrics,
        "by_source_split": by_source_split,
        "split_policy": "Group by manifest split only; absent or blank splits use unspecified; failed and missing results remain in each split denominator",
        "timing": {
            "original_inference": {
                **_timing_summary(original_ms),
                "timed_audio_seconds": timed_audio_seconds,
                "aggregate_processing_rtf": paired_original_ms / 1000 / timed_audio_seconds if timed_audio_seconds else None,
                "scope": "Successful original per-item processing retained across cache hits; first inference in each process includes model download/loading when performed, decoding, hash checks and inference",
                "wall_clock_caveat": "Sum of per-item processing; excludes some run-level state/export overhead and is not a pure decoder benchmark",
            },
            "current_run_all_rows": _timing_summary(current_ms),
            "current_run_cache_overhead": _timing_summary(cache_ms),
            "current_run_uncached_successful_processing": _timing_summary(uncached_ms),
            "current_run_failed_processing": _timing_summary(failed_ms),
            "comparison_policy": "Cache overhead is reported separately and must not be presented as model inference speed",
        },
        "items": items,
        "limitations": [
            (
                "Deterministic archive-prefix train samples are not representative or independently held out"
                if evaluation_kind == "smoke" else
                "A published test split does not establish that the evaluated model never saw these recordings during training"
            ),
            *COMMON_LIMITATIONS,
        ],
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="Defaults to manifest.jsonl beside --results")
    parser.add_argument("--evaluation-kind", choices=("smoke", "benchmark"), default="smoke")
    parser.add_argument("--purpose", help="Override the evaluation purpose recorded in the report")
    parser.add_argument("--dataset-description", help="Describe the corpus, revision and evaluation scope")
    args = parser.parse_args()
    manifest = args.manifest or args.results.parent / "manifest.jsonl"
    paths = [path.resolve() for path in (args.results, args.references, manifest, args.output)]
    if len(set(paths)) != len(paths):
        raise ValueError("Results, references, manifest and report must have distinct paths")
    report = evaluate(
        args.results, args.references, manifest, evaluation_kind=args.evaluation_kind,
        purpose=args.purpose, dataset_description=args.dataset_description,
    )
    write_report(args.output, report)
    print(json.dumps({
        "report": str(args.output.resolve()),
        "coverage": report["coverage"],
        "quality_by_language": report["quality_by_language"],
        "by_source_split": report["by_source_split"],
        "original_inference": report["timing"]["original_inference"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
