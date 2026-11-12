"""Canonical endpoint and edge schemas shared by sprint graphs and retirement receipts."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from agents_remember.models.task_document_ref import TaskDocumentRef


class _Doc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SprintExecutionEndpoint(_Doc):
    """One edge endpoint: a bare master ref, or a leaf id sampling the target segment.

    A bare ``ref`` addresses the master's only node (a lump, or its single segment);
    ``ref`` + ``leafId`` addresses the segment node containing that leaf. Resolution
    to a node happens in graph validation, never at parse time.
    """

    ref: TaskDocumentRef
    leafId: str | None = None

    @field_validator("leafId")
    @classmethod
    def _trim_nonblank_leaf_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("execution-graph endpoint leafId must not be blank")
        return trimmed


class SprintExecutionEdge(_Doc):
    """One reasoned predecessor edge in the sprint's activity-on-node graph."""

    predecessor: TaskDocumentRef | SprintExecutionEndpoint
    successor: TaskDocumentRef | SprintExecutionEndpoint
    reason: str
    judgmentId: str | None = None

    @field_validator("reason")
    @classmethod
    def _trim_nonblank_reason(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("execution-graph edge reason must not be blank")
        return trimmed

    @field_validator("judgmentId")
    @classmethod
    def _trim_nonblank_judgment_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("execution-graph edge judgmentId must not be blank")
        return trimmed

    @model_validator(mode="after")
    def _check_distinct_endpoints(self) -> Self:
        if self.predecessor == self.successor:
            raise ValueError("execution-graph edge cannot point a node to itself")
        return self
