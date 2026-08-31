"""One-shot migration from leaf-key catalog rows to document-owned seat bindings."""

from __future__ import annotations

import json
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import migrated_seat_role
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA, read_task_doc
from agents_remember.tasks.document_refs import (
    SPRINT_ROLES,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.worktrees.leaf_refs import LeafRefResolutionError, resolve_leaf_ref


class TerminalCatalogMigrationError(ValueError):
    """A v1 binding could not be mapped to one real task document."""


def migrate_terminal_catalog_v1(
    coordination_root: Path, rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Translate every v1 row exactly once; refuse any ambiguous structural binding."""

    topology = TaskDocumentTopology(coordination_root)
    return [_migrate_row(coordination_root, topology, row) for row in rows]


def _migrate_row(
    coordination_root: Path,
    topology: TaskDocumentTopology,
    row: dict[str, object],
) -> dict[str, object]:
    migrated = dict(row)
    role = migrated_seat_role(
        persisted=_text(row.get("seatRole")),
        spawn_role=_text(row.get("spawnRole")),
        kind="harness" if row.get("kind") == "harness" else "terminal",
    )
    binding = _legacy_binding_ref(coordination_root, topology, row, role, "leafKey")
    replacement = _legacy_binding_ref(coordination_root, topology, row, role, "replacementForLeaf")
    for obsolete in ("leafKey", "replacementForLeaf", "spawnRepo", "spawnSprint"):
        migrated.pop(obsolete, None)
    if binding is not None:
        migrated["taskDocumentRef"] = binding.model_dump()
    if replacement is not None:
        migrated["replacementForTaskDocumentRef"] = replacement.model_dump()
    return migrated


def _legacy_binding_ref(
    coordination_root: Path,
    topology: TaskDocumentTopology,
    row: dict[str, object],
    role: str,
    field: str,
) -> TaskDocumentRef | None:
    legacy = _text(row.get(field))
    if legacy is not None:
        leaf = legacy_leaf_document_ref(coordination_root, topology, legacy)
        return task_ref_for_role(topology, leaf, role)
    if field == "leafKey":
        scoped = _legacy_named_scope(topology, row, role)
        if scoped is not None:
            return scoped
    return None


def legacy_leaf_document_ref(
    coordination_root: Path, topology: TaskDocumentTopology, leaf_key: str
) -> TaskDocumentRef:
    try:
        resolved = resolve_leaf_ref(coordination_root, None, leaf_key)
    except LeafRefResolutionError as exc:
        raise TerminalCatalogMigrationError(str(exc)) from exc
    matches: list[Path] = []
    for path in sorted(resolved.task_root.glob("*.json")):
        if path.name == "task.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
            continue
        try:
            document = read_task_doc(path)
        except (OSError, ValueError):
            continue
        if document.id == resolved.doc_id:
            matches.append(path)
    if len(matches) != 1:
        raise TerminalCatalogMigrationError(
            f"legacy leaf {leaf_key!r} resolved to {len(matches)} task documents"
        )
    return topology.canonical_ref(resolved.repo_name, matches[0])


def task_ref_for_role(
    topology: TaskDocumentTopology, leaf: TaskDocumentRef, role: str
) -> TaskDocumentRef:
    try:
        if role in SPRINT_ROLES:
            master = topology.parent(leaf)
            if master is None:
                raise TerminalCatalogMigrationError(f"legacy leaf {leaf.key} has no master")
            sprint = topology.parent(master)
            if sprint is None:
                raise TerminalCatalogMigrationError(f"legacy master {master.key} has no sprint")
            topology.validate_role(sprint, role)
            return sprint
        if role == "manager":
            master = topology.parent(leaf)
            if master is None:
                raise TerminalCatalogMigrationError(f"legacy leaf {leaf.key} has no master")
            topology.validate_role(master, role)
            return master
        # Generic chat/terminal bindings were leaf-local too. They remain document-owned but are
        # not orchestration roles, so no altitude policy is fabricated for them.
        if role in {"worker", "reviewer", "curator"}:
            topology.validate_role(leaf, role)
        return leaf
    except TaskDocumentRefError as exc:
        raise TerminalCatalogMigrationError(str(exc)) from exc


def _legacy_named_scope(
    topology: TaskDocumentTopology, row: dict[str, object], role: str
) -> TaskDocumentRef | None:
    repository = _text(row.get("spawnRepo"))
    folder = _text(row.get("spawnSprint"))
    if repository is None and folder is None:
        return None
    if repository is None or folder is None:
        raise TerminalCatalogMigrationError("legacy named-seat scope is incomplete")
    if role == "reviewer":
        raise TerminalCatalogMigrationError(
            "legacy reviewer rows require their original leafKey; a named scope cannot prove a "
            "new master or sprint review seam"
        )
    candidate = topology.canonical_ref(repository, f"{folder}/task.json")
    altitude = topology.altitude(candidate)
    if role in SPRINT_ROLES and altitude == "master":
        parent = topology.parent(candidate)
        if parent is None:
            raise TerminalCatalogMigrationError(f"legacy master {candidate.key} has no sprint")
        candidate = parent
    topology.validate_role(candidate, role)
    return candidate


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
