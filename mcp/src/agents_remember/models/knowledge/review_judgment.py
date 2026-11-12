"""The selected record's leaf judgments and proof entries, read without conclusions."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    PROSE_MAX_LENGTH,
    KnowledgeModel,
)
from agents_remember.models.knowledge.effect import EffectLabel
from agents_remember.models.knowledge_files.shapes import RequirementReference


class ReviewJudgmentRow(KnowledgeModel):
    """One history row, including non-meaning judgments and its file's closed flag."""

    attempt: int = Field(ge=1)
    row_id: str
    subject: str
    disposition: str
    effect: EffectLabel | None = None
    revision: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    because: tuple[str | RequirementReference, ...] = ()
    covered_entries: int | None = Field(default=None, ge=0)
    examined_members: int | None = Field(default=None, ge=0)
    history_path: str
    closed: bool


class ReviewJudgmentProof(KnowledgeModel):
    """A proof's authored facet and its entry state at the after code tree, not a run result."""

    id: str
    invariant: str
    path: str
    symbol: str | None = None
    facet: str
    state: Literal["current", "stale", "unverifiable"]
    detail: str


class ReviewJudgmentPane(KnowledgeModel):
    """Rows, a measured absence, or an unreadable history; proofs survive history failures."""

    state: Literal["rows", "none", "unavailable"]
    rows: tuple[ReviewJudgmentRow, ...] = ()
    proofs: tuple[ReviewJudgmentProof, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_one_history_state(self) -> ReviewJudgmentPane:
        if (self.state == "rows") != bool(self.rows):
            raise ValueError("judgment rows are present exactly in the rows state")
        return self
