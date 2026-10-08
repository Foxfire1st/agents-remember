"""Fixture models for datasets that tests build directly in the index schema.

The canonical database and every writer of its rows are retired (MIK-R26); the readers of the
derived index still run over the same logical tables, and several suites fill them with rows
through ``knowledge_rows_test_support``. These models are the request vocabulary of that fixture
writer. They moved here, text-identical, from ``models/knowledge/source.py`` and ``graph.py`` because
no production code constructs them.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    UUID_PATTERN,
    KnowledgeModel,
    require_plain_git_path,
)
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.source import SourceLocator
from pydantic import Field, field_validator


class GitBlobIdentity(KnowledgeModel):
    """The one v1 source identity: an exact Git object in the selected repository."""

    kind: Literal["git_blob"] = "git_blob"
    object_id: str = Field(pattern=GIT_OBJECT_PATTERN)


SourceIdentity = GitBlobIdentity


class SourceAnchorDraft(KnowledgeModel):
    """One attributed location proposed for a repository namespace, before its provenance.

    The split from :class:`SourceAnchor` is what keeps provenance out of a caller's hands: a
    draft carries only what the author decided -- where the location is and which source object
    it names -- while the admitted application attaches the provenance envelope.
    """

    # A real ``UUID``, not a pattern-constrained string: Pydantic refuses to apply a string
    # ``pattern`` constraint to its UUID schema, so the earlier spelling made every anchor
    # unconstructible. The canonical stored text is derived from the parsed value at the storage
    # boundary, exactly as it is for an authorship operation identity.
    anchor_id: UUID
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    source_identity: SourceIdentity
    locator: SourceLocator

    @field_validator("path")
    @classmethod
    def _require_confined_relative_posix_path(cls, value: str) -> str:
        """Refuse anything that is not a repository-relative POSIX path.

        Resolution against a real filesystem happens at the filesystem boundary; this is the
        vocabulary-level shape check that keeps an absolute, drive, UNC, backslash, parent-escaping
        or Git-pathspec spelling out of a stored record in the first place. The pathspec half
        matters here rather than only at the tree lookup: a stored path is later handed to ``git
        ls-tree``, and a spelling such as ``:(exclude)src/x.py`` is read by Git as a pathspec and
        answered with an error -- which the read path would otherwise report as an absent path that
        Git never looked up.
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
        return require_plain_git_path(cleaned, what="source path")


class SourceAnchor(SourceAnchorDraft):
    """One stored attributed location: the draft plus the provenance envelope that recorded it."""

    provenance: Authorship


class RealizationClaimDraft(KnowledgeModel):
    """One authored realization: what the anchor does for the obligation, in the author's words.

    ``role`` and ``rationale`` are authored claims, never inferred from the source file; the
    anchor endpoint is supplied by the request that carries this draft, because naming an
    existing anchor and recording a new one in the same transaction are different inputs.
    """

    claim_id: str = Field(pattern=UUID_PATTERN)
    invariant_revision_id: str = Field(pattern=UUID_PATTERN)
    role: RealizationRole
    rationale: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @field_validator("rationale")
    @classmethod
    def _require_nonblank_rationale(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("a realization rationale must not be blank")
        return cleaned
