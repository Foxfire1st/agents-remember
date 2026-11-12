"""The read-only legacy database reader and the one-time export (MIK-R24 rules 4 and 9).

The committed ``knowledge.sqlite`` is read, never written: every line and every user converts
through this reader, so it outlives the database code (MIK-R26 retires the rest).

**Export.** Each invariant and family is exported at its head revision (the revision no predecessor
row names as parent):

* ``id`` is derived from the database ID (:func:`derived_record_id`), which is kept in
  ``origin.legacyId``; ``revision`` is the head's position in its predecessor chain (root is 1);
* ``origin.task`` and ``origin.leaf`` come from the head's ``provenance.actor_ref``: a leaf named in
  parentheses (``… (260921-ICR-L30)``) or a leaf document (``…/<task-dir>/38_<slug>.json``, whose
  task ID is learned from a parenthesised leaf of the same task directory); otherwise the task
  directory alone;
* the slug comes from ``display_label``; ``status`` from ``state_at_origin``;
* ``conditions`` lines beginning ``Hand-off kind:``, ``Producer's disposition:`` or ``Evidence:``
  move to ``origin.handoff.evidence``;
* families get ``routes: []`` (MIK-R04 ``route_unassigned``) and records ``admission:
  "legacy-unassessed"``.

Every realization claim on a head revision becomes a ``realizes`` entry: its anchor keeps the claim's
recorded blob and locator, and ``content`` is computed in that recorded blob -- nothing is re-hashed
at the conversion's code tree, so staleness stays visible. Entry IDs are derived from (invariant
legacy ID, path, locator). Any collision among derived IDs refuses the export, naming both legacy
IDs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import apsw

from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.models.knowledge_files.documents import record_path
from agents_remember.models.knowledge_files.ids import (
    derived_realization_id,
    derived_record_id,
)

HANDOFF_PREFIXES: Final = ("Hand-off kind:", "Producer's disposition:", "Evidence:")
_LEAF_IN_PARENS: Final = re.compile(r"\((?P<leaf>(?P<task>[0-9]{6}-[A-Z0-9]+)-L[0-9A-Za-z]+)\)")
_TASK_DIRECTORY: Final = re.compile(r"(?:^|/)tasks/[^/]+/(?P<directory>[^/]+)/(?P<rest>.+)$")
_LEAF_DOCUMENT: Final = re.compile(r"^(?P<number>[0-9]+)_[^/]+\.json$")
_SLUG_CHARACTERS: Final = re.compile(r"[^A-Za-z0-9._-]+")
_STATUSES: Final = frozenset({"proposed", "accepted", "retired"})
# MIK-R12's ruling: the hand-off role ``incidental`` is written as ``support``.
_ROLE_SPELLINGS: Final = {"incidental": "support"}


class ExportError(ValueError):
    """The database cannot be exported as it stands; the message names the rows."""


@dataclass
class ExportResult:
    """Exported records by path, entries by source path, and what the report needs."""

    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    entries: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    invariants: int = 0
    families: int = 0
    revision_depths: dict[int, int] = field(default_factory=dict)
    disambiguated: list[str] = field(default_factory=list)


def slug(label: str) -> str:
    """The display slug of a record file: ``display_label`` with other characters as ``-``."""

    cleaned = _SLUG_CHARACTERS.sub("-", label).strip("-._")
    return cleaned[:80].rstrip("-._") or "record"


def _heads(
    connection: apsw.Connection, table: str, predecessor: str, identity: str
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Each identity's head revision row, and every revision's depth in its chain."""

    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    revisions = {
        str(row[columns.index("revision_id")]): dict(zip(columns, row, strict=True))
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY revision_id")
    }
    parents: dict[str, list[str]] = {}
    children: set[str] = set()
    for child, parent in connection.execute(
        f"SELECT child_revision_id, parent_revision_id FROM {predecessor} ORDER BY 1, 2"
    ):
        parents.setdefault(str(child), []).append(str(parent))
        children.add(str(parent))

    def depth(revision: str, seen: frozenset[str] = frozenset()) -> int:
        if revision in seen:
            raise ExportError(f"{table}: revision {revision} is in a predecessor cycle")
        chain = parents.get(revision, [])
        return 1 + max((depth(parent, seen | {revision}) for parent in chain), default=0)

    heads: dict[str, dict[str, Any]] = {}
    for revision, row in revisions.items():
        if revision in children:
            continue
        key = str(row[identity])
        if key in heads:
            raise ExportError(
                f"{table}: {key} has more than one head revision "
                f"({heads[key]['revision_id']}, {revision})"
            )
        heads[key] = row
    return heads, {revision: depth(revision) for revision in revisions}


