"""Task-derived ownership for integration refs that are never ordinary workbenches."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import MasterExecutionNature, TaskDocument
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.worktrees.atomic_series_seal import require_series_accepting_leaves
from agents_remember.worktrees.integration.integration_branch_repository import (
    branch_worktree_owners,
    canonical_local_branch,
    memory_repository_default_branch,
    repository_default_branch,
)
from agents_remember.worktrees.integration.integration_branch_types import (
    IntegrationSurface,
    IntegrationSurfaceKind,
    IntegrationSurfaceSide,
    IntegrationTarget,
    IntegrationTargetKind,
    ProposedWorkBranches,
    RepositoryCheckoutRequest,
    _BranchScope,
    _MasterAuthority,
    _RepositorySide,
)
from agents_remember.worktrees.modules.git import (
    branch_exists,
    current_branch,
    repository_identity,
)
from agents_remember.worktrees.scheduling_mode import (
    TERMINAL_SERIES_CLEANUP,
    commanded_sprint_masters,
    effective_execution_nature,
)
from agents_remember.worktrees.task_resolver import (
    iter_leaf_enclosure_contracts,
    series_contract_path,
    slugify,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


def integration_surfaces(contract: WorktreeContract) -> tuple[IntegrationSurface, ...]:
    return _integration_surfaces(_scope(contract))


def integration_targets(contract: WorktreeContract) -> tuple[IntegrationTarget, ...]:
    sides = _repository_sides(contract)
    authority = _master_authority(
        _BranchScope(
            coordination_root=contract.coordination_root,
            repo_name=contract.repo_name,
            task_root=contract.task_root,
            sides=sides,
        )
    )
    default_by_side = {side.side: _side_default_branch(side) for side in sides}
    if contract.kind == "leaf":
        kind, branch, owner = _leaf_target(contract, authority)
    elif contract.kind == "series":
        if authority.sprint_ref is None or authority.sprint_branch is None:
            default = default_by_side["code"]
            if (
                canonical_local_branch(contract.code_repo_path, contract.code_source_branch)
                != default
            ):
                raise RuntimeError(
                    "standalone atomic series source must be the configured repository-default "
                    "branch; only the PR landing plane may move that root"
                )
            raise RuntimeError(
                "generic integration refuses a standalone atomic series -> repository-default "
                "landing; publish it through the PR landing plane"
            )
        else:
            _require_atomic_master(authority)
            branch = authority.sprint_branch
            kind = "sprint-super"
            owner = _ref_key(authority.sprint_ref)
    else:
        raise RuntimeError(
            f"integration target requires a leaf or series contract, got {contract.kind!r}"
        )

    targets: list[IntegrationTarget] = []
    for side in sides:
        source = canonical_local_branch(side.repository, side.source_branch)
        expected = canonical_local_branch(side.repository, branch)
        default = default_by_side[side.side]
        if source != expected:
            raise RuntimeError(
                f"{side.side} integration source {source!r} does not match task-derived "
                f"target {expected!r}"
            )
        if expected == default:
            raise RuntimeError(
                f"generic integration refuses repository-default {side.side} branch "
                f"{expected!r}; the repository landing plane must move the PR-gated root"
            )
        surface = _surface(side, kind=kind, branch=expected, owner=owner)
        targets.append(
            IntegrationTarget(
                side=surface.side,
                kind=kind,
                repository=surface.repository,
                branch=surface.branch,
                owner=surface.owner,
            )
        )
    return tuple(targets)


def require_proposed_work_branches(proposal: ProposedWorkBranches) -> None:
    """Refuse protected work-branch requests before start can mutate Git."""

    sides = [
        _RepositorySide(
            side="code",
            repository=proposal.code_repository,
            worktree=proposal.code_repository,
            source_branch="",
            work_branch=proposal.code_work_branch,
        )
    ]
    if proposal.memory_repository is not None:
        if _repository_identity(proposal.memory_repository, "memory") == _repository_identity(
            proposal.code_repository, "code"
        ):
            raise RuntimeError("external memory must not share the code repository Git common-dir")
        sides.append(
            _RepositorySide(
                side="memory",
                repository=proposal.memory_repository,
                worktree=proposal.memory_repository,
                source_branch="",
                work_branch=proposal.memory_work_branch,
            )
        )
    scope = _BranchScope(
        proposal.coordination_root,
        proposal.repo_name,
        proposal.task_root,
        tuple(sides),
    )
    surfaces = _integration_surfaces(scope)
    for side in scope.sides:
        _require_unprotected_branch(side, surfaces, operation="worktree_start")


def require_ordinary_worktree(contract: WorktreeContract, *, operation: str) -> None:
    """Refuse a leaf whose proposed or checked-out work branch is protected."""

    if contract.kind != "leaf":
        return
    surfaces = integration_surfaces(contract)
    for side in _repository_sides(contract):
        _require_unprotected_branch(side, surfaces, operation=operation)
        if not side.worktree.exists():
            continue
        expected_identity = _repository_identity(side.repository, side.side)
        actual_identity = repository_identity(side.worktree)
        if actual_identity != expected_identity:
            raise RuntimeError(
                f"{operation} refused: {side.side} worktree does not belong to its recorded "
                "repository"
            )
        checked_out = canonical_local_branch(side.repository, current_branch(side.worktree))
        expected = canonical_local_branch(side.repository, side.work_branch)
        if checked_out != expected:
            raise RuntimeError(
                f"{operation} refused: {side.side} worktree has {checked_out!r} checked out, "
                f"expected {expected!r}"
            )
        _require_unprotected_name(side, checked_out, surfaces, operation=operation)


def require_terminal_worktree(contract: WorktreeContract, *, operation: str) -> None:
    """Refuse terminal deletion when a contract names another owner's protected ref."""

    if contract.kind == "leaf":
        require_ordinary_worktree(contract, operation=operation)
        return
    if contract.kind != "series":
        raise RuntimeError(f"{operation} refused: unsupported contract kind {contract.kind!r}")
    authority = require_series_contract_authority(contract, operation=operation)
    if operation == "worktree_cleanup":
        master = authority.topology.resolve(authority.master_ref)
        if master.document.status != "Completed":
            raise RuntimeError(
                "worktree_cleanup refused: atomic master task must be Completed before its "
                "integration branch can retire"
            )
    expected = f"ar/{slugify(contract.task_root.name)}"
    for side in _repository_sides(contract):
        spelled = side.work_branch.strip().removeprefix("refs/heads/")
        if spelled != expected:
            raise RuntimeError(
                f"{operation} refused: series {side.side} branch must use exact task-owned "
                f"spelling {expected!r}; aliases cannot be retired"
            )
        actual = canonical_local_branch(side.repository, side.work_branch)
        if actual != canonical_local_branch(side.repository, expected):
            raise RuntimeError(
                f"{operation} refused: series {side.side} branch {actual!r} is not its "
                f"task-owned atomic branch {expected!r}"
            )
        default = _side_default_branch(side)
        parent = (
            canonical_local_branch(side.repository, authority.sprint_branch)
            if authority.sprint_branch is not None
            else None
        )
        if actual in (default, parent):
            raise RuntimeError(f"{operation} refused: protected parent ref cannot be retired")


