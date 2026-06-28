from __future__ import annotations

from pathlib import Path
from typing import Literal

from agents_remember.kernel.coordination_context.contracts import resolve_contract
from agents_remember.kernel.coordination_context.cross_repo import resolve_cross_repo_settings
from agents_remember.kernel.coordination_context.models import (
    CoordinationContext,
    CoordinationSelection,
    CrossRepoSettings,
    MissingMemoryError,
    StorageSettings,
)
from agents_remember.kernel.coordination_context.paths import (
    external_memory_root,
    find_code_repository_root,
    infer_settings_path,
    infer_topology_from_onboarding_root,
    internal_coordination_root,
    internal_memory_root,
    memory_roots_from_settings,
    path_settings_path_for,
    resolve_coordination_root_hint,
    settings_path_for_roots,
)
from agents_remember.kernel.coordination_context.settings import parse_coordination_settings
from agents_remember.worktrees.task_resolver import resolve_active_task_root
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    load_contract,
    worktree_group_for,
)


def detect_coordination_selection(
    code_repository_name: str,
    code_repository_root: Path,
    requested_topology: Literal["internal", "external"] | None = None,
    coordination_root_hint: Path | None = None,
    settings_path: Path | None = None,
) -> CoordinationSelection:
    roots = _selection_roots(code_repository_name, code_repository_root, coordination_root_hint)
    if settings_path is not None:
        return _selection_from_settings(
            settings_path.resolve(),
            requested_topology,
            code_repository_name,
            code_repository_root,
            roots,
        )
    if requested_topology == "internal":
        return _required_internal_selection(code_repository_name, roots)
    if requested_topology == "external":
        return _required_external_selection(code_repository_name, roots)
    if roots["internal_root"].exists():
        settings = settings_path_for_roots(
            roots["internal_root"], roots["internal_coordination"], "internal"
        )
        return CoordinationSelection(
            "internal", roots["internal_coordination"], roots["internal_root"], settings
        )
    if roots["external_root"].exists():
        settings = settings_path_for_roots(
            roots["external_root"], roots["coordination_root"], "external"
        )
        return CoordinationSelection(
            "external", roots["coordination_root"], roots["external_root"], settings
        )
    raise _missing_memory_error(code_repository_name, roots)


def _selection_roots(
    code_repository_name: str, code_repository_root: Path, coordination_root_hint: Path | None
) -> dict[str, Path]:
    coordination_root = resolve_coordination_root_hint(coordination_root_hint)
    return {
        "internal_coordination": internal_coordination_root(code_repository_root),
        "internal_root": internal_memory_root(code_repository_root),
        "coordination_root": coordination_root,
        "external_root": external_memory_root(coordination_root, code_repository_name),
    }


def _selection_from_settings(
    resolved_settings: Path,
    requested_topology: Literal["internal", "external"] | None,
    code_repository_name: str,
    code_repository_root: Path,
    roots: dict[str, Path],
) -> CoordinationSelection:
    topology = requested_topology or _infer_settings_topology(
        resolved_settings, roots["coordination_root"], roots["external_root"]
    )
    coordination_root, memory_root = memory_roots_from_settings(
        resolved_settings, code_repository_root, code_repository_name, topology
    )
    if not memory_root.exists():
        raise _missing_memory_error(code_repository_name, roots)
    return CoordinationSelection(topology, coordination_root, memory_root, resolved_settings)


def _infer_settings_topology(
    resolved_settings: Path, coordination_root: Path, external_root_for_repo: Path
) -> Literal["internal", "external"]:
    settings_root = resolved_settings.parent.parent
    if settings_root in (coordination_root, external_root_for_repo):
        return "external"
    return "internal"


def _required_internal_selection(
    code_repository_name: str, roots: dict[str, Path]
) -> CoordinationSelection:
    if not roots["internal_root"].exists():
        raise _missing_memory_error(code_repository_name, roots)
    settings = settings_path_for_roots(
        roots["internal_root"], roots["internal_coordination"], "internal"
    )
    return CoordinationSelection(
        "internal", roots["internal_coordination"], roots["internal_root"], settings
    )


def _required_external_selection(
    code_repository_name: str, roots: dict[str, Path]
) -> CoordinationSelection:
    if not roots["external_root"].exists():
        raise _missing_memory_error(code_repository_name, roots)
    settings = settings_path_for_roots(
        roots["external_root"], roots["coordination_root"], "external"
    )
    return CoordinationSelection(
        "external", roots["coordination_root"], roots["external_root"], settings
    )