class _Origins:
    """``origin.task``/``origin.leaf`` from ``provenance.actor_ref``, learned per task directory."""

    def __init__(self, actor_refs: list[str]) -> None:
        self.task_ids: dict[str, str] = {}
        for actor in sorted(actor_refs):
            match = _LEAF_IN_PARENS.search(actor)
            located = _TASK_DIRECTORY.search(actor)
            if match and located:
                self.task_ids.setdefault(located["directory"], match["task"])

    def of(self, provenance: str) -> dict[str, str]:
        actor = str(json.loads(provenance).get("actor_ref", "")).strip()
        match = _LEAF_IN_PARENS.search(actor)
        if match:
            return {"task": match["task"], "leaf": match["leaf"]}
        located = _TASK_DIRECTORY.search(actor)
        if located is None:
            return {"task": actor or "legacy-export"}
        directory = located["directory"]
        task = self.task_ids.get(directory, directory)
        document = _LEAF_DOCUMENT.match(located["rest"])
        if document and directory in self.task_ids:
            return {"task": task, "leaf": f"{task}-L{int(document['number']):02d}"}
        return {"task": task}


def _split_conditions(conditions: list[str]) -> tuple[list[str], list[str]]:
    kept = [line for line in conditions if not line.startswith(HANDOFF_PREFIXES)]
    moved = [line for line in conditions if line.startswith(HANDOFF_PREFIXES)]
    return kept, moved


def _status(value: object, what: str) -> str:
    status = str(value)
    if status not in _STATUSES:
        raise ExportError(f"{what}: state_at_origin {status!r} has no text-format status")
    return status


def _new_locator(legacy: Mapping[str, Any]) -> dict[str, Any]:
    kind = legacy.get("kind")
    if kind == "symbol":
        return {"kind": "symbol", "name": str(legacy["qualified_name"])}
    if kind == "line_range":
        return {
            "kind": "line_range",
            "start": int(legacy["start_line"]),
            "end": int(legacy["end_line"]),
        }
    if kind == "file":
        return {"kind": "file"}
    raise ExportError(f"unknown legacy locator kind {kind!r}")


def _claim_origin(origins: _Origins, provenance: str) -> dict[str, str] | None:
    origin = origins.of(provenance)
    return {"leaf": origin["leaf"]} if "leaf" in origin else None


def _record_origin(origins: _Origins, provenance: str, legacy_id: str) -> dict[str, Any]:
    return {**origins.of(provenance), "legacyId": legacy_id}


def export_database(database: Path, objects: CodeObjects) -> ExportResult:
    """Export the head knowledge of ``database`` (opened read-only) into file documents."""

    connection = apsw.Connection(str(database), flags=apsw.SQLITE_OPEN_READONLY)
    try:
        return _export(connection, objects)
    finally:
        connection.close()


