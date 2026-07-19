"""Judge abstraction. Judge is a first-pass filter, never the final arbiter:
if it is down, annotation continues untouched. MockJudge keeps demos honest & offline."""
import hashlib
import json
import os

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
    errs = [e for e in raw.get("error_types", []) if e in ERROR_TYPES] or ["no_error"]
    sev = raw.get("worst_severity")
    if sev not in SEVERITIES:
        sev = "neutral" if errs == ["no_error"] else "major"
    return {"error_types": errs, "worst_severity": sev,
            "adequacy": min(5, max(1, int(raw.get("adequacy", 3)))),
            "rationale": str(raw.get("rationale", ""))[:2000],
            "confidence": min(1.0, max(0.0, float(raw.get("confidence", 0.7))))}


SYSTEM_TEMPLATE = """You are a strict medical-translation QA judge. Apply this rubric verbatim:

{guideline}

Judge the HYPOTHESIS against the SOURCE. Reason first, then output ONLY one JSON object:
{{"rationale": "<short>", "error_types": [<subset of {etypes}>],
"worst_severity": "<one of {sevs}>", "adequacy": <1-5>, "confidence": <0.0-1.0>}}"""


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
                 timeout=30.0, n_samples=1):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.guideline_text = guideline_text
        self.timeout = timeout
        self.n_samples = max(1, int(n_samples))

    def _resolve_model(self):
        if self.model:
            return self.model
        r = httpx.get(f"{self.base_url}/models",
                      headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout)
        self.model = r.json()["data"][0]["id"]
        return self.model

    def _evaluate_once(self, task, temperature):
        system = SYSTEM_TEMPLATE.format(guideline=self.guideline_text,
                                        etypes=ERROR_TYPES, sevs=SEVERITIES)
        user = f"SOURCE:\n{task['source']}\n\nHYPOTHESIS:\n{task['hypothesis']}"
        try:
            model = self._resolve_model()
            r = httpx.post(f"{self.base_url}/chat/completions",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"model": model, "temperature": temperature,
                                 "messages": [{"role": "system", "content": system},
                                              {"role": "user", "content": user}]},
                           timeout=self.timeout)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
            raise JudgeUnavailable(str(e)) from e
        return parse_judge_json(content)

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
                           n_samples=os.environ.get("ANOTA_JUDGE_SAMPLES", "1"))
    return MockJudge()


def judge_health(j):
    if j.is_mock:
        return {"mode": "mock", "reachable": True}
    try:
        httpx.get(f"{j.base_url}/models", timeout=2.0)
        return {"mode": "openai", "reachable": True}
    except httpx.HTTPError:
        return {"mode": "openai", "reachable": False}
