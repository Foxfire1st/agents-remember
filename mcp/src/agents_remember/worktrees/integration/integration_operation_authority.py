"""Durable capability proof for the only operation allowed to move integration refs."""

from __future__ import annotations

from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationOperationAuthority,
    LifecycleOperationRecord,
)
from agents_remember.worktrees.integration.integration_branch_authority import integration_targets
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import repository_identity
from agents_remember.worktrees.worktree_contract import WorktreeContract


def require_plane_integration_operation(
    contract: WorktreeContract, args: WorktreeArgs
) -> LifecycleOperationRecord:
    """Bind a real integration to its exact task-addressed detached operation."""

    if not args.operation_key:
        raise RuntimeError(
            "integration-branch mutation requires a plane-owned journaled integration operation"
        )
    record = located_lifecycle_operation_store(contract, "integrate").read()
    if record is None:
        raise RuntimeError("plane-owned integration operation record is missing")
    if record.operationKey != args.operation_key:
        raise RuntimeError("integration operation key does not own this protected-ref mutation")
    if record.status != "running":
        raise RuntimeError(
            f"integration operation is not the active running owner: {record.status}"
        )
    if record.contractPath != contract.contract_path.as_posix():
        raise RuntimeError("integration operation contract identity changed")
    if not isinstance(record.input, IntegrateOperationInput):
        raise RuntimeError("integration operation carries the wrong durable input kind")
    if record.input.strategy != args.strategy:
        raise RuntimeError("integration strategy changed after the operation was journaled")
    authority = record.integrationAuthority
    if authority is None:
        raise RuntimeError("integration operation is missing exact source/candidate authority")
    _require_contract_authority(contract, authority)
    return record


def require_current_integration_sources(
    contract: WorktreeContract,
    args: WorktreeArgs,
    *,
    code_source_commit: str,
    memory_source_commit: str,
) -> LifecycleOperationRecord:
    """Revalidate accepted repositories, named refs, and tips before protected refs move."""

    record = require_plane_integration_operation(contract, args)
    authority = record.integrationAuthority
    assert authority is not None
    if authority.codeSourceCommit != code_source_commit:
        raise RuntimeError("code integration source moved after the plane accepted this operation")
    if authority.memorySourceCommit != memory_source_commit:
        raise RuntimeError(
            "memory integration source moved after the plane accepted this operation"
        )
    return record


def require_authorized_integration_commits(
    contract: WorktreeContract,
    args: WorktreeArgs,
    *,
    code_commit: str,
    memory_content_commit: str,
    ledger_commit: str,
) -> LifecycleOperationRecord:
    """Prove the landing output is still the exact closeout candidate, never a replay."""

    record = require_plane_integration_operation(contract, args)
    authority = record.integrationAuthority
    assert authority is not None
    found = (code_commit, memory_content_commit, ledger_commit)
    expected = (
        authority.codeCandidateCommit,
        authority.memoryContentCommit,
        authority.ledgerCommit,
    )
    if found != expected:
        raise RuntimeError(
            "integration output is not the exact journaled closeout candidate; conflict "
            "resolution must produce a new leaf closeout before integration"
        )
    return record


def _require_contract_authority(
    contract: WorktreeContract, authority: IntegrationOperationAuthority
) -> None:
    targets = {target.side: target for target in integration_targets(contract)}
    code = targets["code"]
    if (
        authority.targetKind != code.kind
        or authority.codeRepository != code.repository.as_posix()
        or authority.codeSourceBranch != code.branch
        or authority.codeSourceRef != f"refs/heads/{code.branch}"
    ):
        raise RuntimeError("journaled code repository or target ref no longer matches the task")
    if contract.kind == "leaf":
        code_worktree_identity = repository_identity(contract.code_worktree)
        if code_worktree_identity != code.repository:
            raise RuntimeError("journaled code candidate worktree changed repository identity")
    memory = targets.get("memory")
    if memory is None:
        if (
            authority.memoryRepository
            or authority.memorySourceBranch
            or authority.memorySourceRef
            or authority.memorySourceCommit
        ):
            raise RuntimeError("internal-memory integration carries external-memory authority")
    else:
        if (
            authority.memoryRepository != memory.repository.as_posix()
            or authority.memorySourceBranch != memory.branch
            or authority.memorySourceRef != f"refs/heads/{memory.branch}"
        ):
            raise RuntimeError(
                "journaled memory repository or target ref no longer matches the task"
            )
        if contract.kind == "leaf":
            assert contract.memory_worktree is not None
            if repository_identity(contract.memory_worktree) != memory.repository:
                raise RuntimeError(
                    "journaled memory candidate worktree changed repository identity"
                )
    _require_closed_candidate(contract, authority)


def _require_closed_candidate(
    contract: WorktreeContract, authority: IntegrationOperationAuthority
) -> None:
    if authority.codeCandidateCommit != contract.code_commit:
        raise RuntimeError("journaled code candidate no longer matches closeout")
    if authority.memoryContentCommit != contract.memory_content_commit:
        raise RuntimeError("journaled memory candidate no longer matches closeout")
    if authority.ledgerCommit != contract.ledger_commit:
        raise RuntimeError("journaled ledger candidate no longer matches closeout")