def require_series_contract_authority(
    contract: WorktreeContract,
    *,
    operation: str,
) -> _MasterAuthority:
    """Bind an atomic series contract to its exact task-derived repositories and refs."""

    if contract.kind != "series":
        raise RuntimeError(f"{operation} requires an atomic series contract")
    scope = _scope(contract)
    authority = _master_authority(scope)
    _require_atomic_master(authority)
    _require_series_identity(
        scope,
        contract,
        contract.task_root,
        authority.sprint_branch,
    )
    if authority.sprint_branch is None:
        for side in _repository_sides(contract):
            source = canonical_local_branch(side.repository, side.source_branch)
            default = _side_default_branch(side)
            if source != default:
                raise RuntimeError(
                    f"{operation} refused: standalone atomic {side.side} source must be "
                    f"repository-default {default!r}, got {source!r}"
                )
    return authority


def require_parent_series_accepting_leaves(
    contract: WorktreeContract,
    *,
    operation: str,
) -> WorktreeContract | None:
    """Return an atomic leaf's open parent, or None for organizational direct-super work."""

    authority = _master_authority(_scope(contract))
    if authority.sprint_ref is not None and authority.execution_nature == "organizational":
        return None
    _require_atomic_master(authority)
    parent_path = contract.parent_contract_path or series_contract_path(contract.task_root)
    if not parent_path.is_file():
        raise RuntimeError(f"{operation} requires its exact parent series contract")
    series = _load_series(parent_path)
    _require_series_identity(
        _scope(contract),
        series,
        contract.task_root,
        authority.sprint_branch,
    )
    require_series_accepting_leaves(series, operation=operation)
    return series


def require_sync_worktree(contract: WorktreeContract) -> None:
    """Sync is ordinary work-branch mutation and never operates on a series ref."""

    if contract.kind != "leaf":
        raise RuntimeError(
            "worktree_sync refused: a series integration branch is not an ordinary workbench; "
            "move it only through a plane-owned integration transaction"
        )
    require_ordinary_worktree(contract, operation="worktree_sync")


def require_source_branch_write(
    contract: WorktreeContract, *, side_name: IntegrationSurfaceSide, operation: str
) -> None:
    """Refuse ordinary start/recovery code that would write a protected source ref."""

    sides = _repository_sides(contract)
    side = next(item for item in sides if item.side == side_name)
    surfaces = integration_surfaces(contract)
    source = canonical_local_branch(side.repository, side.source_branch)
    for surface in surfaces:
        if (
            surface.side == side.side
            and surface.repository == _repository_identity(side.repository, side.side)
            and surface.branch == source
        ):
            raise RuntimeError(
                f"{operation} refused: protected {side.side} source {source!r} moves only "
                "through its task-owned landing plane"
            )


