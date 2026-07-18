"""Four built-in labeling functions (Snorkel lesson: prefer ABSTAIN over guessing).
Rules mirror AQB weak_supervision/lfs.py; profiles select cue lexicons per language pair."""
import re

ERROR, OK, ABSTAIN = "ERROR", "OK", "ABSTAIN"

LF_TO_ERROR = {"lf_negation_drop": "negation_polarity", "lf_number_mismatch": "number_unit",
               "lf_untranslated_fragment": "omission", "lf_length_ratio": "omission"}

PROFILES = {
    "en-es": {
        "src_neg": re.compile(r"\b(no|not|never|without|don't|do not|cannot|must not)\b", re.I),
        "hyp_neg": re.compile(r"\b(no|nunca|sin|jamás|tampoco|ni)\b", re.I),
        "len_bounds": (0.7, 1.6),
        "src_lang": "en",
        "hyp_lang": "es",
    },
    "zh-en": {
        "src_neg": re.compile(r"[不没無无别勿禁未]"),
        "hyp_neg": re.compile(r"\b(no|not|never|without|none|neither|nor)\b", re.I),
        "len_bounds": (1.0, 6.0),
        "src_lang": "zh",
        "hyp_lang": "en",
    },
}

ZH_DIGITS = {"零": "0", "一": "1", "两": "2", "二": "2", "三": "3", "四": "4",
             "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
EN_WORDS = {"once": "1", "twice": "2", "thrice": "3"}
ES_WORDS = {"dos": "2", "tres": "3", "once": "11"}
NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
CJK_RE = re.compile(r"[一-鿿]")


def _numbers(text, lang):
    if lang == "zh":
        for zh, d in ZH_DIGITS.items():
            text = text.replace(zh, d)
    elif lang == "en":
        for en, d in EN_WORDS.items():
            text = re.sub(r"\b" + en + r"\b", d, text, flags=re.I)
    elif lang == "es":
        for es, d in ES_WORDS.items():
            text = re.sub(r"\b" + es + r"\b", d, text, flags=re.I)
    return set(NUM_RE.findall(text))


def _lf_negation(src, hyp, prof):
    m = prof["src_neg"].search(src)
    if not m:
        return ABSTAIN, ""
    if prof["hyp_neg"].search(hyp):
        return OK, m.group(0)
    return ERROR, f"source negation '{m.group(0)}' has no counterpart"


def _lf_numbers(src, hyp, prof):
    s, h = _numbers(src, prof["src_lang"]), _numbers(hyp, prof["hyp_lang"])
    if not s:
        return ABSTAIN, ""
    missing = s - h
    if missing:
        return ERROR, f"missing numbers: {sorted(missing)}"
    return OK, f"all {len(s)} numbers preserved"


def _lf_untranslated(src, hyp, lang_profile):
    if lang_profile == "zh-en":
        residue = CJK_RE.findall(hyp)
        if residue:
            return ERROR, f"CJK residue: {''.join(residue[:5])}"
        return OK, ""
    src_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ']+", src)]
    hyp_lower = hyp.lower()
    for i in range(len(src_tokens) - 3):
        window = " ".join(src_tokens[i:i + 4])
        if window in hyp_lower:
            return ERROR, f"untranslated span: '{window}'"
    return OK, ""


def _lf_length(src, hyp, prof):
    if len(src) < 10:
        return ABSTAIN, ""
    ratio = len(hyp) / len(src)
    lo, hi = prof["len_bounds"]
    if ratio < lo or ratio > hi:
        return ERROR, f"length ratio {ratio:.2f} outside [{lo}, {hi}]"
    return OK, f"ratio {ratio:.2f}"


def run_lfs(source, hypothesis, lang_profile):
    prof = PROFILES[lang_profile]
    results = []
    for name, (label, evidence) in [
        ("lf_negation_drop", _lf_negation(source, hypothesis, prof)),
        ("lf_number_mismatch", _lf_numbers(source, hypothesis, prof)),
        ("lf_untranslated_fragment", _lf_untranslated(source, hypothesis, lang_profile)),
        ("lf_length_ratio", _lf_length(source, hypothesis, prof)),
    ]:
        results.append({"lf": name, "label": label, "evidence": evidence})
    return results
