"""Ambient caller identity supplied as request data.

The plane injects a hosted seat into AR-launched processes. A caller without a
plane identity (an external or ambient agent) declares its structural identity
as request data instead. Every consuming tool validates the declared role and
task document against its own policy before any mutation -- the declaration
grants no authority the same role/document pair would not have from a seat.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_remember.models.task_document_ref import TaskDocumentRef

MAX_DECLARED_ROLE_LENGTH = 64


class DeclaredCaller(BaseModel):
    """One declared structural identity (role + canonical task document).

    Strict and bounded: the role is a short non-blank label and the document is
    a canonical ``TaskDocumentRef``. Semantic authorization (may this role grade,
    select, decide, or declare) stays in the consuming mechanism -- this model
    only bounds the shape of what an ambient caller may supply.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(max_length=MAX_DECLARED_ROLE_LENGTH)
    task_document_ref: TaskDocumentRef

    @field_validator("role")
    @classmethod
    def _nonblank_role(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("declared caller role must not be blank")
        return cleaned
