"""Every row of the owner's history file agrees with the candidate the operation produces (MIK-R07).

An operation may leave a row it did not rewrite: a later run of the same leaf re-anchors an entry,
removes one, or changes an invariant's meaning, and a row written earlier still names the old
anchor or revision. MIK-R07's failure rule makes such a row the writer's refusal, not something to
repair quietly, so after the edits every row of the owner's file is checked against the candidate
with the MIK-R07 writer-support checks:

* :func:`unknown_subjects` -- the subject names a record of the candidate;
* :func:`reanchor_mismatches` -- each covered entry's ``after`` equals its anchor in the candidate
  (``absent`` when it is gone), rule 4;
* :func:`invariant_revision_violation` -- an invariant row's revision is the candidate revision and
  binds the base revision, rule 2;
* :func:`stale_examined_members` -- a family row examined each member at its candidate revision,
  rule 5.

The remedy is always the same: name the row again in ``history`` so the writer rewrites it.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import ValidationError

from agents_remember.application.knowledge_writer.handoff import Problem
from agents_remember.application.knowledge_writer.memory_state import MemoryState, Owner
from agents_remember.models.knowledge_files.documents import history_path
from agents_remember.models.knowledge_files.history import (
    FamilyRow,
    HistoryFile,
    InvariantRow,
    invariant_revision_violation,
    reanchor_mismatches,
    sidecar_entry_anchors,
    stale_examined_members,
    unknown_subjects,
)
from agents_remember.models.knowledge_files.shapes import Anchor
from agents_remember.models.knowledge_files.sidecars import ProofEntry, RealizationEntry

_REMEDY = "name this row again in 'history' so the writer rewrites it"
_FROZEN = (
    "this history file is closed and frozen (MIK-R07 rule 7), so the row cannot be rewritten; "
    "a correction belongs to a new leaf's rows"
)


def owner_history_problems(state: MemoryState, owner: Owner) -> list[Problem]:
    """Every row of the owner's history file that the candidate contradicts, as a problem."""

    path = history_path(owner.id)
    document = state.document(path)
    if document is None:
        return []
    try:
        history = HistoryFile.model_validate(document)
    except ValidationError:
        return []  # the render step reports the file's shape
    remedy = _FROZEN if history.closed else _REMEDY
    problems = [
        Problem(path, f"a row's subject {subject} names no record of the candidate; {remedy}")
        for subject in unknown_subjects(history, set(state.records))
    ]
    anchors = _candidate_anchors(state, history)
    for row in history.rows:
        where = f"{path}: row {row.id} ({row.subject})"
        problems.extend(
            Problem(where, message.replace(_REMEDY, remedy))
            for message in _row_messages(state, row, anchors)
        )
    return problems


def _row_messages(state: MemoryState, row: object, anchors: dict[str, Anchor]) -> Iterator[str]:
    if isinstance(row, InvariantRow):
        mismatched = reanchor_mismatches(row, anchors)
        if mismatched:
            yield (
                f"covered entries {mismatched} no longer carry the row's 'after' anchor in the "
                f"candidate (MIK-R07 rule 4); {_REMEDY}"
            )
        revision = _revision(state, row.subject)
        if revision is not None:
            base = state.base_record(row.subject)
            base_revision = base.get("revision") if base is not None else None
            violation = invariant_revision_violation(
                row,
                base_revision=base_revision if isinstance(base_revision, int) else None,
                candidate_revision=revision,
            )
            if violation is not None:
                yield f"{violation}; {_REMEDY}"
    elif isinstance(row, FamilyRow):
        revisions = {
            member.id: revision
            for member in row.examined
            if (revision := _revision(state, member.id)) is not None
        }
        stale = stale_examined_members(row, revisions)
        if stale:
            yield f"examined members {stale} changed revision since the row (D7); {_REMEDY}"


def _revision(state: MemoryState, record_id: str) -> int | None:
    found = state.record(record_id)
    revision = found[2].get("revision") if found is not None else None
    return revision if isinstance(revision, int) else None


def _candidate_anchors(state: MemoryState, history: HistoryFile) -> dict[str, Anchor]:
    """The candidate anchor, path filled in, of every entry some invariant row covers."""

    covered = {
        entry.id for row in history.rows if isinstance(row, InvariantRow) for entry in row.covers
    }
    anchors: dict[str, Anchor] = {}
    if not covered:
        return anchors
    for _path, sidecar in state.sidecars():
        entries = []
        for key, model in (("realizes", RealizationEntry), ("proves", ProofEntry)):
            for entry in sidecar.get(key) or ():
                if entry.get("id") in covered:
                    try:
                        entries.append(model.model_validate(entry))
                    except ValidationError:
                        continue  # the render step reports the entry's shape
        if entries:
            anchors.update(sidecar_entry_anchors(str(sidecar.get("path")), entries))
    return anchors
