"""Judge abstraction. Judge is a first-pass filter, never the final arbiter:
if it is down, annotation continues untouched. MockJudge keeps demos honest & offline."""
import hashlib
import json
import math
import os
import time

import httpx

from .lf import LF_TO_ERROR
from .models import ERROR_TYPES, SEVERITIES


class JudgeUnavailable(Exception):
    pass


class MockJudge:
    model = "mock-judge"
    is_mock = True

    def evaluate(self, task, lf_results):
        h = int(hashlib.sha256(str(task["id"]).encode()).hexdigest(), 16)
        error_lfs = [r["lf"] for r in lf_results if r["label"] == "ERROR"]
        abstains = sum(1 for r in lf_results if r["label"] == "ABSTAIN")
        errs = sorted({LF_TO_ERROR[n] for n in error_lfs})
        if errs:
            severity = "critical" if {"negation_polarity", "number_unit"} & set(errs) else "major"
        else:
            errs, severity = ["no_error"], "neutral"
        n_err = 0 if errs == ["no_error"] else len(errs)
        adequacy = max(1, int(5 - 1.5 * n_err + 0.5))
        if not error_lfs:
            base = 0.90
        elif error_lfs == ["lf_length_ratio"]:
            base = 0.55          # weak single signal -> low confidence -> routing candidate
        else:
            base = 0.85
        conf = base - 0.08 * abstains + ((h % 7) - 3) / 100.0
        return {"error_types": errs, "worst_severity": severity, "adequacy": adequacy,
                "rationale": "[MOCK] derived from lint signals: "
                             + (", ".join(error_lfs) or "none"),
                "confidence": round(min(0.99, max(0.05, conf)), 3)}


def parse_judge_json(content):
    if not isinstance(content, str):
        raise JudgeUnavailable("judge returned non-text content")
    decoder = json.JSONDecoder()
    raw = None
    for i, ch in enumerate(content):
        if ch != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(content, i)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            raw = candidate
            break
    if raw is None:
        raise JudgeUnavailable("judge returned no JSON")
    errs = raw.get("error_types")
    if (not isinstance(errs, list) or not errs
            or any(not isinstance(e, str) or e not in ERROR_TYPES for e in errs)
            or len(set(errs)) != len(errs)):
        raise JudgeUnavailable("judge returned invalid error_types")
    sev = raw.get("worst_severity")
    if sev not in SEVERITIES:
        raise JudgeUnavailable("judge returned invalid severity")
    if ("no_error" in errs and errs != ["no_error"]
            or (errs == ["no_error"]) != (sev == "neutral")):
        raise JudgeUnavailable("judge returned contradictory labels")
    adequacy, confidence = raw.get("adequacy"), raw.get("confidence")
    if type(adequacy) is not int or not 1 <= adequacy <= 5:
        raise JudgeUnavailable("judge adequacy must be an integer from 1 to 5")
    if (type(confidence) not in (int, float) or not 0 <= confidence <= 1
            or not math.isfinite(confidence)):
        raise JudgeUnavailable("judge confidence must be a finite number from 0 to 1")
    if not isinstance(raw.get("rationale"), str):
        raise JudgeUnavailable("judge rationale must be text")
    return {"error_types": errs, "worst_severity": sev,
            "adequacy": adequacy, "rationale": raw["rationale"][:2000],
            "confidence": confidence}


SYSTEM_TEMPLATE = """You are a strict medical-translation QA judge. Apply this rubric verbatim:

{guideline}

Judge the HYPOTHESIS against the SOURCE. Reason first, then output ONLY one JSON object:
{{"rationale": "<short>", "error_types": [<subset of {etypes}>],
"worst_severity": "<one of {sevs}>", "adequacy": <1-5>, "confidence": <0.0-1.0>}}"""

VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "rationale": {"type": "string"},
        "error_types": {"type": "array", "items": {"type": "string", "enum": ERROR_TYPES}, "minItems": 1},
        "worst_severity": {"type": "string", "enum": SEVERITIES},
        "adequacy": {"type": "integer", "minimum": 1, "maximum": 5},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["rationale", "error_types", "worst_severity", "adequacy", "confidence"],
}


