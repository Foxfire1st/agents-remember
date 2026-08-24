"""Public request/response envelope for the disposable closeout projection."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.models.base import ToolResponse
from agents_remember.models.closeout_projection import (
    MAX_CLOSEOUT_CANDIDATES,
    MAX_CLOSEOUT_SHORT_TEXT,
    MAX_CLOSEOUT_SOURCE_PROBLEMS,
    MAX_CLOSEOUT_TEXT,
    CloseoutProjectionMember,
    ProjectionServiceCondition,
    ProjectionSourceClassification,
    ProjectionSourceProblem,
)
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.task_document_ref import TaskDocumentRef

MAX_CLOSEOUT_MASTERS = 256
MAX_CLOSEOUT_GRAPH_EDGES = 4096
MAX_QUEUE_EVIDENCE = MAX_CLOSEOUT_SOURCE_PROBLEMS
MAX_QUEUE_REASONS = MAX_CLOSEOUT_MASTERS + 8
MAX_QUEUE_TEXT = MAX_CLOSEOUT_TEXT
MAX_QUEUE_SHORT_TEXT = MAX_CLOSEOUT_SHORT_TEXT

QueueAction = Literal["status", "rebuild"]


class CloseoutQueueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: QueueAction
    sprint_task_document_ref: TaskDocumentRef
    caller: DeclaredCaller | None = None


class CloseoutQueueResponse(ToolResponse):
    operation: Literal["closeout_queue"] = "closeout_queue"
    action: QueueAction
    state: ProjectionServiceCondition
    summary: str = Field(max_length=MAX_CLOSEOUT_TEXT)
    sprintTaskDocumentRef: TaskDocumentRef
    revision: int = Field(ge=0)
    sourceClassification: ProjectionSourceClassification | None = None
    sourceFingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effectiveSourceFingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    sourceProblems: list[ProjectionSourceProblem] = Field(
        default_factory=list, max_length=MAX_CLOSEOUT_SOURCE_PROBLEMS
    )
    members: list[CloseoutProjectionMember] = Field(
        default_factory=list, max_length=MAX_CLOSEOUT_CANDIDATES
    )
    firstReadyGenerationId: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    nextAction: str | None = Field(default=None, max_length=MAX_CLOSEOUT_TEXT)
    updatedAt: str = Field(max_length=MAX_CLOSEOUT_SHORT_TEXT)
