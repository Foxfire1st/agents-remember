"""Repository namespace identity for the knowledge substrate.

A namespace is a stable stored identifier. It is not a filesystem root, a branch name or a
repository display name: those all change while the knowledge they scope does not, and a
namespace that moved with them would silently re-scope every revision underneath it.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    UUID_PATTERN,
    KnowledgeModel,
)


class RepositoryIdentity(KnowledgeModel):
    """One repository's knowledge namespace and its authority home."""

    repository_id: str = Field(pattern=UUID_PATTERN)
    authority_home: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)

    @field_validator("authority_home")
    @classmethod
    def _require_nonblank_authority_home(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("authority_home must not be blank")
        return cleaned
