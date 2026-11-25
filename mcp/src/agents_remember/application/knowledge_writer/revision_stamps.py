"""A placed record's revision number and the stamp of the writer of that revision.

The number rises as it always has: once in an owner, when the meaning changes against the owner's
base, and to one more than the higher side for a record a merge left conflicted.
Only a revision above the base's -- or a record new to the base -- is this owner's own, and only
such a revision gets the stamp of the write that stores it:

* the stamp stands in the record (``writer``), and the owner's writable history file names the
  revision with the same stamp (``written``), a record the owner created included;
* a second write of the same revision by the same owner renews the stamp, so it names the last
  writer; a rerun by the same writer that changes nothing in the record keeps it, so a rerun of the
  same hand-off writes the same bytes;
* a write that leaves the number as the base has it leaves the base's stamp (or its absence), and
  a history row naming a revision this write no longer produces goes.

The stamp is resolved by the write's route from the caller's launch binding and receipt; the
hand-off never carries one. A request that names no stamp is stamped ``unknown`` with the reason.
A write that changes a record also drops an old typed state word from it: ``status`` is
written only as a kind's own lifecycle word.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from agents_remember.application.knowledge_writer.history_check import REOPEN_STEP
from agents_remember.application.knowledge_writer.memory_state import (
    MemoryState,
    MergeStageError,
    Owner,
    canonical_equal,
    deep_copy,
)
from agents_remember.application.writer_stamp_source import clock_now
from agents_remember.models.knowledge_files.history import HISTORY_SCHEMA, is_closed_history
from agents_remember.models.knowledge_files.history_product import record_content_digest
from agents_remember.models.knowledge_files.history_product import record_meaning as meaning
from agents_remember.models.knowledge_files.records import LEGACY_STATE_WORDS
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp
from agents_remember.tasks.writer_stamps import StampOwner, StampRequest, stamp

NO_CALLER: Final = "the write request named no caller"


@dataclass(frozen=True)
class RevisionStep:
    """The revision a placed record gets, and whether it is this owner's own."""

    revision: int
    raised: bool
    """Above the base's revision, or new to the base: the revision this write stamps."""
    base_writer: Mapping[str, Any] | None = None
    """The stamp the base holds for an unraised revision; the record keeps it."""


def stamp_owner(owner: Owner) -> StampOwner:
    """The task and owner a stamp names for a write of ``owner``."""

    return StampOwner(task=owner.task, kind=owner.kind, owner_id=owner.id)


def unresolved_stamp(owner: Owner, reason: str = NO_CALLER) -> WriterStamp:
    """A stamp whose caller could not be resolved: every caller value reads ``unknown``."""

    request = StampRequest(
        by="agent",
        role=None,
        seat=None,
        owner=stamp_owner(owner),
        at=clock_now(),
        unresolved=reason,
    )
    return stamp(request, None)


def revision_step(
    state: MemoryState,
    record_id: str,
    document: Mapping[str, Any],
    before: Mapping[str, Any] | None,
    problem: Callable[[str, str], None],
) -> RevisionStep:
    """The record's revision after this operation.

    A record a merge left unmerged (both sides changed it) is resolved at
    one more than the higher side's revision. Otherwise the base's revision, incremented once when
    the meaning changed; 1 for a record new to the base.
    """

    try:
        merged = state.merged_sides_revision(record_id)
    except MergeStageError as error:
        problem(f"record {record_id}", str(error))
        merged = None
    if merged is not None:
        return RevisionStep(merged + 1, raised=True)
    reference = state.base_record(record_id)
    if reference is None and state.base is None:
        reference = before
    if reference is None:
        return RevisionStep(1, raised=True)
    base_revision = reference.get("revision")
    number = base_revision if isinstance(base_revision, int) else 1
    if not canonical_equal(meaning(reference), meaning(document)):
        return RevisionStep(number + 1, raised=True)
    writer = reference.get("writer")
    return RevisionStep(
        number, raised=False, base_writer=writer if isinstance(writer, Mapping) else None
    )


