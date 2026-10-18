"""A gate-owned item verdict and the witness or reason for it."""

from typing import Literal

from pydantic import Field

from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH, KnowledgeModel


class ItemVerdict(KnowledgeModel):
    state: Literal["answered", "open", "undecided"]
    witness: str | None = None
    row_id: str | None = None
    reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
