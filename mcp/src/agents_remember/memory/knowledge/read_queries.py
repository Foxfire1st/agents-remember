"""The recorded-scope read queries: one statement per lookup, all addressed at one namespace.

Every function here takes the caller's connection and never opens one, so a selection's statements
all run inside whatever transaction the caller holds and therefore observe one snapshot. Table and
column names come only from the declared manifest in :mod:`agents_remember.memory.knowledge.schema`;
a caller's text reaches these statements as a bound parameter and never as an identifier.

The typed JSON columns are decoded here rather than by a second decoder: :mod:`logical` owns which
columns are JSON and how a cell decodes, and these readers use it, so a read page and a logical
digest can never disagree about what a stored cell means.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import apsw

from agents_remember.memory.knowledge import logical, schema
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError

# The declared read order of each table: the DDL's primary key, which is not every table's leading
# column list. Ordering a read by a stored key rather than by insertion order is what makes two
# datasets holding the same records page identically.
_ORDER_COLUMNS: Mapping[str, str] = {
    "invariant": "invariant_id",
    "invariant_revision": "revision_id",
    "family": "family_id",
    "family_revision": "revision_id",
    "family_member": "member_id",
    "realization_claim": "claim_id",
    "source_anchor": "anchor_id",
}


def _require_order_column(table: str) -> str:
    """Return one table's declared order column, refusing a table this reader does not own."""

    column = _ORDER_COLUMNS.get(table)
    if column is None:  # pragma: no cover - a programming error, not a reachable input
        raise KnowledgeStorageError(f"{table} is not a table this reader declares an order for")
    return column


def fetch_revision_ids(
    connection: apsw.Connection, repository_id: str, table: str, identity_column: str, identity: str
) -> tuple[str, ...]:
    """Return every retained revision id of one identity, in declared order."""

    order = _require_order_column(table)
    rows = connection.execute(
        f"SELECT revision_id FROM {table} WHERE repository_id = ? AND {identity_column} = ? "
        f"ORDER BY {order}",
        (repository_id, identity),
    )
    return tuple(str(row[0]) for row in rows)


def fetch_identity_rows(
    connection: apsw.Connection,
    repository_id: str,
    table: str,
    identity_column: str,
    label_column: str,
) -> dict[str, str]:
    """Return one identity label per stored identity, keyed by identity."""

    order = _require_order_column(table)
    rows = connection.execute(
        f"SELECT {identity_column}, {label_column} FROM {table} WHERE repository_id = ? "
        f"ORDER BY {order}",
        (repository_id,),
    )
    return {str(row[0]): str(row[1]) for row in rows}


