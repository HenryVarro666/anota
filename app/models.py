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
