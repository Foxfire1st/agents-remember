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
#
# The composition generation's six tables are declared here for exactly that reason. A composition
# read ordered by insertion is a defect rather than a presentation choice: two datasets holding the
# same edges must report the same links in the same order, and a revision's links must not move when
# an unrelated row is written. Each entry is the primary key's distinguishing column, so the order is
# the row's own identity rather than the order a write happened to produce.
#
# The supporting-record generation's five tables are declared here for the same reason, and the
# identity column named for them is the *second* column of every key below, because the first is the
# namespace: a table keyed ``(repository_id, identity)`` is ordered by its identity. The coverage
# table's key is the pair of endpoint columns, so it is declared in the order the DDL names them --
# which is also why the read orders a relation's rows by the pair rather than by either half.
_ORDER_COLUMNS: Mapping[str, str] = {
    "invariant": "invariant_id",
    "invariant_revision": "revision_id",
    "family": "family_id",
    "family_revision": "revision_id",
    "family_member": "member_id",
    "realization_claim": "claim_id",
    "source_anchor": "anchor_id",
    "family_revision_context_revision": "revision_id",
    "evidence_claim": "claim_id",
    "evidence_claim_invariant_subject": "claim_id",
    "evidence_claim_facet_subject": "claim_id",
    "evidence_claim_coverage": "claim_id_endpoint",
    "verification_observation": "observation_id",
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


# --- existence, for the two-snapshot comparison ---------------------------------------------
#
# A comparison has to distinguish three states that a single read never needs to tell apart: a
# record the other snapshot **does not hold**, a record it holds but the other side's declared
# selection **did not reach**, and a record both sides selected. Only the first is absence; the
# second is a fact about the selection, and reporting it as a deletion is the design's own named
# misreading ("present-but-outside-the-other-selected-scope is not deletion"). These lookups answer
# the first question and nothing else, by exact identity, one statement each; the caller already
# holds the selected set that answers the second.


def _row_exists(connection: apsw.Connection, statement: str, parameters: tuple[Any, ...]) -> bool:
    """Return whether one existence statement selects any row."""

    return next(iter(connection.execute(statement, parameters)), None) is not None


def invariant_revision_is_recorded(
    connection: apsw.Connection, repository_id: str, invariant_revision_id: str
) -> bool:
    """Return whether one snapshot holds the named invariant revision."""

    return _row_exists(
        connection,
        "SELECT 1 FROM invariant_revision WHERE repository_id = ? AND revision_id = ?",
        (repository_id, invariant_revision_id),
    )


def family_revision_is_recorded(
    connection: apsw.Connection, repository_id: str, family_revision_id: str
) -> bool:
    """Return whether one snapshot holds the named family revision."""

    return _row_exists(
        connection,
        "SELECT 1 FROM family_revision WHERE repository_id = ? AND revision_id = ?",
        (repository_id, family_revision_id),
    )


def membership_is_recorded(connection: apsw.Connection, repository_id: str, member_id: str) -> bool:
    """Return whether one snapshot holds the named membership row."""

    return _row_exists(
        connection,
        "SELECT 1 FROM family_member WHERE repository_id = ? AND member_id = ?",
        (repository_id, member_id),
    )


def fetch_composition_rows(
    connection: apsw.Connection, repository_id: str, family_revision_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Return the recorded composition links of the named family revisions, in declared order.

    The filter is on the revision the edge *participates in*, at either endpoint, so a link is
    reported for both of the revisions it relates and neither revision's link is inferred from the
    other's. The declared policy's version spelling, widened scope and bound travel with the link
    when one is declared, which is what requirement 3.3 asks of anything that reports a traversal
    result -- and this reports the *declaration*, never a traversal.
    """

    if not family_revision_ids:
        return []
    placeholders = ", ".join("?" for _ in family_revision_ids)
    rows = connection.execute(
        "SELECT composition.composition_id, composition.from_family_revision_id, "
        "composition.to_family_revision_id, composition.policy_id, composition.policy_version_id, "
        "composition.provenance, version.declared_version, version.widened_scope, "
        "version.depth_bound "
        "FROM family_composition AS composition "
        "LEFT JOIN family_composition_policy_version AS version "
        "ON version.repository_id = composition.repository_id "
        "AND version.policy_id = composition.policy_id "
        "AND version.policy_version_id = composition.policy_version_id "
        f"WHERE composition.repository_id = ? "
        f"AND (composition.from_family_revision_id IN ({placeholders}) "
        f"OR composition.to_family_revision_id IN ({placeholders})) "
        "ORDER BY composition.composition_id",
        (repository_id, *family_revision_ids, *family_revision_ids),
    )
    return [
        {
            "composition_id": str(row[0]),
            "from_family_revision_id": str(row[1]),
            "to_family_revision_id": str(row[2]),
            "policy_id": None if row[3] is None else str(row[3]),
            "policy_version_id": None if row[4] is None else str(row[4]),
            "provenance": logical.cell_value(row[5], is_json=True),
            "declared_version": None if row[6] is None else str(row[6]),
            "widened_scope": None if row[7] is None else str(row[7]),
            "depth_bound": None if row[8] is None else int(row[8]),
        }
        for row in rows
    ]


def fetch_owning_routes_for_revisions(
    connection: apsw.Connection, repository_id: str, family_revision_ids: Sequence[str]
) -> dict[str, str]:
    """Return ``family_revision_id -> route_id`` for the revisions that record an owning route.

    A revision absent from this mapping is the explicit **ungoverned** state. Nothing here fills the
    gap: there is no default to the repository root, to the family identity's own route or to a
    route derived from the revision's members.
    """

    if not family_revision_ids:
        return {}
    placeholders = ", ".join("?" for _ in family_revision_ids)
    rows = connection.execute(
        "SELECT family_revision_id, route_id FROM family_revision_route "
        f"WHERE repository_id = ? AND family_revision_id IN ({placeholders}) "
        "ORDER BY family_revision_id",
        (repository_id, *family_revision_ids),
    )
    return {str(row[0]): str(row[1]) for row in rows}


def fetch_contexts_for_revisions(
    connection: apsw.Connection, repository_id: str, family_revision_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Return the authored explanatory context of the named family revisions, in declared order.

    The row is the context's *current* revision, joined through the record's own designation, and it
    carries its exact revision identity and provenance so a caller can hold the text it read. A
    revision with no row here has no recorded context: it is reported absent, never reconstructed
    from the joint guarantee.
    """

    if not family_revision_ids:
        return []
    placeholders = ", ".join("?" for _ in family_revision_ids)
    rows = connection.execute(
        "SELECT context.context_id, context.family_id, context.family_revision_id, "
        "revision.revision_id, revision.predecessor_revision_id, revision.body, "
        "revision.provenance "
        "FROM family_revision_context AS context "
        "JOIN family_revision_context_revision AS revision "
        "ON revision.repository_id = context.repository_id "
        "AND revision.revision_id = context.current_revision_id "
        f"WHERE context.repository_id = ? "
        f"AND context.family_revision_id IN ({placeholders}) "
        "ORDER BY context.context_id",
        (repository_id, *family_revision_ids),
    )
    return [
        {
            "context_id": str(row[0]),
            "family_id": str(row[1]),
            "family_revision_id": str(row[2]),
            "revision_id": str(row[3]),
            "predecessor_revision_id": str(row[4]),
            "body": str(row[5]),
            "provenance": logical.cell_value(row[6], is_json=True),
        }
        for row in rows
    ]


def fetch_predecessor_edges(
    connection: apsw.Connection, repository_id: str
) -> tuple[tuple[str, str], ...]:
    """Return every ``(successor_revision_id, predecessor_revision_id)`` edge one snapshot records.

    Each revision kind records its own authored old/new relation in its own table, and both are read
    here because a comparison asks the same question of either. The edges are *authored*: they are
    what an author declared when they wrote the successor, so a comparison can pair two revisions
    without comparing labels, versions or insertion order -- none of which an author guarantees.
    """

    rows = connection.execute(
        "SELECT child_revision_id, parent_revision_id FROM invariant_predecessor "
        "WHERE repository_id = ? "
        "UNION ALL "
        "SELECT child_revision_id, parent_revision_id FROM family_predecessor "
        "WHERE repository_id = ? "
        "ORDER BY 1, 2",
        (repository_id, repository_id),
    )
    return tuple((str(row[0]), str(row[1])) for row in rows)


def realization_claim_is_recorded(
    connection: apsw.Connection, repository_id: str, claim_id: str
) -> bool:
    """Return whether one snapshot holds the named realization claim.

    The claim is looked up without its anchor: a comparison asks whether the *relationship* is
    still recorded, and a claim whose anchor is a separate row is still that relationship.
    """

    return _row_exists(
        connection,
        "SELECT 1 FROM realization_claim WHERE repository_id = ? AND claim_id = ?",
        (repository_id, claim_id),
    )