def require_ordinary_repository_checkout(request: RepositoryCheckoutRequest) -> None:
    """Refuse a repo-level writer when its selected checkout owns a protected ref.

    Carryover is intentionally contract-independent at planning time. Its apply path still
    needs the same repository-global branch census as task work, so it supplies the configured
    repositories and the exact checkout it proposes to mutate here.
    """

    sides = [
        _RepositorySide(
            "code",
            request.code_repository,
            request.code_repository,
            "",
            "",
        )
    ]
    if request.memory_repository is not None:
        if _repository_identity(request.code_repository, "code") == _repository_identity(
            request.memory_repository, "memory"
        ):
            raise RuntimeError(
                f"{request.operation} refused: external memory must not share the code "
                "repository Git common-dir"
            )
        sides.append(
            _RepositorySide(
                "memory",
                request.memory_repository,
                request.checkout if request.side_name == "memory" else request.memory_repository,
                "",
                "",
            )
        )
    selected = next((side for side in sides if side.side == request.side_name), None)
    if selected is None:
        raise RuntimeError(
            f"{request.operation} cannot resolve the configured {request.side_name} repository"
        )
    expected_identity = _repository_identity(selected.repository, selected.side)
    if repository_identity(request.checkout) != expected_identity:
        raise RuntimeError(
            f"{request.operation} refused: selected {request.side_name} checkout belongs to "
            "another repository"
        )
    scope = _BranchScope(
        request.coordination_root,
        request.repo_name,
        request.coordination_root / "tasks" / request.repo_name,
        tuple(sides),
    )
    branch = canonical_local_branch(selected.repository, current_branch(request.checkout))
    _require_unprotected_name(
        selected,
        branch,
        _integration_surfaces(scope),
        operation=request.operation,
    )


def require_topology_publication_authority(
    coordination_root: Path,
    repo_name: str,
    code_repository: Path,
    memory_repository: Path | None,
    overrides: Mapping[TaskDocumentRef, TaskDocument],
) -> None:
    """Refuse a task edit that would convert a live leaf workbench into authority."""

    scope = _publication_scope(
        coordination_root,
        repo_name,
        code_repository,
        memory_repository,
    )
    candidate = _integration_surfaces(scope, overrides=overrides)
    repaired_owners: set[TaskDocumentRef] = set()
    try:
        current = _integration_surfaces(scope)
    except RuntimeError as error:
        cause = error.__cause__
        if not (
            isinstance(cause, TaskDocumentRefError)
            and cause.status == "task-execution-graph-membership-invalid"
        ):
            raise
        repaired_owners = {
            ref
            for ref in overrides
            if not (coordination_root / "tasks" / ref.repository / ref.path).is_file()
        }
        if not repaired_owners or len(repaired_owners) != len(overrides):
            raise
        repaired_owner_keys = {ref.key for ref in repaired_owners}
        current = tuple(
            surface for surface in candidate if surface.owner not in repaired_owner_keys
        )
    _require_stable_surface_owners(current, candidate)
    _require_no_live_leaf_collisions(
        scope,
        current,
        candidate,
        overrides,
        repaired_owners=repaired_owners,
    )
    _require_new_surface_availability(scope, current, candidate, allow_existing_super=False)


def require_topology_migration_authority(
    coordination_root: Path,
    repo_name: str,
    code_repository: Path,
    memory_repository: Path | None,
    overrides: Mapping[TaskDocumentRef, TaskDocument],
) -> None:
    """Validate the explicit legacy cutover without inventing pre-migration authority."""

    scope = _publication_scope(
        coordination_root,
        repo_name,
        code_repository,
        memory_repository,
    )
    candidate = _integration_surfaces(scope, overrides=overrides)
    _require_no_live_leaf_collisions(scope, (), candidate, overrides)
    _require_new_surface_availability(scope, (), candidate, allow_existing_super=True)


def _publication_scope(
    coordination_root: Path,
    repo_name: str,
    code_repository: Path,
    memory_repository: Path | None,
) -> _BranchScope:
    sides = [_RepositorySide("code", code_repository, code_repository, "", "")]
    if memory_repository is not None:
        code_identity = _repository_identity(code_repository, "code")
        memory_identity = repository_identity(memory_repository)
        if memory_identity is not None and memory_identity != code_identity:
            sides.append(_RepositorySide("memory", memory_repository, memory_repository, "", ""))
    return _BranchScope(
        coordination_root,
        repo_name,
        coordination_root / "tasks" / repo_name,
        tuple(sides),
    )


