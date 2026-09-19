"""Canonical routes: the normalised confined path and the acyclic hierarchy rule.

A ``Route`` is the detection scope axis: it carries a stable ID, its repository, its current
code-path scope, its parent route, and a normalised confined path within an acyclic hierarchy.

Two rules live here and both are *recorded* rather than inferred:

* **Confinement** (requirement 4.2). A route path is a nonempty repository-relative POSIX path with
  no absolute root, no drive or UNC form, no backslash escape, no NUL, no ``..``, and no escaping
  symlink at resolution. Two spellings of one path must not produce two routes, so the spelling is
  normalised once, here, and a path that cannot be normalised into the admitted form is refused
  rather than stored.
* **Acyclicity** (requirement 4.3). The hierarchy is acyclic within one repository, and the check
  runs over parent → child **after insertion and before commit, in the same transaction**. A cycle
  refuses the whole batch and rolls it back.

**Scope is never inferred** (requirement 4.5). Nothing here derives a route membership from a name,
a folder ancestry, a path prefix or a symbol string. The path is a declared input; a comparison of a
stored anchor path against it is a resolution fact a caller may compute, and it is not authored
membership.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

import apsw

from agents_remember.memory.knowledge.records import encode_authorship
from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

ROUTE_OPERATION: KnowledgeOperation = "create_invariant_revision"

# A Windows drive-relative or drive-absolute spelling, and a UNC or backslash form. All three are
# machine locations rather than repository-relative scopes, so they are refused by name instead of
# being normalised into something that looks confined.
_DRIVE_FORM = re.compile(r"^[A-Za-z]:")
_UNC_FORM = re.compile(r"^(\\\\|//)[^/\\]")

# The one spelling a route path may take: repository-relative POSIX, no leading separator, no empty
# or dot segment, no traversal.
_PATH_SEGMENT_FORBIDDEN = {"", ".", ".."}


# One row per way a path can fail confinement, as a rule table rather than an if-ladder: the rule
# *is* the reason, so a reader can add a form to the list instead of threading a tenth branch
# through the guard sequence.
_CONFINEMENT_RULES: tuple[tuple[str, object], ...] = (
    ("a route path must be a nonempty repository-relative POSIX path", lambda path: not path),
    ("a route path must not contain a NUL", lambda path: "\x00" in path),
    (
        "a route path must be a repository-relative POSIX path with forward separators only",
        lambda path: "\\" in path,
    ),
    ("a route path must not carry a drive letter", lambda path: bool(_DRIVE_FORM.match(path))),
    ("a route path must not be a UNC or network form", lambda path: bool(_UNC_FORM.match(path))),
    ("a route path must be repository-relative, not absolute", lambda path: path.startswith("/")),
    ("a route path must not end with a separator", lambda path: path.endswith("/")),
    (
        "a route path must already be in its normalised form; a path that needs normalising to "
        "become confined is refused rather than rewritten",
        lambda path: path != posixpath.normpath(path),
    ),
    (
        "a route path must not carry an empty or dot segment, and must not traverse outside the "
        "repository",
        lambda path: any(segment in _PATH_SEGMENT_FORBIDDEN for segment in path.split("/")),
    ),
)


def normalize_route_path(path: object) -> str | KnowledgeRefusal:
    """Return the one admitted spelling of one route path, or the refusal that replaced it.

    Normalisation is not repair: ``posixpath.normpath`` is used as a *comparison* against a path
    that is already confined, and a path that needed normalising to become confined is refused
    rather than silently rewritten. Rewriting is how two spellings become one route while a caller
    believes it authored two.
    """

    if not isinstance(path, str):
        return _path_refusal("a route path must be a string", repr(path))
    for detail, breached in _CONFINEMENT_RULES:
        if breached(path):  # type: ignore[operator]
            return _path_refusal(detail, path)
    return path


def require_acyclic_routes(
    connection: apsw.Connection,
    repository_id: str,
    operation: KnowledgeOperation = ROUTE_OPERATION,
) -> KnowledgeRefusal | None:
    """Return the refusal for a route hierarchy that reaches itself, or ``None``.

    The walk is a ``UNION``-deduplicated recursive CTE over parent → child. The anchor is
    ``(route_id, parent_route_id)`` -- one row per *edge*, not per route -- and each step replaces
    the walked node with its parent. Anchoring on the edge is what keeps the check honest: an
    anchor of ``(route_id, route_id)`` would satisfy ``start = node`` for every route and report a
    cycle on an acyclic hierarchy. Deduplication is over the whole ``(start, node)`` pair, and both
    members range over the repository's route IDs, so the walk closes on the very graph it is
    checking instead of looping -- and it still reaches ``start = node`` exactly when the parent
    chain from ``start`` returns to ``start``.

    A one-node cycle is caught by the table's own ``CHECK (parent_route_id <> route_id)``; this
    catches the longer one. It runs **inside the caller's transaction**, after the insertions and
    before the commit, so a cycle refuses the whole batch rather than leaving a partial hierarchy.

    ``operation`` names the operation the refusal is attributed to, and it defaults to the
    authoring operation this rule was written for. A second production path that replays
    ``route.parent_route_id`` changes -- the merge, which applies a side's changeset and must
    refuse a candidate whose hierarchy reaches itself before that candidate exists -- passes its
    own identity, so the refusal a caller branches on names the call it made while the rule itself
    stays the one walk in this module.
    """

    rows = connection.execute(
        """
        WITH RECURSIVE walk(start, node) AS (
            SELECT route_id, parent_route_id FROM route
            WHERE repository_id = ? AND parent_route_id IS NOT NULL
            UNION
            SELECT walk.start, route.parent_route_id
            FROM walk
            JOIN route ON route.repository_id = ? AND route.route_id = walk.node
            WHERE route.parent_route_id IS NOT NULL
        )
        SELECT start FROM walk WHERE start = node ORDER BY start
        """,
        (repository_id, repository_id),
    )
    cycle = tuple(sorted({str(row[0]) for row in rows}))
    if not cycle:
        return None
    return refusal(
        "lineage_cycle",
        operation,
        "the route hierarchy reaches itself, so it is not an acyclic hierarchy and no route in "
        "the cycle names a scope",
        facts=RefusalFacts(
            table="route",
            record_id=cycle[0],
            expected="an acyclic parent chain",
            observed=" | ".join(cycle),
        ),
        next_action=(
            "Correct the parent_route_id of the named routes so the chain terminates. The whole "
            "batch was rolled back; no partial hierarchy was stored."
        ),
    )


def _path_refusal(detail: str, observed: str) -> KnowledgeRefusal:
    """Refuse a route path that is not in the one admitted form."""

    return refusal(
        "invalid_reference",
        ROUTE_OPERATION,
        f"the route path is not a confined repository-relative scope: {detail}",
        facts=RefusalFacts(
            table="route", expected="a confined repository-relative path", observed=observed
        ),
        next_action=(
            "Spell the scope as a normalised repository-relative POSIX path. Route scope is a "
            "recorded input, never inferred from a name, a folder ancestry or a path prefix."
        ),
    )


# ---------------------------------------------------------------------------
# The write layer: making Route an operable scope axis (requirement 4.4).
#
# Before this layer, ``Route`` was generation-2 DDL plus a primary-key constraint, so
# nothing could author a route or attach a governed row to one and the association 4.4
# promises was not operable. These are the authored side; :func:`find_governing_route`
# is the read side.


@dataclass(frozen=True)
class RouteDraft:
    """One authored route, as a request rather than six positional arguments."""

    route_id: str
    path: str
    parent_route_id: str | None = None


@dataclass(frozen=True)
class GoverningRouteDraft:
    """One declared association of a governed row with the route that governs it."""

    governed_table: str
    governed_id: str
    route_id: str


AUTHOR_ROUTE_OPERATION: KnowledgeOperation = "author_route"
GOVERNING_ROUTE_OPERATION: KnowledgeOperation = "set_governing_route"

# One governed generation-1 entity maps to exactly one join table, and that table's
# primary key is the governed entity's own key. The mapping is data rather than a branch,
# so "a governed row has at most one governing route" is enforced by the table key rather
# than by a convention a later edit could quietly drop.
_GOVERNED_TABLES: dict[str, tuple[str, str]] = {
    "source_anchor": ("source_anchor_route", "anchor_id"),
    "invariant": ("invariant_route", "invariant_id"),
    "family": ("family_route", "family_id"),
}

# The one governed entity that carries its route as its own column instead of through a join
# table (requirement 4.4): a ``knowledge_record`` is new in generation 2, so it can hold
# ``governing_route_id`` directly and has nothing to join. It is therefore governed without an
# entry in the mapping above -- and the read path below still answers for it, because "governed by
# a column" and "not a governed entity" are different answers and only one of them is a caller
# error.
_ENVELOPE_TABLE = "knowledge_record"
_ENVELOPE_KEY_COLUMN = "record_id"
_ENVELOPE_ROUTE_COLUMN = "governing_route_id"

_ROUTE_INSERT = (
    "INSERT INTO route (repository_id, route_id, parent_route_id, path, provenance) "
    "VALUES (?, ?, ?, ?, ?)"
)

_ROUTE_BY_PATH = "SELECT route_id FROM route WHERE repository_id = ? AND path = ?"
_ROUTE_BY_ID = "SELECT 1 FROM route WHERE repository_id = ? AND route_id = ?"


def _route_for_path(connection: apsw.Connection, repository_id: str, path: str) -> str | None:
    """Return the id of the route already authored for this exact path, or ``None``."""

    for row in connection.execute(_ROUTE_BY_PATH, (repository_id, path)):
        return str(row[0])
    return None


def route_for_path(connection: apsw.Connection, repository_id: str, path: str) -> str | None:
    """Return the id of the route already authored for one admissible path, or ``None``.

    Public because ``author_route`` answers a path that already has a row by returning *that* row's
    id, and a caller reporting what it wrote needs the two facts apart: "this run authored the row"
    and "this run answered with a row that was already there" are the same return value. The path is
    normalised first, because the lookup ``author_route`` performs is on the admitted spelling, and
    asking with an unadmitted spelling would answer ``None`` for a row that is really there -- which
    would make a caller believe it had written one it had not.
    """

    admitted = normalize_route_path(path)
    if isinstance(admitted, KnowledgeRefusal):
        return None
    return _route_for_path(connection, repository_id, admitted)


def route_exists(connection: apsw.Connection, repository_id: str, route_id: str) -> bool:
    """Whether one route identity is authored in this repository.

    Public because a writer that records a *governing* route has to answer this question before it
    writes the governed row: a named route that does not exist is a dangling reference to refuse,
    and ``None`` is a different fact -- the explicit ungoverned state requirement 4.4 permits.
    """

    for _ in connection.execute(_ROUTE_BY_ID, (repository_id, route_id)):
        return True
    return False


def _route_exists(connection: apsw.Connection, repository_id: str, route_id: str) -> bool:
    return route_exists(connection, repository_id, route_id)


def author_route(
    connection: apsw.Connection,
    repository_id: str,
    draft: RouteDraft,
    authorship: Authorship,
) -> str | KnowledgeRefusal:
    """Author one route, or return the refusal that replaced it.

    ``path`` must already be in the one admitted form: a path that would need normalising to
    become confined is refused rather than rewritten, because rewriting is how two spellings
    become one route while a caller believes it authored two. If a route is already authored
    for the admitted path, **its id is returned and no second row is written** -- that lookup
    is what keeps two spellings of one scope from producing two routes.

    The hierarchy is checked after the insert and inside the caller's transaction, so a cycle
    returns a refusal and the caller's rollback discards the whole batch rather than leaving a
    partial hierarchy stored.
    """

    admitted = normalize_route_path(draft.path)
    if isinstance(admitted, KnowledgeRefusal):
        return admitted
    existing = _route_for_path(connection, repository_id, admitted)
    if existing is not None:
        return existing
    if draft.parent_route_id is not None and not _route_exists(
        connection, repository_id, draft.parent_route_id
    ):
        return refusal(
            "missing_expected_row",
            AUTHOR_ROUTE_OPERATION,
            "the named parent route is not authored in this repository, so the hierarchy this "
            "route would join does not exist",
            facts=RefusalFacts(
                table="route",
                record_id=str(draft.parent_route_id),
                expected="an authored parent route in this repository",
                observed=str(draft.parent_route_id),
            ),
            next_action="Author the parent route first, or omit parent_route_id for a root route.",
        )
    connection.execute(
        _ROUTE_INSERT,
        (
            repository_id,
            draft.route_id,
            draft.parent_route_id,
            admitted,
            encode_authorship(authorship),
        ),
    )
    cycle = require_acyclic_routes(connection, repository_id)
    if cycle is not None:
        return cycle
    return draft.route_id


def find_governing_route(
    connection: apsw.Connection, repository_id: str, governed_table: str, governed_id: str
) -> str | None:
    """Return the id of the route governing this row, or ``None`` for explicitly ungoverned.

    ``None`` is a fact, not a default. It is never "the repository root": a route that was
    never authored is not a scope a row can be silently placed in, and requirement 4.5 keeps
    scope recorded rather than inferred.

    **One value answers two questions, deliberately.** ``None`` is returned for a row that is stored
    and carries no governing route, and for a row that is not stored in this repository at all -- for
    the three generation-1 entities because an absent row has no join row either, and for the envelope
    because there is no row to read a column from. A caller that must tell those apart asks the
    existence question itself: this function answers the governing question, and inventing a third
    answer for "absent" would make the three shipped entities and the envelope disagree about the same
    word.

    The four governed shapes of requirement 4.4 are all answerable here: the three generation-1
    entities through their join tables, and the envelope through ``governing_route_id`` on its own
    row. The envelope is read as its own column rather than through a join, so an ungoverned record
    -- a stored ``NULL`` -- reports the same explicit ungoverned state as a missing join row.
    """

    if governed_table == _ENVELOPE_TABLE:
        statement = (
            f"SELECT {_ENVELOPE_ROUTE_COLUMN} FROM {_ENVELOPE_TABLE} "
            f"WHERE repository_id = ? AND {_ENVELOPE_KEY_COLUMN} = ?"
        )
        for row in connection.execute(statement, (repository_id, governed_id)):
            return None if row[0] is None else str(row[0])
        return None
    mapping = _GOVERNED_TABLES.get(governed_table)
    if mapping is None:
        # "Not a governed entity" is a caller error, not an ungoverned row, and the two must not
        # collapse into one ``None``: a caller that asked about a table this leaf does not govern
        # would otherwise read a silent "ungoverned" where the honest answer is "wrong question".
        raise ValueError(
            f"{governed_table!r} is not a governed entity; expected one of "
            f"{' | '.join(sorted((*_GOVERNED_TABLES, _ENVELOPE_TABLE)))}"
        )
    join_table, governed_column = mapping
    statement = (
        f"SELECT route_id FROM {join_table} WHERE repository_id = ? AND {governed_column} = ?"
    )
    for row in connection.execute(statement, (repository_id, governed_id)):
        return str(row[0])
    return None


def _governing_association(draft: GoverningRouteDraft) -> tuple[str, str] | KnowledgeRefusal:
    """Return the join table and governed column for one draft, or the refusal replacing it.

    The dispatch is one place rather than a branch inside the write path, because "which table
    carries this association" is the question the write path asks and the only question a new
    governed entity changes. The envelope answers that question with a refusal rather than a join
    table: it is governed, but by its own column.
    """

    if draft.governed_table == _ENVELOPE_TABLE:
        return _envelope_association_refusal(draft)
    mapping = _GOVERNED_TABLES.get(draft.governed_table)
    if mapping is None:
        return refusal(
            "invalid_reference",
            GOVERNING_ROUTE_OPERATION,
            "the named table is not a governed entity, so it cannot carry a governing route",
            facts=RefusalFacts(
                table=str(draft.governed_table),
                record_id=str(draft.governed_id),
                expected=" | ".join(sorted(_GOVERNED_TABLES)),
                observed=str(draft.governed_table),
            ),
            next_action="Name one of the governed generation-1 entities.",
        )
    return mapping


def _envelope_association_refusal(draft: GoverningRouteDraft) -> KnowledgeRefusal:
    """Refuse an after-the-fact association for the envelope, truthfully.

    Requirement 4.4 makes the envelope a **governed** entity whose route is its own column, so
    "not a governed entity" would be a false refusal. What is refused is the *operation*: there is
    no association to set apart from the record write itself.
    """

    return refusal(
        "invalid_reference",
        GOVERNING_ROUTE_OPERATION,
        "a record envelope carries its governing route in its own column, written with the record, "
        "so there is no association to set after the fact",
        facts=RefusalFacts(
            table=_ENVELOPE_TABLE,
            record_id=str(draft.governed_id),
            expected=" | ".join(sorted(_GOVERNED_TABLES)),
            observed=f"{_ENVELOPE_TABLE} (governed by {_ENVELOPE_ROUTE_COLUMN})",
        ),
        next_action=(
            "Write the record with its governing_route_id; the envelope's association is part of "
            "the record write rather than a separate operation."
        ),
    )


def set_governing_route(
    connection: apsw.Connection,
    repository_id: str,
    draft: GoverningRouteDraft,
    authorship: Authorship,
) -> KnowledgeRefusal | None:
    """Attach one governed row to the route that governs it, or return the refusal.

    The governed row's key *is* the join table's primary key, so at most one governing route
    per governed row is a constraint of the table rather than a check this function remembers
    to perform. Re-stating the same association is idempotent; naming a different route for an
    already-governed row is refused rather than silently overwritten.

    The envelope is deliberately not an input here. Its association is a column on the record
    itself, written with the record, so there is no association to set afterwards; the refusal says
    that rather than claiming the envelope is not a governed entity, which requirement 4.4 makes
    false.
    """

    association = _governing_association(draft)
    if isinstance(association, KnowledgeRefusal):
        return association
    join_table, governed_column = association
    if not _route_exists(connection, repository_id, draft.route_id):
        return refusal(
            "missing_expected_row",
            GOVERNING_ROUTE_OPERATION,
            "the named route is not authored in this repository, so it cannot govern a row",
            facts=RefusalFacts(
                table="route",
                record_id=str(draft.route_id),
                expected="an authored route in this repository",
                observed=str(draft.route_id),
            ),
            next_action="Author the route first with author_route.",
        )
    governed_exists = False
    for _ in connection.execute(
        f"SELECT 1 FROM {draft.governed_table} WHERE repository_id = ? AND {governed_column} = ?",
        (repository_id, draft.governed_id),
    ):
        governed_exists = True
    if not governed_exists:
        return refusal(
            "missing_expected_row",
            GOVERNING_ROUTE_OPERATION,
            "the row this association would govern is not stored in this repository",
            facts=RefusalFacts(
                table=draft.governed_table,
                record_id=str(draft.governed_id),
                expected=f"a stored {draft.governed_table} row",
                observed=str(draft.governed_id),
            ),
            next_action="Author the governed row first, then associate it with its route.",
        )
    existing = find_governing_route(
        connection, repository_id, draft.governed_table, draft.governed_id
    )
    if existing is not None:
        if existing == draft.route_id:
            return None
        return refusal(
            "relationship_constraint",
            GOVERNING_ROUTE_OPERATION,
            "this row is already governed by a different route, and a governed row names at most "
            "one governing route",
            facts=RefusalFacts(
                table=join_table,
                record_id=str(draft.governed_id),
                expected=existing,
                observed=str(draft.route_id),
            ),
            next_action=(
                "Remove the existing association before naming another route; a row cannot be "
                "governed by two scopes at once."
            ),
        )
    connection.execute(
        f"INSERT INTO {join_table} (repository_id, {governed_column}, route_id, provenance) "
        "VALUES (?, ?, ?, ?)",
        (repository_id, draft.governed_id, draft.route_id, encode_authorship(authorship)),
    )
    return None


def require_confined_resolution(root: Path, path: str) -> KnowledgeRefusal | None:
    """Refuse a confined path that resolves outside ``root`` through a symlink, or return ``None``.

    The lexical rules in :func:`normalize_route_path` cannot see a symlink: ``src/link.py`` is a
    confined repository-relative spelling whether or not ``src/link.py`` actually points at
    ``/etc/passwd``. Requirement 4.2's confinement therefore has two halves, and this is the second
    one -- a resolution check that needs the repository root the path is relative to, which is why
    it is a separate function rather than another entry in the rule table.

    A path that does not exist yet is not refused here: it is confined by spelling, and a route may
    legitimately name a scope before a file exists in it. Only an escaping resolution is refused.
    """

    candidate = (root / path) if path else root
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=False)
    except OSError as error:
        return _path_refusal(f"the route path could not be resolved: {error}", path)
    if resolved.is_relative_to(resolved_root):
        return None
    return refusal(
        "invalid_reference",
        AUTHOR_ROUTE_OPERATION,
        "the route path resolves outside the repository through a symlink, so it is not a confined "
        "repository-relative scope",
        facts=RefusalFacts(
            table="route",
            expected=f"a path resolving inside {resolved_root}",
            observed=str(resolved),
        ),
        next_action=(
            "Name a scope whose resolution stays inside the repository. Confinement is a property of "
            "the resolved path, not only of its spelling."
        ),
    )