def aggregate_samples(samples):
    """Self-consistency aggregation over k judge samples (temperature > 0).
    Majority error types, modal severity (tie -> stricter), median adequacy,
    confidence = fraction of samples agreeing with the modal severity."""
    k = len(samples)
    if k == 1:
        return samples[0]
    counts = {}
    for s in samples:
        for e in s["error_types"]:
            counts[e] = counts.get(e, 0) + 1
    types = [e for e in ERROR_TYPES if counts.get(e, 0) * 2 > k]
    sev_counts = {}
    for s in samples:
        sev_counts[s["worst_severity"]] = sev_counts.get(s["worst_severity"], 0) + 1
    top = max(sev_counts.values())
    severity = [s for s in SEVERITIES if sev_counts.get(s, 0) == top][-1]
    if not types:
        types = ["no_error"] if severity == "neutral" else samples[0]["error_types"]
    if types == ["no_error"] and severity != "neutral":
        severity = "neutral"
    adequacies = sorted(s["adequacy"] for s in samples)
    adequacy = adequacies[k // 2] if k % 2 else int(sum(adequacies[k // 2 - 1:k // 2 + 1]) / 2 + 0.5)
    agree = sev_counts[severity]
    return {"error_types": types, "worst_severity": severity, "adequacy": adequacy,
            "rationale": samples[0]["rationale"] + f" [self-consistency: {agree}/{k} on severity]",
            "confidence": round(agree / k, 3)}


class OpenAIJudge:
    is_mock = False

    def __init__(self, base_url, model=None, api_key="EMPTY", guideline_text="",
                 timeout=30.0, n_samples=1, max_tokens=512, structured_output=False,
                 prompt_suffix="", observer=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.guideline_text = guideline_text
        self.timeout = float(timeout)
        self.n_samples = int(n_samples)
        self.max_tokens = int(max_tokens)
        self.structured_output = structured_output
        self.prompt_suffix = prompt_suffix
        self.observer = observer  # Optional experiment sink; production does not log content.
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("judge timeout must be finite and greater than zero")
        if self.n_samples <= 0:
            raise ValueError("judge sample count must be greater than zero")
        if not 32 <= self.max_tokens <= 8192:
            raise ValueError("judge max_tokens must be between 32 and 8192")

    def _resolve_model(self):
        if self.model:
            return self.model
        r = httpx.get(f"{self.base_url}/models",
                      headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout)
        r.raise_for_status()
        models = r.json()["data"]
        if not isinstance(models, list) or len(models) != 1:
            raise JudgeUnavailable("set ANOTA_JUDGE_MODEL explicitly when the server lists zero or multiple models")
        self.model = models[0]["id"]
        if not isinstance(self.model, str) or not self.model:
            raise JudgeUnavailable("server returned an invalid model id")
        return self.model

    def _evaluate_once(self, task, temperature):
        system = SYSTEM_TEMPLATE.format(guideline=self.guideline_text,
                                        etypes=ERROR_TYPES, sevs=SEVERITIES)
        user = f"SOURCE:\n{task['source']}\n\nHYPOTHESIS:\n{task['hypothesis']}"
        if self.prompt_suffix:
            user += "\n\n" + self.prompt_suffix
        trace = {"task_id": task.get("id"), "request": None, "response": None,
                 "http_status": None, "error": None}
        started = time.perf_counter()
        try:
            model = self._resolve_model()
            payload = {"model": model, "temperature": temperature, "max_tokens": self.max_tokens,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}
            if self.structured_output:
                payload["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "translation_verdict", "strict": True, "schema": VERDICT_SCHEMA}}
            trace["request"] = payload
            r = httpx.post(f"{self.base_url}/chat/completions",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json=payload,
                           timeout=self.timeout)
            trace["http_status"] = getattr(r, "status_code", None)
            r.raise_for_status()
            trace["response"] = r.json()
            choice = trace["response"]["choices"][0]
            if choice.get("finish_reason") == "length":
                raise JudgeUnavailable("judge response reached its token limit")
            return parse_judge_json(choice["message"]["content"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, JudgeUnavailable) as e:
            trace["error"] = type(e).__name__ + ": " + str(e)
            raise JudgeUnavailable(str(e)) from e
        finally:
            trace["elapsed_seconds"] = time.perf_counter() - started
            if self.observer is not None:
                self.observer(trace)

    def evaluate(self, task, lf_results):
        if self.n_samples == 1:
            return self._evaluate_once(task, temperature=0.0)
        return aggregate_samples(
            [self._evaluate_once(task, temperature=0.7) for _ in range(self.n_samples)])


def get_judge(guideline_text=""):
    if os.environ.get("ANOTA_JUDGE", "mock") == "openai":
        return OpenAIJudge(os.environ.get("ANOTA_JUDGE_BASE_URL", "http://localhost:8000/v1"),
                           os.environ.get("ANOTA_JUDGE_MODEL") or None,
                           os.environ.get("ANOTA_JUDGE_API_KEY", "EMPTY"),
                           guideline_text,
                           timeout=os.environ.get("ANOTA_JUDGE_TIMEOUT", "30"),
                           max_tokens=os.environ.get("ANOTA_JUDGE_MAX_TOKENS", "512"),
                           structured_output=os.environ.get("ANOTA_JUDGE_JSON_SCHEMA", "0") == "1",
                           prompt_suffix=os.environ.get("ANOTA_JUDGE_PROMPT_SUFFIX", ""),
                           n_samples=os.environ.get("ANOTA_JUDGE_SAMPLES", "1"))
    return MockJudge()


def judge_health(j):
    if j.is_mock:
        return {"mode": "mock", "reachable": True}
    try:
        response = httpx.get(f"{j.base_url}/models", timeout=2.0,
                             headers={"Authorization": f"Bearer {j.api_key}"})
        response.raise_for_status()
        return {"mode": "openai", "reachable": True}
    except httpx.HTTPError:
        return {"mode": "openai", "reachable": False}
