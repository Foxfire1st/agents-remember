from __future__ import annotations

from pathlib import Path
from typing import Literal

from agents_remember.kernel.coordination_context.contracts import resolve_contract
from agents_remember.kernel.coordination_context.cross_repo import resolve_cross_repo_settings
from agents_remember.kernel.coordination_context.models import (
    CodeRepository,
    ContractReaderPort,
    CoordinationContext,
    CoordinationHints,
    CoordinationRequest,
    CoordinationRoots,
    CoordinationSelection,
    CrossRepoSettings,
    EnclosureResolution,
    EnclosureSelector,
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
    code_repository_root: Path | None = None,
    *,
    request: CoordinationRequest,
) -> CoordinationContext:
    hints = request.hints or CoordinationHints()
    selector = request.selector or EnclosureSelector()
    if request.contract_reader is None:
        raise ValueError("coordination resolution requires a contract reader")
    repo = _resolve_code_repository(code_repository_name, workspace_root, code_repository_root)
    if hints.onboarding_root is not None:
        return _context_from_onboarding_root(
            repo, hints, hints.onboarding_root, selector, request.contract_reader
        )
    return _context_from_selection(repo, hints, selector, request.contract_reader)


def _resolve_code_repository(
    code_repository_name: str | None,
    workspace_root: Path | None,
    code_repository_root: Path | None,
) -> CodeRepository:
    resolved_workspace_root = (workspace_root or Path.cwd()).resolve()
    if code_repository_root is not None:
        resolved_code_root = code_repository_root.resolve()
        return CodeRepository(
            name=code_repository_name or resolved_code_root.name,
            root=resolved_code_root,
            workspace=(workspace_root.resolve() if workspace_root else resolved_code_root.parent),
        )
    if not code_repository_name:
        raise ValueError(
            "code_repository_name is required when code_repository_root is not supplied"
        )
    return CodeRepository(
        name=code_repository_name,
        root=find_code_repository_root(resolved_workspace_root, code_repository_name),
        workspace=resolved_workspace_root,
    )


def _context_from_onboarding_root(
    repo: CodeRepository,
    hints: CoordinationHints,
    onboarding_root: Path,
    selector: EnclosureSelector,
    contract_reader: ContractReaderPort,
) -> CoordinationContext:
    resolved_onboarding_root = onboarding_root.resolve()
    resolved_settings = (
        hints.settings_path.resolve()
        if hints.settings_path
        else infer_settings_path(resolved_onboarding_root)
    )
    topology = hints.topology or infer_topology_from_onboarding_root(resolved_onboarding_root)
    coordination_root, memory_root = memory_roots_from_settings(
        resolved_settings, repo.root, repo.name, topology
    )
    storage, cross_repo = parse_coordination_settings(resolved_settings, topology)
    return build_coordination_context(
        repo,
        roots=CoordinationRoots(
            topology=topology,
            coordination_root=coordination_root,
            memory_root=memory_root,
            onboarding_root=resolved_onboarding_root,
            settings_path=resolved_settings,
        ),
        storage=storage,
        cross_repo=cross_repo,
        resolution=EnclosureResolution(selector=selector, contract_reader=contract_reader),
    )


def _context_from_selection(
    repo: CodeRepository,
    hints: CoordinationHints,
    selector: EnclosureSelector,
    contract_reader: ContractReaderPort,
) -> CoordinationContext:
    contract_coordination_root = _contract_coordination_root(
        selector.contract_path, hints.coordination_root, contract_reader
    )
    selection = detect_coordination_selection(
        code_repository_name=repo.name,
        code_repository_root=repo.root,
        requested_topology=hints.topology,
        coordination_root_hint=contract_coordination_root,
        settings_path=hints.settings_path,
    )
    resolved_settings = (
        hints.settings_path.resolve() if hints.settings_path else selection.settings_path
    )
    storage, cross_repo = parse_coordination_settings(resolved_settings, selection.topology)
    return build_coordination_context(
        repo,
        roots=CoordinationRoots(
            topology=selection.topology,
            coordination_root=selection.coordination_root,
            memory_root=selection.memory_root,
            onboarding_root=selection.memory_root / "onboarding",
            settings_path=resolved_settings,
        ),
        storage=storage,
        cross_repo=cross_repo,
        resolution=EnclosureResolution(selector=selector, contract_reader=contract_reader),
    )


