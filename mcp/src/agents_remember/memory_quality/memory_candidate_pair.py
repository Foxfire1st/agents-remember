"""Resolve and re-prove one exact external-memory worktree pair."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from agents_remember.errors import MemoryCandidatePairError, MemoryCandidatePairFailure
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.worktrees.modules.git import (
    branch_commit,
    current_branch,
    is_ancestor,
    repository_identity,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


@dataclass(frozen=True)
class _BranchPlan:
    side: str
    repository: Path
    worktree: Path
    source_branch: str
    work_branch: str
    base_commit: str
    accepted_source_heads: frozenset[str]


@dataclass(frozen=True)
class _FailureEvidence:
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None
    next_action: str = "developer-decision"
    next_args: dict[str, object] | None = None


_NO_EVIDENCE = _FailureEvidence()


def resolve_memory_candidate_pair(
    contract: WorktreeContract,
    *,
    requested_contract_path: str | Path | None = None,
    requested_repo_id: str | None = None,
) -> MemoryCandidatePairIdentity:
    """Return one exact pair after validating every contract-owned identity pre-scan."""

    contract_path = contract.contract_path.resolve()
    _require_requested_authority(
        contract,
        contract_path=contract_path,
        requested_contract_path=requested_contract_path,
        requested_repo_id=requested_repo_id,
    )
    memory_root, ledger_path = _require_candidate_shape(contract, contract_path)
    current = _reread_contract(contract, contract_path)
    code_root = current.code_worktree.resolve()
    onboarding_root = (memory_root / "onboarding").resolve()
    _require_path(code_root, "codeRoot", kind="directory", contract_path=contract_path)
    _require_path(memory_root, "memoryRoot", kind="directory", contract_path=contract_path)
    _require_path(onboarding_root, "onboardingRoot", kind="directory", contract_path=contract_path)
    _require_path(ledger_path, "ledgerPath", kind="file", contract_path=contract_path)
    expected_ledger = (memory_root / "memory.md").resolve()
    if ledger_path != expected_ledger:
        _refuse(
            "memory-candidate-pair-path-mismatch",
            "ledgerPath",
            "the contract ledger path does not belong to its exact memory worktree",
            contract_path,
            _FailureEvidence(
                expected={"ledgerPath": expected_ledger.as_posix()},
                observed={"ledgerPath": ledger_path.as_posix()},
            ),
        )
    _require_repository_pair(current, code_root, memory_root, contract_path)
    _require_branch_plan(
        _BranchPlan(
            side="code",
            repository=current.code_repo_path,
            worktree=code_root,
            source_branch=current.code_source_branch,
            work_branch=current.code_work_branch,
            base_commit=current.code_base_commit,
            accepted_source_heads=_accepted_source_heads(
                current.code_base_commit,
                current.integrated_code_commit,
                integration_completed=current.integration_status == "completed",
            ),
        ),
        contract_path=contract_path,
    )
    assert current.memory_repo_path is not None
    _require_branch_plan(
        _BranchPlan(
            side="memory",
            repository=current.memory_repo_path,
            worktree=memory_root,
            source_branch=current.memory_source_branch,
            work_branch=current.memory_work_branch,
            base_commit=current.memory_base_commit,
            accepted_source_heads=_accepted_source_heads(
                current.memory_base_commit,
                current.integrated_ledger_commit,
                integration_completed=current.integration_status == "completed",
            ),
        ),
        contract_path=contract_path,
    )
    projection: dict[str, str] = {
        "repoId": current.repo_name,
        "contractPath": contract_path.as_posix(),
        "codeRoot": code_root.as_posix(),
        "memoryRoot": memory_root.as_posix(),
        "codeSourceBranch": current.code_source_branch,
        "codeWorkBranch": current.code_work_branch,
        "codeBaseCommit": current.code_base_commit,
        "memorySourceBranch": current.memory_source_branch,
        "memoryWorkBranch": current.memory_work_branch,
        "memoryBaseCommit": current.memory_base_commit,
        "onboardingRoot": onboarding_root.as_posix(),
        "ledgerPath": ledger_path.as_posix(),
    }
    canonical_projection = {
        "schemaVersion": "ar-memory-candidate-pair/v1",
        **projection,
    }
    digest = hashlib.sha256(
        json.dumps(canonical_projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return MemoryCandidatePairIdentity(
        schemaVersion="ar-memory-candidate-pair/v1",
        **projection,
        contractDigest=digest,
    )


def _require_requested_authority(
    contract: WorktreeContract,
    *,
    contract_path: Path,
    requested_contract_path: str | Path | None,
    requested_repo_id: str | None,
) -> None:
    if requested_contract_path is not None:
        requested = Path(requested_contract_path).resolve()
        if requested != contract_path:
            _refuse(
                "memory-candidate-pair-contract-mismatch",
                "contractPath",
                "requested and contract-owned leaf addresses differ",
                contract_path,
                _FailureEvidence(
                    expected={"contractPath": contract_path.as_posix()},
                    observed={"contractPath": requested.as_posix()},
                ),
            )
    if requested_repo_id is not None and requested_repo_id != contract.repo_name:
        _refuse(
            "memory-candidate-pair-repository-mismatch",
            "repoId",
            "requested repository and contract-owned repository differ",
            contract_path,
            _FailureEvidence(
                expected={"repoId": contract.repo_name},
                observed={"repoId": requested_repo_id},
            ),
        )


def _reread_contract(contract: WorktreeContract, contract_path: Path) -> WorktreeContract:
    try:
        current = load_contract(contract_path)
    except (ContractError, OSError, UnicodeError, ValueError) as exc:
        _refuse(
            "memory-candidate-pair-contract-unreadable",
            "contractPath",
            "the exact leaf contract is missing or unreadable",
            contract_path,
            _FailureEvidence(observed={"errorType": type(exc).__name__}),
        )
    if current != contract:
        _refuse(
            "memory-candidate-pair-contract-stale",
            "contractPath",
            "the exact leaf contract changed after admission",
            contract_path,
            _FailureEvidence(
                expected={"state": "unchanged-since-admission"},
                observed={"state": "changed"},
            ),
        )
    return current


def _require_external_leaf(contract: WorktreeContract, contract_path: Path) -> None:
    if contract.kind != "leaf":
        _refuse(
            "memory-candidate-pair-contract-kind-invalid",
            "kind",
            "memory-candidate acceptance requires a leaf enclosure contract",
            contract_path,
            _FailureEvidence(expected={"kind": "leaf"}, observed={"kind": contract.kind}),
        )
    if contract.memory_mode != "external":
        _refuse(
            "memory-candidate-pair-memory-mode-invalid",
            "memoryMode",
            "memory-candidate acceptance requires an external memory worktree",
            contract_path,
            _FailureEvidence(
                expected={"memoryMode": "external"},
                observed={"memoryMode": contract.memory_mode},
            ),
        )


def _require_candidate_shape(
    contract: WorktreeContract,
    contract_path: Path,
) -> tuple[Path, Path]:
    """Validate admitted object shape before relying on a second filesystem read."""

    _require_external_leaf(contract, contract_path)
    return (
        _required_path(contract.memory_worktree, "memoryRoot", contract_path),
        _required_path(contract.ledger_path, "ledgerPath", contract_path),
    )


def _required_path(value: Path | None, field: str, contract_path: Path) -> Path:
    if value is None:
        _refuse(
            "memory-candidate-pair-field-missing",
            field,
            f"the leaf contract does not identify {field}",
            contract_path,
            _FailureEvidence(
                expected={field: "absolute contract-owned path"},
                observed={field: "missing"},
            ),
        )
    return value.resolve()


def _require_path(path: Path, field: str, *, kind: str, contract_path: Path) -> None:
    present = path.is_dir() if kind == "directory" else path.is_file()
    if not present:
        _refuse(
            "memory-candidate-pair-path-unavailable",
            field,
            f"the contract-owned {field} is not a live {kind}",
            contract_path,
            _FailureEvidence(
                expected={field: path.as_posix(), "kind": kind},
                observed={"state": "missing-or-wrong-kind"},
            ),
        )


def _require_repository_pair(
    contract: WorktreeContract,
    code_root: Path,
    memory_root: Path,
    contract_path: Path,
) -> None:
    code_repository = repository_identity(contract.code_repo_path)
    code_candidate = repository_identity(code_root)
    memory_repository = repository_identity(contract.memory_repo_path)
    memory_candidate = repository_identity(memory_root)
    if code_repository is None or code_candidate != code_repository:
        _refuse(
            "memory-candidate-pair-repository-mismatch",
            "codeRoot",
            "the code worktree does not belong to the contract-owned code repository",
            contract_path,
        )
    if memory_repository is None or memory_candidate != memory_repository:
        _refuse(
            "memory-candidate-pair-repository-mismatch",
            "memoryRoot",
            "the memory worktree does not belong to the contract-owned memory repository",
            contract_path,
        )
    if code_repository == memory_repository:
        _refuse(
            "memory-candidate-pair-repository-mismatch",
            "memoryRoot",
            "external code and memory roots must belong to distinct repositories",
            contract_path,
        )


def _require_branch_plan(
    plan: _BranchPlan,
    *,
    contract_path: Path,
) -> None:
    for suffix, value in (
        ("SourceBranch", plan.source_branch),
        ("WorkBranch", plan.work_branch),
        ("BaseCommit", plan.base_commit),
    ):
        if not value:
            _refuse(
                "memory-candidate-pair-field-missing",
                f"{plan.side}{suffix}",
                f"the contract does not identify the {plan.side} {suffix}",
                contract_path,
            )
    try:
        checked_out = current_branch(plan.worktree)
        source_head = branch_commit(plan.repository, plan.source_branch)
        work_head = branch_commit(plan.repository, plan.work_branch)
    except RuntimeError as exc:
        _refuse(
            "memory-candidate-pair-branch-unreadable",
            f"{plan.side}Branches",
            f"the exact {plan.side} branch plan cannot be resolved",
            contract_path,
            _FailureEvidence(observed={"error": str(exc)}),
        )
    if checked_out != plan.work_branch:
        _refuse(
            "memory-candidate-pair-branch-mismatch",
            f"{plan.side}WorkBranch",
            f"the {plan.side} worktree has a different branch checked out",
            contract_path,
            _FailureEvidence(
                expected={"branch": plan.work_branch},
                observed={"branch": checked_out or "detached"},
            ),
        )
    if source_head not in plan.accepted_source_heads:
        _refuse(
            "memory-candidate-pair-base-stale",
            f"{plan.side}BaseCommit",
            f"the recorded {plan.side} base no longer equals its source branch",
            contract_path,
            _FailureEvidence(
                expected=_expected_source_head(plan),
                observed={"sourceCommit": source_head},
                next_action="worktree_sync",
                next_args={"contract_path": contract_path.as_posix(), "dry_run": True},
            ),
        )
    if not is_ancestor(plan.repository, plan.base_commit, work_head):
        _refuse(
            "memory-candidate-pair-base-contradictory",
            f"{plan.side}BaseCommit",
            f"the recorded {plan.side} base is not an ancestor of its work branch",
            contract_path,
            _FailureEvidence(
                expected={"ancestor": plan.base_commit},
                observed={"workCommit": work_head},
            ),
        )


def _accepted_source_heads(
    base_commit: str,
    integrated_commit: str,
    *,
    integration_completed: bool,
) -> frozenset[str]:
    """Source heads valid for an open leaf or a landed leaf being reclosed."""

    heads = {base_commit}
    if integration_completed and integrated_commit:
        heads.add(integrated_commit)
    return frozenset(heads)


def _expected_source_head(plan: _BranchPlan) -> dict[str, object]:
    if plan.accepted_source_heads == frozenset({plan.base_commit}):
        return {"baseCommit": plan.base_commit}
    return {"sourceCommitOneOf": sorted(plan.accepted_source_heads)}


def _refuse(
    status: str,
    field: str,
    detail: str,
    contract_path: Path,
    evidence: _FailureEvidence = _NO_EVIDENCE,
) -> NoReturn:
    raise MemoryCandidatePairError(
        status,
        detail,
        failure=MemoryCandidatePairFailure(
            field=field,
            contract_path=contract_path.as_posix(),
            expected=evidence.expected,
            observed=evidence.observed,
            next_action=evidence.next_action,
            next_args=evidence.next_args,
        ),
    )


__all__ = ["resolve_memory_candidate_pair"]
