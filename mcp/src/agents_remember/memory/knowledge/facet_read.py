"""The facet-specific selection: one seed, one complete aggregate, or one typed refusal.

``KS-R07@v1``'s recorded-scope selection is unchanged by this leaf, and this module is why: the
facet selection is a **separate** selection with its own declared policy name, its own seeds and its
own item stream, reachable from no shipped seed. Nothing here calls into the shipped selection, and
the shipped selection calls into nothing here, so a shipped seed's serialized page is byte-identical
to what it was before this leaf because no code path is shared.

The contract this selection declares:

* **One seed selects one bounded aggregate.** A facet-record seed selects that record, every
  retained revision of it, every attachment row of those revisions, and every recorded supersession
  edge that touches any of them -- in both directions, so a decision's page reports what it
  supersedes and what supersedes it. An explanation-subject seed selects every explanation whose
  subject is that exact statement revision, plus every retained revision of those explanations.
* **Complete or refused.** The whole aggregate is selected and the whole aggregate is served. A
  selection that reaches ``FACET_SELECTION_ITEM_LIMIT`` raises rather than emitting a partial page
  with a total that was never computed, and the caller converts that into the shipped
  ``selection_incomplete`` refusal. There is no cursor, so there is no continuation contract to
  bind and no position that could be read as a different selection.
* **Nothing is derived.** An item's order is fixed by its kind and by stable identifiers, never by
  an authored label, an insertion order or a timestamp; every retained revision is served as its own
  item; and the only statement about which explanation revision matters is the designation the
  explanation record *stores*. No field here could be read as "newest", and no endorsement,
  confidence, severity or score is computed or served.

The two absences a caller can tell apart are the shipped ones: a seed naming nothing recorded is
``selector_absent``, while a recorded facet record with no attachments or no supersession edges is a
real page reporting zero counts for those kinds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import apsw

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge import facet_records
from agents_remember.models.knowledge.facet import (
    INVARIANT_STATEMENT_SUBJECT_KIND,
)
from agents_remember.models.knowledge.facet_read import (
    FACET_SELECTION_ITEM_LIMIT,
    FACET_SELECTION_POLICY_VERSION,
    DecisionSupersessionItem,
    ExplanationItem,
    ExplanationRevisionItem,
    ExplanationSubjectSeed,
    FacetAttachmentItem,
    FacetReadCounts,
    FacetReadItem,
    FacetReadSeed,
    FacetRecordItem,
    FacetRecordSeed,
    FacetRevisionItem,
    facet_item_id,
    facet_item_sort_key,
)

# The selection's declared execution bound, named beside the reason so a refusal can say which bound
# it reached without restating the number.
ITEM_LIMIT = FACET_SELECTION_ITEM_LIMIT


class FacetSelectionIncomplete(ValueError):
    """The selected aggregate reached its declared execution bound before it was enumerated.

    It is an exception rather than a returned refusal because the caller is the read operation,
    which owns the operation name the refusal is reported under. The detail names both the bound and
    the count, so a caller is never told "too large" without being told what was too large.
    """

    def __init__(self, item_count: int, bound: int) -> None:
        super().__init__(
            f"the facet selection reached its declared execution bound of {bound} items "
            f"({item_count} counted)"
        )
        self.item_count = item_count
        self.bound = bound


@dataclass(frozen=True)
class FacetSelectionQuery:
    """One facet selection: the namespace it is read in and the seed it selects."""

    repository_id: str
    seed: FacetReadSeed


@dataclass(frozen=True)
class FacetSelection:
    """One selected facet aggregate, as the shared tuple every count and the manifest derive from."""

    items: tuple[FacetReadItem, ...]
    counts: FacetReadCounts
    manifest_digest: str
    seed_recorded: bool

    @property
    def empty(self) -> bool:
        """Whether the selection holds no item at all."""

        return not self.items


def select_facet_scope(connection: apsw.Connection, query: FacetSelectionQuery) -> FacetSelection:
    """Select one facet aggregate at one snapshot, completely or not at all.

    The connection is the caller's read-only handle, so this function issues ``SELECT`` statements
    and nothing else: a refusal leaves the file byte-identical because there is no statement that
    could change it.
    """

    if isinstance(query.seed, FacetRecordSeed):
        recorded, items = _facet_record_items(connection, query.repository_id, query.seed)
    else:
        recorded, items = _explanation_items(connection, query.repository_id, query.seed)
    if len(items) > ITEM_LIMIT:
        raise FacetSelectionIncomplete(len(items), ITEM_LIMIT)
    ordered = tuple(sorted(items, key=facet_item_sort_key))
    return FacetSelection(
        items=ordered,
        counts=_counts(ordered),
        manifest_digest=_manifest_digest(query.seed, ordered),
        seed_recorded=recorded,
    )


def _facet_record_items(
    connection: apsw.Connection, repository_id: str, seed: FacetRecordSeed
) -> tuple[bool, tuple[FacetReadItem, ...]]:
    """Return one facet record's whole aggregate: record, revisions, attachments and edges."""

    record_row = _one(connection, _RECORD_BY_ID, (repository_id, seed.record_id))
    if record_row is None:
        return (False, ())
    record = facet_records.decode_facet_record_row(record_row)
    items: list[FacetReadItem] = [FacetRecordItem(facet=record)]
    revisions = _many(connection, _REVISIONS_OF_RECORD, (repository_id, seed.record_id))
    for row in revisions:
        items.append(
            FacetRevisionItem(
                revision=facet_records.decode_facet_revision_row(row, record.facet_kind)
            )
        )
    for row in _many(connection, _ATTACHMENTS_OF_RECORD, (repository_id, seed.record_id)):
        items.append(FacetAttachmentItem(attachment=facet_records.decode_attachment_row(row)))
    for row in _supersessions_of_record(connection, repository_id, seed.record_id):
        items.append(
            DecisionSupersessionItem(supersession=facet_records.decode_supersession_row(row))
        )
    return (True, tuple(items))


