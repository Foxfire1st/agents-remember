"""Live-leaf collision policy for integration-topology publication."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import MasterExecutionNature, TaskDocument
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.worktrees.integration.integration_branch_types import (
    IntegrationSurface,
    IntegrationSurfaceKind,
    _BranchScope,
    _RepositorySide,
)
from agents_remember.worktrees.scheduling_mode import (
    commanded_sprint_masters,
    effective_execution_nature,
)
from agents_remember.worktrees.task_resolver import iter_leaf_enclosure_contracts
from agents_remember.worktrees.worktree_contract import WorktreeContract

_PublicationMasterAuthority = dict[
    TaskDocumentRef,
    tuple[ResolvedTaskDocument, TaskDocumentRef | None],
]
_SurfaceKey = tuple[str, Path, str]


@dataclass(frozen=True)
class TopologyCollisionServices:
    """Stable surface primitives owned by the integration-branch facade."""

    repository_masters: Callable[
        [TaskDocumentTopology, str, Mapping[TaskDocumentRef, TaskDocument] | None],
        tuple[ResolvedTaskDocument, ...],
    ]
    repository_sides: Callable[[WorktreeContract], tuple[_RepositorySide, ...]]
    load_contract: Callable[[Path], WorktreeContract]
    branch_key: Callable[[_RepositorySide, str], _SurfaceKey]


@dataclass(frozen=True)
class TopologyCollisionRequest:
    """One candidate topology publication and its current authority boundary."""

    scope: _BranchScope
    current: tuple[IntegrationSurface, ...]
    candidate: tuple[IntegrationSurface, ...]
    overrides: Mapping[TaskDocumentRef, TaskDocument]
    repaired_owners: set[TaskDocumentRef] | None = None


@dataclass(frozen=True)
class _CollisionContext:
    topology: TaskDocumentTopology
    current: tuple[IntegrationSurface, ...]
    candidate: tuple[IntegrationSurface, ...]
    overrides: Mapping[TaskDocumentRef, TaskDocument]
    candidate_authority: _PublicationMasterAuthority
    current_authority: _PublicationMasterAuthority
    current_keys: set[_SurfaceKey]
    candidate_keys: set[_SurfaceKey]
    services: TopologyCollisionServices


def require_no_live_leaf_collisions(
    request: TopologyCollisionRequest,
    services: TopologyCollisionServices,
) -> None:
    """Refuse a topology edit that strands or re-owns a live leaf."""

    context = _collision_context(request, services)
    enclosure_root = request.scope.coordination_root / "tasks" / request.scope.repo_name
    for path in iter_leaf_enclosure_contracts(enclosure_root):
        _require_no_live_leaf_collision(context, services.load_contract(path))


def _collision_context(
    request: TopologyCollisionRequest,
    services: TopologyCollisionServices,
) -> _CollisionContext:
    topology = TaskDocumentTopology(request.scope.coordination_root)
    candidate_authority = _publication_master_authority(
        topology,
        request.scope.repo_name,
        request.overrides,
        services,
    )
    current_authority = _current_publication_master_authority(
        topology,
        request,
        candidate_authority,
        services,
    )
    return _CollisionContext(
        topology=topology,
        current=request.current,
        candidate=request.candidate,
        overrides=request.overrides,
        candidate_authority=candidate_authority,
        current_authority=current_authority,
        current_keys={_surface_key(surface) for surface in request.current},
        candidate_keys={_surface_key(surface) for surface in request.candidate},
        services=services,
    )


def _current_publication_master_authority(
    topology: TaskDocumentTopology,
    request: TopologyCollisionRequest,
    candidate_authority: _PublicationMasterAuthority,
    services: TopologyCollisionServices,
) -> _PublicationMasterAuthority:
    if not request.current:
        return {}
    if request.repaired_owners:
        return _authority_without_repaired_owners(
            candidate_authority,
            request.repaired_owners,
        )
    return _publication_master_authority(
        topology,
        request.scope.repo_name,
        None,
        services,
    )


def _authority_without_repaired_owners(
    authority: _PublicationMasterAuthority,
    repaired_owners: set[TaskDocumentRef],
) -> _PublicationMasterAuthority:
    return {ref: value for ref, value in authority.items() if ref not in repaired_owners}


def _publication_master_authority(
    topology: TaskDocumentTopology,
    repo_name: str,
    overrides: Mapping[TaskDocumentRef, TaskDocument] | None,
    services: TopologyCollisionServices,
) -> _PublicationMasterAuthority:
    masters = services.repository_masters(topology, repo_name, overrides)
    authority: _PublicationMasterAuthority = {}
    commanded: set[TaskDocumentRef] = set()
    for sprint in masters:
        if not sprint.document.orchestrates:
            continue
        _record_commanded_authority(topology, sprint, overrides, authority, commanded)
    _record_standalone_atomic_authority(masters, authority, commanded)
    return authority


def _record_commanded_authority(
    topology: TaskDocumentTopology,
    sprint: ResolvedTaskDocument,
    overrides: Mapping[TaskDocumentRef, TaskDocument] | None,
    authority: _PublicationMasterAuthority,
    commanded: set[TaskDocumentRef],
) -> None:
    for master in commanded_sprint_masters(topology, sprint, overrides=overrides):
        existing = authority.get(master.ref)
        if existing is not None and existing[1] != sprint.ref:
            raise RuntimeError(
                f"task topology publication gives {master.ref.key} multiple sprint owners"
            )
        authority[master.ref] = (master, sprint.ref)
        commanded.add(master.ref)


def _record_standalone_atomic_authority(
    masters: tuple[ResolvedTaskDocument, ...],
    authority: _PublicationMasterAuthority,
    commanded: set[TaskDocumentRef],
) -> None:
    for master in masters:
        if _is_standalone_atomic_master(master, commanded):
            authority[master.ref] = (master, None)


def _is_standalone_atomic_master(
    master: ResolvedTaskDocument,
    commanded: set[TaskDocumentRef],
) -> bool:
    observed = (
        master.ref in commanded,
        bool(master.document.orchestrates),
        effective_execution_nature(master.document, None),
    )
    return observed == (False, False, "atomic")


def _require_no_live_leaf_collision(
    context: _CollisionContext,
    contract: WorktreeContract,
) -> None:
    if contract.kind != "leaf":
        return
    master_ref = context.topology.canonical_ref(
        contract.repo_name,
        contract.task_root / "task.json",
    )
    authority = context.candidate_authority.get(master_ref)
    current_owner = context.current_authority.get(master_ref)
    if _completed_leaf_is_released(contract, authority or current_owner):
        return
    master, sprint_ref = _required_live_leaf_owner(contract, authority)
    _require_live_leaf_task_identity(context.topology, contract, master, context.overrides)
    _require_stable_live_leaf_sprint(context.current, current_owner, sprint_ref, contract)
    _require_live_leaf_workbench_unprotected(context, contract)
    nature = _live_leaf_execution_nature(context.topology, master, sprint_ref)
    _require_live_leaf_source_authority(context, contract, master, sprint_ref, nature)
    _require_preserved_live_leaf_sources(context, contract)


def _completed_leaf_is_released(
    contract: WorktreeContract,
    owner: tuple[ResolvedTaskDocument, TaskDocumentRef | None] | None,
) -> bool:
    if contract.cleanup != "completed":
        return False
    return owner is None or owner[0].document.executionNature != "atomic"


def _required_live_leaf_owner(
    contract: WorktreeContract,
    authority: tuple[ResolvedTaskDocument, TaskDocumentRef | None] | None,
) -> tuple[ResolvedTaskDocument, TaskDocumentRef | None]:
    if authority is None:
        raise RuntimeError(
            "task topology publication refused: live leaf would lose its exact owning "
            f"master/sprint authority: {contract.contract_path}"
        )
    return authority


def _require_stable_live_leaf_sprint(
    current: tuple[IntegrationSurface, ...],
    current_owner: tuple[ResolvedTaskDocument, TaskDocumentRef | None] | None,
    sprint_ref: TaskDocumentRef | None,
    contract: WorktreeContract,
) -> None:
    if not current:
        return
    if current_owner is None or current_owner[1] != sprint_ref:
        raise RuntimeError(
            "task topology publication refused: live leaf owning sprint would change for "
            f"{contract.contract_path}"
        )


def _live_leaf_execution_nature(
    topology: TaskDocumentTopology,
    master: ResolvedTaskDocument,
    sprint_ref: TaskDocumentRef | None,
) -> MasterExecutionNature:
    sprint = topology.resolve(sprint_ref).document if sprint_ref is not None else None
    return effective_execution_nature(master.document, sprint)


def _require_live_leaf_workbench_unprotected(
    context: _CollisionContext,
    contract: WorktreeContract,
) -> None:
    for side in context.services.repository_sides(contract):
        if context.services.branch_key(side, side.work_branch) in context.candidate_keys:
            raise RuntimeError(
                "task topology publication refused: proposed protected branch already "
                f"belongs to live leaf workbench {contract.contract_path}"
            )


def _require_live_leaf_task_identity(
    topology: TaskDocumentTopology,
    contract: WorktreeContract,
    master: ResolvedTaskDocument,
    overrides: Mapping[TaskDocumentRef, TaskDocument],
) -> None:
    row_file = _required_live_leaf_row_file(contract, master)
    leaf_ref = _live_leaf_ref(topology, contract, master, row_file)
    document = _resolved_live_leaf_document(topology, leaf_ref, overrides, contract)
    if (document.id, document.kind == "master") != (contract.leaf_id, False):
        raise RuntimeError(
            "task topology publication refused: live leaf task identity changed for "
            f"{contract.contract_path}"
        )


def _required_live_leaf_row_file(
    contract: WorktreeContract,
    master: ResolvedTaskDocument,
) -> str:
    rows = [row for row in master.document.subTasks if row.number == contract.leaf_id]
    if len(rows) != 1 or not rows[0].file:
        raise RuntimeError(
            "task topology publication refused: live leaf is not declared by one exact "
            f"owning-master row: {contract.contract_path}"
        )
    return rows[0].file


def _resolved_live_leaf_document(
    topology: TaskDocumentTopology,
    leaf_ref: TaskDocumentRef,
    overrides: Mapping[TaskDocumentRef, TaskDocument],
    contract: WorktreeContract,
) -> TaskDocument:
    try:
        return topology.resolve(leaf_ref, overrides).document
    except TaskDocumentRefError as exc:
        raise RuntimeError(
            "task topology publication refused: live leaf task document authority is "
            f"invalid for {contract.contract_path}: {exc}"
        ) from exc


def _live_leaf_ref(
    topology: TaskDocumentTopology,
    contract: WorktreeContract,
    master: ResolvedTaskDocument,
    row_file: str,
) -> TaskDocumentRef:
    leaf_path = (master.path.parent / row_file).with_suffix(".json")
    task_root = (topology.coordination_root / "tasks" / contract.repo_name).resolve()
    resolved = leaf_path.resolve(strict=False)
    if not resolved.is_relative_to(task_root):
        raise RuntimeError(
            "task topology publication refused: live leaf task document escapes its "
            f"repository task tree: {leaf_path}"
        )
    if resolved.parent != master.path.parent.resolve(strict=False):
        raise RuntimeError(
            "task topology publication refused: live leaf task document leaves its exact "
            f"owning master task root: {leaf_path}"
        )
    return TaskDocumentRef(
        repository=contract.repo_name,
        path=resolved.relative_to(task_root).as_posix(),
    )


def _require_live_leaf_source_authority(
    context: _CollisionContext,
    contract: WorktreeContract,
    master: ResolvedTaskDocument,
    sprint_ref: TaskDocumentRef | None,
    nature: MasterExecutionNature,
) -> None:
    expected_kind: IntegrationSurfaceKind = (
        "sprint-super" if nature == "organizational" else "atomic-integration"
    )
    expected_owner = (
        sprint_ref.key
        if sprint_ref is not None and expected_kind == "sprint-super"
        else master.ref.key
    )
    for side in context.services.repository_sides(contract):
        _require_live_leaf_side_source(
            context,
            contract,
            side,
            expected_kind,
            expected_owner,
        )


def _require_live_leaf_side_source(
    context: _CollisionContext,
    contract: WorktreeContract,
    side: _RepositorySide,
    expected_kind: IntegrationSurfaceKind,
    expected_owner: str,
) -> None:
    source = context.services.branch_key(side, side.source_branch)
    matches = sum(
        _surface_matches_owner(surface, source, expected_kind, expected_owner)
        for surface in context.candidate
    )
    if matches != 1:
        raise RuntimeError(
            "task topology publication refused: live leaf source no longer matches its "
            f"exact {expected_kind} owner: {contract.contract_path}"
        )


def _surface_matches_owner(
    surface: IntegrationSurface,
    source: _SurfaceKey,
    expected_kind: IntegrationSurfaceKind,
    expected_owner: str,
) -> bool:
    return (_surface_key(surface), surface.kind, surface.owner) == (
        source,
        expected_kind,
        expected_owner,
    )


def _require_preserved_live_leaf_sources(
    context: _CollisionContext,
    contract: WorktreeContract,
) -> None:
    for side in context.services.repository_sides(contract):
        source = context.services.branch_key(side, side.source_branch)
        if source in context.current_keys and source not in context.candidate_keys:
            raise RuntimeError(
                "task topology publication refused: live leaf source authority would be "
                f"removed for {contract.contract_path}"
            )


def _surface_key(surface: IntegrationSurface) -> _SurfaceKey:
    return surface.side, surface.repository, surface.branch
