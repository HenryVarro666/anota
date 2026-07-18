import pytest
from pydantic import ValidationError
from app.models import ERROR_TYPES, SEVERITIES, SEVERITY_WEIGHTS, AnnotationPayload

BASE = dict(error_types=["omission"], worst_severity="major", adequacy=3, fluency=4)

def test_constants():
    assert ERROR_TYPES[0] == "no_error" and len(ERROR_TYPES) == 9
    assert SEVERITIES == ["neutral", "minor", "major", "critical"]
    assert SEVERITY_WEIGHTS == {"neutral": 0, "minor": 1, "major": 5, "critical": 25}

def test_valid_payload():
    assert AnnotationPayload(**BASE).error_types == ["omission"]

def test_no_error_exclusive():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "error_types": ["no_error", "omission"], "worst_severity": "neutral"})

def test_no_error_forces_neutral():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "error_types": ["no_error"], "worst_severity": "minor"})
    ok = AnnotationPayload(**{**BASE, "error_types": ["no_error"], "worst_severity": "neutral"})
    assert ok.worst_severity == "neutral"

def test_real_error_cannot_be_neutral():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "worst_severity": "neutral"})

def test_critical_requires_note():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "worst_severity": "critical"})
    ok = AnnotationPayload(**{**BASE, "worst_severity": "critical", "note": "dosage flipped"})
    assert ok.note

def test_unknown_error_type_rejected():
    with pytest.raises(ValidationError):
        AnnotationPayload(**{**BASE, "error_types": ["typo"]})
