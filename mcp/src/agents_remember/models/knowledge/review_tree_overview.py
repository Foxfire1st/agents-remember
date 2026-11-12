"""Compact review facts, distinct from location rows and full worklist evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agents_remember.models.knowledge.base import KnowledgeModel

EntryCurrentness = Literal["stale", "unverifiable", "current", "not measured"]


class ReviewSubjectSummary(KnowledgeModel):
    """Exact counts and ordering facts for one invariant, without loading its rows."""

    invariant_key: str
    changed: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    not_comparable: int = Field(ge=0)
    changed_span: int = Field(ge=0)
    changed_paths: tuple[str, ...] = ()
    entry_currentness: EntryCurrentness = "not measured"
    proofs: dict[EntryCurrentness, int] = Field(default_factory=dict)
    # The identity of what this row summarizes: every one of the invariant's entries, sides
    # included (D576). Two comparisons' rows are the same row only when their entries are.
    content: str = ""


class ReviewOverview(KnowledgeModel):
    """Complete invariant summaries, or a named inability to measure them."""

    state: Literal["ready", "unavailable"]
    subjects: tuple[ReviewSubjectSummary, ...] = ()
    reason: str | None = None


class ReviewInvariantChanges(KnowledgeModel):
    """The invariants that differ between two comparisons, by key, or why they cannot be named.

    ``unavailable`` is never an empty list: a page that cannot be told what changed renews all.
    """

    state: Literal["named", "unavailable"]
    from_comparison: int
    to_comparison: int
    invariants: tuple[str, ...] = ()
    reason: str | None = None


class ReviewWorklistSummary(KnowledgeModel):
    """The gate's count, planning marks and file counts; never fabricated item evidence."""

    # None has exactly the complete worklist's "not available" meaning.
    open_count: int | None = Field(default=None, ge=0)
    planning: dict[str, str] = Field(default_factory=dict)
    file_open_counts: dict[str, int] = Field(default_factory=dict)
