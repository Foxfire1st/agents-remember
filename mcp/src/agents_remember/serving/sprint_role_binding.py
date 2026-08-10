"""Sprint provenance policy for named orchestration seats."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agents_remember.serving.terminal_catalog import TerminalCatalogEntry


NAMED_SPRINT_ROLES = frozenset({"architect", "orchestrator", "manager"})
SprintBindingRefusal = Literal["sprint-binding-required", "sprint-binding-conflict"]


@dataclass(frozen=True)
class SprintRoleBinding:
    """The immutable repository and sprint provenance of one named role seat."""

    repo: str
    sprint: str


@dataclass(frozen=True)
class SprintOpenBindingRequest:
    """The shared opener's named-seat identity inputs."""

    role: str | None
    leaf_key: str | None
    replacement_for_leaf: str | None
    existing: TerminalCatalogEntry | None
    spawned_by_session: str | None
    spawn_repo: str | None
    spawn_sprint: str | None


def sprint_binding_from_leaf(leaf_key: str | None) -> SprintRoleBinding | None:
    """Derive a sprint scope from one canonical ``repo/sprint/leaf`` key."""

    if leaf_key is None:
        return None
    parts = leaf_key.split("/", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return SprintRoleBinding(repo=parts[0], sprint=parts[1])


def sprint_binding_for_spawn(
    role: str | None,
    *,
    leaf_key: str | None,
    replacement_for_leaf: str | None,
    parent: TerminalCatalogEntry | None,
    parent_session_id: str | None = None,
) -> tuple[SprintRoleBinding | None, SprintBindingRefusal | None]:
    """Resolve a named seat's scope, inheriting it from a proven spawning seat."""

    if role not in NAMED_SPRINT_ROLES:
        return None, None
    declared = sprint_binding_from_leaf(leaf_key) or sprint_binding_from_leaf(replacement_for_leaf)
    if parent_session_id is not None and parent is None:
        return None, "sprint-binding-required"
    inherited = _parent_binding(parent)
    if parent_session_id is not None and inherited is None:
        return None, "sprint-binding-required"
    if declared is not None and inherited is not None and declared != inherited:
        return None, "sprint-binding-conflict"
    binding = declared or inherited
    if binding is None or (parent_session_id is None and role != "architect"):
        return None, "sprint-binding-required"
    return binding, None


def sprint_binding_for_attachment(
    role: str,
    *,
    leaf_key: str,
    entry: TerminalCatalogEntry,
) -> tuple[SprintRoleBinding | None, SprintBindingRefusal | None]:
    """Resolve a named attachment without permitting a global legacy identity."""

    if role not in NAMED_SPRINT_ROLES:
        return None, None
    declared = sprint_binding_from_leaf(leaf_key)
    if declared is None:
        return None, "sprint-binding-required"
    existing = _parent_binding(entry)
    if existing is not None:
        if existing != declared:
            return None, "sprint-binding-conflict"
        return existing, None
    if entry.spawned_by_session is not None or role != "architect":
        return None, "sprint-binding-required"
    return declared, None


def sprint_binding_for_reopen(
    request: SprintOpenBindingRequest,
) -> tuple[SprintRoleBinding | None, SprintBindingRefusal | None]:
    """Validate shared opener/reopen provenance before any host side effect."""

    if request.role not in NAMED_SPRINT_ROLES:
        return None, None
    supplied = (
        SprintRoleBinding(repo=request.spawn_repo, sprint=request.spawn_sprint)
        if request.spawn_repo is not None and request.spawn_sprint is not None
        else None
    )
    if (request.spawn_repo is None) != (request.spawn_sprint is None):
        return None, "sprint-binding-required"
    declared = sprint_binding_from_leaf(request.leaf_key) or sprint_binding_from_leaf(
        request.replacement_for_leaf
    )
    if supplied is not None:
        return _validate_supplied_binding(supplied, declared, request.existing)
    return _reopen_inherited_binding(request, declared)


def _validate_supplied_binding(
    supplied: SprintRoleBinding,
    declared: SprintRoleBinding | None,
    existing: TerminalCatalogEntry | None,
) -> tuple[SprintRoleBinding | None, SprintBindingRefusal | None]:
    if declared is not None and declared != supplied:
        return None, "sprint-binding-conflict"
    existing_binding = _parent_binding(existing)
    if existing_binding is not None and existing_binding != supplied:
        return None, "sprint-binding-conflict"
    return supplied, None


def _reopen_inherited_binding(
    request: SprintOpenBindingRequest,
    declared: SprintRoleBinding | None,
) -> tuple[SprintRoleBinding | None, SprintBindingRefusal | None]:
    inherited = _parent_binding(request.existing)
    if inherited is not None:
        if declared is not None and declared != inherited:
            return None, "sprint-binding-conflict"
        return inherited, None
    return sprint_binding_for_spawn(
        request.role,
        leaf_key=request.leaf_key,
        replacement_for_leaf=request.replacement_for_leaf,
        parent=None,
        parent_session_id=request.spawned_by_session,
    )


def _parent_binding(parent: TerminalCatalogEntry | None) -> SprintRoleBinding | None:
    if parent is None or parent.spawn_repo is None or parent.spawn_sprint is None:
        return None
    return SprintRoleBinding(repo=parent.spawn_repo, sprint=parent.spawn_sprint)
