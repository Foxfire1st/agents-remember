"""Shared source vocabulary: what a knowledge claim points at, and in which revision.

One locator union is shared by storage and by any reader or differ. A stored locator is a
well-formed record even when no resolver currently supports it: a symbol locator remains
readable after the symbol moves, and losing resolvability never erases the claim. The blob
identity is a Git object identity, not a copy of the bytes -- there is no second content
store here.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    UUID_PATTERN,
    KnowledgeModel,
)


class GitBlobIdentity(KnowledgeModel):
    """The one v1 source identity: an exact Git object in the selected repository."""

    kind: Literal["git_blob"] = "git_blob"
    object_id: str = Field(pattern=GIT_OBJECT_PATTERN)


SourceIdentity = GitBlobIdentity


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
    """A qualified symbol name; recorded even while no resolver supports it."""

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


class SourceAnchor(KnowledgeModel):
    """One attributed location in the selected repository's source."""

    anchor_id: UUID = Field(pattern=UUID_PATTERN)
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    source_identity: SourceIdentity
    locator: SourceLocator
    provenance: Authorship

    @field_validator("path")
    @classmethod
    def _require_confined_relative_posix_path(cls, value: str) -> str:
        """Refuse anything that is not a repository-relative POSIX path.

        Resolution against a real filesystem happens at the filesystem boundary; this is the
        vocabulary-level shape check that keeps an absolute, drive, UNC, backslash or
        parent-escaping path out of a stored record in the first place.
        """

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("source path must not be blank")
        if cleaned.startswith(("/", "\\")) or cleaned.startswith("~"):
            raise ValueError(f"source path must be repository-relative: {value!r}")
        if "\\" in cleaned:
            raise ValueError(f"source path must use POSIX separators: {value!r}")
        if "\x00" in cleaned:
            raise ValueError("source path must not contain a NUL byte")
        parts = cleaned.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"source path must not contain empty, '.' or '..' segments: {value!r}")
        if len(cleaned) > PROSE_MAX_LENGTH:
            raise ValueError("source path is longer than the stored limit")
        return cleaned
