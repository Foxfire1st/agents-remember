"""Writer support: a history row checked against K_B / K_C facts the caller supplies (MIK-R07).

The row models check shape (:mod:`.history`). These checks need the trees, which only the caller
reads: :func:`reanchor_mismatches` (rule 4: each ``after`` equals the entry's anchor in K_C),
:func:`unknown_subjects`, :func:`invariant_revision_violation` and :func:`stale_examined_members`
(the revision binding of rules 2 and 5). Whether a row is *current* for an item is the gate's rule
(MIK-R09 rule 2), not this module's.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping

from agents_remember.models.knowledge_files.history import (
    ABSENT,
    FamilyRow,
    HistoryFile,
    InvariantRow,
    MembershipRow,
    RecordRow,
    membership_invariant,
)
from agents_remember.models.knowledge_files.shapes import Anchor
from agents_remember.models.knowledge_files.sidecars import ProofEntry, RealizationEntry


def reanchor_mismatches(row: InvariantRow, candidate_anchors: Mapping[str, Anchor]) -> list[str]:
    """Return the covered entry IDs whose ``after`` differs from the entry's anchor in K_C.

    ``candidate_anchors`` maps each entry ID present in K_C to its anchor with ``path`` filled in
    (:func:`sidecar_entry_anchors`). An ``after`` of ``"absent"`` requires the entry to be absent.
    """

    mismatches = []
    for entry in row.covers:
        expected: Anchor | str = candidate_anchors.get(entry.id, ABSENT)
        if entry.after != expected:
            mismatches.append(entry.id)
    return mismatches


def sidecar_entry_anchors(
    sidecar_path: str, entries: Iterable[RealizationEntry | ProofEntry]
) -> dict[str, Anchor]:
    """Map each realization or proof entry of one file sidecar to its anchor, path filled in."""

    return {entry.id: entry.anchor.model_copy(update={"path": sidecar_path}) for entry in entries}


def unknown_subjects(history: HistoryFile, known_record_ids: Collection[str]) -> list[str]:
    """Return the row subjects that name no known record.

    Invariant, family and ``record`` rows name their record; a ``membership`` row names its
    invariant after the ``membership:`` prefix. Every other kind answers worklist items, not
    records, and is not checked here.
    """

    unknown = []
    for row in history.rows:
        if isinstance(row, InvariantRow | FamilyRow | RecordRow):
            if row.subject not in known_record_ids:
                unknown.append(row.subject)
        elif isinstance(row, MembershipRow):
            invariant = membership_invariant(row.subject)
            if invariant is None or invariant not in known_record_ids:
                unknown.append(row.subject)
    return unknown


def invariant_revision_violation(
    row: InvariantRow,
    *,
    base_revision: int | None,
    candidate_revision: int,
    restated_revision: int | None = None,
) -> str | None:
    """Return why ``row.revision`` does not bind the invariant's revisions, or ``None``.

    The row's revision is the invariant's revision in K_C. A ``changed`` row's revision is the
    K_B revision plus one: a leaf changes an invariant's meaning once, so its revision increments
    exactly once (MIK-R21 rule 4). ``moved``, ``extended`` and ``no_impact`` leave meaning, and so the
    revision, unchanged. ``deleted`` is not constrained beyond K_C. ``base_revision`` is ``None``
    for an invariant absent from K_B, which needs no row (rule 6).

    ``restated_revision`` is the revision of the owner's governing ``changed`` row in an earlier,
    frozen attempt of the same leaf (``None`` when it has none). A ``changed`` row at an unchanged
    revision is accepted only as a restatement of that revision step: the record has not changed
    since that row (K_C's revision is K_B's) and both rows are at that revision. It corrects the
    step's effect, because and reason; it is no second change (L37 ruling of 2026-10-01T22:49:36,
    Q2 a).
    """

    if row.revision != candidate_revision:
        return f"row revision {row.revision} is not the K_C revision {candidate_revision}"
    if base_revision is None or row.disposition == "deleted":
        return None
    if row.disposition == "changed" and candidate_revision != base_revision + 1:
        if candidate_revision == base_revision == restated_revision:
            return None
        return f"a changed row's revision is the K_B revision {base_revision} plus one"
    if row.disposition != "changed" and candidate_revision != base_revision:
        return f"a {row.disposition} row keeps the K_B revision {base_revision}"
    return None


def stale_examined_members(row: FamilyRow, candidate_revisions: Mapping[str, int]) -> list[str]:
    """Return the examined members whose K_C revision differs from the one the row examined.

    A member absent from K_C (``candidate_revisions`` has no entry) is not stale. A non-empty
    result means the family must be examined again (rule 5).
    """

    return [
        member.id
        for member in row.examined
        if member.id in candidate_revisions and candidate_revisions[member.id] != member.revision
    ]
