"""Build the one MCP runtime config bound to an admitted AR execution scope.

The profile is loaded once at MCP process startup. Every registered tool then closes over the
same config, so reads and writes share either the selected task enclosure or the configured
Projects repository roots.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractAccepted,
    ConfiguredContractRefused,
    admit_configured_contract,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.task_document_ref import TaskDocumentRef, TaskScopedReaderContext
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentTopology
from agents_remember.tasks.task_paths import leaf_enclosure_path

TASK_SCOPED_MCP_PROFILE_SCHEMA = "ar-task-scoped-mcp-profile/v1"
PROJECTS_MCP_PROFILE_SCHEMA = "ar-projects-mcp-profile/v1"


@dataclass(frozen=True, slots=True)
class TaskScopedMcpBinding:
    task_document_ref: TaskDocumentRef
    contract_path: Path
    workspace_root: Path
    code_root: Path
    memory_root: Path | None


def task_scoped_mcp_config(
    config: McpRuntimeConfig, binding: TaskScopedMcpBinding
) -> McpRuntimeConfig:
    """Return a startup config whose complete tool registry is bound to one current leaf.

    ``config.config_path`` and ``coordination_root`` remain the original MCP authorities. The
    selected repository entry is replaced only in memory, after the canonical task and contract
    admission proves that the paths belong to that exact leaf.
    """

    admitted = _admit_task_scope(
        config,
        binding.task_document_ref,
        binding.contract_path,
    )
    _require_binding_roots(binding, admitted)
    return _config_from_admitted_task(config, binding.task_document_ref, admitted)


def task_scoped_mcp_config_for_task(
    config: McpRuntimeConfig,
    task_document_ref: TaskDocumentRef,
    contract_path: str | Path,
) -> McpRuntimeConfig:
    """Derive an immutable call-local config from a canonical task and contract address.

    Roots come only from the admitted contract and lifecycle location. Callers assert the exact
    task/contract identity but cannot choose workspace, code, or memory roots.
    """

    admitted = _admit_task_scope(config, task_document_ref, Path(contract_path))
    return _config_from_admitted_task(config, task_document_ref, admitted)


def task_scoped_mcp_config_for_reader(
    config: McpRuntimeConfig,
    *,
    repository_id: str,
    task_context: TaskScopedReaderContext | None,
) -> McpRuntimeConfig:
    """Select configured Projects roots or one admitted leaf for a reader call."""

    if task_context is None:
        return config
    if task_context.task_document_ref.repository != repository_id:
        raise ValueError("repo_id must match task_context.task_document_ref.repository.")
    return task_scoped_mcp_config_for_task(
        config,
        task_context.task_document_ref,
        task_context.contract_path,
    )


def _config_from_admitted_task(
    config: McpRuntimeConfig,
    task_document_ref: TaskDocumentRef,
    admitted: tuple[RepositoryScope, Path, Path, Path | None, Path],
) -> McpRuntimeConfig:
    base_repository, workspace_root, code_root, memory_root, contract_path = admitted
    scoped_repository = replace(
        base_repository,
        path=code_root,
        memory_root=memory_root,
        contract_path=contract_path,
    )
    return replace(
        config,
        workspace_root=workspace_root,
        repositories={task_document_ref.repository: scoped_repository},
    )


def projects_mcp_config(config: McpRuntimeConfig, repository_id: str | None) -> McpRuntimeConfig:
    """Remove any inherited leaf-contract pin while retaining configured project roots.

    Projects sessions use the registered code and memory roots. A taskless session keeps the
    configured registry but selects no repository; a sprint/master session is narrowed to its
    already-resolved canonical repository.
    """

    if repository_id is None:
        repositories = {
            key: replace(repository, contract_path=None)
            for key, repository in config.repositories.items()
        }
    else:
        repository = config.repositories.get(repository_id)
        if repository is None:
            raise ValueError(
                "The selected Projects task repository is not admitted by MCP settings."
            )
        repositories = {repository_id: replace(repository, contract_path=None)}
    return replace(config, repositories=repositories)


def _admit_task_scope(
    config: McpRuntimeConfig,
    task_document_ref: TaskDocumentRef,
    contract_path: Path,
) -> tuple[RepositoryScope, Path, Path, Path | None, Path]:
    resolved = TaskDocumentTopology(config.coordination_root).resolve(task_document_ref)
    supplied_contract = _canonical_leaf_contract(task_document_ref, contract_path, resolved)
    admission = admit_configured_contract(config, supplied_contract)
    if isinstance(admission, ConfiguredContractRefused):
        raise ValueError(
            "AR refused the task-scoped MCP context because its enclosure authority is not current: "
            f"{admission.status}."
        )
    assert isinstance(admission, ConfiguredContractAccepted)
    contract = admission.contract
    if contract.kind != "leaf" or contract.leaf_id != resolved.document.id:
        raise ValueError("The admitted contract does not own the selected leaf.")

    admitted_workspace, admitted_code, admitted_memory = _admitted_leaf_roots(admission)
    base_repository = config.repositories.get(task_document_ref.repository)
    if base_repository is None:
        raise ValueError("The task repository is not present in the original MCP authority.")
    return base_repository, admitted_workspace, admitted_code, admitted_memory, supplied_contract


def _canonical_leaf_contract(
    task_document_ref: TaskDocumentRef,
    contract_path: Path,
    resolved: ResolvedTaskDocument,
) -> Path:
    if resolved.document.kind != "subTask":
        raise ValueError("A task-scoped MCP context requires a canonical leaf document.")
    expected_contract = leaf_enclosure_path(resolved.path.parent, resolved.document.id).resolve(
        strict=False
    )
    supplied_contract = contract_path.resolve(strict=False)
    if supplied_contract != expected_contract:
        raise ValueError("The task context contract is not the canonical enclosure for this leaf.")
    if len(resolved.document.enclosures) != 1:
        raise ValueError("The selected leaf must have exactly one canonical enclosure binding.")
    enclosure = resolved.document.enclosures[0]
    if enclosure.leafId != resolved.document.id:
        raise ValueError("The selected leaf enclosure has a conflicting leaf identity.")
    if Path(enclosure.enclosurePath).resolve(strict=False) != supplied_contract:
        raise ValueError("The selected leaf document does not bind the supplied enclosure.")
    if resolved.ref.repository != task_document_ref.repository:
        raise ValueError("The selected task document does not match its repository identity.")
    return supplied_contract


def _admitted_leaf_roots(
    admission: ConfiguredContractAccepted,
) -> tuple[Path, Path, Path | None]:
    admitted_workspace = admission.location.worktree_group.resolve(strict=False)
    admitted_code = admission.contract.code_worktree.resolve(strict=False)
    admitted_memory = (
        admission.contract.memory_worktree.resolve(strict=False)
        if admission.contract.memory_worktree is not None
        else None
    )
    if not admitted_workspace.is_dir() or not admitted_code.is_dir():
        raise ValueError("The admitted leaf MCP workspace or code worktree is unavailable.")
    if admitted_memory is not None and not admitted_memory.is_dir():
        raise ValueError("The admitted leaf memory worktree is unavailable.")
    return admitted_workspace, admitted_code, admitted_memory


def _require_binding_roots(
    binding: TaskScopedMcpBinding,
    admitted: tuple[RepositoryScope, Path, Path, Path | None, Path],
) -> None:
    _repository, workspace, code, memory, _contract = admitted
    if (
        binding.workspace_root.resolve(strict=False) != workspace
        or binding.code_root.resolve(strict=False) != code
        or (binding.memory_root.resolve(strict=False) if binding.memory_root else None) != memory
    ):
        raise ValueError("The MCP profile roots do not match the admitted leaf worktrees.")


def mcp_config_from_scope_profile(
    config: McpRuntimeConfig,
    profile: dict[str, Any],
) -> McpRuntimeConfig:
    """Load the typed task or Projects scope represented by a private startup profile."""

    base_config_path = profile.get("baseConfigPath")
    if (
        not isinstance(base_config_path, str)
        or Path(base_config_path).resolve(strict=False) != config.config_path.resolve()
    ):
        raise ValueError("The native MCP scope profile names a different authority settings file.")
    if profile.get("schema") == PROJECTS_MCP_PROFILE_SCHEMA:
        workspace_root = _required_profile_path(profile, "workspaceRoot")
        if workspace_root.resolve(strict=False) != config.workspace_root.resolve():
            raise ValueError("The Projects MCP profile names a different workspace root.")
        repository_id = profile.get("repositoryId")
        if repository_id is not None and not isinstance(repository_id, str):
            raise ValueError("The Projects MCP profile has an invalid repository identity.")
        return projects_mcp_config(config, repository_id)
    if profile.get("schema") != TASK_SCOPED_MCP_PROFILE_SCHEMA:
        raise ValueError("The native MCP scope profile has an unsupported schema.")
    task_document_ref = profile.get("taskDocumentRef")
    if not isinstance(task_document_ref, dict):
        raise ValueError("The native MCP scope profile has no canonical task document reference.")
    return task_scoped_mcp_config(
        config,
        TaskScopedMcpBinding(
            task_document_ref=TaskDocumentRef.model_validate(task_document_ref),
            contract_path=_required_profile_path(profile, "contractPath"),
            workspace_root=_required_profile_path(profile, "workspaceRoot"),
            code_root=_required_profile_path(profile, "codeRoot"),
            memory_root=_optional_profile_path(profile, "memoryRoot"),
        ),
    )


def _required_profile_path(profile: dict[str, Any], key: str) -> Path:
    value = profile.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"The native MCP scope profile has no {key} path.")
    return Path(value)


def _optional_profile_path(profile: dict[str, Any], key: str) -> Path | None:
    value = profile.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"The native MCP scope profile has an invalid {key} path.")
    return Path(value)


__all__ = [
    "PROJECTS_MCP_PROFILE_SCHEMA",
    "TASK_SCOPED_MCP_PROFILE_SCHEMA",
    "TaskScopedMcpBinding",
    "mcp_config_from_scope_profile",
    "projects_mcp_config",
    "task_scoped_mcp_config",
    "task_scoped_mcp_config_for_reader",
    "task_scoped_mcp_config_for_task",
]
