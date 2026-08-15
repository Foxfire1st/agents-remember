"""Exact code, memory, ledger, lineage, and route evidence for queue candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import find_mapping, load_ledger
from agents_remember.models.closeout_queue import EvidenceFact, RouteReviewFact
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.leaf_doc import TerminalLeafResolutionError, resolve_terminal_leaf_doc
from agents_remember.worktrees.modules.git import head_commit, is_ancestor, worktree_candidate_tree
from agents_remember.worktrees.route_review import RouteReviewError, require_current_route_review
from agents_remember.worktrees.source_lineage import require_current_source_lineage
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

from .closeout_queue_errors import CloseoutQueueError


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
        head_commit(contract.code_repo_path, contract.code_source_branch)
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
            head_commit(contract.memory_repo_path, contract.memory_source_branch)
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


def require_atomic_master_landed(master: ResolvedTaskDocument) -> None:
    """Prove the completed atomic series contract has landed on both super source refs."""

    try:
        contract = load_contract(master.path.parent / "series-contract.md")
        code_landed = _atomic_code_landed(contract)
        memory_landed = _atomic_memory_landed(contract)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CloseoutQueueError(
            "atomic-barrier-master-landing-unproven",
            f"atomic master has no valid exact landing contract: {exc}",
        ) from exc
    if not all(
        (
            _atomic_contract_matches_master(contract, master),
            _atomic_finalization_is_exact(contract),
            _atomic_landing_changes_content(contract),
            code_landed,
            memory_landed,
        )
    ):
        raise CloseoutQueueError(
            "atomic-barrier-master-landing-unproven",
            "atomic master completion does not prove one exact code-and-memory landing onto super",
        )


def _atomic_code_landed(contract: WorktreeContract) -> bool:
    return bool(contract.integrated_code_commit) and is_ancestor(
        contract.code_repo_path,
        contract.integrated_code_commit,
        head_commit(contract.code_repo_path, contract.code_source_branch),
    )


def _atomic_memory_landed(contract: WorktreeContract) -> bool:
    if contract.memory_mode != "external":
        return True
    if contract.memory_repo_path is None:
        raise ValueError("external atomic series has no memory repository")
    mapping = find_mapping(
        load_ledger(contract.memory_repo_path / "memory.md"),
        contract.integrated_code_commit,
    )
    return (
        bool(contract.integrated_memory_content_commit and contract.integrated_ledger_commit)
        and is_ancestor(
            contract.memory_repo_path,
            contract.integrated_memory_content_commit,
            contract.integrated_ledger_commit,
        )
        and is_ancestor(
            contract.memory_repo_path,
            contract.integrated_ledger_commit,
            head_commit(contract.memory_repo_path, contract.memory_source_branch),
        )
        and mapping is not None
        and mapping.memory_commit == contract.integrated_memory_content_commit
    )


def _atomic_contract_matches_master(
    contract: WorktreeContract, master: ResolvedTaskDocument
) -> bool:
    return (
        contract.kind == "series"
        and contract.task_root.resolve() == master.path.parent.resolve()
        and contract.integration_status == "completed"
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