def _missing_memory_error(code_repository_name: str, roots: dict[str, Path]) -> MissingMemoryError:
    return MissingMemoryError(
        code_repository_name,
        roots["internal_root"],
        roots["coordination_root"],
        roots["external_root"],
    )


def resolve_coordination_context(
    code_repository_name: str | None = None,
    workspace_root: Path | None = None,
    requested_topology: Literal["internal", "external"] | None = None,
    coordination_root: Path | None = None,
    settings_path: Path | None = None,
    onboarding_root: Path | None = None,
    code_repository_root: Path | None = None,
    contract_path: Path | None = None,
    task_name: str | None = None,
    parent_task: str | None = None,
    leaf_id: str | None = None,
    worktree_name: str | None = None,
) -> CoordinationContext:
    repo = _resolve_code_repository(code_repository_name, workspace_root, code_repository_root)
    if onboarding_root is not None:
        return _context_from_onboarding_root(
            repo,
            requested_topology,
            settings_path,
            onboarding_root,
            contract_path,
            task_name,
            parent_task,
            leaf_id,
            worktree_name,
        )
    return _context_from_selection(
        repo,
        requested_topology,
        coordination_root,
        settings_path,
        contract_path,
        task_name,
        parent_task,
        leaf_id,
        worktree_name,
    )


def _resolve_code_repository(
    code_repository_name: str | None,
    workspace_root: Path | None,
    code_repository_root: Path | None,
) -> dict[str, Path | str]:
    resolved_workspace_root = (workspace_root or Path.cwd()).resolve()
    if code_repository_root is not None:
        resolved_code_root = code_repository_root.resolve()
        return {
            "name": code_repository_name or resolved_code_root.name,
            "root": resolved_code_root,
            "workspace": workspace_root.resolve() if workspace_root else resolved_code_root.parent,
        }
    if not code_repository_name:
        raise ValueError(
            "code_repository_name is required when code_repository_root is not supplied"
        )
    return {
        "name": code_repository_name,
        "root": find_code_repository_root(resolved_workspace_root, code_repository_name),
        "workspace": resolved_workspace_root,
    }


def _context_from_onboarding_root(
    repo: dict[str, Path | str],
    requested_topology: Literal["internal", "external"] | None,
    settings_path: Path | None,
    onboarding_root: Path,
    contract_path: Path | None,
    task_name: str | None,
    parent_task: str | None,
    leaf_id: str | None,
    worktree_name: str | None,
) -> CoordinationContext:
    resolved_onboarding_root = onboarding_root.resolve()
    resolved_settings = (
        settings_path.resolve() if settings_path else infer_settings_path(resolved_onboarding_root)
    )
    topology = requested_topology or infer_topology_from_onboarding_root(resolved_onboarding_root)
    coordination_root, memory_root = memory_roots_from_settings(
        resolved_settings, Path(repo["root"]), str(repo["name"]), topology
    )
    storage, cross_repo = parse_coordination_settings(resolved_settings, topology)
    return build_coordination_context(
        code_repository_name=str(repo["name"]),
        code_repository_root=Path(repo["root"]),
        topology=topology,
        coordination_root=coordination_root,
        memory_root=memory_root,
        onboarding_root=resolved_onboarding_root,
        settings_path=resolved_settings,
        storage=storage,
        cross_repo=cross_repo,
        contract_path=contract_path,
        task_name=task_name,
        parent_task=parent_task,
        leaf_id=leaf_id,
        worktree_name=worktree_name,
        workspace_root=Path(repo["workspace"]),
    )


def _context_from_selection(
    repo: dict[str, Path | str],
    requested_topology: Literal["internal", "external"] | None,
    coordination_root: Path | None,
    settings_path: Path | None,
    contract_path: Path | None,
    task_name: str | None,
    parent_task: str | None,
    leaf_id: str | None,
    worktree_name: str | None,
) -> CoordinationContext:
    contract_coordination_root = _contract_coordination_root(contract_path, coordination_root)
    selection = detect_coordination_selection(
        code_repository_name=str(repo["name"]),
        code_repository_root=Path(repo["root"]),
        requested_topology=requested_topology,
        coordination_root_hint=contract_coordination_root,
        settings_path=settings_path,
    )
    resolved_settings = settings_path.resolve() if settings_path else selection.settings_path
    storage, cross_repo = parse_coordination_settings(resolved_settings, selection.topology)
    return build_coordination_context(
        code_repository_name=str(repo["name"]),
        code_repository_root=Path(repo["root"]),
        topology=selection.topology,
        coordination_root=selection.coordination_root,
        memory_root=selection.memory_root,
        onboarding_root=selection.memory_root / "onboarding",
        settings_path=resolved_settings,
        storage=storage,
        cross_repo=cross_repo,
        contract_path=contract_path,
        task_name=task_name,
        parent_task=parent_task,
        leaf_id=leaf_id,
        worktree_name=worktree_name,
        workspace_root=Path(repo["workspace"]),
    )


