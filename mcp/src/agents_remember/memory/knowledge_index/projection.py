"""The index as a knowledge dataset: the logical tables the existing read code runs over (MIK-R23 rule 6).

The recorded-scope selection (:mod:`agents_remember.memory.knowledge.read`), the views
(:mod:`.view_source`, :mod:`.family_view`) and the read seams in ``application/`` are SQL over the
knowledge store's logical tables, addressed by UUIDs. Rule 6 adapts them to the index rather than
rewriting them, so the index file *is* a dataset of the store's newest schema generation: this module
fills its ``repository``, ``invariant``, ``invariant_revision``, ``family``, ``family_revision``,
``family_member``, ``source_anchor`` and ``realization_claim`` tables from the same parsed files, and
every existing reader that takes a ``database_path`` reads the index path unchanged.

The mapping is a pure function of the files:

* **Identities.** Every text ID maps to a name-based UUID (``uuid5``) in one fixed namespace, and
  ``ix_uuid`` records the mapping both ways, so a read's UUIDs translate back to ``INV-…``,
  ``FAM-…`` and ``RLZ-…``. A record has exactly one revision in a tree, so its revision UUID is
  derived from ``<ID>@<revision>``.
* **The dataset's namespace** is the constant :data:`INDEX_REPOSITORY_ID` bound to the authority
  home :data:`INDEX_AUTHORITY_HOME`: the index is keyed by its tree alone (rule 2), so nothing
  outside the tree may enter it.
* **Provenance.** The files carry origin (task, leaf or wave) but no time of recording -- time is
  in ``git log``. The projected ``Authorship`` therefore names the origin and the record file, and
  its ``recorded_at`` is the fixed epoch instant, which no reader may read as a recording time.
* **Retired records are not live, so they are not projected.** The logical vocabulary has only the
  live states ``proposed`` and ``accepted`` (``KnowledgeState``); the database expressed "no longer
  current" by lineage (a superseded revision), which a text record does not have. Projecting a
  ``status: retired`` invariant or family under either live state would present it as current, so
  :func:`project` leaves it out: the reused read, views, comparison and scope construction never
  select it, a family's projected membership omits a retired member, and a realization of a retired
  invariant is not projected. The record, its entries, its memberships, links and history rows stay
  in the ``ix_*`` tables, where :mod:`.query` answers them with ``status: "retired"`` on the record.
* **What is not projected:** proofs, links, history rows, routes and facet records have no table
  the reused code reads; they are answered from the ``ix_*`` tables (:mod:`.query`). A membership
  or realization naming an invariant the tree does not hold is left out of the projection (the
  logical tables enforce that reference) and stays visible in ``ix_member`` and ``ix_entry``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

import apsw

from agents_remember.kernel.canonical_json import canonical_json_bytes, sha256_digest
from agents_remember.memory_quality.style.citations.grammars import grammar_of
from agents_remember.models.knowledge_files.records import FamilyRecord, InvariantRecord
from agents_remember.models.knowledge_files.shapes import (
    LineRangeLocator,
    Origin,
    SymbolLocator,
)
from agents_remember.models.knowledge_files.sidecars import RealizationEntry

if TYPE_CHECKING:
    from agents_remember.memory.knowledge_index.build import IndexedEntry, ParsedTree

# The fixed uuid5 namespace every projected identity is derived in (a chosen constant; any change
# would change every projected UUID, so it never changes within one index format).
INDEX_NAMESPACE: Final = uuid.UUID("6f0c7a53-3f1e-5b7e-9d0a-5a2c1e4b8d21")
# The one dataset namespace of every index: an index is keyed by its tree alone (rule 2).
INDEX_REPOSITORY_ID: Final = str(uuid.uuid5(INDEX_NAMESPACE, "repository"))
INDEX_AUTHORITY_HOME: Final = "ar-knowledge-index"
# The projected ``recorded_at``: files record no time of recording, so the epoch stands in and must
# never be read as one.
PROJECTED_RECORDED_AT: Final = "1970-01-01T00:00:00+00:00"
# A record's projected ``display_version`` is ``r<revision>``, its text ``revision`` field.
DISPLAY_VERSION_PREFIX: Final = "r"
RETIRED_STATUS: Final = "retired"

UUID_DDL: Final = """
CREATE TABLE ix_uuid (
  uuid TEXT PRIMARY KEY NOT NULL,
  id TEXT NOT NULL,
  role TEXT NOT NULL
) STRICT
"""


def text_uuid(role: str, text_id: str) -> str:
    """Return the UUID the projection gives ``text_id`` in ``role`` (identity, revision, …)."""

    return str(uuid.uuid5(INDEX_NAMESPACE, f"{role}:{text_id}"))


def project(connection: apsw.Connection, parsed: ParsedTree) -> None:
    """Fill the logical tables from ``parsed`` inside the caller's transaction."""

    connection.execute(UUID_DDL)
    connection.execute(
        "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
        (INDEX_REPOSITORY_ID, INDEX_AUTHORITY_HOME),
    )
    invariant_revisions: dict[str, str] = {}
    for indexed in parsed.records.values():
        record = indexed.record
        if isinstance(record, InvariantRecord) and record.status != RETIRED_STATUS:
            invariant_revisions[record.id] = _invariant(connection, record, indexed.path)
    for indexed in parsed.records.values():
        record = indexed.record
        if isinstance(record, FamilyRecord) and record.status != RETIRED_STATUS:
            _family(connection, record, indexed.path, invariant_revisions)
    for indexed in parsed.entries.values():
        revision = invariant_revisions.get(indexed.entry.invariant)
        if isinstance(indexed.entry, RealizationEntry) and revision is not None:
            _realization(connection, indexed, revision)


