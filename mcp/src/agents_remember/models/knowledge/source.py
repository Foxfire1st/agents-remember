"""Shared source vocabulary: what a knowledge claim points at, and in which revision.

One locator union is shared by storage and by any reader or differ. A stored locator is a
well-formed record even when no resolver currently supports it: a symbol locator remains
readable after the symbol moves, and losing resolvability never erases the claim. The blob
identity is a Git object identity, not a copy of the bytes -- there is no second content
store here.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    KnowledgeModel,
)


class FileLocator(KnowledgeModel):
    """The whole file at the recorded blob."""

    kind: Literal["file"] = "file"


class LineRangeLocator(KnowledgeModel):
    """A one-based inclusive line range inside the recorded blob."""

    kind: Literal["line_range"] = "line_range"
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @field_validator("end_line")
    @classmethod
    def _require_ordered_range(cls, value: int, info: object) -> int:
        start = getattr(info, "data", {}).get("start_line")
        if isinstance(start, int) and value < start:
            raise ValueError("end_line must not precede start_line")
        return value


class SymbolLocator(KnowledgeModel):
    """A qualified symbol name, observed through the shipped extractor rather than guessed at.

    The knowledge read rail resolves this kind: it reads the recorded blob out of the requested tree
    and asks the same tree-sitter machinery the citation fixer, repair and migration paths use
    whether those exact bytes bind the name. A name they do not bind is reported as a mismatch, and
    a suffix with no grammar is reported unsupported -- the recorded claim is carried on every
    outcome, because an observation that cannot be made is a fact about the reader and not about
    the claim.
    """

    kind: Literal["symbol"] = "symbol"
    language: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    qualified_name: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)

    @field_validator("language", "qualified_name")
    @classmethod
    def _require_nonblank_symbol_part(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("symbol locator parts must not be blank")
        return cleaned


SourceLocator = Annotated[
    FileLocator | LineRangeLocator | SymbolLocator,
    Field(discriminator="kind"),
]