def fetch_invariant_revisions(
    connection: apsw.Connection, repository_id: str, revision_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Return the complete rows of the named invariant revisions, in declared order."""

    return _fetch_rows_by_ids(
        connection,
        repository_id,
        table="invariant_revision",
        revision_ids=revision_ids,
        json_columns=logical.JSON_COLUMNS["invariant_revision"],
    )


def fetch_family_revisions(
    connection: apsw.Connection, repository_id: str, revision_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Return the complete rows of the named family revisions, in declared order."""

    return _fetch_rows_by_ids(
        connection,
        repository_id,
        table="family_revision",
        revision_ids=revision_ids,
        json_columns=logical.JSON_COLUMNS["family_revision"],
    )


def _fetch_rows_by_ids(
    connection: apsw.Connection,
    repository_id: str,
    *,
    table: str,
    revision_ids: Sequence[str],
    json_columns: frozenset[str],
) -> list[dict[str, Any]]:
    """Return the declared columns of each named revision row, decoded, in declared order."""

    if not revision_ids:
        return []
    order = _require_order_column(table)
    columns = schema.CANONICAL_COLUMNS[table]
    placeholders = ", ".join("?" for _ in revision_ids)
    parameters: tuple[Any, ...] = (repository_id, *revision_ids)
    statement = (
        f"SELECT {', '.join(columns)} FROM {table} "
        f"WHERE repository_id = ? AND revision_id IN ({placeholders}) ORDER BY {order}"
    )
    return [
        {
            column: logical.cell_value(value, is_json=column in json_columns)
            for column, value in zip(columns, tuple(row), strict=True)
        }
        for row in connection.execute(statement, parameters)
    ]


def fetch_memberships_of_families_full(
    connection: apsw.Connection, repository_id: str, family_revision_ids: Sequence[str]
) -> list[tuple[str, str, str, Any]]:
    """Return the complete membership rows of the named family revisions, in member order.

    The rows are the memberships the named families actually hold: the filter is on the family
    revision the edge belongs to, so a member's *other* memberships are never selected by this
    lookup. That is the difference between reading a family's composition and traversing from it.
    """

    if not family_revision_ids:
        return []
    placeholders = ", ".join("?" for _ in family_revision_ids)
    rows = connection.execute(
        "SELECT member_id, family_revision_id, invariant_revision_id, provenance "
        f"FROM family_member WHERE repository_id = ? AND family_revision_id IN ({placeholders}) "
        "ORDER BY member_id",
        (repository_id, *family_revision_ids),
    )
    return [
        (str(row[0]), str(row[1]), str(row[2]), logical.cell_value(row[3], is_json=True))
        for row in rows
    ]


def fetch_family_ids_for_revisions(
    connection: apsw.Connection, repository_id: str, family_revision_ids: Sequence[str]
) -> dict[str, str]:
    """Return ``family_revision_id -> family_id`` for the named family revisions."""

    if not family_revision_ids:
        return {}
    placeholders = ", ".join("?" for _ in family_revision_ids)
    rows = connection.execute(
        "SELECT revision_id, family_id FROM family_revision "
        f"WHERE repository_id = ? AND revision_id IN ({placeholders}) ORDER BY revision_id",
        (repository_id, *family_revision_ids),
    )
    return {str(row[0]): str(row[1]) for row in rows}


def fetch_memberships_of_families(
    connection: apsw.Connection, repository_id: str, family_revision_ids: Sequence[str]
) -> dict[str, str]:
    """Return ``invariant_revision_id -> family_revision_id`` for the named family revisions."""

    if not family_revision_ids:
        return {}
    placeholders = ", ".join("?" for _ in family_revision_ids)
    rows = connection.execute(
        "SELECT invariant_revision_id, family_revision_id FROM family_member "
        f"WHERE repository_id = ? AND family_revision_id IN ({placeholders}) ORDER BY member_id",
        (repository_id, *family_revision_ids),
    )
    return {str(row[0]): str(row[1]) for row in rows}


def fetch_memberships_of_invariants(
    connection: apsw.Connection, repository_id: str, invariant_revision_ids: Sequence[str]
) -> list[tuple[str, str, str]]:
    """Return ``(member_id, family_revision_id, invariant_revision_id)`` for the named revisions."""

    if not invariant_revision_ids:
        return []
    placeholders = ", ".join("?" for _ in invariant_revision_ids)
    rows = connection.execute(
        "SELECT member_id, family_revision_id, invariant_revision_id FROM family_member "
        f"WHERE repository_id = ? AND invariant_revision_id IN ({placeholders}) ORDER BY member_id",
        (repository_id, *invariant_revision_ids),
    )
    return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def fetch_realizations_at_path(
    connection: apsw.Connection, repository_id: str, path: str
) -> list[dict[str, Any]]:
    """Return every recorded realization claim at one repository-relative path.

    The path is the anchor's own stored path, matched exactly: a stored anchor is authored data and
    this lookup resolves nothing, so a path that is not recorded selects nothing rather than being
    normalised, globbed or resolved against a working tree.
    """

    rows = connection.execute(
        "SELECT c.claim_id, c.invariant_revision_id, c.role, c.rationale, c.provenance, "
        "a.anchor_id, a.path, a.source_identity, a.locator, r.invariant_id "
        "FROM realization_claim c JOIN source_anchor a "
        "ON a.repository_id = c.repository_id AND a.anchor_id = c.anchor_id "
        "JOIN invariant_revision r ON r.repository_id = c.repository_id "
        "AND r.revision_id = c.invariant_revision_id "
        "WHERE c.repository_id = ? AND a.path = ? ORDER BY c.claim_id",
        (repository_id, path),
    )
    return [
        {
            "claim_id": str(row[0]),
            "invariant_revision_id": str(row[1]),
            "role": str(row[2]),
            "rationale": str(row[3]),
            "provenance": logical.cell_value(row[4], is_json=True),
            "anchor_id": str(row[5]),
            "path": str(row[6]),
            "source_identity": logical.cell_value(row[7], is_json=True),
            "locator": logical.cell_value(row[8], is_json=True),
            "invariant_id": str(row[9]),
        }
        for row in rows
    ]


def fetch_realizations_for_invariants(
    connection: apsw.Connection, repository_id: str, invariant_revision_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Return each realization claim of the named revisions with its recorded anchor.

    The claim's own row and the anchor it cites are one item: a claim without its location would
    report an authored relationship while hiding the source it names.
    """

    if not invariant_revision_ids:
        return []
    placeholders = ", ".join("?" for _ in invariant_revision_ids)
    rows = connection.execute(
        "SELECT c.claim_id, c.invariant_revision_id, c.role, c.rationale, c.provenance, "
        "a.anchor_id, a.path, a.source_identity, a.locator "
        "FROM realization_claim c JOIN source_anchor a "
        "ON a.repository_id = c.repository_id AND a.anchor_id = c.anchor_id "
        f"WHERE c.repository_id = ? AND c.invariant_revision_id IN ({placeholders}) "
        "ORDER BY c.claim_id",
        (repository_id, *invariant_revision_ids),
    )
    return [
        {
            "claim_id": str(row[0]),
            "invariant_revision_id": str(row[1]),
            "role": str(row[2]),
            "rationale": str(row[3]),
            "provenance": logical.cell_value(row[4], is_json=True),
            "anchor_id": str(row[5]),
            "path": str(row[6]),
            "source_identity": logical.cell_value(row[7], is_json=True),
            "locator": logical.cell_value(row[8], is_json=True),
        }
        for row in rows
    ]