def _map(connection: apsw.Connection, role: str, text_id: str) -> str:
    value = text_uuid(role, text_id)
    connection.execute(
        "INSERT INTO ix_uuid (uuid, id, role) VALUES (?, ?, ?)", (value, text_id, role)
    )
    return value


def _cell(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _provenance(origin: Origin | None, path: str) -> str:
    actor = (
        "unrecorded"
        if origin is None
        else "/".join(part for part in (origin.task, origin.leaf or origin.wave) if part)
    )
    return _cell(
        {
            "actor_ref": actor,
            "authorization_ref": f"text-knowledge:{path}",
            "operation_id": text_uuid("file", path),
            "origin_refs": [],
            "recorded_at": PROJECTED_RECORDED_AT,
        }
    )


def _state(status: str, path: str) -> tuple[str, str | None]:
    """Map a live text status to the logical vocabulary; ``retired`` never reaches here."""

    return ("accepted", f"text-knowledge:{path}") if status == "accepted" else ("proposed", None)


def _invariant(connection: apsw.Connection, record: InvariantRecord, path: str) -> str:
    identity = _map(connection, "identity", record.id)
    revision = _map(connection, "revision", f"{record.id}@{record.revision}")
    provenance = _provenance(record.origin, path)
    state, acceptance = _state(record.status, path)
    connection.execute(
        "INSERT INTO invariant (repository_id, invariant_id, display_label, label_provenance) "
        "VALUES (?, ?, ?, ?)",
        (INDEX_REPOSITORY_ID, identity, record.id, provenance),
    )
    connection.execute(
        "INSERT INTO invariant_revision (repository_id, invariant_id, revision_id, display_version, "
        "statement, applicability, conditions, exclusions, state_at_origin, acceptance_ref, "
        "provenance, payload_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            INDEX_REPOSITORY_ID,
            identity,
            revision,
            f"{DISPLAY_VERSION_PREFIX}{record.revision}",
            record.statement,
            record.applicability,
            _cell(list(record.conditions)),
            _cell(list(record.exclusions)),
            state,
            acceptance,
            provenance,
            sha256_digest(record.to_document()),
        ),
    )
    return revision


def _family(
    connection: apsw.Connection,
    record: FamilyRecord,
    path: str,
    invariant_revisions: dict[str, str],
) -> None:
    identity = _map(connection, "identity", record.id)
    revision = _map(connection, "revision", f"{record.id}@{record.revision}")
    provenance = _provenance(record.origin, path)
    state, acceptance = _state(record.status, path)
    connection.execute(
        "INSERT INTO family (repository_id, family_id, display_label, label_provenance) "
        "VALUES (?, ?, ?, ?)",
        (INDEX_REPOSITORY_ID, identity, record.title, provenance),
    )
    connection.execute(
        "INSERT INTO family_revision (repository_id, family_id, revision_id, display_version, "
        "joint_guarantee, state_at_origin, acceptance_ref, provenance, payload_digest) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            INDEX_REPOSITORY_ID,
            identity,
            revision,
            f"{DISPLAY_VERSION_PREFIX}{record.revision}",
            record.guarantee,
            state,
            acceptance,
            provenance,
            sha256_digest(record.to_document()),
        ),
    )
    for member in record.members:
        member_revision = invariant_revisions.get(member)
        if member_revision is None:
            continue
        connection.execute(
            "INSERT INTO family_member (repository_id, member_id, family_revision_id, "
            "invariant_revision_id, provenance) VALUES (?, ?, ?, ?, ?)",
            (
                INDEX_REPOSITORY_ID,
                _map(connection, "member", f"{record.id}/{member}"),
                revision,
                member_revision,
                provenance,
            ),
        )


def _realization(connection: apsw.Connection, indexed: IndexedEntry, revision: str) -> None:
    entry = indexed.entry
    assert isinstance(entry, RealizationEntry)
    provenance = _provenance(None, indexed.sidecar)
    anchor = _map(connection, "anchor", entry.id)
    connection.execute(
        "INSERT INTO source_anchor (repository_id, anchor_id, path, source_identity, locator, "
        "provenance) VALUES (?, ?, ?, ?, ?, ?)",
        (
            INDEX_REPOSITORY_ID,
            anchor,
            indexed.path,
            _cell({"kind": "git_blob", "object_id": entry.anchor.blob}),
            _cell(_legacy_locator(entry, indexed.path)),
            provenance,
        ),
    )
    connection.execute(
        "INSERT INTO realization_claim (repository_id, claim_id, invariant_revision_id, anchor_id, "
        "role, rationale, provenance) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            INDEX_REPOSITORY_ID,
            _map(connection, "claim", entry.id),
            revision,
            anchor,
            entry.role,
            entry.rationale,
            provenance,
        ),
    )


def _legacy_locator(entry: RealizationEntry, path: str) -> dict[str, Any]:
    """Spell a sidecar locator the way the logical ``source_anchor.locator`` column does.

    The symbol's ``language`` is not stored in a sidecar (MIK-R21 dropped it: the extractor chooses
    its grammar by suffix); it is restored here from the same suffix table, or ``unparsed`` for a
    suffix no grammar covers.
    """

    locator = entry.anchor.locator
    if isinstance(locator, SymbolLocator):
        return {
            "kind": "symbol",
            "language": grammar_of(path) or "unparsed",
            "qualified_name": locator.name,
        }
    if isinstance(locator, LineRangeLocator):
        return {"kind": "line_range", "start_line": locator.start, "end_line": locator.end}
    return {"kind": "file"}
