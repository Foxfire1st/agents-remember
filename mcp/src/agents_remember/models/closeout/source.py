"""Neutral typed inputs and evidence for closeout door source publication."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_CLOSEOUT_SOURCE_EVIDENCE = 256
MAX_CLOSEOUT_SOURCE_TEXT = 8192
MAX_CLOSEOUT_SOURCE_SHORT_TEXT = 256

PriorityGrade = Literal["critical", "high", "normal", "low"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateAdmissionFacts(_StrictModel):
    resourceReady: bool = True
    resourceReason: str = Field(default="", max_length=MAX_CLOSEOUT_SOURCE_TEXT)
    admissionReady: bool = True
    admissionReason: str = Field(default="", max_length=MAX_CLOSEOUT_SOURCE_TEXT)

    @model_validator(mode="after")
    def _explain_false_facts(self) -> CandidateAdmissionFacts:
        self.resourceReason = self.resourceReason.strip()
        self.admissionReason = self.admissionReason.strip()
        if not self.resourceReady and not self.resourceReason:
            raise ValueError("resourceReason is required when resourceReady is false")
        if not self.admissionReady and not self.admissionReason:
            raise ValueError("admissionReason is required when admissionReady is false")
        return self


class SchedulingGradeInput(_StrictModel):
    priority: PriorityGrade
    judgmentId: str = Field(min_length=1, max_length=MAX_CLOSEOUT_SOURCE_SHORT_TEXT)
    urgency: str | None = Field(default=None, max_length=MAX_CLOSEOUT_SOURCE_TEXT)
    risk: str | None = Field(default=None, max_length=MAX_CLOSEOUT_SOURCE_TEXT)


class SchedulingGrade(_StrictModel):
    priority: PriorityGrade
    urgency: str | None = Field(default=None, max_length=MAX_CLOSEOUT_SOURCE_TEXT)
    risk: str | None = Field(default=None, max_length=MAX_CLOSEOUT_SOURCE_TEXT)
    judgmentId: str = Field(min_length=1, max_length=MAX_CLOSEOUT_SOURCE_SHORT_TEXT)
    subject: str = Field(min_length=1, max_length=MAX_CLOSEOUT_SOURCE_TEXT)
    rationale: str = Field(min_length=1, max_length=MAX_CLOSEOUT_SOURCE_TEXT)
    evidenceRefs: list[str] = Field(default_factory=list, max_length=MAX_CLOSEOUT_SOURCE_EVIDENCE)
    decidedBy: str = Field(min_length=1, max_length=MAX_CLOSEOUT_SOURCE_SHORT_TEXT)
    confidence: str = Field(min_length=1, max_length=MAX_CLOSEOUT_SOURCE_SHORT_TEXT)
    supersedes: str = Field(default="", max_length=MAX_CLOSEOUT_SOURCE_SHORT_TEXT)


class EvidenceFact(_StrictModel):
    path: str = Field(min_length=1, max_length=MAX_CLOSEOUT_SOURCE_TEXT)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RouteReviewFact(_StrictModel):
    required: bool
    status: str = Field(max_length=MAX_CLOSEOUT_SOURCE_SHORT_TEXT)
    candidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    verdict: str | None = Field(default=None, max_length=MAX_CLOSEOUT_SOURCE_SHORT_TEXT)
    verdictRef: str | None = Field(default=None, max_length=MAX_CLOSEOUT_SOURCE_TEXT)
    routeCount: int | None = Field(default=None, ge=1)
    recordSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: list[EvidenceFact] = Field(
        default_factory=list, max_length=MAX_CLOSEOUT_SOURCE_EVIDENCE
    )
