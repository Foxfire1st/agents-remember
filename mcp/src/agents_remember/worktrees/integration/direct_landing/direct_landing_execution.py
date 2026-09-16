"""Crash-recoverable memory content publication for direct landing."""

from __future__ import annotations

import tempfile
from pathlib import Path

from agents_remember.kernel.memory_cache import refresh_memory_cache
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.mutation_evidence import GitMutationEvidence
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.memory_content_excludes import (
    MEMORY_CONTENT_EXCLUDES,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    DirectLandingRuntime,
    reconcile_direct_landing,
    reset_reconciled_attempt,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_recovery_state import (
    DirectLandingRecoveryClassification,
    classify_direct_landing_recovery,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    git_mutation_snapshot,
    prove_git_commit,
    snapshot_is_clean,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    commit_if_dirty,
    ensure_git_identity,
    head_commit,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def execute_direct_landing(
    contract: WorktreeContract,
    runtime: DirectLandingRuntime,
) -> dict[str, object]:
    """Recover and finish one accepted direct generation without repeating proof."""
    record = runtime.store.read() or runtime.record
    _require_mechanically_convergent_direct_state(contract, runtime, record)
    record = reconcile_direct_landing(contract, runtime.store)
    runtime.record = record
    operation_input = direct_landing_input(record)
    memory_repo = Path(operation_input.memoryRepository)
    ensure_git_identity(memory_repo)
    args = WorktreeArgs(
        contract_path=contract.contract_path,
        closeout_input=operation_input.effectiveInput,
        operation_key=record.operationKey,
        operation_progress=runtime.progress,
        recovery_commits=record.recoveryCommits,
    )
    memory_commit = _direct_memory_commit(
        runtime,
        args,
        memory_repo,
        code_commit=operation_input.codeCommit,
    )
    result = _direct_landing_result(contract, operation_input, memory_commit)
    result["ledgerCache"] = refresh_memory_cache(
        memory_repo,
        memory_commit,
        path=contract.ledger_path,
        repo_name=contract.repo_name,
    )
    runtime.finish(result)
    return result


def execute_or_require_direct_landing_recovery(
    contract: WorktreeContract,
    runtime: DirectLandingRuntime,
) -> dict[str, object]:
    """Execute once and persist a typed recovery requirement after ambiguity."""
    try:
        return execute_direct_landing(contract, runtime)
    except DirectLandingError as exc:
        classification = classify_direct_landing_recovery(
            contract,
            runtime.store.read() or runtime.record,
        )
        if classification.state == "developer-decision":
            _persist_direct_decision(runtime, classification)
            raise DirectLandingError(
                classification.status,
                classification.detail,
                expected=classification.expected,
                observed=classification.observed,
            ) from exc
        runtime.require_input(
            status=exc.status,
            detail=exc.detail,
            expected=exc.expected,
            observed=exc.observed,
        )
        raise
    except (OSError, RuntimeError) as exc:
        classification = classify_direct_landing_recovery(
            contract,
            runtime.store.read() or runtime.record,
        )
        if classification.state == "developer-decision":
            _persist_direct_decision(runtime, classification)
            raise DirectLandingError(
                classification.status,
                classification.detail,
                expected=classification.expected,
                observed=classification.observed,
            ) from exc
        observed = public_failure_evidence(
            stage="direct-recovery-execution",
            side="direct-landing",
            name="accepted-generation",
            error_type=type(exc).__name__,
            observed={"state": "interrupted"},
        )
        detail = "direct landing was interrupted and requires same-generation recovery"
        runtime.require_input(
            status="direct-landing-recovery-required",
            detail=detail,
            observed=observed,
        )
        raise DirectLandingError(
            "direct-landing-recovery-required",
            detail,
            observed=observed,
        ) from exc


def _require_mechanically_convergent_direct_state(
    contract: WorktreeContract,
    runtime: DirectLandingRuntime,
    record: LifecycleOperationRecord,
) -> None:
    classification = classify_direct_landing_recovery(contract, record)
    if classification.state != "developer-decision":
        return
    _persist_direct_decision(runtime, classification)
    raise DirectLandingError(
        classification.status,
        classification.detail,
        expected=classification.expected,
        observed=classification.observed,
    )


def _persist_direct_decision(
    runtime: DirectLandingRuntime,
    classification: DirectLandingRecoveryClassification,
) -> None:
    runtime.require_input(
        status=classification.status,
        detail=classification.detail,
        expected=classification.expected,
        observed=classification.observed,
        developer_decision=True,
    )


def direct_landing_input(record: LifecycleOperationRecord) -> DirectLandingOperationInput:
    value = record.input
    if not isinstance(value, DirectLandingOperationInput):
        raise RuntimeError("direct landing journal contains another operation kind")
    return value


def _direct_memory_commit(
    runtime: DirectLandingRuntime,
    args: WorktreeArgs,
    memory_repo: Path,
    *,
    code_commit: str,
) -> str:
    """Create the memory-content commit, attributed to the verified code commit.

    Direct landing is the branch-addressed closeout route, so its memory content is the
    memory side of the same pairing and carries the same one ``Code-Commit:`` trailer. The
    trailer is written into the object at creation for the reason it is everywhere else:
    ``prove_git_commit`` below journals this exact commit, and a later append would have to
    rewrite it.
    """

    evidence = runtime.record.mutationEvidence["memory"]
    if evidence.state == "commit-proven":
        assert evidence.commit is not None
        return evidence.commit
    recovery = runtime.record.recoveryCommits
    if recovery is not None and recovery.memoryContentCommit:
        return recovery.memoryContentCommit
    if evidence.state == "reconciled-unchanged":
        runtime.record = reset_reconciled_attempt(runtime.store, leg="memory")
        evidence = runtime.record.mutationEvidence["memory"]
    if evidence.state == "mutation-intent":
        _require_prepared_direct_attempt(evidence, memory_repo)
        intent = evidence
    elif evidence.state == "pre-mutation":
        _require_accepted_memory_prestate(runtime, memory_repo)
        if snapshot_is_clean(direct_landing_input(runtime.record).memoryBefore):
            memory_commit = head_commit(memory_repo)
            runtime.progress(
                "direct-memory-commit",
                {
                    "current_command": "record verified-existing memory content",
                    "recovery_commits": _recovery_payload(
                        runtime.record, memory_commit=memory_commit
                    ),
                },
            )
            return memory_commit
        intent = begin_git_mutation(
            args,
            leg="memory",
            repository=memory_repo,
            expected_output_tree=direct_landing_input(runtime.record).memoryBefore.candidateTree,
            use_current_candidate=True,
        )
    else:
        raise DirectLandingError(
            "direct-landing-memory-output-ambiguous",
            "memory Git evidence does not prove the accepted output; recover this generation",
        )
    committed = commit_if_dirty(
        memory_repo,
        direct_landing_input(runtime.record).effectiveInput.memory_content_message(code_commit),
        exclude_paths=MEMORY_CONTENT_EXCLUDES,
    )
    prove_git_commit(args, intent, repository=memory_repo, commit=committed)
    return _required_recovery_commit(runtime.store.read(), "memoryContentCommit")


def _require_accepted_memory_prestate(
    runtime: DirectLandingRuntime,
    memory_repo: Path,
) -> None:
    accepted = direct_landing_input(runtime.record)
    observed = git_mutation_snapshot(
        memory_repo,
        runtime.contract.worktree_group / "reports" / ".direct-admission.index",
        memory_cache=True,
    )
    if observed != accepted.memoryBefore:
        raise DirectLandingError(
            "direct-landing-memory-prestate-changed",
            "memory Git state changed after admission; recover or revise the accepted generation",
            expected=accepted.memoryBefore.model_dump(mode="json"),
            observed=observed.model_dump(mode="json"),
        )


def _require_prepared_direct_attempt(
    evidence: GitMutationEvidence,
    repository: Path,
) -> None:
    before = evidence.before
    expected_tree = evidence.expectedOutputTree
    with tempfile.TemporaryDirectory(prefix="ar-direct-recovery-") as temp_dir:
        observed = git_mutation_snapshot(
            repository,
            Path(temp_dir) / f"{evidence.leg}.index",
            memory_cache=True,
        )
    if (
        before is None
        or expected_tree is None
        or observed.headRef != before.headRef
        or observed.head != before.head
        or observed.headTree != before.headTree
        or observed.indexTree not in {before.indexTree, expected_tree}
        or observed.candidateTree != expected_tree
    ):
        raise DirectLandingError(
            f"direct-landing-{evidence.leg}-output-ambiguous",
            "live Git evidence does not prove a prepared, uncommitted accepted output",
        )
    if Path(evidence.repository).resolve() != repository.resolve():
        raise DirectLandingError(
            f"direct-landing-{evidence.leg}-repository-changed",
            "the accepted direct-landing repository identity changed",
        )


def _recovery_payload(
    record: LifecycleOperationRecord,
    *,
    memory_commit: str | None = None,
) -> dict[str, str]:
    current = record.recoveryCommits
    if current is None:
        raise RuntimeError("direct landing has no accepted code recovery commit")
    return {
        "codeCommit": current.codeCommit,
        "memoryContentCommit": memory_commit or current.memoryContentCommit,
    }


def _required_recovery_commit(
    record: LifecycleOperationRecord | None,
    field: str,
) -> str:
    if record is None or record.recoveryCommits is None:
        raise RuntimeError("direct landing did not publish its proven recovery commit")
    value = getattr(record.recoveryCommits, field)
    if not value:
        raise RuntimeError("direct landing recovery projection omitted a proven commit")
    return value


def _direct_landing_result(
    contract: WorktreeContract,
    operation_input: DirectLandingOperationInput,
    memory_commit: str,
) -> dict[str, object]:
    return {
        "ok": True,
        "operation": "direct_landing",
        "state": "landed",
        "summary": "Direct landing: code commit verified and memory content published "
        "or reused on the series memory branch.",
        "contractPath": contract.contract_path.as_posix(),
        "codeCommit": operation_input.codeCommit,
        "memoryContentCommit": memory_commit,
        "dryRun": False,
        "memory": {
            "memoryMode": "external",
            "memoryBranch": operation_input.memoryBranch,
            "memoryHead": memory_commit,
        },
        "effectiveInput": operation_input.effectiveInput.model_dump(mode="json"),
    }