def _contract_coordination_root(
    contract_path: Path | None, coordination_root: Path | None
) -> Path | None:
    if contract_path is None or not contract_path.exists():
        return coordination_root
    try:
        return load_contract(contract_path.resolve()).coordination_root
    except ContractError:
        return coordination_root


def build_coordination_context(
    code_repository_name: str,
    code_repository_root: Path,
    topology: Literal["internal", "external"],
    coordination_root: Path,
    memory_root: Path,
    onboarding_root: Path,
    settings_path: Path,
    storage: StorageSettings,
    cross_repo: CrossRepoSettings,
    contract_path: Path | None = None,
    task_name: str | None = None,
    parent_task: str | None = None,
    leaf_id: str | None = None,
    worktree_name: str | None = None,
    workspace_root: Path | None = None,
) -> CoordinationContext:
    contract, resolved_contract_path = resolve_contract(
        contract_path,
        coordination_root,
        code_repository_name,
        task_name,
        parent_task,
        leaf_id,
        worktree_name,
    )
    task_root = _task_root(
        coordination_root, code_repository_name, task_name, parent_task, contract
    )
    worktree_group = _worktree_group(
        coordination_root, code_repository_name, worktree_name, contract
    )
    memory_mode = contract.memory_mode if contract is not None else _memory_mode(topology)
    effective_memory_root = _effective_memory_root(memory_root, contract)
    system_root = _system_root(memory_root, coordination_root)
    return CoordinationContext(
        topology=topology,
        code_repository_name=code_repository_name,
        code_repository_root=code_repository_root,
        coordination_root=coordination_root,
        memory_root=effective_memory_root,
        onboarding_root=_effective_child_root(effective_memory_root, onboarding_root, "onboarding"),
        settings_path=settings_path,
        path_settings_path=_existing_path_settings(settings_path),
        task_root=task_root,
        temp_root=coordination_root / "temp",
        docs_root=_effective_child_root(effective_memory_root, coordination_root / "docs", "docs"),
        system_root=system_root,
        sources_path=system_root / "sources.md",
        tools_path=system_root / "tools.md",
        storage=storage,
        path_rules=storage.path_rules,
        cross_repo=resolve_cross_repo_settings(
            cross_repo, workspace_root or code_repository_root.parent, coordination_root
        ),
        memory_mode=memory_mode,
        contract_path=resolved_contract_path,
        worktree_group=worktree_group,
        code_worktree=contract.code_worktree if contract is not None else None,
        memory_worktree=contract.memory_worktree if contract is not None else None,
        ledger_path=_ledger_path(memory_root, topology, contract),
    )


def _task_root(
    coordination_root: Path,
    code_repository_name: str,
    task_name: str | None,
    parent_task: str | None,
    contract,
) -> Path:
    if contract is not None:
        return contract.task_root
    if task_name:
        return resolve_active_task_root(
            coordination_root, code_repository_name, task_name, parent_task=parent_task
        )
    return coordination_root / "tasks" / code_repository_name


def _worktree_group(
    coordination_root: Path, code_repository_name: str, worktree_name: str | None, contract
) -> Path | None:
    if contract is not None:
        return contract.worktree_group
    if worktree_name:
        return worktree_group_for(coordination_root, code_repository_name, worktree_name)
    return None


def _memory_mode(topology: Literal["internal", "external"]) -> Literal["internal", "external"]:
    return "internal" if topology == "internal" else "external"


def _effective_memory_root(memory_root: Path, contract) -> Path:
    if contract is not None and contract.memory_worktree is not None:
        return contract.memory_worktree
    return memory_root


def _system_root(memory_root: Path, coordination_root: Path) -> Path:
    memory_system_root = memory_root / "system"
    coordination_system_root = coordination_root / "system"
    return memory_system_root if memory_system_root.exists() else coordination_system_root


def _effective_child_root(effective_memory_root: Path, fallback: Path, child: str) -> Path:
    candidate = effective_memory_root / child
    return candidate if candidate.exists() else fallback


def _existing_path_settings(settings_path: Path) -> Path | None:
    path_settings_path = path_settings_path_for(settings_path)
    return path_settings_path if path_settings_path.exists() else None


def _ledger_path(memory_root: Path, topology: str, contract) -> Path | None:
    if contract is not None:
        return contract.ledger_path
    if topology == "external":
        return memory_root / "memory.md"
    return None