def _require_new_surface_availability(
    scope: _BranchScope,
    current: tuple[IntegrationSurface, ...],
    candidate: tuple[IntegrationSurface, ...],
    *,
    allow_existing_super: bool,
) -> None:
    current_keys = {_surface_key(surface) for surface in current}
    for surface in candidate:
        if surface.kind == "repository-default" or _surface_key(surface) in current_keys:
            continue
        if surface.kind == "sprint-super" and allow_existing_super:
            continue
        if (
            surface.kind == "atomic-integration"
            and branch_exists(surface.repository, surface.branch)
            and not _atomic_surface_has_series(scope, surface)
        ):
            raise RuntimeError(
                "task topology publication refused: proposed atomic branch already exists "
                f"without its exact task-owned series contract: {surface.branch!r}"
            )
        owners = branch_worktree_owners(surface.repository, surface.branch)
        if owners and not _atomic_surface_has_series(scope, surface):
            raise RuntimeError(
                "task topology publication refused: proposed protected "
                f"{surface.side} branch {surface.branch!r} is already checked out at "
                f"{[path.as_posix() for path in owners]!r}"
            )


def _atomic_surface_has_series(scope: _BranchScope, surface: IntegrationSurface) -> bool:
    if surface.kind != "atomic-integration":
        return False
    prefix = f"{scope.repo_name}/"
    if not surface.owner.startswith(prefix):
        return False
    master_path = (
        scope.coordination_root / "tasks" / scope.repo_name / surface.owner.removeprefix(prefix)
    )
    path = series_contract_path(master_path.parent)
    if not path.is_file():
        return False
    series = _load_series(path)
    side = next(item for item in _repository_sides(series) if item.side == surface.side)
    return _branch_key(side, side.work_branch) == _surface_key(surface)


def _scope(contract: WorktreeContract) -> _BranchScope:
    return _BranchScope(
        coordination_root=contract.coordination_root,
        repo_name=contract.repo_name,
        task_root=contract.task_root,
        sides=_repository_sides(contract),
    )


def _integration_surfaces(
    scope: _BranchScope,
    *,
    overrides: Mapping[TaskDocumentRef, TaskDocument] | None = None,
) -> tuple[IntegrationSurface, ...]:
    surfaces = [
        _surface(
            side,
            kind="repository-default",
            branch=_side_default_branch(side),
            owner="repository default branch",
        )
        for side in scope.sides
    ]
    topology = TaskDocumentTopology(scope.coordination_root)
    try:
        masters = _repository_masters_with_overrides(topology, scope.repo_name, overrides)
    except TaskDocumentRefError as exc:
        raise RuntimeError(
            f"cannot resolve integration-branch authority: {exc.status}: {exc}"
        ) from exc
    commanded_refs: set[TaskDocumentRef] = set()
    for sprint in masters:
        if not sprint.document.orchestrates:
            continue
        branch = sprint.document.integrationBranch
        if not branch:
            raise RuntimeError(
                f"integration-branch authority requires {sprint.ref.key} to declare "
                "integrationBranch"
            )
        try:
            commanded = commanded_sprint_masters(topology, sprint, overrides=overrides)
        except TaskDocumentRefError as exc:
            raise RuntimeError(
                f"cannot resolve integration-branch authority: {exc.status}: {exc}"
            ) from exc
        commanded_refs.update(master.ref for master in commanded)
        surfaces.extend(
            _surface(
                side,
                kind="sprint-super",
                branch=branch,
                owner=sprint.ref.key,
            )
            for side in scope.sides
        )
        for master in commanded:
            surfaces.extend(
                _master_integration_surfaces(
                    scope,
                    master,
                    branch,
                    nature=effective_execution_nature(master.document, sprint.document),
                )
            )
    for master in masters:
        if (
            master.ref in commanded_refs
            or master.document.orchestrates
            or effective_execution_nature(master.document, None) != "atomic"
        ):
            continue
        surfaces.extend(
            _master_integration_surfaces(
                scope,
                master,
                None,
                nature=effective_execution_nature(master.document, None),
            )
        )
    return _deduplicated(surfaces)


def _repository_masters_with_overrides(
    topology: TaskDocumentTopology,
    repo_name: str,
    overrides: Mapping[TaskDocumentRef, TaskDocument] | None,
) -> tuple[ResolvedTaskDocument, ...]:
    masters = {master.ref: master for master in topology.repository_masters(repo_name)}
    root = topology.coordination_root / "tasks" / repo_name
    for ref, document in (overrides or {}).items():
        if ref.repository != repo_name:
            continue
        if document.kind != "master":
            masters.pop(ref, None)
            continue
        masters[ref] = ResolvedTaskDocument(ref, root / ref.path, document)
    return tuple(masters[ref] for ref in sorted(masters, key=lambda item: item.key))


def _require_stable_surface_owners(
    current: tuple[IntegrationSurface, ...],
    candidate: tuple[IntegrationSurface, ...],
) -> None:
    current_by_key = {_surface_key(surface): surface for surface in current}
    for surface in candidate:
        existing = current_by_key.get(_surface_key(surface))
        if existing is not None and (
            existing.kind != surface.kind or existing.owner != surface.owner
        ):
            raise RuntimeError(
                "task topology publication refused: protected branch ownership would change "
                f"from {existing.kind} owned by {existing.owner} to {surface.kind} owned by "
                f"{surface.owner}"
            )


