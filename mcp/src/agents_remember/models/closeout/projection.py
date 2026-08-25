"""Neutral persisted projection state and task-publication effect models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_remember.models.closeout.source import PriorityGrade
from agents_remember.models.task_document_ref import TaskDocumentRef

MAX_CLOSEOUT_CANDIDATES = 256
MAX_CLOSEOUT_SOURCE_PROBLEMS = 256
MAX_CLOSEOUT_REASONS = 264
MAX_CLOSEOUT_TEXT = 8192
MAX_CLOSEOUT_SHORT_TEXT = 256

ProjectionServiceCondition = Literal["valid-built", "invalid-empty"]
ProjectionSourceClassification = Literal["active", "terminal"]
ProjectionMemberClassification = Literal["ready", "waiting", "blocked"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectionSourceProblem(_StrictModel):
    kind: Literal["task", "door", "series", "projection"]
    address: str = Field(min_length=1, max_length=MAX_CLOSEOUT_TEXT)
    state: Literal["missing", "unreadable", "invalid"]
    errorType: str = Field(min_length=1, max_length=MAX_CLOSEOUT_SHORT_TEXT)
    repairAction: str = Field(min_length=1, max_length=MAX_CLOSEOUT_TEXT)


class CloseoutProjectionMember(_StrictModel):
    generationId: str = Field(pattern=r"^[0-9a-f]{64}$")
    taskDocumentRef: TaskDocumentRef
    owningMaster: TaskDocumentRef
    contractPath: str = Field(min_length=1, max_length=MAX_CLOSEOUT_TEXT)
    candidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    sourceDoorFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification: ProjectionMemberClassification
    reasons: list[str] = Field(default_factory=list, max_length=MAX_CLOSEOUT_REASONS)
    priority: PriorityGrade
    order: int = Field(ge=0)

    @field_validator("reasons")
    @classmethod
    def _bounded_nonblank_reasons(cls, value: list[str]) -> list[str]:
        cleaned = [reason.strip() for reason in value]
        if any(not reason or len(reason) > MAX_CLOSEOUT_TEXT for reason in cleaned):
            raise ValueError("projection reasons must be nonblank and bounded")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("projection reasons must be unique")
        return cleaned


class CloseoutQueueState(_StrictModel):
    schemaVersion: Literal["ar-closeout-projection/v2"] = "ar-closeout-projection/v2"
    sprintTaskDocumentRef: TaskDocumentRef
    revision: int = Field(ge=0)
    serviceCondition: ProjectionServiceCondition
    sourceClassification: ProjectionSourceClassification | None = None
    sourceFingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    members: list[CloseoutProjectionMember] = Field(
        default_factory=list, max_length=MAX_CLOSEOUT_CANDIDATES
    )
    sourceProblems: list[ProjectionSourceProblem] = Field(
        default_factory=list, max_length=MAX_CLOSEOUT_SOURCE_PROBLEMS
    )
    updatedAt: str = Field(min_length=1, max_length=MAX_CLOSEOUT_SHORT_TEXT)

    @model_validator(mode="after")
    def _condition_is_exact(self) -> CloseoutQueueState:
        identities = [member.generationId for member in self.members]
        task_refs = [member.taskDocumentRef.key for member in self.members]
        if len(identities) != len(set(identities)) or len(task_refs) != len(set(task_refs)):
            raise ValueError("a projection may contain each waiting generation and task once")
        if self.serviceCondition == "invalid-empty":
            if self.members or self.sourceFingerprint is not None or self.sourceClassification:
                raise ValueError("invalid-empty carries no membership or accepted source identity")
        elif self.sourceFingerprint is None or self.sourceClassification is None:
            raise ValueError("valid-built requires one exact source identity and classification")
        elif self.sourceProblems:
            raise ValueError("valid-built cannot carry unresolved source problems")
        if self.sourceClassification == "terminal" and self.members:
            raise ValueError("terminal valid-built projections are empty")
        return self


class ProjectionInvalidationResult(_StrictModel):
    outcome: Literal[
        "persisted-empty",
        "already-empty",
        "not-created",
        "recovered-malformed",
        "would-recover-malformed",
        "would-persist-empty",
        "failed",
    ]
    revision: int | None = Field(default=None, ge=0)
    diagnostic: ProjectionSourceProblem | None = None


class ProjectionRebuildResult(_StrictModel):
    outcome: Literal[
        "published",
        "already-current",
        "source-changed",
        "source-unreadable",
        "would-publish",
        "not-attempted",
    ]
    revision: int | None = Field(default=None, ge=0)
    sourceFingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sourceClassification: ProjectionSourceClassification | None = None
    memberCount: int = Field(default=0, ge=0)
    sourceProblems: list[ProjectionSourceProblem] = Field(
        default_factory=list, max_length=MAX_CLOSEOUT_SOURCE_PROBLEMS
    )


class TaskDocProjectionEffect(_StrictModel):
    sprintTaskDocumentRef: TaskDocumentRef
    queueExisted: bool
    priorRevision: int | None = Field(default=None, ge=0)
    priorSourceFingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    invalidation: ProjectionInvalidationResult
    rebuild: ProjectionRebuildResult
    rebuiltRevision: int | None = Field(default=None, ge=0)
    nextAction: str | None = Field(default=None, max_length=MAX_CLOSEOUT_TEXT)

    @model_validator(mode="after")
    def _incomplete_has_action(self) -> TaskDocProjectionEffect:
        complete = self.rebuild.outcome in {
            "published",
            "already-current",
            "would-publish",
        }
        if complete == (self.nextAction is not None):
            raise ValueError("only an incomplete projection effect requires a next action")
        return self
