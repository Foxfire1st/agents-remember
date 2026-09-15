"""The provenance envelope stored with every authored knowledge row.

Authorship records who authored a value, under which authority, in which operation, at which
recorded time, and from which explicit source references. It is authored input: the store
never manufactures an author, and importing a payload preserves the original envelope instead
of replacing it with the importer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import Field, field_validator

from agents_remember.models.knowledge.base import (
    ACCEPTED_STATE,
    PROPOSED_STATE,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
    KnowledgeState,
)

__all__ = [
    "ACCEPTED_STATE",
    "PROPOSED_STATE",
    "Authorship",
    "KnowledgeState",
]


class Authorship(KnowledgeModel):
    """One immutable provenance envelope for an authored knowledge value.

    ``recorded_at`` is normalized UTC; the admitted application assigns it, so a caller
    cannot back-date a record by supplying a local-time string. ``origin_refs`` names the
    explicit sources this value was authored from. An empty tuple means no source was
    declared, which is a fact about the record rather than permission to infer one.
    """

    actor_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    authorization_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    # A real ``UUID``, not a pattern-constrained string: Pydantic normalizes the accepted
    # spellings to one value, and the canonical stored text is derived from it at the
    # storage boundary rather than validated as text here.
    operation_id: UUID
    recorded_at: str = Field(min_length=1, max_length=64)
    origin_refs: tuple[str, ...] = ()

    @field_validator("actor_ref", "authorization_ref")
    @classmethod
    def _require_nonblank_reference(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("authorship references must not be blank")
        return cleaned

    @field_validator("origin_refs")
    @classmethod
    def _require_nonblank_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item for item in cleaned):
            raise ValueError("origin references must not be blank")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("origin references must be unique within one authorship envelope")
        return cleaned

    @field_validator("recorded_at")
    @classmethod
    def _require_normalized_utc(cls, value: str) -> str:
        """Accept only a timezone-aware instant, stored as normalized UTC.

        A naive or offset-bearing spelling would make two equal instants compare as different
        stored text, so the canonical ``...+00:00`` UTC form is required.
        """

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"recorded_at must be an ISO-8601 timestamp: {value!r}") from error
        if parsed.tzinfo is None:
            raise ValueError("recorded_at must carry a timezone offset")
        normalized = parsed.astimezone(UTC)
        if normalized.isoformat() != value:
            raise ValueError(
                f"recorded_at must be normalized UTC (expected {normalized.isoformat()!r})"
            )
        return value
