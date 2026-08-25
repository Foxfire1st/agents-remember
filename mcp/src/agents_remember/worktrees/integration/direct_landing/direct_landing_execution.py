"""Crash-recoverable memory and ledger execution for direct landing."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    find_mapping,
    ledger_to_text,
    load_ledger,
    parse_ledger_text,
    prepend_mapping,
    write_ledger,
)
from agents_remember.models.lifecycles.direct_landing import (
    DirectLandingLedgerIntent,
    DirectLandingOperationInput,
)
from agents_remember.models.lifecycles.mutation_evidence import (
    GitMutationEvidence,
    GitMutationSnapshot,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
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
    bind_expected_output_tree,
    git_mutation_snapshot,
    prove_git_commit,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.git import (
    commit_if_dirty,
    ensure_git_identity,
    head_commit,
    is_ancestor,
    require_git,
    worktree_dirty,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class _LedgerExecution:
    memory_repo: Path
    ledger_path: Path
    code_commit: str
    memory_commit: str


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
    memory_commit = _direct_memory_commit(runtime, args, memory_repo)
    ledger_commit = _direct_ledger_commit(
        runtime,
        args,
        _LedgerExecution(
            memory_repo,
            Path(operation_input.ledgerPath),
            operation_input.codeCommit,
            memory_commit,
        ),
    )
    if not is_ancestor(memory_repo, memory_commit, ledger_commit):
        raise DirectLandingError(
            "direct-landing-ledger-unreachable",
            "the committed ledger row does not reach the memory content commit",
        )
    result = _direct_landing_result(contract, operation_input, memory_commit, ledger_commit)
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
) -> str:
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
        _require_prepared_direct_attempt(evidence, memory_repo, expected_file=None)
        intent = evidence
    elif evidence.state == "pre-mutation":
        _require_accepted_memory_prestate(runtime, memory_repo)
        if not worktree_dirty(memory_repo):
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
        direct_landing_input(runtime.record).effectiveInput.message_for("memory"),
    )
    prove_git_commit(args, intent, repository=memory_repo, commit=committed)
    return _required_recovery_commit(runtime.store.read(), "memoryContentCommit")


def _direct_ledger_commit(
    runtime: DirectLandingRuntime,
    args: WorktreeArgs,
    facts: _LedgerExecution,
) -> str:
    record = runtime.store.read()
    if record is None:
        raise RuntimeError("direct landing journal disappeared")
    runtime.record = record
    evidence = record.mutationEvidence["ledger"]
    if evidence.state == "commit-proven":
        assert evidence.commit is not None
        return evidence.commit
    recovery = record.recoveryCommits
    if recovery is not None and recovery.ledgerCommit:
        return recovery.ledgerCommit
    current_text = _read_ledger_text(facts.ledger_path)
    ledger = _load_direct_ledger(facts.ledger_path)
    intent = record.directLandingLedgerIntent
    if intent is None:
        head_text, head_ledger = _head_ledger(facts)
        existing_commit = _existing_direct_mapping(
            runtime,
            facts,
            find_mapping(head_ledger, facts.code_commit),
            current_text=current_text,
            head_text=head_text,
        )
        if existing_commit is not None:
            return existing_commit
        intent = _prepare_ledger_intent(runtime, facts, ledger, current_text)
        evidence = runtime.record.mutationEvidence["ledger"]
    elif current_text not in {intent.beforeText, intent.intendedText}:
        _raise_direct_input_required(
            runtime,
            "direct-landing-ledger-changed",
            "ledger bytes contradict the durable direct-landing ledger intent",
        )
    if evidence.state == "reconciled-unchanged":
        runtime.record = reset_reconciled_attempt(runtime.store, leg="ledger")
        evidence = runtime.record.mutationEvidence["ledger"]
    evidence = _prepare_or_resume_ledger_mutation(args, facts, intent, evidence)
    if evidence.state == "commit-proven":
        assert evidence.commit is not None
        return evidence.commit
    committed = commit_if_dirty(
        facts.memory_repo,
        direct_landing_input(runtime.record).effectiveInput.message_for("ledger"),
    )
    prove_git_commit(args, evidence, repository=facts.memory_repo, commit=committed)
    return _required_recovery_commit(runtime.store.read(), "ledgerCommit")


def _prepare_or_resume_ledger_mutation(
    args: WorktreeArgs,
    facts: _LedgerExecution,
    intent: DirectLandingLedgerIntent,
    evidence: GitMutationEvidence,
) -> GitMutationEvidence:
    if evidence.state == "pre-mutation":
        evidence = begin_git_mutation(
            args,
            leg="ledger",
            repository=facts.memory_repo,
            expected_output_tree=None,
        )
        write_ledger(facts.ledger_path, parse_ledger_text(intent.intendedText))
        require_git(
            facts.memory_repo,
            ["add", _ledger_relative_path(facts.memory_repo, facts.ledger_path)],
        )
        return bind_expected_output_tree(args, evidence, repository=facts.memory_repo)
    if evidence.state == "mutation-intent":
        if evidence.expectedOutputTree is None:
            return _resume_unbound_ledger_intent(args, facts, intent, evidence)
        _require_prepared_direct_attempt(
            evidence,
            facts.memory_repo,
            expected_file=(facts.ledger_path, intent.intendedText),
        )
        return evidence
    raise DirectLandingError(
        "direct-landing-ledger-output-ambiguous",
        "ledger Git evidence does not prove the accepted output; recover this generation",
    )


def _existing_direct_mapping(
    runtime,
    facts: _LedgerExecution,
    existing,
    *,
    current_text: str,
    head_text: str,
) -> str | None:
    if existing is None:
        return None
    if existing.memory_commit != facts.memory_commit:
        _raise_direct_input_required(
            runtime,
            "direct-landing-ledger-conflict",
            f"ledger maps the accepted code commit to {existing.memory_commit}, "
            f"not the proven memory commit {facts.memory_commit}",
            expected={"memoryCommit": facts.memory_commit},
            observed={"memoryCommit": existing.memory_commit},
        )
    if current_text != head_text:
        _raise_direct_input_required(
            runtime,
            "direct-landing-ledger-bytes-ambiguous",
            "the working ledger differs from the exact branch HEAD ledger blob",
            expected={"headLedgerSha256": _text_sha256(head_text)},
            observed={"workingLedgerSha256": _text_sha256(current_text)},
        )
    ledger_commit = head_commit(facts.memory_repo)
    snapshot = git_mutation_snapshot(
        facts.memory_repo,
        runtime.contract.worktree_group / "reports" / ".direct-existing-ledger.index",
    )
    if snapshot.head != ledger_commit or not _snapshot_is_clean(snapshot):
        _raise_direct_input_required(
            runtime,
            "direct-landing-ledger-repository-dirty",
            "existing ledger mapping can be reused only from an exact clean branch HEAD",
            expected={"head": ledger_commit, "repositoryState": "clean"},
            observed=snapshot.model_dump(mode="json"),
        )
    runtime.progress(
        "direct-ledger-commit",
        {
            "current_command": "record verified-existing ledger mapping",
            "recovery_commits": _recovery_payload(
                runtime.record,
                memory_commit=facts.memory_commit,
                ledger_commit=ledger_commit,
            ),
        },
    )
    return ledger_commit


def _require_accepted_memory_prestate(
    runtime: DirectLandingRuntime,
    memory_repo: Path,
) -> None:
    accepted = direct_landing_input(runtime.record)
    observed = git_mutation_snapshot(
        memory_repo,
        runtime.contract.worktree_group / "reports" / ".direct-admission.index",
    )
    if observed != accepted.memoryBefore:
        raise DirectLandingError(
            "direct-landing-memory-prestate-changed",
            "memory Git state changed after admission; recover or revise the accepted generation",
            expected=accepted.memoryBefore.model_dump(mode="json"),
            observed=observed.model_dump(mode="json"),
        )


def _resume_unbound_ledger_intent(
    args: WorktreeArgs,
    facts: _LedgerExecution,
    intent: DirectLandingLedgerIntent,
    evidence: GitMutationEvidence,
) -> GitMutationEvidence:
    before = evidence.before
    if before is None:
        raise DirectLandingError(
            "direct-landing-ledger-output-ambiguous",
            "ledger mutation intent has no accepted pre-command Git evidence",
        )
    current = git_mutation_snapshot(
        facts.memory_repo,
        _ledger_evidence_index(args),
    )
    current_text = _read_ledger_text(facts.ledger_path)
    if current.headRef != before.headRef or current_text not in {
        intent.beforeText,
        intent.intendedText,
    }:
        raise DirectLandingError(
            "direct-landing-ledger-output-ambiguous",
            "ledger intent cannot bind because the ref or intended bytes changed",
            expected={
                "headRef": before.headRef,
                "ledgerSha256": [intent.beforeSha256, intent.intendedSha256],
            },
            observed={
                "headRef": current.headRef,
                "ledgerSha256": _text_sha256(current_text),
            },
        )
    if current.head == before.head:
        if current_text == intent.beforeText:
            if current != before:
                raise DirectLandingError(
                    "direct-landing-ledger-output-ambiguous",
                    "ledger Git state drifted before the journaled write began",
                    expected=before.model_dump(mode="json"),
                    observed=current.model_dump(mode="json"),
                )
            write_ledger(facts.ledger_path, parse_ledger_text(intent.intendedText))
        _require_only_ledger_change(facts)
        require_git(
            facts.memory_repo,
            ["add", _ledger_relative_path(facts.memory_repo, facts.ledger_path)],
        )
        return bind_expected_output_tree(args, evidence, repository=facts.memory_repo)
    return _prove_advanced_ledger_commit(args, facts, intent, evidence, current)


def _prove_advanced_ledger_commit(
    args: WorktreeArgs,
    facts: _LedgerExecution,
    intent: DirectLandingLedgerIntent,
    evidence: GitMutationEvidence,
    current: GitMutationSnapshot,
) -> GitMutationEvidence:
    before = evidence.before
    assert before is not None
    _require_single_clean_ledger_commit(facts, before, current)
    head_text, head_ledger = _head_ledger(facts, commit=current.head)
    _require_intended_ledger_mapping(facts, intent, head_text, head_ledger)
    return _proven_ledger_evidence(args, facts, evidence, current)


def _require_single_clean_ledger_commit(
    facts: _LedgerExecution,
    before: GitMutationSnapshot,
    current: GitMutationSnapshot,
) -> None:
    observed = (
        require_git(facts.memory_repo, ["rev-parse", f"{current.head}^"]),
        current.indexTree,
        current.candidateTree,
        current.statusFingerprint,
    )
    expected = (
        before.head,
        current.headTree,
        current.headTree,
        hashlib.sha256(b"").hexdigest(),
    )
    if observed != expected:
        raise DirectLandingError(
            "direct-landing-ledger-output-ambiguous",
            "ledger ref movement is not the one clean commit attributable to this intent",
            expected={
                "parent": before.head,
                "headRef": before.headRef,
                "repositoryState": "clean",
            },
            observed=current.model_dump(mode="json"),
        )


def _require_intended_ledger_mapping(
    facts: _LedgerExecution,
    intent: DirectLandingLedgerIntent,
    head_text: str,
    head_ledger,
) -> None:
    mapping = find_mapping(head_ledger, facts.code_commit)
    observed = (head_text, mapping.memory_commit if mapping is not None else None)
    expected = (intent.intendedText, facts.memory_commit)
    if observed != expected:
        raise DirectLandingError(
            "direct-landing-ledger-output-ambiguous",
            "the advanced ledger commit does not contain the exact intended mapping",
            expected={
                "ledgerSha256": intent.intendedSha256,
                "memoryCommit": facts.memory_commit,
            },
            observed={
                "ledgerSha256": _text_sha256(head_text),
                "memoryCommit": mapping.memory_commit if mapping is not None else "",
            },
        )


def _proven_ledger_evidence(
    args: WorktreeArgs,
    facts: _LedgerExecution,
    evidence: GitMutationEvidence,
    current: GitMutationSnapshot,
) -> GitMutationEvidence:
    rebound = evidence.model_copy(update={"expectedOutputTree": current.headTree})
    prove_git_commit(
        args,
        rebound,
        repository=facts.memory_repo,
        commit=current.head,
    )
    return rebound.model_copy(
        update={"state": "commit-proven", "observed": current, "commit": current.head}
    )


def _require_only_ledger_change(facts: _LedgerExecution) -> None:
    result = run_git(facts.memory_repo, ["status", "--porcelain=v1", "-z"])
    if result.returncode != 0:
        raise DirectLandingError(
            "direct-landing-ledger-status-unreadable",
            "direct landing cannot read the prepared ledger Git state",
            observed=public_failure_evidence(
                stage="direct-ledger-git-read",
                side="ledger",
                name="status",
                error_type="GitCommandError",
                observed={"state": "unreadable"},
            ),
        )
    entries = [item for item in result.stdout.split("\0") if item]
    paths = {item[3:] for item in entries if len(item) >= 4}
    expected = _ledger_relative_path(facts.memory_repo, facts.ledger_path)
    if not entries or paths != {expected}:
        raise DirectLandingError(
            "direct-landing-ledger-output-ambiguous",
            "prepared ledger recovery contains changes outside the exact ledger file",
        )


def _head_ledger(
    facts: _LedgerExecution,
    *,
    commit: str = "HEAD",
):
    relative = _ledger_relative_path(facts.memory_repo, facts.ledger_path)
    result = run_git(facts.memory_repo, ["show", f"{commit}:{relative}"])
    if result.returncode != 0:
        raise DirectLandingError(
            "direct-landing-ledger-head-invalid",
            "direct landing cannot read the exact branch ledger blob",
            observed=public_failure_evidence(
                stage="direct-ledger-git-read",
                side="ledger",
                name="head-blob",
                error_type="GitCommandError",
                observed={"state": "unreadable"},
            ),
        )
    try:
        return result.stdout, parse_ledger_text(result.stdout)
    except LedgerError as exc:
        raise DirectLandingError(
            "direct-landing-ledger-head-invalid",
            "the exact branch ledger blob is invalid",
            observed=public_failure_evidence(
                stage="direct-ledger-parse",
                side="ledger",
                name="head-blob",
                error_type=type(exc).__name__,
                observed={"state": "invalid"},
            ),
        ) from exc


def _prepare_ledger_intent(runtime, facts: _LedgerExecution, ledger, current_text: str):
    accepted = direct_landing_input(runtime.record)
    current = git_mutation_snapshot(
        facts.memory_repo,
        runtime.contract.worktree_group / "reports" / ".direct-ledger-preflight.index",
    )
    head_text, _ = _head_ledger(facts, commit=current.head)
    if (
        current_text != accepted.ledgerBeforeText
        or head_text != accepted.ledgerBeforeText
        or current.head != facts.memory_commit
        or not _snapshot_is_clean(current)
    ):
        _raise_direct_input_required(
            runtime,
            "direct-landing-ledger-changed",
            "memory repository drifted between proven memory output and ledger intent",
            expected={
                "head": facts.memory_commit,
                "ledgerSha256": accepted.ledgerBeforeSha256,
                "repositoryState": "clean",
            },
            observed={
                **current.model_dump(mode="json"),
                "workingLedgerSha256": _text_sha256(current_text),
                "headLedgerSha256": _text_sha256(head_text),
            },
        )
    intended = ledger_to_text(prepend_mapping(ledger, facts.code_commit, facts.memory_commit))
    intent = DirectLandingLedgerIntent(
        codeCommit=facts.code_commit,
        memoryCommit=facts.memory_commit,
        beforeText=current_text,
        beforeSha256=_text_sha256(current_text),
        intendedText=intended,
        intendedSha256=_text_sha256(intended),
    )
    runtime.publish_ledger_intent(intent)
    return intent


def _require_prepared_direct_attempt(
    evidence: GitMutationEvidence,
    repository: Path,
    *,
    expected_file: tuple[Path, str] | None,
) -> None:
    before = evidence.before
    expected_tree = evidence.expectedOutputTree
    with tempfile.TemporaryDirectory(prefix="ar-direct-recovery-") as temp_dir:
        observed = git_mutation_snapshot(
            repository,
            Path(temp_dir) / f"{evidence.leg}.index",
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
    if expected_file is not None:
        path, intended = expected_file
        if _read_ledger_text(path) != intended:
            raise DirectLandingError(
                "direct-landing-ledger-bytes-ambiguous",
                "prepared Git tree exists but ledger bytes differ from durable intent",
            )


def _recovery_payload(
    record: LifecycleOperationRecord,
    *,
    memory_commit: str | None = None,
    ledger_commit: str | None = None,
) -> dict[str, str]:
    current = record.recoveryCommits
    if current is None:
        raise RuntimeError("direct landing has no accepted code recovery commit")
    return {
        "codeCommit": current.codeCommit,
        "memoryContentCommit": memory_commit or current.memoryContentCommit,
        "ledgerCommit": ledger_commit or current.ledgerCommit,
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


def _raise_direct_input_required(
    runtime: DirectLandingRuntime,
    status: str,
    detail: str,
    *,
    expected: dict[str, object] | None = None,
    observed: dict[str, object] | None = None,
) -> None:
    runtime.require_input(
        status=status,
        detail=detail,
        expected=expected,
        observed=observed,
    )
    raise DirectLandingError(
        status,
        detail,
        expected=expected,
        observed=observed,
    )


def _direct_landing_result(
    contract: WorktreeContract,
    operation_input: DirectLandingOperationInput,
    memory_commit: str,
    ledger_commit: str,
) -> dict[str, object]:
    return {
        "ok": True,
        "operation": "direct_landing",
        "state": "landed",
        "summary": "Direct landing: code commit verified and the memory + ledger "
        "commits landed on the series memory branch.",
        "contractPath": contract.contract_path.as_posix(),
        "codeCommit": operation_input.codeCommit,
        "memoryContentCommit": memory_commit,
        "ledgerCommit": ledger_commit,
        "dryRun": False,
        "memory": {
            "memoryMode": "external",
            "memoryBranch": operation_input.memoryBranch,
            "memoryHead": ledger_commit,
        },
        "effectiveInput": operation_input.effectiveInput.model_dump(mode="json"),
    }


def _load_direct_ledger(path: Path):
    try:
        return load_ledger(path)
    except (LedgerError, OSError) as exc:
        raise DirectLandingError(
            "direct-landing-ledger-invalid",
            "direct landing cannot parse the accepted ledger",
            observed=public_failure_evidence(
                stage="direct-ledger-read",
                side="ledger",
                name=path.name,
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        ) from exc


def _read_ledger_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DirectLandingError(
            "direct-landing-ledger-unreadable",
            "direct landing cannot read the accepted ledger bytes",
            observed=public_failure_evidence(
                stage="direct-ledger-read",
                side="ledger",
                name=path.name,
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        ) from exc


def _ledger_relative_path(repository: Path, ledger_path: Path) -> str:
    try:
        return ledger_path.resolve().relative_to(repository.resolve()).as_posix()
    except ValueError as exc:
        raise DirectLandingError(
            "direct-landing-ledger-authority-missing",
            "ledger path is outside the accepted memory repository",
        ) from exc


def _ledger_evidence_index(args: WorktreeArgs) -> Path:
    contract_path = args.contract_path
    if contract_path is None:
        raise RuntimeError("direct landing ledger recovery has no contract authority")
    return contract_path.parent / "reports" / ".ledger-mutation-evidence.index"


def _snapshot_is_clean(snapshot) -> bool:
    return (
        snapshot.indexTree == snapshot.headTree
        and snapshot.candidateTree == snapshot.headTree
        and snapshot.statusFingerprint == hashlib.sha256(b"").hexdigest()
    )


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
