"""Derive the MCP runtime config of one reader call bound to an admitted leaf.

A reader call that carries a task context is answered from a config whose repository entry is the
leaf's admitted enclosure; a call without one is answered from the configured Projects roots.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractAccepted,
    ConfiguredContractRefused,
    admit_configured_contract,
)
from agents_remember.kernel.direct_execution_grants import DirectGrantError, read_grant
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.task_document_ref import TaskDocumentRef, TaskScopedReaderContext
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentTopology
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.task_resolver import series_contract_path


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


def _admit_task_scope(
    config: McpRuntimeConfig,
    task_document_ref: TaskDocumentRef,
    contract_path: Path,
) -> tuple[RepositoryScope, Path, Path, Path | None, Path]:
    resolved = TaskDocumentTopology(config.coordination_root).resolve(task_document_ref)
    supplied_contract, direct = _canonical_leaf_contract(
        config, task_document_ref, contract_path, resolved
    )
    admission = admit_configured_contract(config, supplied_contract)
    if isinstance(admission, ConfiguredContractRefused):
        raise ValueError(
            "AR refused the task-scoped MCP context because its enclosure authority is not current: "
            f"{admission.status}."
        )
    assert isinstance(admission, ConfiguredContractAccepted)
    contract = admission.contract
    if direct:
        if contract.kind != "series" or contract.task_root.resolve(
            strict=False
        ) != resolved.path.parent.resolve(strict=False):
            raise ValueError("The admitted series contract does not own the selected leaf's task.")
    elif contract.kind != "leaf" or contract.leaf_id != resolved.document.id:
        raise ValueError("The admitted contract does not own the selected leaf.")

    admitted_workspace, admitted_code, admitted_memory = _admitted_leaf_roots(admission)
    base_repository = config.repositories.get(task_document_ref.repository)
    if base_repository is None:
        raise ValueError("The task repository is not present in the original MCP authority.")
    return base_repository, admitted_workspace, admitted_code, admitted_memory, supplied_contract


def _enclosure_binding_checks(
    task_document_ref: TaskDocumentRef,
    supplied_contract: Path,
    resolved: ResolvedTaskDocument,
) -> None:
    """The enclosure binding of a leaf admitted to its own enclosure."""
    if len(resolved.document.enclosures) != 1:
        raise ValueError("The selected leaf must have exactly one canonical enclosure binding.")
    enclosure = resolved.document.enclosures[0]
    if enclosure.leafId != resolved.document.id:
        raise ValueError("The selected leaf enclosure has a conflicting leaf identity.")
    if Path(enclosure.enclosurePath).resolve(strict=False) != supplied_contract:
        raise ValueError("The selected leaf document does not bind the supplied enclosure.")
    if resolved.ref.repository != task_document_ref.repository:
        raise ValueError("The selected task document does not match its repository identity.")


def _canonical_leaf_contract(
    config: McpRuntimeConfig,
    task_document_ref: TaskDocumentRef,
    contract_path: Path,
    resolved: ResolvedTaskDocument,
) -> tuple[Path, bool]:
    """The admitted contract and whether the leaf is admitted direct through its grant.

    A direct leaf is bound by its canonical leaf document and its master's
    series contract (MIK-R94 rule 12): the grant is read, never inferred, and a
    grant that cannot be read refuses the admission.
    """
    if resolved.document.kind != "subTask":
        raise ValueError("A task-scoped MCP context requires a canonical leaf document.")
    expected_contract = leaf_enclosure_path(resolved.path.parent, resolved.document.id).resolve(
        strict=False
    )
    supplied_contract = contract_path.resolve(strict=False)
    if supplied_contract == expected_contract:
        _enclosure_binding_checks(task_document_ref, supplied_contract, resolved)
        return supplied_contract, False
    try:
        grant = read_grant(config.coordination_root, task_document_ref)
    except DirectGrantError as error:
        raise ValueError(
            f"The direct-execution grant for the selected leaf cannot be read: {error.detail}"
        ) from error
    if grant is None:
        raise ValueError("The task context contract is not the canonical enclosure for this leaf.")
    expected_series = series_contract_path(resolved.path.parent).resolve(strict=False)
    if supplied_contract != expected_series:
        raise ValueError(
            "The task context contract of a direct leaf is its master's series contract."
        )
    if resolved.ref.repository != task_document_ref.repository:
        raise ValueError("The selected task document does not match its repository identity.")
    return supplied_contract, True


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


__all__ = [
    "task_scoped_mcp_config_for_reader",
    "task_scoped_mcp_config_for_task",
]
