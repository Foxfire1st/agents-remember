"""Crash-recoverable memory content publication for direct landing."""

from __future__ import annotations

import tempfile
from pathlib import Path

from agents_remember.kernel.memory_cache import refresh_memory_cache
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.mutation_evidence import GitMutationEvidence
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
    DirectLandingPublicationRefused,
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
from agents_remember.worktrees.knowledge_gate import ClosingReceiptError
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    CommitPublicationRefusal,
    PublicationHooks,
    branch_commit,
    ensure_git_identity,
    head_commit,
    publish_tree_commit,
    stage_tree,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def execute_direct_landing(
    contract: WorktreeContract,
    runtime: DirectLandingRuntime,
) -> dict[str, object]:
    """Recover and finish one accepted direct generation without repeating proof."""
    record = runtime.store.read() or runtime.record
    _require_mechanically_convergent_direct_state(contract, record)
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
    """Execute once; a failure either leaves nothing behind or leaves a typed recovery.

    A generation stays in flight, with a recovery action or a developer decision, only when
    something irreversible has happened or cannot be ruled out (its memory commit is, or may be,
    on the branch). A failure before that (a refusal by the stricter check, a refused or failed
    publication, a memory or code state that changed after admission) cancels the generation and
    restores its preparations (:func:`_refuse_unpublished`); the same request then starts a new
    generation, which judges the memory checkout as it is then.
    """
    try:
        return execute_direct_landing(contract, runtime)
    except DirectLandingPublicationRefused:
        raise
    except DirectLandingError as exc:
        if _published_nothing(runtime):
            raise _refuse_unpublished(runtime, exc) from exc
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
        observed = public_failure_evidence(
            stage="direct-recovery-execution",
            side="direct-landing",
            name="accepted-generation",
            error_type=type(exc).__name__,
            observed={"state": "interrupted"},
        )
        if _published_nothing(runtime):
            raise _refuse_unpublished(
                runtime,
                DirectLandingError(
                    "direct-landing-interrupted-unpublished",
                    f"direct landing was interrupted ({type(exc).__name__})",
                    observed=observed,
                ),
            ) from exc
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


def _published_nothing(runtime: DirectLandingRuntime) -> bool:
    """Whether this generation provably has no memory commit on its branch.

    True when no memory commit is recorded and either no publication was begun (or the one begun
    was proven unchanged), or the one begun left the admitted branch on the admitted tip. A branch
    that names anything else may hold this generation's commit, so that is never called
    unpublished here; the publication's own refusals are, by the primitive's guarantee
    (:func:`_direct_memory_commit`).
    """

    try:
        record = runtime.store.read() or runtime.record
    except RuntimeError:  # an unreadable journal proves nothing
        return False
    evidence = record.mutationEvidence.get("memory")
    recovery = record.recoveryCommits
    if (
        record.status in {"completed", "cancelled"}
        or evidence is None
        or (recovery is not None and recovery.memoryContentCommit)
    ):
        return False
    if evidence.state in {"pre-mutation", "reconciled-unchanged"}:
        return True
    before = evidence.before
    if evidence.state != "mutation-intent" or before is None:
        return False
    try:
        return branch_commit(Path(evidence.repository), before.headRef) == before.head
    except (OSError, RuntimeError):
        return False


def _refuse_unpublished(
    runtime: DirectLandingRuntime, error: DirectLandingError
) -> DirectLandingPublicationRefused:
    """Cancel the generation that published nothing, restore its preparations, name the refusal."""

    detail = (
        f"{error.detail}; nothing was published: this generation was cancelled and its "
        "preparations were restored. Repeat the direct landing once the cause is gone"
    )
    try:
        runtime.cancel_unpublished(status=error.status, detail=detail)
    except ClosingReceiptError as unreadable:
        return DirectLandingPublicationRefused(
            "direct-landing-closing-receipt-unreadable", str(unreadable)
        )
    return DirectLandingPublicationRefused(
        error.status, detail, expected=error.expected, observed=error.observed
    )


def _require_mechanically_convergent_direct_state(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> None:
    """Refuse a generation whose live evidence contradicts it (the caller decides what follows)."""

    classification = classify_direct_landing_recovery(contract, record)
    if classification.state != "developer-decision":
        return
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
        # An attempt resumed from an earlier call may already be on the branch, so its refusal is
        # judged by live evidence (:func:`_published_nothing`) and never cancelled outright.
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
        )
        if intent.before != direct_landing_input(runtime.record).memoryBefore:
            raise DirectLandingError(
                "direct-landing-memory-prestate-changed",
                "memory Git state changed before the commit attempt; nothing lands",
            )
    else:
        raise DirectLandingError(
            "direct-landing-memory-output-ambiguous",
            "memory Git evidence does not prove the accepted output; recover this generation",
        )
    message = direct_landing_input(runtime.record).effectiveInput.memory_content_message(
        code_commit
    )
    committed = _publish_judged_tree(runtime, memory_repo, intent, message)
    prove_git_commit(args, intent, repository=memory_repo, commit=committed)
    return _required_recovery_commit(runtime.store.read(), "memoryContentCommit")


def _publish_judged_tree(
    runtime: DirectLandingRuntime,
    memory_repo: Path,
    intent: GitMutationEvidence,
    message: str,
) -> str:
    """Publish the intended tree, or cancel the generation that provably published nothing.

    Direct landing refuses any change since its judged snapshot, before staging and again from
    staging up to the commit object (the publication's ``confirm``). Such a refusal, like the
    publication's own (the branch moved, ``HEAD`` switched, an unfinished merge), comes before any
    ref moves: the primitive has put the index back, the generation is cancelled and its kept
    closing (history file and ignore rule) restored, so the same request can be made again once
    the cause is gone (review R3, findings 1 and 4).
    """

    assert intent.expectedOutputTree is not None
    assert intent.before is not None
    expected_tree = intent.expectedOutputTree

    def stage() -> str:
        stage_tree(memory_repo, expected_tree)
        return expected_tree

    try:
        _require_prepared_direct_attempt(intent, memory_repo)
        return publish_tree_commit(
            memory_repo,
            message,
            tree=expected_tree,
            before=intent.before,
            hooks=PublicationHooks(
                stage=stage, confirm=lambda: _require_prepared_direct_attempt(intent, memory_repo)
            ),
        )
    except CommitPublicationRefusal as refusal:
        raise _refuse_unpublished(
            runtime,
            DirectLandingError("direct-landing-memory-publication-refused", str(refusal)),
        ) from refusal
    except DirectLandingError as changed:
        raise _refuse_unpublished(runtime, changed) from changed


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
