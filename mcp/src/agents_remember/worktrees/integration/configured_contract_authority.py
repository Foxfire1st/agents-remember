"""Current configured-contract authority at a mutation boundary."""

from __future__ import annotations

from pathlib import Path

from agents_remember.errors import (
    AuthorityError,
    ConfiguredContractAuthorityError,
    ConfiguredContractRereadError,
)
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import RepositoryScope, load_config
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
    LifecycleOperationLocationError,
    require_matching_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.modules.git import repository_identity
from agents_remember.worktrees.modules.startup.start_contract import memory_mode_for_repository
from agents_remember.worktrees.task_resolver import leaf_enclosure_path, series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
    worktree_group_for,
)


def reread_configured_contract(
    admitted_contract: WorktreeContract,
    configured_authority: str,
) -> tuple[WorktreeContract, LifecycleOperationLocation]:
    """Re-prove current contract, repository, and location truth under route authority."""

    contract_path = admitted_contract.contract_path
    try:
        current = load_contract(contract_path)
    except (ContractError, OSError, UnicodeError, ValueError) as error:
        state = "missing" if isinstance(error, FileNotFoundError) else "unreadable"
        detail = "the canonical configured task contract is missing or unreadable"
        raise ConfiguredContractRereadError(
            reason="contract-unreadable",
            status="configured-contract-unreadable",
            detail=detail,
            expected={
                "contractPath": contract_path.as_posix(),
                "route": "locator -> root manifest -> root journal",
            },
            observed=public_failure_evidence(
                stage="contract-read",
                side="contract",
                name=contract_path.name,
                error_type=type(error).__name__,
                observed={"state": state},
            ),
        ) from error
    try:
        require_configured_contract_repositories(current, configured_authority)
    except ConfiguredContractAuthorityError as error:
        detail = "the canonical task contract does not match configured repository authority"
        raise ConfiguredContractRereadError(
            reason="authority-invalid",
            status="configured-contract-authority-invalid",
            detail=detail,
            expected={
                "contractPath": contract_path.as_posix(),
                "repositoryAuthority": "configured",
            },
            observed=public_failure_evidence(
                stage="contract-authority",
                side=error.side,
                name=error.name,
                error_type=type(error).__name__,
                observed={"state": "mismatch"},
            ),
        ) from error
    try:
        location = require_matching_lifecycle_operation_location(current)
    except LifecycleOperationLocationError as error:
        raise ConfiguredContractRereadError(
            reason="location-invalid",
            status=error.status,
            detail=error.detail,
            expected=error.expected,
            observed=error.observed,
        ) from error
    return current, location


def require_configured_contract_repositories(
    contract: WorktreeContract,
    config_path: str,
) -> None:
    """Bind a task contract to repository identities selected by MCP authority."""

    configured, code_identity = _require_configured_repository_authority(
        contract,
        config_path,
    )
    candidate_code_identity = repository_identity(contract.code_worktree)
    if candidate_code_identity != code_identity:
        raise ConfiguredContractAuthorityError(side="code", name="candidate")
    if contract.memory_mode != "external":
        return
    memory_identity = _require_external_memory_repository_authority(
        contract,
        configured,
        code_identity,
    )
    if contract.kind == "leaf" and (
        contract.memory_worktree is None
        or repository_identity(contract.memory_worktree) != memory_identity
    ):
        raise ConfiguredContractAuthorityError(side="memory", name="candidate")


def require_configured_terminal_contract_repositories(
    contract: WorktreeContract,
    config_path: str,
) -> None:
    """Bind archived deletion authority without requiring already-deleted candidates."""

    configured, code_identity = _require_configured_repository_authority(
        contract,
        config_path,
    )
    if contract.memory_mode == "external":
        _require_external_memory_repository_authority(
            contract,
            configured,
            code_identity,
        )


def _require_configured_repository_authority(
    contract: WorktreeContract,
    config_path: str,
) -> tuple[RepositoryScope, Path]:
    config = load_config(config_path)
    try:
        configured = require_repo(config, contract.repo_name)
    except AuthorityError as error:
        raise ConfiguredContractAuthorityError(side="task", name="repository") from error
    _require_configured_task_identity(contract, config.coordination_root)
    code_identity = repository_identity(configured.path)
    contract_code_identity = repository_identity(contract.code_repo_path)
    if code_identity is None or contract_code_identity != code_identity:
        raise ConfiguredContractAuthorityError(side="code", name="repository")
    expected_memory_mode = memory_mode_for_repository(configured.path, configured.memory_root)
    if contract.memory_mode != expected_memory_mode:
        raise ConfiguredContractAuthorityError(side="memory", name="mode")
    return configured, code_identity


def _require_external_memory_repository_authority(
    contract: WorktreeContract,
    configured: RepositoryScope,
    code_identity: Path,
) -> Path:
    if configured.memory_root is None or contract.memory_repo_path is None:
        raise ConfiguredContractAuthorityError(side="memory", name="repository")
    memory_identity = repository_identity(configured.memory_root)
    contract_memory_identity = repository_identity(contract.memory_repo_path)
    if memory_identity is None or contract_memory_identity != memory_identity:
        raise ConfiguredContractAuthorityError(side="memory", name="repository")
    if memory_identity == code_identity:
        raise ConfiguredContractAuthorityError(side="memory", name="repository-separation")
    return memory_identity


def _require_configured_task_identity(
    contract: WorktreeContract,
    configured_coordination_root: Path,
) -> None:
    coordination_root = configured_coordination_root.resolve()
    if contract.coordination_root.resolve() != coordination_root:
        raise ConfiguredContractAuthorityError(side="task", name="coordination-root")
    repository_task_root = (coordination_root / "tasks" / contract.repo_name).resolve()
    task_root = contract.task_root.resolve()
    if not task_root.is_relative_to(repository_task_root):
        raise ConfiguredContractAuthorityError(side="task", name="task-root")
    if contract.task_artifact.resolve() != (task_root / "task.md").resolve():
        raise ConfiguredContractAuthorityError(side="task", name="task-artifact")
    expected_contract = (
        leaf_enclosure_path(task_root, contract.leaf_id)
        if contract.kind == "leaf"
        else series_contract_path(task_root)
    )
    if contract.contract_path.resolve() != expected_contract.resolve():
        raise ConfiguredContractAuthorityError(side="task", name="contract-path")
    if contract.kind == "series":
        expected_group = worktree_group_for(
            coordination_root, contract.repo_name, contract.task_name
        )
        if contract.worktree_group.resolve() != expected_group.resolve():
            raise ConfiguredContractAuthorityError(side="task", name="worktree-group")
        return
    worktree_root = (coordination_root / "worktrees" / contract.repo_name).resolve()
    group = contract.worktree_group.resolve()
    if not group.is_relative_to(worktree_root):
        raise ConfiguredContractAuthorityError(side="task", name="worktree-group")
    if contract.code_worktree.resolve().parent != group:
        raise ConfiguredContractAuthorityError(side="code", name="candidate-owner")
    if contract.memory_mode == "external" and (
        contract.memory_worktree is None or contract.memory_worktree.resolve().parent != group
    ):
        raise ConfiguredContractAuthorityError(side="memory", name="candidate-owner")
