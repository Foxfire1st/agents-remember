"""Bounded, task-owned proof for retrying an individual master retirement."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_execution_edges import SprintExecutionEdge, SprintExecutionEndpoint


class RetirementEdgeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predecessor: TaskDocumentRef | SprintExecutionEndpoint
    successor: TaskDocumentRef | SprintExecutionEndpoint


class MasterRetirementProof(BaseModel):
    """The one record of a master's retirement, and what a repeated request is compared with.

    A sprint row carries it when a sprint commanded the master; otherwise it is stored in the
    master's own folder. Where the folder is then tells how far the retirement has come.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal["master-retirement/v1"] = "master-retirement/v1"
    masterRef: TaskDocumentRef
    archiveRef: TaskDocumentRef
    reason: str = Field(min_length=1, max_length=4096)
    retiredAt: str
    removedOrchestrates: list[str] = Field(max_length=2048)
    removedGraphNodes: int = Field(ge=0, le=2048)
    removedEdges: list[SprintExecutionEdge] = Field(max_length=2048)
    affirmedEdges: list[RetirementEdgeSelection] = Field(max_length=2048)
    masterJsonSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    masterMarkdownSha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # What the readiness found and left as it is when the retirement was admitted: branches and
    # worktrees that stay in place, and contract cells it could not interpret.
    readinessFacts: list[str] = Field(default_factory=list, max_length=1024)

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("master retirement requires a nonblank reason")
        return trimmed

    @field_validator("retiredAt")
    @classmethod
    def _timestamp(cls, value: str) -> str:
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("master retirement time requires a timezone")
        return value
