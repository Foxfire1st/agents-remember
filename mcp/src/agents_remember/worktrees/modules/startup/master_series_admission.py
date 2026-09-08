"""Validate persisted master-series edges and project actionable admission refusal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.activation.atomic_series_activation import bounded_activation_detail
from agents_remember.worktrees.activation.atomic_series_admission import (
    AtomicSeriesAdmissionRequest,
    atomic_series_admission_projection,
)
from agents_remember.worktrees.modules.git import repository_identity, run_git
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.scheduling_mode import TERMINAL_SERIES_CLEANUP
from agents_remember.worktrees.task_resolver import series_contract_path, slugify
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
    worktree_group_for,
)


class MasterSeriesContractSpecLike(Protocol):
    """Identity fields needed to validate one persisted master-series contract."""

    @property
    def coordination_root(self) -> Path: ...

    @property
    def repo_name(self) -> str: ...

    @property
    def code_repo(self) -> Path: ...

    @property
    def memory_root(self) -> Path | None: ...

    @property
    def task_root(self) -> Path: ...

    @property
    def task_name(self) -> str: ...

    @property
    def parent_task_name(self) -> str: ...

    @property
    def protected_branch(self) -> str: ...

    @property
    def workflow_kind(self) -> str: ...


@dataclass(frozen=True)
class MasterSeriesContractAdmissionEvidence:
    """Structured edge evidence carried by a startup admission error."""

    contract_path: Path
    existing_contract: WorktreeContract | None = None
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None


class MasterSeriesContractAdmissionError(RuntimeError):
    """A persisted master-series contract cannot satisfy the requested edge."""

    def __init__(self, status: str, detail: str, evidence: MasterSeriesContractAdmissionEvidence):
        self.status = status
        self.detail = detail
        self.contract_path = evidence.contract_path
        self.existing_contract = evidence.existing_contract
        self.expected = evidence.expected
        self.observed = evidence.observed
        super().__init__(detail)


def memory_mode_for_repository(code_repo: Path, memory_root: Path | None) -> str:
    """Derive the contract vocabulary from the configured repository topology."""

    if memory_root is None:
        return "disabled"
    if memory_root.resolve() == (code_repo / "ar-memory").resolve():
        return "internal"
    return "external"


def _master_series_admission_refusal(
    spec: MasterSeriesContractSpecLike,
    error: MasterSeriesContractAdmissionError,
    *,
    operation: str,
) -> WorktreeCommandResult:
    public_detail = bounded_activation_detail(error.detail)
    assert public_detail is not None
    public_observed = error.observed
    if public_observed is not None:
        observed_detail = public_observed.get("detail")
        if isinstance(observed_detail, str):
            bounded_observed_detail = bounded_activation_detail(observed_detail)
            assert bounded_observed_detail is not None
            public_observed = {**public_observed, "detail": bounded_observed_detail}
    public_summary = bounded_activation_detail(
        f"Master-series startup refused ({error.status}): {public_detail} "
        "Inspect the supplied worktree_status address and correct the named edge "
        "before retrying."
    )
    assert public_summary is not None
    requested_master: TaskDocumentRef | None = None
    repository_root = (spec.coordination_root / "tasks" / spec.repo_name).resolve(strict=False)
    task_path = (spec.task_root / "task.json").resolve(strict=False)
    if task_path.is_relative_to(repository_root):
        requested_master = TaskDocumentRef(
            repository=spec.repo_name,
            path=task_path.relative_to(repository_root).as_posix(),
        )
    admission = atomic_series_admission_projection(
        AtomicSeriesAdmissionRequest(
            operation=operation,
            status=error.status,
            detail=public_detail,
            contract=error.existing_contract,
            requested_master=requested_master,
            requested_contract_path=error.contract_path,
            expected=error.expected,
            observed=public_observed,
        )
    )
    status_action = admission.get("statusAction")
    if not isinstance(status_action, dict):
        status_action = None
    return WorktreeCommandResult(
        2,
        {
            "state": error.status,
            "status": error.status,
            "summary": public_summary,
            "detail": public_detail,
            "contract_path": error.contract_path.as_posix(),
            "retryable": True,
            "nextTool": "worktree_status",
            "nextArgs": status_action.get("args") if status_action is not None else None,
            "admission": admission,
            "retryPrecondition": admission["retryPrecondition"],
            "statusAction": status_action,
        },
    )


def _existing_master_series_contract(
    spec: MasterSeriesContractSpecLike,
) -> WorktreeContract | None:
    path = series_contract_path(spec.task_root)
    if not path.exists():
        return None
    try:
        existing = load_contract(path)
    except ContractError as exc:
        detail = f"parent task contract is not readable: {path}: {exc}"
        raise MasterSeriesContractAdmissionError(
            "atomic-series-contract-unreadable",
            detail,
            MasterSeriesContractAdmissionEvidence(
                contract_path=path,
                expected={"kind": "series", "contractPath": path.as_posix()},
                observed={
                    "errorType": type(exc).__name__,
                    "detail": str(exc),
                    "path": path.as_posix(),
                },
            ),
        ) from exc
    if existing.kind != "series":
        raise MasterSeriesContractAdmissionError(
            "atomic-series-contract-kind-mismatch",
            f"parent task contract is not a series contract: {path}",
            MasterSeriesContractAdmissionEvidence(
                contract_path=path,
                existing_contract=existing,
                expected={"kind": "series", "contractPath": path.as_posix()},
                observed={"kind": existing.kind, "contractPath": path.as_posix()},
            ),
        )
    if existing.cleanup in TERMINAL_SERIES_CLEANUP:
        # Stale terminal artifact (L13-R5b): it no longer owns the lane; the
        # caller's fresh bootstrap replaces it.
        return None
    task_edge = _same_master_task_edge(existing, spec, path)
    repository_edge = _same_master_repository_edge(existing, spec)
    branch_edge = _same_master_branch_edge(existing, spec)
    if not all((task_edge, repository_edge, branch_edge)):
        mismatches = [
            edge
            for edge, matches in (
                ("task", task_edge),
                ("repository", repository_edge),
                ("branch", branch_edge),
            )
            if not matches
        ]
        raise MasterSeriesContractAdmissionError(
            "atomic-series-contract-edge-mismatch",
            "existing master series contract does not match the commanding sprint's "
            f"declared edge(s): {', '.join(mismatches)}",
            MasterSeriesContractAdmissionEvidence(
                contract_path=path,
                existing_contract=existing,
                expected=_master_series_expected_edges(spec, path),
                observed=_master_series_observed_edges(existing),
            ),
        )
    return existing


def _same_master_task_edge(
    existing: WorktreeContract, spec: MasterSeriesContractSpecLike, path: Path
) -> bool:
    expected_task_artifact = spec.task_root / "task.md"
    expected_worktree_group = worktree_group_for(
        spec.coordination_root, spec.repo_name, spec.task_name
    )
    return all(
        (
            existing.task_id == slugify(spec.task_name).upper(),
            existing.task_name == spec.task_name,
            existing.repo_name == spec.repo_name,
            existing.workflow_kind == spec.workflow_kind,
            existing.coordination_root.resolve() == spec.coordination_root.resolve(),
            existing.task_root.resolve() == spec.task_root.resolve(),
            existing.contract_path.resolve() == path.resolve(),
            existing.task_artifact.resolve() == expected_task_artifact.resolve(),
            existing.worktree_group.resolve() == expected_worktree_group.resolve(),
            existing.parent_task_name == spec.parent_task_name,
            existing.parent_contract_path is None,
            existing.leaf_id == "",
            existing.lifecycle_id == "",
        )
    )


def _same_master_repository_edge(
    existing: WorktreeContract, spec: MasterSeriesContractSpecLike
) -> bool:
    expected_memory_mode = memory_mode_for_repository(spec.code_repo, spec.memory_root)
    expected_memory_repo = spec.memory_root if expected_memory_mode == "external" else None
    return all(
        (
            _same_repository_root(existing.code_repo_path, spec.code_repo),
            _same_repository_root(existing.code_worktree, spec.code_repo),
            existing.memory_mode == expected_memory_mode,
            _same_optional_repository_root(existing.memory_repo_path, expected_memory_repo),
            _same_series_memory_edge(
                existing.ledger_path,
                existing.memory_repo_path,
                existing.memory_worktree,
            ),
        )
    )


def _same_master_branch_edge(
    existing: WorktreeContract, spec: MasterSeriesContractSpecLike
) -> bool:
    expected_branch = f"ar/{slugify(spec.task_name)}"
    external_memory = existing.memory_mode == "external"
    return all(
        (
            existing.code_source_branch == spec.protected_branch,
            existing.code_work_branch == expected_branch,
            existing.memory_source_branch == (spec.protected_branch if external_memory else ""),
            existing.memory_work_branch == (expected_branch if external_memory else ""),
        )
    )


def _master_series_expected_edges(
    spec: MasterSeriesContractSpecLike,
    path: Path,
) -> dict[str, object]:
    expected_memory_mode = memory_mode_for_repository(spec.code_repo, spec.memory_root)
    expected_memory_repo = spec.memory_root if expected_memory_mode == "external" else None
    expected_branch = f"ar/{slugify(spec.task_name)}"
    expected_task_artifact = spec.task_root / "task.md"
    expected_worktree_group = worktree_group_for(
        spec.coordination_root, spec.repo_name, spec.task_name
    )
    return {
        "taskEdge": {
            "taskId": slugify(spec.task_name).upper(),
            "taskName": spec.task_name,
            "repository": spec.repo_name,
            "workflowKind": spec.workflow_kind,
            "coordinationRoot": spec.coordination_root.resolve().as_posix(),
            "taskRoot": spec.task_root.resolve().as_posix(),
            "contractPath": path.resolve().as_posix(),
            "taskArtifact": expected_task_artifact.resolve().as_posix(),
            "worktreeGroup": expected_worktree_group.resolve().as_posix(),
            "parentTaskName": spec.parent_task_name,
            "parentContractPath": None,
            "leafId": "",
            "lifecycleId": "",
        },
        "repositoryEdge": {
            "codeRepository": spec.code_repo.resolve().as_posix(),
            "memoryMode": expected_memory_mode,
            "memoryRepository": (
                expected_memory_repo.resolve().as_posix()
                if expected_memory_repo is not None
                else None
            ),
            "memoryEdge": "memory.md at the selected memory repository/worktree root",
        },
        "branchEdge": {
            "codeSourceBranch": spec.protected_branch,
            "codeWorkBranch": expected_branch,
            "memorySourceBranch": spec.protected_branch
            if expected_memory_mode == "external"
            else "",
            "memoryWorkBranch": expected_branch if expected_memory_mode == "external" else "",
        },
    }


def _master_series_observed_edges(existing: WorktreeContract) -> dict[str, object]:
    return {
        "taskEdge": {
            "taskId": existing.task_id,
            "taskName": existing.task_name,
            "repository": existing.repo_name,
            "workflowKind": existing.workflow_kind,
            "coordinationRoot": existing.coordination_root.resolve().as_posix(),
            "taskRoot": existing.task_root.resolve().as_posix(),
            "contractPath": existing.contract_path.resolve().as_posix(),
            "taskArtifact": existing.task_artifact.resolve().as_posix(),
            "worktreeGroup": existing.worktree_group.resolve().as_posix(),
            "parentTaskName": existing.parent_task_name,
            "parentContractPath": (
                existing.parent_contract_path.resolve().as_posix()
                if existing.parent_contract_path is not None
                else None
            ),
            "leafId": existing.leaf_id,
            "lifecycleId": existing.lifecycle_id,
        },
        "repositoryEdge": {
            "codeRepository": existing.code_repo_path.resolve().as_posix(),
            "codeWorktree": existing.code_worktree.resolve().as_posix(),
            "memoryMode": existing.memory_mode,
            "memoryRepository": (
                existing.memory_repo_path.resolve().as_posix()
                if existing.memory_repo_path is not None
                else None
            ),
            "memoryWorktree": (
                existing.memory_worktree.resolve().as_posix()
                if existing.memory_worktree is not None
                else None
            ),
            "ledgerPath": (
                existing.ledger_path.resolve().as_posix()
                if existing.ledger_path is not None
                else None
            ),
        },
        "branchEdge": {
            "codeSourceBranch": existing.code_source_branch,
            "codeWorkBranch": existing.code_work_branch,
            "memorySourceBranch": existing.memory_source_branch,
            "memoryWorkBranch": existing.memory_work_branch,
        },
    }


def _repository_root(path: Path | None) -> Path | None:
    if path is None or not path.is_dir():
        return None
    result = run_git(path, ["rev-parse", "--show-toplevel"])
    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        return None
    root = Path(output).resolve()
    return root if path.resolve() == root else None


def _same_repository_root(left: Path | None, right: Path | None) -> bool:
    if _repository_root(left) is None or _repository_root(right) is None:
        return False
    left_identity = repository_identity(left)
    right_identity = repository_identity(right)
    return left_identity is not None and left_identity == right_identity


def _same_optional_repository_root(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return _same_repository_root(left, right)


def _same_series_memory_edge(
    ledger: Path | None,
    memory_repo: Path | None,
    memory_worktree: Path | None,
) -> bool:
    if ledger is None or memory_repo is None:
        return ledger is None and memory_repo is None and memory_worktree is None
    repository_root = _repository_root(memory_repo)
    if repository_root is None:
        return False
    authority_root = repository_root
    if memory_worktree is not None:
        worktree_root = _repository_root(memory_worktree)
        if worktree_root is None or not _same_repository_root(memory_worktree, memory_repo):
            return False
        authority_root = worktree_root
    return ledger.resolve() == (authority_root / "memory.md").resolve()