def _current_publication_master_authority(
    topology: TaskDocumentTopology,
    repo_name: str,
    current: tuple[IntegrationSurface, ...],
    candidate_authority: dict[
        TaskDocumentRef,
        tuple[ResolvedTaskDocument, TaskDocumentRef | None],
    ],
    repaired_owners: set[TaskDocumentRef] | None,
) -> dict[TaskDocumentRef, tuple[ResolvedTaskDocument, TaskDocumentRef | None]]:
    if not current:
        return {}
    if repaired_owners:
        return {
            ref: authority
            for ref, authority in candidate_authority.items()
            if ref not in repaired_owners
        }
    return _publication_master_authority(topology, repo_name, None)


def _require_no_live_leaf_collisions(
    scope: _BranchScope,
    current: tuple[IntegrationSurface, ...],
    candidate: tuple[IntegrationSurface, ...],
    overrides: Mapping[TaskDocumentRef, TaskDocument],
    *,
    repaired_owners: set[TaskDocumentRef] | None = None,
) -> None:
    topology = TaskDocumentTopology(scope.coordination_root)
    candidate_authority = _publication_master_authority(topology, scope.repo_name, overrides)
    current_authority = _current_publication_master_authority(
        topology,
        scope.repo_name,
        current,
        candidate_authority,
        repaired_owners,
    )
    current_keys = {_surface_key(surface) for surface in current}
    candidate_keys = {_surface_key(surface) for surface in candidate}
    for path in iter_leaf_enclosure_contracts(scope.coordination_root / "tasks" / scope.repo_name):
        contract = _load_series(path)
        if contract.kind != "leaf":
            continue
        master_ref = topology.canonical_ref(
            scope.repo_name,
            contract.task_root / "task.json",
        )
        authority = candidate_authority.get(master_ref)
        current_owner = current_authority.get(master_ref)
        atomic_owner = authority or current_owner
        if (
            contract.cleanup == "completed"
            and atomic_owner is not None
            and atomic_owner[0].document.executionNature != "atomic"
        ):
            continue
        if contract.cleanup == "completed" and atomic_owner is None:
            continue
        if authority is None:
            raise RuntimeError(
                "task topology publication refused: live leaf would lose its exact owning "
                f"master/sprint authority: {contract.contract_path}"
            )
        master, sprint_ref = authority
        _require_live_leaf_task_identity(
            topology,
            contract,
            master,
            sprint_ref,
            overrides,
        )
        if current and (current_owner is None or current_owner[1] != sprint_ref):
            raise RuntimeError(
                "task topology publication refused: live leaf owning sprint would change for "
                f"{contract.contract_path}"
            )
        _require_live_leaf_workbench_unprotected(contract, candidate_keys)
        # The effective nature (L13-R1): under the atomic-sequential default even an
        # organizational-declared master lands through its atomic series lane.
        nature = effective_execution_nature(
            master.document,
            topology.resolve(sprint_ref).document if sprint_ref is not None else None,
        )
        _require_live_leaf_source_authority(contract, master, sprint_ref, candidate, nature)
        for side in _repository_sides(contract):
            source_key = _branch_key(side, side.source_branch)
            if source_key in current_keys and source_key not in candidate_keys:
                raise RuntimeError(
                    "task topology publication refused: live leaf source authority would be "
                    f"removed for {contract.contract_path}"
                )


def _require_live_leaf_workbench_unprotected(
    contract: WorktreeContract,
    candidate_keys: set[tuple[str, Path, str]],
) -> None:
    for side in _repository_sides(contract):
        if _branch_key(side, side.work_branch) in candidate_keys:
            raise RuntimeError(
                "task topology publication refused: proposed protected branch already "
                f"belongs to live leaf workbench {contract.contract_path}"
            )


def _publication_master_authority(
    topology: TaskDocumentTopology,
    repo_name: str,
    overrides: Mapping[TaskDocumentRef, TaskDocument] | None,
) -> dict[TaskDocumentRef, tuple[ResolvedTaskDocument, TaskDocumentRef | None]]:
    masters = _repository_masters_with_overrides(topology, repo_name, overrides)
    authority: dict[TaskDocumentRef, tuple[ResolvedTaskDocument, TaskDocumentRef | None]] = {}
    commanded: set[TaskDocumentRef] = set()
    for sprint in masters:
        if not sprint.document.orchestrates:
            continue
        for master in commanded_sprint_masters(topology, sprint, overrides=overrides):
            existing = authority.get(master.ref)
            if existing is not None and existing[1] != sprint.ref:
                raise RuntimeError(
                    f"task topology publication gives {master.ref.key} multiple sprint owners"
                )
            authority[master.ref] = (master, sprint.ref)
            commanded.add(master.ref)
    for master in masters:
        if master.ref in commanded or master.document.orchestrates:
            continue
        if effective_execution_nature(master.document, None) == "atomic":
            authority[master.ref] = (master, None)
    return authority