def stamp_record(
    document: dict[str, Any],
    before: Mapping[str, Any] | None,
    step: RevisionStep,
    writer: WriterStamp,
) -> None:
    """Set the record's ``writer`` for ``step``; a record this write changes loses an old state word.

    A placement that changes nothing keeps the record's bytes, an old word included, so a rerun
    and a row that only re-reads a record write nothing; the conversion removes what is left.
    """

    _set_writer(document, before, step, writer)
    if document.get("status") in LEGACY_STATE_WORDS and (
        before is None or not canonical_equal(before, document)
    ):
        document.pop("status")


def _set_writer(
    document: dict[str, Any],
    before: Mapping[str, Any] | None,
    step: RevisionStep,
    writer: WriterStamp,
) -> None:
    if not step.raised:
        if step.base_writer is None:
            document.pop("writer", None)
        else:
            document["writer"] = deep_copy(step.base_writer)
        return
    stamped = writer.to_document()
    kept = before.get("writer") if before is not None else None
    if (
        isinstance(kept, Mapping)
        and before is not None
        and _same_writer(kept, stamped)
        and canonical_equal(_unstamped(before), _unstamped(document))
    ):
        document["writer"] = deep_copy(kept)
        return
    document["writer"] = stamped


def record_written_revision(
    state: MemoryState, owner: Owner, record_id: str, step: RevisionStep, record: Mapping[str, Any]
) -> tuple[str, str] | None:
    """Name the stamped revision in the owner's writable history file; a problem if it is closed.

    Rows of ``record_id`` above the stored revision go: this write no longer produces them. A
    raised revision gets its row with ``writer``, the stamp the record holds; an unraised one keeps
    the row it has (the owner's own revision a moved base already holds).
    """

    path, attempt = state.history_target(owner)
    stored = state.document(path)
    current = [row for row in (stored or {}).get("written") or () if isinstance(row, Mapping)]
    # The raised revision's row is written anew below; an unraised one keeps the row it has.
    kept_up_to = step.revision - 1 if step.raised else step.revision
    rows = [
        row for row in current if row.get("record") != record_id or _revision_of(row) <= kept_up_to
    ]
    if step.raised:
        rows.append(
            {
                "record": record_id,
                "revision": step.revision,
                "writer": deep_copy(record["writer"]),
                "contentDigest": record_content_digest(record),
            }
        )
    rows.sort(key=lambda row: (str(row.get("record")), _revision_of(row)))
    if canonical_equal(rows, current):
        return None
    if stored is not None and stored.get("closed") is True:
        return path, closed_history_message(state, path)
    document = deep_copy(stored) if stored is not None else new_history_document(owner, attempt)
    if rows:
        document["written"] = rows
    else:
        document.pop("written", None)
    state.put(path, document)
    return None


def new_history_document(owner: Owner, attempt: int) -> dict[str, Any]:
    """An open, empty history file of ``owner`` at ``attempt``."""

    return {
        "schema": HISTORY_SCHEMA,
        owner.kind: owner.id,
        **({"attempt": attempt} if attempt > 1 else {}),
        "closed": False,
        "rows": [],
    }


def closed_history_message(state: MemoryState, path: str) -> str:
    """Why a closed history file is not written: frozen by a base, or still the leaf's own."""

    base = state.base
    if base is not None and is_closed_history(base.get(path)):
        return "this history file is closed and frozen (MIK-R07 rule 7)"
    return f"this history file is closed and frozen (MIK-R07 rule 7){REOPEN_STEP}"


def _same_writer(kept: Mapping[str, Any], stamped: Mapping[str, Any]) -> bool:
    return {key: value for key, value in kept.items() if key != "at"} == {
        key: value for key, value in stamped.items() if key != "at"
    }


def _unstamped(document: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "writer"}


def _revision_of(row: Mapping[str, Any]) -> int:
    revision = row.get("revision")
    return revision if isinstance(revision, int) else 0
