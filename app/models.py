"""Schema constants + request payloads. Mirrors AQB Argilla schema exactly."""
from pydantic import BaseModel, Field, model_validator

ERROR_TYPES = ["no_error", "mistranslation", "omission", "addition", "terminology",
               "number_unit", "negation_polarity", "grammar", "punctuation"]
SEVERITIES = ["neutral", "minor", "major", "critical"]
SEVERITY_WEIGHTS = {"neutral": 0, "minor": 1, "major": 5, "critical": 25}
LANG_PROFILES = ["en-es", "zh-en"]


class AnnotationPayload(BaseModel):
    error_types: list[str] = Field(min_length=1)
    worst_severity: str
    adequacy: int = Field(ge=1, le=5)
    fluency: int = Field(ge=1, le=5)
    correction: str = ""
    note: str = ""
    elapsed_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_rules(self):
        unknown = set(self.error_types) - set(ERROR_TYPES)
        if unknown:
            raise ValueError(f"unknown error_types: {unknown}")
        if len(set(self.error_types)) != len(self.error_types):
            raise ValueError("duplicate error_types")
        if self.worst_severity not in SEVERITIES:
            raise ValueError(f"unknown severity: {self.worst_severity}")
        if "no_error" in self.error_types:
            if len(self.error_types) > 1:
                raise ValueError("no_error is exclusive")
            if self.worst_severity != "neutral":
                raise ValueError("no_error forces severity=neutral")
        elif self.worst_severity == "neutral":
            raise ValueError("a real error cannot have severity=neutral")
        if self.worst_severity == "critical" and not self.note.strip():
            raise ValueError("critical requires a note (QA rubric rule)")
        return self


class SubmitRequest(AnnotationPayload):
    assignment_id: int
    annotator: str = Field(min_length=1)


class UncertainSpan(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("uncertain span end must be greater than start")
        if not self.reason.strip():
            raise ValueError("uncertain span requires a reason")
        return self


class SpeechReviewContext(BaseModel):
    """Client telemetry, validated against server-owned task and batch policy."""
    model_config = {"extra": "forbid"}

    analysis_id: str | None = Field(default=None, min_length=64, max_length=64)
    hints_exposed: bool = False
    played_issue_ids: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def valid_ids(self):
        if self.analysis_id is not None and any(c not in "0123456789abcdef" for c in self.analysis_id):
            raise ValueError("analysis_id must be a lowercase SHA256 digest")
        if any(not value or len(value) > 128 for value in self.played_issue_ids):
            raise ValueError("invalid issue id")
        if len(set(self.played_issue_ids)) != len(self.played_issue_ids):
            raise ValueError("duplicate issue ids")
        return self


class SpeechSubmitRequest(BaseModel):
    assignment_id: int = Field(gt=0)
    annotator: str = Field(min_length=1)
    corrected_transcript: str = Field(max_length=100000)
    uncertain_spans: list[UncertainSpan] = Field(default_factory=list, max_length=1000)
    note: str = Field(default="", max_length=10000)
    elapsed_ms: int = Field(default=0, ge=0)
    active_ms: int = Field(default=0, ge=0)
    review_context: SpeechReviewContext | None = None

    @model_validator(mode="after")
    def timing(self):
        if self.active_ms > self.elapsed_ms:
            raise ValueError("active_ms cannot exceed elapsed_ms")
        return self