def _require_live_leaf_task_identity(
    topology: TaskDocumentTopology,
    contract: WorktreeContract,
    master: ResolvedTaskDocument,
    sprint_ref: TaskDocumentRef | None,
    overrides: Mapping[TaskDocumentRef, TaskDocument],
) -> None:
    rows = [row for row in master.document.subTasks if row.number == contract.leaf_id]
    if len(rows) != 1 or not rows[0].file:
        raise RuntimeError(
            "task topology publication refused: live leaf is not declared by one exact "
            f"owning-master row: {contract.contract_path}"
        )
    leaf_path = (master.path.parent / rows[0].file).with_suffix(".json")
    repository_task_root = (topology.coordination_root / "tasks" / contract.repo_name).resolve()
    resolved_leaf_path = leaf_path.resolve(strict=False)
    if not resolved_leaf_path.is_relative_to(repository_task_root):
        raise RuntimeError(
            "task topology publication refused: live leaf task document escapes its "
            f"repository task tree: {leaf_path}"
        )
    owning_master_root = master.path.parent.resolve(strict=False)
    if resolved_leaf_path.parent != owning_master_root:
        raise RuntimeError(
            "task topology publication refused: live leaf task document leaves its exact "
            f"owning master task root: {leaf_path}"
        )
    leaf_ref = TaskDocumentRef(
        repository=contract.repo_name,
        path=resolved_leaf_path.relative_to(repository_task_root).as_posix(),
    )
    try:
        document = topology.resolve_candidate(leaf_ref, overrides).document
    except TaskDocumentRefError as exc:
        raise RuntimeError(
            "task topology publication refused: live leaf task document authority is "
            f"invalid for {contract.contract_path}: {exc}"
        ) from exc
    if document.id != contract.leaf_id or document.kind == "master":
        raise RuntimeError(
            "task topology publication refused: live leaf task identity changed for "
            f"{contract.contract_path}"
        )
    if (
        contract.queue_candidate_task_document
        and contract.queue_candidate_task_document != leaf_ref.key
    ):
        raise RuntimeError("task topology publication refused: live leaf queue identity changed")
    if bool(contract.queue_candidate_task_document) != bool(contract.queue_sprint_task_document):
        raise RuntimeError("task topology publication refused: live leaf queue binding is partial")
    if contract.queue_sprint_task_document and contract.queue_sprint_task_document != (
        sprint_ref.key if sprint_ref is not None else ""
    ):
        raise RuntimeError("task topology publication refused: live leaf queue owner changed")


