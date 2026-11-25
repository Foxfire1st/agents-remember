"""The parts of a history file only the product writes: written revisions and the review fact.

Beside the curator's rows, a history file carries two parts no curator authors: ``written`` names
every revision the owner wrote, with its writer's stamp (:class:`WrittenRevision`), a record the
owner created included; ``review`` is the fact of the memory review that passed the owner's change
(:class:`ReviewFact`), written by the closeout in the same write that sets ``closed``. A revision
is *reviewed* when the file that names it holds a review fact; the rule that reads it is
:mod:`.record_state`. A crossing can also witness different contents under one revision number.
Its own history retains those witnesses even when a source record had no row naming its write.
These fields are absent where empty, so a file written before them keeps its bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Final

from pydantic import Field, StrictInt

from agents_remember.kernel.canonical_json import prefixed_sha256_digest
from agents_remember.models.knowledge_files.canonical import canonical_value
from agents_remember.models.knowledge_files.shapes import (
    ContentHash,
    FileModel,
    GitBlobId,
    Label,
    RecordId,
)
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp

_NON_MEANING_FIELDS: Final = frozenset(
    {"id", "schema", "origin", "revision", "admission", "status", "writer"}
)


def _invariant_title_field(document: Mapping[str, Any]) -> frozenset[str]:
    """An invariant's title is its display name, independent of its written revision."""

    return frozenset({"title"}) if document.get("schema") == "ar-invariant/v1" else frozenset()


def record_meaning(document: Mapping[str, Any]) -> dict[str, Any]:
    """The fields that the writer compares when deciding whether a revision changed."""

    excluded = _NON_MEANING_FIELDS | _invariant_title_field(document)
    return {key: value for key, value in document.items() if key not in excluded}


def record_content_digest(document: Mapping[str, Any]) -> str:
    """The revision's exact meaning, independent of its writer and lifecycle metadata."""

    return prefixed_sha256_digest(canonical_value(record_meaning(document)))


def stored_record_content_digest(record: FileModel) -> str:
    """Hash the meaning of a parsed record using only the values its stored document supplied."""

    return record_content_digest(stored_record_document(record))


def stored_record_document(record: FileModel) -> dict[str, Any]:
    """Normalize stored fields through the record schema without inventing defaults."""

    return record.model_dump(mode="json", by_alias=True, exclude_unset=True, exclude_none=True)


class RevisionRef(FileModel):
    """A revision number scoped to one record."""

    record: RecordId
    revision: Annotated[StrictInt, Field(ge=1)]


class SharedRevision(RevisionRef):
    """A crossing witnessed different contents under this revision number."""

    conflicting_writer: WriterStamp | None = Field(default=None, alias="conflictingWriter")
    """Present only when the same stamp could name either content."""


class CrossingSelection(RevisionRef):
    """Which evidence belongs to the exact record bytes selected by a crossing."""

    document_digest: ContentHash = Field(alias="documentDigest")
    writer: WriterStamp | None = None
    excluded_reviews: tuple[ContentHash, ...] = Field(default=(), alias="excludedReviews")
    supersedes: tuple[Label, ...] = ()


def crossing_selection_digest(document: Mapping[str, Any]) -> str:
    """Identify the selected record across a retirement or invariant title edit."""

    excluded = {"status"} | _invariant_title_field(document)
    return prefixed_sha256_digest(
        canonical_value(
            {
                key: value
                for key, value in document.items()
                if key not in excluded and value is not None
            }
        )
    )


class WrittenRevision(RevisionRef):
    """One revision this owner wrote, with its writer's stamp and the content it wrote.

    A second write of the same revision by the same owner renews the stamp, so it names the last
    writer of that revision.
    """

    writer: WriterStamp
    content_digest: ContentHash | None = Field(default=None, alias="contentDigest")


class ReviewFact(FileModel):
    """``{ round, verdictRef?, tree?, reviewer, handedOverBy }``: the memory review that passed.

    ``round`` is the review round recorded as passed, ``verdictRef`` the reference to its verdict and
    ``tree`` the memory tree the reviewer passed. ``reviewer`` is the stamp of the agent that recorded
    the pass, ``handedOverBy`` the stamp of the agent that handed the memory change to the review (a
    curator, or a consolidator). The conversion of an old owner may know neither stamp nor tree; the
    stamps then read ``unknown`` with the reason.
    """

    round: Annotated[StrictInt, Field(ge=1)]
    verdict_ref: Label | None = Field(default=None, alias="verdictRef")
    tree: GitBlobId | None = None
    reviewer: WriterStamp
    handed_over_by: WriterStamp = Field(alias="handedOverBy")
