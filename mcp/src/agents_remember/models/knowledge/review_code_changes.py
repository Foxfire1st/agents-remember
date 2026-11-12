"""Leaf-wide implementation facts, never inferred from an unopened family."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH, KnowledgeModel


class ReviewCodeChange(KnowledgeModel):
    invariant: str
    invariant_key: str
    implementation: Literal["established", "unknown", "not_established"]
    proof: bool = False
    range_unresolved: bool = False
    evidence: tuple[str, ...] = ()
    unknown_reasons: tuple[str, ...] = ()


class ReviewCodeChanges(KnowledgeModel):
    state: Literal["facts", "unavailable"]
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    invariants: tuple[ReviewCodeChange, ...] = ()