def _export(connection: apsw.Connection, objects: CodeObjects) -> ExportResult:
    invariants, invariant_depths = _heads(
        connection, "invariant_revision", "invariant_predecessor", "invariant_id"
    )
    families, family_depths = _heads(
        connection, "family_revision", "family_predecessor", "family_id"
    )
    labels = {
        "invariant": dict(connection.execute("SELECT invariant_id, display_label FROM invariant")),
        "family": dict(connection.execute("SELECT family_id, display_label FROM family")),
    }
    origins = _Origins(
        [
            str(json.loads(provenance).get("actor_ref", ""))
            for (provenance,) in connection.execute(
                "SELECT provenance FROM invariant_revision UNION ALL "
                "SELECT provenance FROM family_revision UNION ALL "
                "SELECT provenance FROM realization_claim"
            )
        ]
    )
    result = ExportResult()
    derived: dict[str, str] = {}

    def claim_id(kind: str, legacy_id: str) -> str:
        identifier = derived_record_id(kind, legacy_id)  # type: ignore[arg-type]
        if identifier in derived and derived[identifier] != legacy_id:
            raise ExportError(
                f"derived ID {identifier} collides: legacy IDs {derived[identifier]} and {legacy_id}"
            )
        derived[identifier] = legacy_id
        return identifier

    invariant_of_revision = dict(
        connection.execute("SELECT revision_id, invariant_id FROM invariant_revision")
    )
    for legacy_id, row in sorted(invariants.items()):
        identifier = claim_id("invariant", legacy_id)
        conditions, handoff = _split_conditions(list(json.loads(row["conditions"])))
        origin = _record_origin(origins, row["provenance"], legacy_id)
        if handoff:
            origin["handoff"] = {"evidence": handoff}
        revision = invariant_depths[str(row["revision_id"])]
        result.revision_depths[revision] = result.revision_depths.get(revision, 0) + 1
        path = record_path("invariant", identifier, slug(str(labels["invariant"][legacy_id])))
        result.records[path] = {
            "schema": "ar-invariant/v1",
            "id": identifier,
            "revision": revision,
            "status": _status(row["state_at_origin"], legacy_id),
            "statement": row["statement"],
            "applicability": row["applicability"],
            "conditions": conditions,
            "exclusions": list(json.loads(row["exclusions"])),
            "supersedes": [],
            "admission": "legacy-unassessed",
            "origin": origin,
        }
        result.invariants += 1
    for legacy_id, row in sorted(families.items()):
        identifier = claim_id("family", legacy_id)
        members = sorted(
            {
                derived_record_id("invariant", str(invariant_of_revision[str(member)]))
                for (member,) in connection.execute(
                    "SELECT invariant_revision_id FROM family_member WHERE family_revision_id = ?",
                    (row["revision_id"],),
                )
            }
        )
        path = record_path("family", identifier, slug(str(labels["family"][legacy_id])))
        result.records[path] = {
            "schema": "ar-family/v1",
            "id": identifier,
            "revision": family_depths[str(row["revision_id"])],
            "status": _status(row["state_at_origin"], legacy_id),
            "title": str(labels["family"][legacy_id]),
            "guarantee": row["joint_guarantee"],
            "members": members,
            "routes": [],
            "admission": "legacy-unassessed",
            "origin": _record_origin(origins, row["provenance"], legacy_id),
        }
        result.families += 1
    _export_realizations(connection, objects, invariants, origins, result)
    return result


def _export_realizations(
    connection: apsw.Connection,
    objects: CodeObjects,
    invariants: Mapping[str, Mapping[str, Any]],
    origins: _Origins,
    result: ExportResult,
) -> None:
    head_revisions = {str(row["revision_id"]): legacy for legacy, row in invariants.items()}
    rows = list(
        connection.execute(
            "SELECT c.claim_id, c.invariant_revision_id, c.role, c.rationale, c.provenance, "
            "a.path, a.source_identity, a.locator FROM realization_claim c "
            "JOIN source_anchor a ON a.anchor_id = c.anchor_id ORDER BY c.claim_id"
        )
    )
    claims = [row for row in rows if str(row[1]) in head_revisions]
    blobs = {str(json.loads(row[6])["object_id"]) for row in claims}
    objects.prefetch(blob for blob in blobs if objects.has_blob(blob))
    seen: dict[str, str] = {}
    for claim, revision, role, rationale, provenance, path, identity, locator_text in claims:
        legacy_invariant = head_revisions[str(revision)]
        locator = _new_locator(json.loads(locator_text))
        entry_id = derived_realization_id(legacy_invariant, str(path), locator)
        if entry_id in seen:
            raise ExportError(
                f"derived entry ID {entry_id} collides: legacy claims {seen[entry_id]} and {claim}"
            )
        seen[entry_id] = str(claim)
        blob = str(json.loads(identity)["object_id"])
        content = (
            objects.content(blob, locator, str(path), top_level=True)
            if objects.has_blob(blob)
            else None
        )
        if (
            content is not None
            and locator["kind"] == "symbol"
            and objects.symbol_span(str(path), blob, str(locator["name"])) is None
        ):
            result.disambiguated.append(f"{path}::{locator['name']} (claim {claim})")
        if content is None:
            raise ExportError(
                f"claim {claim}: {path} {locator} does not resolve in its recorded blob {blob}"
            )
        entry: dict[str, Any] = {
            "id": entry_id,
            "invariant": derived_record_id("invariant", legacy_invariant),
            "anchor": {"locator": locator, "blob": blob, "content": content},
            "role": _ROLE_SPELLINGS.get(str(role), str(role)),
            "rationale": str(rationale),
        }
        origin = _claim_origin(origins, str(provenance))
        if origin is not None:
            entry["origin"] = origin
        result.entries.setdefault(str(path), []).append(entry)
