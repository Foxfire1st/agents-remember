"""Exact code, memory, ledger, lineage, and route evidence for queue candidates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import find_mapping, load_ledger
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.closeout_queue import EvidenceFact, RouteReviewFact
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationOperationAuthority,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.leaf_doc import TerminalLeafResolutionError, resolve_terminal_leaf_doc
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.git import (
    branch_commit,
    is_ancestor,
    repository_identity,
    worktree_candidate_tree,
)
from agents_remember.worktrees.modules.start_contract import memory_mode_for_repository
from agents_remember.worktrees.named_ref_memory import load_named_ref_ledger
from agents_remember.worktrees.route_review import RouteReviewError, require_current_route_review
from agents_remember.worktrees.source_lineage import require_current_source_lineage
from agents_remember.worktrees.task_resolver import series_contract_path, slugify
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

from .closeout_queue_errors import CloseoutQueueError


@dataclass(frozen=True)
class AtomicMasterLandingAuthority:
    """Configured task/ref authority required to release one atomic sprint blocker."""

    coordination_root: Path
    repo_name: str
    sprint_ref: TaskDocumentRef
    source_branch: str
    code_repository: Path
    memory_mode: str
    memory_repository: Path | None


def atomic_master_landing_authority(
    config: McpRuntimeConfig, sprint: ResolvedTaskDocument
) -> AtomicMasterLandingAuthority:
    """Resolve configured repositories and the canonical sprint-super edge fail closed."""

    configured = config.repositories.get(sprint.ref.repository)
    source_branch = sprint.document.integrationBranch
    if configured is None or not source_branch:
        raise CloseoutQueueError(
            "atomic-blocker-authority-unavailable",
            "atomic blocker release requires a configured repository and sprint super",
        )
    code_repository = repository_identity(configured.path)
    memory_mode = memory_mode_for_repository(configured.path, configured.memory_root)
    memory_repository = (
        repository_identity(configured.memory_root) if memory_mode == "external" else None
    )
    if code_repository is None or (memory_mode == "external" and memory_repository is None):
        raise CloseoutQueueError(
            "atomic-blocker-authority-unavailable",
            "atomic blocker release cannot resolve configured Git repository identity",
        )
    return AtomicMasterLandingAuthority(
        coordination_root=config.coordination_root.resolve(),
        repo_name=sprint.ref.repository,
        sprint_ref=sprint.ref,
        source_branch=source_branch,
        code_repository=code_repository,
        memory_mode=memory_mode,
        memory_repository=memory_repository,
    )


def route_review_fact(contract: WorktreeContract) -> RouteReviewFact:
    """Bind the full canonical review record and every referenced evidence file."""

    summary = require_current_route_review(contract)
    found = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    review = found[1].routeReview if found is not None else None
    if bool(summary.get("required")):
        if review is None:
            raise CloseoutQueueError(
                "closeout-candidate-route-review-missing",
                "current route-review summary has no canonical full record",
            )
        record_payload = review.model_dump(mode="json")
        refs = {review.verdictRef, *(route.evidenceRef for route in review.routes)}
        evidence = [_task_evidence(contract.task_root, ref) for ref in sorted(refs)]
    else:
        record_payload = summary
        evidence = []
    digest = hashlib.sha256(
        json.dumps(record_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return RouteReviewFact.model_validate({**summary, "recordSha256": digest, "evidence": evidence})


def route_review_blockers(contract: WorktreeContract, expected: RouteReviewFact) -> list[str]:
    """Compare the complete route-review fact without hiding a malformed current record."""

    try:
        current = route_review_fact(contract)
    except (
        CloseoutQueueError,
        OSError,
        RouteReviewError,
        RuntimeError,
        TerminalLeafResolutionError,
        ValidationError,
        ValueError,
    ) as exc:
        return [f"route-review-invalid: {exc}"]
    return [] if current == expected else ["route-review-stale"]


def require_source_bases_current(contract: WorktreeContract) -> None:
    """Require both transitive ancestry and the exact immediate source heads."""

    try:
        require_current_source_lineage(contract, operation="closeout queue declaration")
    except RuntimeError as exc:
        raise CloseoutQueueError("closeout-candidate-source-lineage-stale", str(exc)) from exc
    if (
        branch_commit(contract.code_repo_path, contract.code_source_branch)
        != contract.code_base_commit
    ):
        raise CloseoutQueueError(
            "closeout-candidate-code-source-moved", "code source moved after leaf start"
        )
    if contract.memory_mode == "external":
        if contract.memory_repo_path is None or not contract.memory_base_commit:
            raise CloseoutQueueError(
                "closeout-candidate-memory-source-missing", "external memory base is incomplete"
            )
        if (
            branch_commit(contract.memory_repo_path, contract.memory_source_branch)
            != contract.memory_base_commit
        ):
            raise CloseoutQueueError(
                "closeout-candidate-memory-source-moved", "memory source moved after leaf start"
            )


def ledger_mapping(contract: WorktreeContract) -> str | None:
    """Return the exact source-code to source-memory ledger edge when applicable."""

    if contract.memory_mode != "external":
        return None
    if contract.ledger_path is None:
        raise CloseoutQueueError(
            "closeout-candidate-ledger-missing", "external-memory contract has no ledger path"
        )
    row = find_mapping(load_ledger(contract.ledger_path), contract.code_base_commit)
    if row is None:
        raise CloseoutQueueError(
            "closeout-candidate-ledger-incompatible",
            f"ledger does not map code base {contract.code_base_commit}",
        )
    return row.memory_commit


def memory_candidate_tree(contract: WorktreeContract) -> str | None:
    """Hash the exact external-memory worktree candidate, or mark memory not applicable."""

    if contract.memory_worktree is None:
        return None
    return worktree_candidate_tree(
        contract.memory_worktree,
        contract.worktree_group / "reports" / ".closeout-queue-memory.index",
    )


def commit_tree(repo: Path, commit: str) -> str:
    """Resolve an exact commit tree or refuse a missing commit."""

    result = run_git(repo, ["rev-parse", f"{commit}^{{tree}}"])
    if result.returncode != 0:
        raise CloseoutQueueError(
            "closeout-candidate-commit-missing",
            result.stderr.strip() or f"cannot resolve commit tree {commit}",
        )
    return result.stdout.strip()


def operation_owner_fingerprint(operation_key: str) -> str:
    """Derive the one-way owner proof persisted by queue state and WAL records."""

    return hashlib.sha256(f"closeout-queue-owner:{operation_key}".encode()).hexdigest()


def require_atomic_master_landed(
    master: ResolvedTaskDocument,
    authority: AtomicMasterLandingAuthority,
) -> None:
    """Prove the completed atomic series contract has landed on both super source refs."""

    try:
        contract = load_contract(series_contract_path(master.path.parent))
    except (OSError, RuntimeError, ValueError) as exc:
        raise CloseoutQueueError(
            "atomic-blocker-master-landing-unproven",
            f"atomic master has no valid exact landing contract: {exc}",
        ) from exc
    if not _atomic_contract_matches_master(contract, master, authority):
        raise CloseoutQueueError(
            "atomic-blocker-master-landing-unproven",
            "atomic master completion does not prove one exact code-and-memory landing onto super",
        )
    try:
        code_landed = _atomic_code_landed(contract)
        memory_landed = _atomic_memory_landed(contract)
        operation_landed = _atomic_operation_landed(contract, authority)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CloseoutQueueError(
            "atomic-blocker-master-landing-unproven",
            f"atomic master has no valid exact landing contract: {exc}",
        ) from exc
    if not all(
        (
            _atomic_finalization_is_exact(contract),
            _atomic_landing_changes_content(contract),
            code_landed,
            memory_landed,
            operation_landed,
        )
    ):
        raise CloseoutQueueError(
            "atomic-blocker-master-landing-unproven",
            "atomic master completion does not prove one exact code-and-memory landing onto super",
        )


def _atomic_code_landed(contract: WorktreeContract) -> bool:
    return bool(contract.integrated_code_commit) and (
        branch_commit(contract.code_repo_path, contract.code_source_branch)
        == contract.integrated_code_commit
    )


def _atomic_memory_landed(contract: WorktreeContract) -> bool:
    if contract.memory_mode != "external":
        return True
    if contract.memory_repo_path is None:
        raise ValueError("external atomic series has no memory repository")
    mapping = find_mapping(
        load_named_ref_ledger(
            contract.memory_repo_path,
            contract.memory_source_branch,
        ),
        contract.integrated_code_commit,
    )
    return (
        bool(contract.integrated_memory_content_commit and contract.integrated_ledger_commit)
        and is_ancestor(
            contract.memory_repo_path,
            contract.integrated_memory_content_commit,
            contract.integrated_ledger_commit,
        )
        and branch_commit(contract.memory_repo_path, contract.memory_source_branch)
        == contract.integrated_ledger_commit
        and mapping is not None
        and mapping.memory_commit == contract.integrated_memory_content_commit
    )


def _atomic_contract_matches_master(
    contract: WorktreeContract,
    master: ResolvedTaskDocument,
    authority: AtomicMasterLandingAuthority,
) -> bool:
    expected_root = master.path.parent.resolve()
    expected_work_branch = f"ar/{slugify(master.path.parent.name)}"
    expected_sprint_name = Path(authority.sprint_ref.path).parent.name
    code_identity = repository_identity(contract.code_repo_path)
    memory_identity = repository_identity(contract.memory_repo_path)
    return (
        contract.kind == "series"
        and authority.sprint_ref.repository == authority.repo_name
        and contract.repo_name == authority.repo_name
        and contract.coordination_root.resolve() == authority.coordination_root
        and contract.task_root.resolve() == expected_root
        and contract.contract_path.resolve() == series_contract_path(expected_root).resolve()
        and contract.worktree_group.resolve() == (expected_root / "enclosures").resolve()
        and contract.code_source_branch == authority.source_branch
        and contract.code_work_branch == expected_work_branch
        and contract.parent_task_name == expected_sprint_name
        and code_identity == authority.code_repository
        and contract.memory_mode == authority.memory_mode
        and _atomic_memory_authority_matches(
            contract,
            authority,
            expected_work_branch=expected_work_branch,
            memory_identity=memory_identity,
        )
        and contract.integration_status == "completed"
    )


def _atomic_memory_authority_matches(
    contract: WorktreeContract,
    authority: AtomicMasterLandingAuthority,
    *,
    expected_work_branch: str,
    memory_identity: Path | None,
) -> bool:
    if authority.memory_mode != "external":
        return (
            contract.memory_repo_path is None
            and not contract.memory_source_branch
            and not contract.memory_work_branch
            and authority.memory_repository is None
        )
    return (
        memory_identity is not None
        and memory_identity == authority.memory_repository
        and contract.memory_source_branch == authority.source_branch
        and contract.memory_work_branch == expected_work_branch
    )


def _atomic_operation_landed(
    contract: WorktreeContract,
    authority: AtomicMasterLandingAuthority,
) -> bool:
    record = LifecycleOperationStore(
        operation_record_path(contract.worktree_group, "integrate")
    ).read()
    if record is None or not isinstance(record.input, IntegrateOperationInput):
        return False
    journal = record.integrationAuthority
    recovery = record.recoveryCommits
    if journal is None or recovery is None or not isinstance(record.result, dict):
        return False
    record_facts = (
        record.operationKind,
        record.status,
        record.phase,
        record.irreversibleBoundaryEntered,
        record.contractPath,
        record.input.contractPath,
    )
    expected_record_facts = (
        "integrate",
        "completed",
        "completed",
        True,
        contract.contract_path.as_posix(),
        contract.contract_path.as_posix(),
    )
    return (
        record_facts == expected_record_facts
        and _atomic_operation_authority_matches(journal, contract, authority)
        and _atomic_recovery_matches(recovery, contract)
        and _atomic_result_matches(record.result, contract)
    )


def _atomic_operation_authority_matches(
    journal: IntegrationOperationAuthority,
    contract: WorktreeContract,
    authority: AtomicMasterLandingAuthority,
) -> bool:
    external = authority.memory_mode == "external"
    memory_repository = (
        authority.memory_repository.as_posix()
        if external and authority.memory_repository is not None
        else ""
    )
    memory_branch = authority.source_branch if external else ""
    memory_ref = f"refs/heads/{memory_branch}" if external else ""
    found = (
        journal.conflictTransaction,
        journal.targetKind,
        journal.codeRepository,
        journal.codeSourceBranch,
        journal.codeSourceRef,
        journal.codeSourceCommit,
        journal.codeCandidateCommit,
        journal.memoryRepository,
        journal.memorySourceBranch,
        journal.memorySourceRef,
        journal.memorySourceCommit,
        journal.memoryContentCommit,
        journal.ledgerCommit,
    )
    expected = (
        None,
        "sprint-super",
        authority.code_repository.as_posix(),
        authority.source_branch,
        f"refs/heads/{authority.source_branch}",
        contract.code_base_commit,
        contract.integrated_code_commit,
        memory_repository,
        memory_branch,
        memory_ref,
        contract.memory_base_commit if external else "",
        contract.integrated_memory_content_commit,
        contract.integrated_ledger_commit,
    )
    return found == expected


def _atomic_recovery_matches(
    recovery: LifecycleOperationRecoveryCommits,
    contract: WorktreeContract,
) -> bool:
    return (
        recovery.codeCommit,
        recovery.memoryContentCommit,
        recovery.ledgerCommit,
    ) == (
        contract.integrated_code_commit,
        contract.integrated_memory_content_commit,
        contract.integrated_ledger_commit,
    )


def _atomic_result_matches(result: dict[str, object], contract: WorktreeContract) -> bool:
    if result.get("state") not in {"integrated", "already-integrated"}:
        return False
    return (
        result.get("ok"),
        result.get("operation"),
        result.get("integrated_code_commit"),
        result.get("integrated_memory_content_commit"),
        result.get("integrated_ledger_commit"),
    ) == (
        True,
        "worktree_integrate",
        contract.integrated_code_commit,
        contract.integrated_memory_content_commit,
        contract.integrated_ledger_commit,
    )


def _atomic_finalization_is_exact(contract: WorktreeContract) -> bool:
    return (
        contract.human_review_status == "approved"
        and contract.approved_for_commit
        and contract.closeout_status == "completed"
        and bool(contract.code_commit)
        and contract.integrated_code_commit == contract.code_commit
        and (
            contract.memory_mode != "external"
            or (
                bool(contract.memory_content_commit and contract.ledger_commit)
                and contract.integrated_memory_content_commit == contract.memory_content_commit
                and contract.integrated_ledger_commit == contract.ledger_commit
            )
        )
    )


def _atomic_landing_changes_content(contract: WorktreeContract) -> bool:
    return contract.integrated_code_commit != contract.code_base_commit or (
        contract.memory_mode == "external"
        and contract.integrated_ledger_commit != contract.memory_base_commit
    )


def _task_evidence(task_root: Path, ref: str) -> EvidenceFact:
    supplied = Path(ref)
    root = task_root.resolve()
    resolved = (root / supplied).resolve(strict=False)
    if supplied.is_absolute() or not resolved.is_relative_to(root) or not resolved.is_file():
        raise CloseoutQueueError(
            "closeout-candidate-route-evidence-invalid",
            f"route-review evidence is not a task-relative file: {ref}",
        )
    try:
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        raise CloseoutQueueError(
            "closeout-candidate-route-evidence-invalid",
            f"route-review evidence cannot be read: {ref}",
        ) from exc
    return EvidenceFact(path=supplied.as_posix(), sha256=digest)