def _supersessions_of_record(
    connection: apsw.Connection, repository_id: str, record_id: str
) -> tuple[tuple[Any, ...], ...]:
    """Return every supersession edge that touches any revision of one facet record.

    Both directions, deduplicated by the edge's own key: a decision's page reports the edges it
    authored *and* the edges that name it, which is what makes the supersession state readable from
    the superseded side as well as the superseding one.
    """

    rows: dict[tuple[str, str], tuple[Any, ...]] = {}
    for statement in (_SUPERSEDING_OF_RECORD, _SUPERSEDED_OF_RECORD):
        for row in _many(connection, statement, (repository_id, record_id)):
            rows[(str(row[1]), str(row[2]))] = row
    return tuple(rows[key] for key in sorted(rows))


def _explanation_items(
    connection: apsw.Connection, repository_id: str, seed: ExplanationSubjectSeed
) -> tuple[bool, tuple[FacetReadItem, ...]]:
    """Return every explanation of one exact statement revision, and its retained revisions."""

    subject = seed.subject
    revision_id = subject.revision_id
    if not _subject_revision_recorded(connection, repository_id, seed):
        return (False, ())
    parameters = (repository_id, subject.kind, revision_id, revision_id)
    records = _many(connection, _EXPLANATIONS_OF_SUBJECT, parameters)
    items: list[FacetReadItem] = [
        ExplanationItem(explanation=facet_records.decode_explanation_row(row)) for row in records
    ]
    for row in _many(connection, _EXPLANATION_REVISIONS_OF_SUBJECT, parameters):
        items.append(
            ExplanationRevisionItem(revision=facet_records.decode_explanation_revision_row(row))
        )
    return (True, tuple(items))


def _subject_revision_recorded(
    connection: apsw.Connection, repository_id: str, seed: ExplanationSubjectSeed
) -> bool:
    """Whether the exact statement revision one subject names is stored in this namespace."""

    statement = (
        _INVARIANT_REVISION_RECORDED
        if seed.subject.kind == INVARIANT_STATEMENT_SUBJECT_KIND
        else _FAMILY_REVISION_RECORDED
    )
    return _one(connection, statement, (repository_id, seed.subject.revision_id)) is not None