def _contract_coordination_root(
    contract_path: Path | None,
    coordination_root: Path | None,
    contract_reader: ContractReaderPort,
) -> Path | None:
    if contract_path is None or not contract_path.exists():
        return coordination_root
    try:
        return contract_reader.load_contract(contract_path.resolve()).coordination_root
    except Exception:
        return coordination_root


def build_coordination_context(
    repo: CodeRepository,
    *,
    roots: CoordinationRoots,
    storage: StorageSettings,
    cross_repo: CrossRepoSettings,
    resolution: EnclosureResolution,
) -> CoordinationContext:
    selector = resolution.selector or EnclosureSelector()
    contract_reader = resolution.contract_reader
    if contract_reader is None:
        raise ValueError("coordination resolution requires a contract reader")
    coordination_root = roots.coordination_root
    memory_root = roots.memory_root
    contract, resolved_contract_path = resolve_contract(
        selector, coordination_root, repo.name, contract_reader
    )
    task_root = _task_root(
        coordination_root,
        repo.name,
        selector,
        contract,
        contract_reader,
    )
    worktree_group = _worktree_group(
        coordination_root, repo.name, selector.worktree_name, contract, contract_reader
    )
    memory_mode = contract.memory_mode if contract is not None else _memory_mode(roots.topology)
    effective_memory_root = _effective_memory_root(memory_root, contract)
    system_root = _system_root(memory_root, coordination_root)
    return CoordinationContext(
        topology=roots.topology,
        code_repository_name=repo.name,
        code_repository_root=repo.root,
        coordination_root=coordination_root,
        memory_root=effective_memory_root,
        onboarding_root=_effective_child_root(
            effective_memory_root, roots.onboarding_root, "onboarding"
        ),
        settings_path=roots.settings_path,
        path_settings_path=_existing_path_settings(roots.settings_path),
        task_root=task_root,
        temp_root=coordination_root / "temp",
        docs_root=_effective_child_root(effective_memory_root, coordination_root / "docs", "docs"),
        system_root=system_root,
        sources_path=system_root / "sources.md",
        tools_path=system_root / "tools.md",
        storage=storage,
        path_rules=storage.path_rules,
        cross_repo=resolve_cross_repo_settings(cross_repo, repo.workspace, coordination_root),
        memory_mode=memory_mode,
        contract_path=resolved_contract_path,
        worktree_group=worktree_group,
        code_worktree=contract.code_worktree if contract is not None else None,
        memory_worktree=contract.memory_worktree if contract is not None else None,
        ledger_path=_ledger_path(memory_root, roots.topology, contract),
    )


def _task_root(
    coordination_root: Path,
    code_repository_name: str,
    selector: EnclosureSelector,
    contract,
    contract_reader: ContractReaderPort,
) -> Path:
    if contract is not None:
        return contract.task_root
    if selector.task_name:
        return contract_reader.resolve_active_task_root(
            coordination_root,
            code_repository_name,
            selector.task_name,
            parent_task=selector.parent_task,
        )
    return coordination_root / "tasks" / code_repository_name


def _worktree_group(
    coordination_root: Path,
    code_repository_name: str,
    worktree_name: str | None,
    contract,
    contract_reader: ContractReaderPort,
) -> Path | None:
    if contract is not None:
        return contract.worktree_group
    if worktree_name:
        return contract_reader.worktree_group_for(
            coordination_root, code_repository_name, worktree_name
        )
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
