"""The reader port's implementation: recorded rows from one opened store, and nothing else.

This module is the only place a view's data comes from, and it is deliberately dull. It reads the
envelope table and the revision table the R10 generation pair owns, decodes each stored payload
through the shipped typed-JSON decoder, and hands the view layer a flat
:class:`~agents_remember.models.knowledge.view.ViewSourceRow`. It performs **no** selection, **no**
ordering and **no** classification.

That division is the contract. :mod:`agents_remember.application.knowledge_views` owns what a view
selects and how it orders it, and every one of those decisions carries a provenance class there. If
this module ordered anything, the ordering would have no class and gap A-G5 would be open again, one
layer lower and harder to see.

**Read-only by handle, not by discipline.** :func:`open_view_reader` opens the database through
``open_read_only_database``, so the strongest statement available to this code is a ``SELECT``. A
refused view therefore leaves the dataset byte-identical as a property of the handle rather than as
a rollback someone has to remember.

**The snapshot is resolved, never asserted.** The reader's snapshot comes from the identity the
dataset actually holds, so a view cannot be handed a snapshot it did not read.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import apsw

from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.records import decode_typed_column
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.context import KnowledgeSchemaIdentity
from agents_remember.models.knowledge.facet import ENDPOINT_COLUMNS
from agents_remember.models.knowledge.read import AnchorResolutionState, KnowledgeReadSnapshot
from agents_remember.models.knowledge.view import ViewReaderError, ViewSourceCounts, ViewSourceRow

__all__ = [
    "RECORDS_OF_KIND",
    "REGISTERED_FAMILIES",
    "REGISTERED_REALIZATIONS",
    "StoreViewReader",
    "open_view_reader",
    "store_view_reader",
]

# One statement per question, with the columns named rather than selected by ``*``: a column added
# to the envelope by a later generation cannot silently change what a view reads.
RECORDS_OF_KIND = (
    "SELECT envelope.record_id, envelope.kind, envelope.record_schema, envelope.lifecycle, "
    "envelope.governing_route_id, revision.revision_id, revision.payload, revision.provenance "
    "FROM knowledge_record AS envelope "
    "LEFT JOIN record_revision AS revision "
    "ON revision.repository_id = envelope.repository_id "
    "AND revision.record_id = envelope.record_id "
    "WHERE envelope.repository_id = ? AND envelope.kind = ? "
    "ORDER BY envelope.record_id, revision.revision_id"
)

REGISTERED_REALIZATIONS = "SELECT COUNT(*) FROM realization_claim WHERE repository_id = ?"

REGISTERED_FAMILIES = "SELECT COUNT(*) FROM family WHERE repository_id = ?"

# Generation 1's own entities are not envelope rows, so they are read by their own statements. A
# view that reads "the invariant statement" must read the shipped table rather than a copy of it in
# generation 2 -- that copy does not exist, and inventing one would be a second authority.
INVARIANT_REVISIONS = (
    "SELECT invariant.invariant_id, invariant.display_label, revision.revision_id, "
    "revision.display_version, revision.statement, revision.applicability, revision.conditions, "
    "revision.exclusions, revision.state_at_origin, revision.acceptance_ref, revision.provenance "
    "FROM invariant AS invariant "
    "LEFT JOIN invariant_revision AS revision "
    "ON revision.repository_id = invariant.repository_id "
    "AND revision.invariant_id = invariant.invariant_id "
    "WHERE invariant.repository_id = ? ORDER BY invariant.invariant_id, revision.revision_id"
)

FAMILY_REVISIONS = (
    "SELECT family.family_id, family.display_label, revision.revision_id, "
    "revision.display_version, revision.joint_guarantee, revision.state_at_origin, "
    "revision.acceptance_ref, revision.provenance "
    "FROM family AS family "
    "LEFT JOIN family_revision AS revision "
    "ON revision.repository_id = family.repository_id "
    "AND revision.family_id = family.family_id "
    "WHERE family.repository_id = ? ORDER BY family.family_id, revision.revision_id"
)

FAMILY_MEMBERS = (
    "SELECT member_id, family_revision_id, invariant_revision_id, provenance "
    "FROM family_member WHERE repository_id = ? ORDER BY family_revision_id, member_id"
)

REALIZATION_CLAIMS = (
    "SELECT claim.claim_id, claim.invariant_revision_id, claim.anchor_id, claim.role, "
    "claim.rationale, claim.provenance, anchor.path, anchor.source_identity, anchor.locator "
    "FROM realization_claim AS claim "
    "LEFT JOIN source_anchor AS anchor "
    "ON anchor.repository_id = claim.repository_id AND anchor.anchor_id = claim.anchor_id "
    "WHERE claim.repository_id = ? ORDER BY claim.claim_id"
)

# One statement for the authored attachments of one exact endpoint. The endpoint's own column is
# chosen from the declared vocabulary rather than interpolated from caller text, so no string a
# caller supplies can reach this SQL.
ATTACHMENTS_OF_ENDPOINT = (
    "SELECT attachment.attachment_id, attachment.facet_revision_id, revision.payload, "
    "revision.provenance "
    "FROM facet_attachment AS attachment "
    "JOIN record_revision AS revision "
    "ON revision.repository_id = attachment.repository_id "
    "AND revision.revision_id = attachment.facet_revision_id "
    "WHERE attachment.repository_id = ? AND attachment.endpoint_kind = ? "
    "AND attachment.ENDPOINT_COLUMN = ? "
    "ORDER BY attachment.attachment_id"
)


def _text(value: object) -> str | None:
    """One recorded scalar as text, or ``None`` when the column is null."""

    return None if value is None else str(value)


def _sequence(value: object) -> tuple[str, ...]:
    """One recorded list column, which generation 1 stores as typed JSON."""

    if value is None:
        return ()
    decoded = decode_typed_column(str(value))
    if isinstance(decoded, (list, tuple)):
        return tuple(str(item) for item in decoded)
    return (str(decoded),)


def _provenance_author(text: object) -> str | None:
    """The actor a stored provenance envelope names, or ``None`` when the row carries none.

    A row with no recorded author is reported as having none. It is never given a default: the whole
    point of an authored classification is that a named actor wrote it, and inventing one here would
    make every row look attributable.
    """

    if not isinstance(text, str):
        return None
    decoded = decode_typed_column(text)
    if not isinstance(decoded, Mapping):
        return None
    actor = decoded.get("actor_ref")
    return None if actor is None else str(actor)


class StoreViewReader:
    """The shipped :class:`~agents_remember.models.knowledge.view.KnowledgeViewReader`.

    Constructed from an already-open connection and the snapshot the dataset declares. It holds no
    path of its own, so it cannot re-open the database at another revision behind the caller's back.
    """

    def __init__(
        self,
        connection: apsw.Connection,
        repository_id: str,
        snapshot: KnowledgeReadSnapshot,
        *,
        resolve_anchor: object = None,
    ) -> None:
        self._connection = connection
        self._repository_id = repository_id
        self._snapshot = snapshot
        self._resolve_anchor = resolve_anchor
        self._cache: dict[str, tuple[ViewSourceRow, ...]] = {}

    def snapshot(self) -> KnowledgeReadSnapshot:
        """The one snapshot every row this reader returns belongs to."""

        return self._snapshot

    def registered_counts(self) -> ViewSourceCounts:
        """The registered realization and family totals, counted from their own tables."""

        return ViewSourceCounts(
            registered_realizations=self._count(REGISTERED_REALIZATIONS),
            registered_families=self._count(REGISTERED_FAMILIES),
        )

    def rows(self, record_kind: str) -> tuple[ViewSourceRow, ...]:
        """Every recorded row of one registered record kind, in recorded order.

        The read is cached per kind for the reader's lifetime, so two views built over one reader
        cannot disagree about the rows of a kind merely because a write landed between them.
        """

        if record_kind not in self._cache:
            self._cache[record_kind] = self._read_kind(record_kind)
        return self._cache[record_kind]

    def invariant_rows(self) -> tuple[ViewSourceRow, ...]:
        """Every recorded invariant revision, as one row per identity and revision."""

        if INVARIANT_REVISIONS not in self._cache:
            self._cache[INVARIANT_REVISIONS] = tuple(
                self._invariant_row(row)
                for row in self._connection.execute(INVARIANT_REVISIONS, (self._repository_id,))
            )
        return self._cache[INVARIANT_REVISIONS]

    def family_rows(self) -> tuple[ViewSourceRow, ...]:
        """Every recorded family revision, as one row per identity and revision."""

        if FAMILY_REVISIONS not in self._cache:
            self._cache[FAMILY_REVISIONS] = tuple(
                self._family_row(row)
                for row in self._connection.execute(FAMILY_REVISIONS, (self._repository_id,))
            )
        return self._cache[FAMILY_REVISIONS]

    def realization_rows(self) -> tuple[ViewSourceRow, ...]:
        """Every recorded realization claim with the source location it attributes."""

        if REALIZATION_CLAIMS not in self._cache:
            self._cache[REALIZATION_CLAIMS] = tuple(
                self._realization_row(row)
                for row in self._connection.execute(REALIZATION_CLAIMS, (self._repository_id,))
            )
        return self._cache[REALIZATION_CLAIMS]

    def attachment_rows(self, endpoint_kind: str, endpoint_id: str) -> tuple[ViewSourceRow, ...]:
        """Every authored facet attached to one exact endpoint revision, in attachment order."""

        column = ENDPOINT_COLUMNS.get(endpoint_kind)  # type: ignore[arg-type]
        if column is None:
            raise ViewReaderError(f"{endpoint_kind!r} is not a registered attachment endpoint kind")
        statement = ATTACHMENTS_OF_ENDPOINT.replace("ENDPOINT_COLUMN", column)
        key = f"attachments:{endpoint_kind}:{endpoint_id}"
        if key not in self._cache:
            self._cache[key] = tuple(
                self._attachment_row(row)
                for row in self._connection.execute(
                    statement, (self._repository_id, endpoint_kind, endpoint_id)
                )
            )
        return self._cache[key]

    def anchor_state(self, locator: Mapping[str, object]) -> AnchorResolutionState:
        """How one recorded source locator resolves against the tree the reader was built with."""

        if self._resolve_anchor is None:
            return "not_requested"
        resolver = self._resolve_anchor
        if not callable(resolver):
            raise ViewReaderError(
                "the reader was built with an anchor resolver that is not callable"
            )
        resolution = resolver(dict(locator))
        if resolution is None:
            return "not_requested"
        state = getattr(resolution, "state", None)
        if not isinstance(state, str):
            raise ViewReaderError("the anchor resolver returned a resolution with no state")
        return cast("AnchorResolutionState", state)

    # -- internals ---------------------------------------------------------

    def _count(self, statement: str) -> int:
        for row in self._connection.execute(statement, (self._repository_id,)):
            return int(row[0])
        raise ViewReaderError("the count statement returned no row")

    def _read_kind(self, record_kind: str) -> tuple[ViewSourceRow, ...]:
        rows: list[ViewSourceRow] = []
        for row in self._connection.execute(RECORDS_OF_KIND, (self._repository_id, record_kind)):
            rows.append(self._decode_row(row))
        return tuple(rows)

    def _invariant_row(self, row: tuple[object, ...]) -> ViewSourceRow:
        return ViewSourceRow(
            record_kind="invariant_revision",
            record_schema="invariant-revision/v1",
            record_id=str(row[0]),
            revision_id=None if row[2] is None else str(row[2]),
            lifecycle=None if row[8] is None else str(row[8]),
            author_ref=_provenance_author(row[10]),
            payload={
                "display_label": _text(row[1]),
                "display_version": _text(row[3]),
                "statement": _text(row[4]),
                "applicability": _text(row[5]),
                "conditions": _sequence(row[6]),
                "exclusions": _sequence(row[7]),
                "acceptance_ref": _text(row[9]),
            },
        )

    def _family_row(self, row: tuple[object, ...]) -> ViewSourceRow:
        return ViewSourceRow(
            record_kind="family_revision",
            record_schema="family-revision/v1",
            record_id=str(row[0]),
            revision_id=None if row[2] is None else str(row[2]),
            lifecycle=None if row[5] is None else str(row[5]),
            author_ref=_provenance_author(row[7]),
            payload={
                "display_label": _text(row[1]),
                "display_version": _text(row[3]),
                "joint_guarantee": _text(row[4]),
                "acceptance_ref": _text(row[6]),
            },
        )

    def _realization_row(self, row: tuple[object, ...]) -> ViewSourceRow:
        return ViewSourceRow(
            record_kind="realization_claim",
            record_schema="realization-claim/v1",
            record_id=str(row[0]),
            revision_id=None if row[1] is None else str(row[1]),
            author_ref=_provenance_author(row[5]),
            payload={
                "anchor_id": _text(row[2]),
                "role": _text(row[3]),
                "rationale": _text(row[4]),
                "path": _text(row[6]),
                "source_identity": _text(row[7]),
                # Decoded, like every other JSON-valued payload value in this module: the column is a
                # typed JSON column, and handing a consumer its stored text is what makes a raw string
                # classify as neither a symbol nor a mapping, so a symbol locator would fall through
                # to the file branch and be published as a path resolution it never was.
                "locator": None if row[8] is None else decode_typed_column(str(row[8])),
            },
        )

    def _attachment_row(self, row: tuple[object, ...]) -> ViewSourceRow:
        decoded = decode_typed_column(str(row[2])) if row[2] is not None else {}
        payload = decoded if isinstance(decoded, Mapping) else {}
        return ViewSourceRow(
            record_kind=str(payload.get("facet_kind", "facet")),
            record_schema="facet/v1",
            record_id=str(row[0]),
            revision_id=str(row[1]),
            author_ref=_provenance_author(row[3]),
            payload={str(key): value for key, value in payload.items()},
        )

    def _decode_row(self, row: tuple[object, ...]) -> ViewSourceRow:
        payload_text = row[6]
        decoded = None if payload_text is None else decode_typed_column(str(payload_text))
        if decoded is not None and not isinstance(decoded, Mapping):
            raise ViewReaderError(
                f"stored revision {row[5]} carries a payload that is not a JSON object"
            )
        return ViewSourceRow(
            record_kind=str(row[1]),
            record_schema=str(row[2]),
            record_id=str(row[0]),
            revision_id=None if row[5] is None else str(row[5]),
            lifecycle=None if row[3] is None else str(row[3]),
            governing_route_id=None if row[4] is None else str(row[4]),
            author_ref=_provenance_author(row[7]),
            payload={} if decoded is None else {str(key): value for key, value in decoded.items()},
        )


def store_view_reader(
    store: OpenedKnowledgeStore,
    identity: KnowledgeSchemaIdentity,
    *,
    resolve_anchor: object = None,
) -> StoreViewReader:
    """Build the reader over one already-open store, binding the snapshot it declares.

    The snapshot is composed from two recorded facts -- the schema generation the dataset implements
    and the logical digest it holds -- and not from anything the caller supplies. ``resolve_anchor``
    is the anchor resolver, passed *in* rather than resolved here: resolving one needs the read
    context the application layer owns, and this package sits below that layer.
    """

    resolved = dataset_identity(store.database_path)
    snapshot = KnowledgeReadSnapshot(
        repository_id=store.repository_id,
        schema_version=identity.schema_name,
        logical_digest=resolved.logical_digest,
        context_digest=identity.fingerprint,
    )
    return StoreViewReader(
        store.connection, store.repository_id, snapshot, resolve_anchor=resolve_anchor
    )


def open_view_reader(
    database_path: Path,
    repository_id: str,
    identity: KnowledgeSchemaIdentity,
    *,
    resolve_anchor: object = None,
) -> tuple[StoreViewReader, apsw.Connection]:
    """Open one database read-only and return its reader together with the handle to close.

    Returning the handle is deliberate: the caller owns the connection's lifetime, so this function
    cannot leak one and a caller cannot forget that it opened something.
    """

    connection = open_read_only_database(database_path)
    resolved = dataset_identity(database_path)
    snapshot = KnowledgeReadSnapshot(
        repository_id=repository_id,
        schema_version=identity.schema_name,
        logical_digest=resolved.logical_digest,
        context_digest=identity.fingerprint,
    )
    return (
        StoreViewReader(connection, repository_id, snapshot, resolve_anchor=resolve_anchor),
        connection,
    )
