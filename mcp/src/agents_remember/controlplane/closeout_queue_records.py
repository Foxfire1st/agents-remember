"""Off-side build vocabulary for one disposable closeout projection."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.controlplane.durable_store import DurableRecord
from agents_remember.models.closeout_projection import (
    CloseoutProjectionMember,
    ProjectionSourceClassification,
)
from agents_remember.models.task_document_ref import TaskDocumentRef


class CloseoutProjectionBuild(DurableRecord):
    """A complete candidate build; never a lock or recoverable semantic owner."""

    schemaVersion: Literal["ar-closeout-projection-build/v1"] = "ar-closeout-projection-build/v1"
    sprintTaskDocumentRef: TaskDocumentRef
    sourceFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    sourceClassification: ProjectionSourceClassification
    members: list[CloseoutProjectionMember] = Field(default_factory=list, max_length=256)
    builtAt: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _terminal_is_empty(self) -> CloseoutProjectionBuild:
        if self.sourceClassification == "terminal" and self.members:
            raise ValueError("terminal projection build must have zero membership")
        return self