def _counts(items: Sequence[FacetReadItem]) -> FacetReadCounts:
    """Return the counted shape of one selected aggregate, derived from its own item tuple."""

    return FacetReadCounts(
        facet_records=sum(1 for item in items if isinstance(item, FacetRecordItem)),
        facet_revisions=sum(1 for item in items if isinstance(item, FacetRevisionItem)),
        attachments=sum(1 for item in items if isinstance(item, FacetAttachmentItem)),
        supersession_edges=sum(1 for item in items if isinstance(item, DecisionSupersessionItem)),
        explanations=sum(1 for item in items if isinstance(item, ExplanationItem)),
        explanation_revisions=sum(1 for item in items if isinstance(item, ExplanationRevisionItem)),
    )


def _manifest_digest(seed: FacetReadSeed, items: Sequence[FacetReadItem]) -> str:
    """Return the digest of one selected set: its policy, its seed and its exact item identities.

    The manifest is what makes "a position in one selection" a checkable fact, and it is derived
    from the same tuple the page is built from, so a manifest that does not describe the items
    cannot be produced here.
    """

    return sha256_digest(
        {
            "policy_version": FACET_SELECTION_POLICY_VERSION,
            "seed": seed.model_dump(mode="json"),
            "items": [{"kind": item.kind, "item_id": facet_item_id(item)} for item in items],
        }
    )


def _one(
    connection: apsw.Connection, statement: str, parameters: Sequence[Any]
) -> tuple[Any, ...] | None:
    return next(iter(connection.execute(statement, tuple(parameters))), None)


def _many(
    connection: apsw.Connection, statement: str, parameters: Sequence[Any]
) -> tuple[tuple[Any, ...], ...]:
    return tuple(connection.execute(statement, tuple(parameters)))


# Every statement below is a ``SELECT`` on a read-only connection. Each is ordered by the item's
# own stable identifier rather than left to the storage engine, because the page's declared order is
# a property of this selection and not of how SQLite happened to answer.
_RECORD_BY_ID = "SELECT * FROM knowledge_record WHERE repository_id = ? AND record_id = ?"
_REVISIONS_OF_RECORD = (
    "SELECT * FROM record_revision WHERE repository_id = ? AND record_id = ? ORDER BY revision_id"
)
_ATTACHMENTS_OF_RECORD = (
    "SELECT attachment.* FROM facet_attachment AS attachment "
    "JOIN record_revision AS revision ON revision.repository_id = attachment.repository_id "
    "AND revision.revision_id = attachment.facet_revision_id "
    "WHERE attachment.repository_id = ? AND revision.record_id = ? ORDER BY attachment.attachment_id"
)
_SUPERSEDING_OF_RECORD = (
    "SELECT edge.* FROM facet_decision_supersession AS edge "
    "JOIN record_revision AS revision ON revision.repository_id = edge.repository_id "
    "AND revision.revision_id = edge.superseding_revision_id "
    "WHERE edge.repository_id = ? AND revision.record_id = ?"
)
_SUPERSEDED_OF_RECORD = (
    "SELECT edge.* FROM facet_decision_supersession AS edge "
    "JOIN record_revision AS revision ON revision.repository_id = edge.repository_id "
    "AND revision.revision_id = edge.superseded_revision_id "
    "WHERE edge.repository_id = ? AND revision.record_id = ?"
)
_EXPLANATIONS_OF_SUBJECT = (
    "SELECT * FROM explanation WHERE repository_id = ? AND subject_kind = ? "
    "AND (subject_invariant_revision_id = ? OR subject_family_revision_id = ?) "
    "ORDER BY explanation_id"
)
_EXPLANATION_REVISIONS_OF_SUBJECT = (
    "SELECT revision.* FROM explanation_revision AS revision "
    "JOIN explanation AS record ON record.repository_id = revision.repository_id "
    "AND record.explanation_id = revision.explanation_id "
    "WHERE record.repository_id = ? AND record.subject_kind = ? "
    "AND (record.subject_invariant_revision_id = ? OR record.subject_family_revision_id = ?) "
    "ORDER BY revision.explanation_id, revision.revision_id"
)
_INVARIANT_REVISION_RECORDED = (
    "SELECT revision_id FROM invariant_revision WHERE repository_id = ? AND revision_id = ?"
)
_FAMILY_REVISION_RECORDED = (
    "SELECT revision_id FROM family_revision WHERE repository_id = ? AND revision_id = ?"
)


__all__ = [
    "ITEM_LIMIT",
    "FacetSelection",
    "FacetSelectionIncomplete",
    "FacetSelectionQuery",
    "select_facet_scope",
]