def _require_live_leaf_source_authority(
    contract: WorktreeContract,
    master: ResolvedTaskDocument,
    sprint_ref: TaskDocumentRef | None,
    candidate: tuple[IntegrationSurface, ...],
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
    for side in _repository_sides(contract):
        source = _branch_key(side, side.source_branch)
        matches = [
            surface
            for surface in candidate
            if _surface_key(surface) == source
            and surface.kind == expected_kind
            and surface.owner == expected_owner
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "task topology publication refused: live leaf source no longer matches its "
                f"exact {expected_kind} owner: {contract.contract_path}"
            )


def _branch_key(side: _RepositorySide, branch: str) -> tuple[str, Path, str]:
    return (
        side.side,
        _repository_identity(side.repository, side.side),
        canonical_local_branch(side.repository, branch),
    )


def _surface_key(surface: IntegrationSurface) -> tuple[str, Path, str]:
    return surface.side, surface.repository, surface.branch


def _master_integration_surfaces(
    scope: _BranchScope,
    master: ResolvedTaskDocument,
    super_branch: str | None,
    *,
    nature: MasterExecutionNature,
) -> list[IntegrationSurface]:
    path = series_contract_path(master.path.parent)
    expected = f"ar/{slugify(master.path.parent.name)}"
    if nature == "organizational":
        if path.is_file() and _load_series(path).cleanup not in TERMINAL_SERIES_CLEANUP:
            raise RuntimeError(
                f"organizational master {master.ref.key} retains a live series contract; "
                "retire it with worktree_cleanup or worktree_abandon before ordinary work starts"
            )
        return []
    if not path.is_file():
        return [
            _surface(
                side,
                kind="atomic-integration",
                branch=expected,
                owner=master.ref.key,
            )
            for side in scope.sides
        ]
    series = _load_series(path)
    _require_series_identity(scope, series, master.path.parent, super_branch)
    # A completed atomic task keeps owning its canonical branch name until archival.
    # Cleanup may delete the ref, but it must not make the name available as a workbench.
    return _series_surfaces(scope.sides, series, owner=master.ref.key)


def _master_authority(scope: _BranchScope) -> _MasterAuthority:
    topology = TaskDocumentTopology(scope.coordination_root)
    try:
        master_ref = topology.canonical_ref(scope.repo_name, scope.task_root / "task.json")
        master = topology.resolve(master_ref)
        sprint_ref = topology.parent(master_ref)
    except TaskDocumentRefError as exc:
        raise RuntimeError(
            f"cannot resolve integration-branch authority: {exc.status}: {exc}"
        ) from exc
    if sprint_ref is None:
        return _MasterAuthority(
            topology,
            master_ref,
            None,
            None,
            effective_execution_nature(master.document, None),
        )
    try:
        sprint = topology.resolve(sprint_ref)
        if sprint.document.executionGraph is not None:
            topology.validate_execution_topology(sprint_ref)
    except TaskDocumentRefError as exc:
        raise RuntimeError(
            f"cannot resolve integration-branch authority: {exc.status}: {exc}"
        ) from exc
    branch = sprint.document.integrationBranch
    if not branch:
        raise RuntimeError(
            f"integration-branch authority requires {sprint.ref.key} to declare integrationBranch"
        )
    return _MasterAuthority(
        topology,
        master_ref,
        sprint_ref,
        branch,
        effective_execution_nature(master.document, sprint.document),
    )


def _leaf_target(
    contract: WorktreeContract, authority: _MasterAuthority
) -> tuple[IntegrationTargetKind, str, str]:
    if authority.sprint_ref is not None:
        if authority.execution_nature == "organizational":
            assert authority.sprint_branch is not None
            return "sprint-super", authority.sprint_branch, _ref_key(authority.sprint_ref)
        _require_atomic_master(authority)
    series = require_parent_series_accepting_leaves(
        contract,
        operation="atomic leaf integration",
    )
    assert series is not None
    return "atomic-integration", series.code_work_branch, contract.task_root.name


def _require_atomic_master(authority: _MasterAuthority) -> None:
    if authority.execution_nature != "atomic":
        raise RuntimeError(
            f"series integration requires executionNature='atomic', got "
            f"{authority.execution_nature!r}"
        )


def _repository_sides(contract: WorktreeContract) -> tuple[_RepositorySide, ...]:
    sides = [
        _RepositorySide(
            side="code",
            repository=contract.code_repo_path,
            worktree=contract.code_worktree,
            source_branch=contract.code_source_branch,
            work_branch=contract.code_work_branch,
        )
    ]
    if contract.memory_mode == "external":
        if contract.memory_repo_path is None:
            raise RuntimeError("external-memory branch authority requires a repository path")
        if _repository_identity(contract.memory_repo_path, "memory") == _repository_identity(
            contract.code_repo_path, "code"
        ):
            raise RuntimeError("external memory must not share the code repository Git common-dir")
        memory_worktree = (
            contract.memory_repo_path if contract.kind == "series" else contract.memory_worktree
        )
        if memory_worktree is None:
            raise RuntimeError("external-memory leaf branch authority requires a worktree path")
        sides.append(
            _RepositorySide(
                side="memory",
                repository=contract.memory_repo_path,
                worktree=memory_worktree,
                source_branch=contract.memory_source_branch,
                work_branch=contract.memory_work_branch,
            )
        )
    return tuple(sides)


def _load_series(path: Path) -> WorktreeContract:
    try:
        return load_contract(path)
    except (ContractError, OSError) as exc:
        raise RuntimeError(
            f"cannot resolve integration-branch authority from {path}: {exc}"
        ) from exc


def _series_surfaces(
    current_sides_input: tuple[_RepositorySide, ...],
    series: WorktreeContract,
    *,
    owner: str,
) -> list[IntegrationSurface]:
    current_sides = {side.side: side for side in current_sides_input}
    series_sides = {side.side: side for side in _repository_sides(series)}
    if set(series_sides) != set(current_sides):
        raise RuntimeError(f"atomic series {owner} does not match the repository memory edge")
    surfaces: list[IntegrationSurface] = []
    for name, current_side in current_sides.items():
        series_side = series_sides[name]
        current_identity = _repository_identity(current_side.repository, name)
        series_identity = _repository_identity(series_side.repository, name)
        if series_identity != current_identity:
            raise RuntimeError(
                f"atomic series {owner} points its {name} branch at a different repository"
            )
        surfaces.append(
            _surface(
                series_side,
                kind="atomic-integration",
                branch=series_side.work_branch,
                owner=owner,
            )
        )
    return surfaces


def _require_series_identity(
    scope: _BranchScope,
    series: WorktreeContract,
    task_root: Path,
    super_branch: str | None,
) -> None:
    expected_root = task_root.resolve()
    expected_work_branch = f"ar/{slugify(task_root.name)}"
    _require_series_contract_shape(scope, series, expected_root, expected_work_branch)
    _require_series_code_source(series, expected_root, super_branch)
    _series_surfaces(scope.sides, series, owner=expected_root.as_posix())
    _require_series_memory_identity(
        series,
        expected_root,
        expected_work_branch,
        super_branch,
    )


def _require_series_contract_shape(
    scope: _BranchScope,
    series: WorktreeContract,
    expected_root: Path,
    expected_work_branch: str,
) -> None:
    if (
        series.kind != "series"
        or series.repo_name != scope.repo_name
        or series.task_root.resolve() != expected_root
        or series.contract_path.resolve() != series_contract_path(expected_root).resolve()
        or canonical_local_branch(series.code_repo_path, series.code_work_branch)
        != canonical_local_branch(series.code_repo_path, expected_work_branch)
    ):
        raise RuntimeError(
            f"series integration-branch authority does not match commanded task {expected_root}"
        )


def _require_series_code_source(
    series: WorktreeContract,
    expected_root: Path,
    super_branch: str | None,
) -> None:
    if super_branch is not None and canonical_local_branch(
        series.code_repo_path, series.code_source_branch
    ) != canonical_local_branch(series.code_repo_path, super_branch):
        raise RuntimeError(
            f"series code source does not match the sprint super for {expected_root}"
        )
    if super_branch is None and canonical_local_branch(
        series.code_repo_path, series.code_source_branch
    ) != repository_default_branch(series.code_repo_path):
        raise RuntimeError(
            f"standalone atomic series code source is not the repository default for "
            f"{expected_root}"
        )


def _require_series_memory_identity(
    series: WorktreeContract,
    expected_root: Path,
    expected_work_branch: str,
    super_branch: str | None,
) -> None:
    if series.memory_mode != "external":
        return
    assert series.memory_repo_path is not None
    if canonical_local_branch(
        series.memory_repo_path, series.memory_work_branch
    ) != canonical_local_branch(series.memory_repo_path, expected_work_branch):
        raise RuntimeError(f"series memory target does not match commanded task {expected_root}")
    if super_branch is not None and canonical_local_branch(
        series.memory_repo_path, series.memory_source_branch
    ) != canonical_local_branch(series.memory_repo_path, super_branch):
        raise RuntimeError(
            f"series memory source does not match the sprint super for {expected_root}"
        )
    if super_branch is None and canonical_local_branch(
        series.memory_repo_path, series.memory_source_branch
    ) != memory_repository_default_branch(series.memory_repo_path):
        raise RuntimeError(
            f"standalone atomic series memory source is not the repository default for "
            f"{expected_root}"
        )


def _side_default_branch(side: _RepositorySide) -> str:
    if side.side == "memory":
        return memory_repository_default_branch(side.repository)
    return repository_default_branch(side.repository)


def _surface(
    side: _RepositorySide,
    *,
    kind: IntegrationSurfaceKind,
    branch: str,
    owner: str,
) -> IntegrationSurface:
    return IntegrationSurface(
        side=side.side,
        kind=kind,
        repository=_repository_identity(side.repository, side.side),
        branch=canonical_local_branch(side.repository, branch),
        owner=owner,
    )


def _repository_identity(repository: Path, side: str) -> Path:
    identity = repository_identity(repository)
    if identity is None:
        raise RuntimeError(f"cannot resolve {side} integration repository identity: {repository}")
    return identity


def _require_unprotected_branch(
    side: _RepositorySide,
    surfaces: tuple[IntegrationSurface, ...],
    *,
    operation: str,
) -> None:
    branch = canonical_local_branch(side.repository, side.work_branch)
    _require_unprotected_name(side, branch, surfaces, operation=operation)


def _require_unprotected_name(
    side: _RepositorySide,
    branch: str,
    surfaces: tuple[IntegrationSurface, ...],
    *,
    operation: str,
) -> None:
    identity = _repository_identity(side.repository, side.side)
    for surface in surfaces:
        if (
            surface.side == side.side
            and surface.repository == identity
            and surface.branch == branch
        ):
            raise RuntimeError(
                f"{operation} refused: integration-branch-is-not-a-workbench: "
                f"{side.side} branch {branch!r} is {surface.kind} owned by {surface.owner}"
            )


def _deduplicated(surfaces: list[IntegrationSurface]) -> tuple[IntegrationSurface, ...]:
    unique: dict[tuple[str, Path, str], IntegrationSurface] = {}
    for surface in surfaces:
        key = (surface.side, surface.repository, surface.branch)
        existing = unique.get(key)
        if existing is not None and (
            existing.kind != surface.kind or existing.owner != surface.owner
        ):
            raise RuntimeError(
                f"integration-branch authority collision for {surface.side} "
                f"{surface.branch!r}: {existing.kind} owned by {existing.owner} conflicts "
                f"with {surface.kind} owned by {surface.owner}"
            )
        unique.setdefault(key, surface)
    return tuple(unique.values())


def _ref_key(value: TaskDocumentRef) -> str:
    return value.key
