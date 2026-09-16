"""Pure live-evidence classifier for one retained direct-landing generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.mutation_evidence import (
    GitMutationEvidence,
    GitMutationSnapshot,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.mutation_evidence import (
    ephemeral_git_mutation_snapshot,
    snapshot_is_clean,
)
from agents_remember.worktrees.modules.git import branch_commit, local_branch_ref, require_git
from agents_remember.worktrees.worktree_contract import WorktreeContract

DirectRecoveryState = Literal[
    "not-applicable",
    "recoverable",
    "terminalizable",
    "developer-decision",
]


@dataclass(frozen=True)
class DirectLandingRecoveryClassification:
    """Exact read-only decision shared by every public/protected recovery surface."""

    state: DirectRecoveryState
    status: str = ""
    detail: str = ""
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None
    memory_commit: str = ""

    @property
    def mechanically_convergent(self) -> bool:
        return self.state in {"recoverable", "terminalizable"}

    def decision_payload(self) -> dict[str, object]:
        return {
            "state": self.status,
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": self.detail,
            "expected": dict(self.expected or {}),
            "observed": dict(self.observed or {}),
        }


@dataclass(frozen=True)
class _DirectLiveEvidence:
    repository: Path
    git: GitMutationSnapshot
    expected: dict[str, object]
    observed: dict[str, object]


@dataclass(frozen=True)
class _MutationIntentLive:
    repository: Path
    git: GitMutationSnapshot


@dataclass(frozen=True)
class _DirectRecoveryOutputs:
    """Exact durable outputs proven by journal cells or accepted Git intent."""

    memory_commit: str = ""


def classify_direct_landing_recovery(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> DirectLandingRecoveryClassification:
    """Return recoverable only for exact accepted, intended, or proven live evidence."""

    operation_input = record.input
    if record.operationKind != "direct-landing":
        return DirectLandingRecoveryClassification("not-applicable")
    if not isinstance(operation_input, DirectLandingOperationInput):
        return _decision(
            "direct-landing-input-authority-missing",
            "the direct-landing journal does not contain its typed accepted input",
            expected={"operationKind": "direct-landing"},
            observed={"inputKind": getattr(operation_input, "kind", "")},
        )
    expected = _expected_payload(record, operation_input)
    observed: dict[str, object] = {}
    try:
        repository = Path(operation_input.memoryRepository)
        if contract.memory_repo_path is None or repository.resolve() != (
            contract.memory_repo_path.resolve()
        ):
            raise RuntimeError("accepted memory repository differs from live contract authority")
        if (
            operation_input.memoryBranch != contract.memory_work_branch
            or operation_input.memoryRef != local_branch_ref(contract.memory_work_branch)
        ):
            raise RuntimeError("accepted memory ref differs from live contract authority")
        code_head = branch_commit(contract.code_repo_path, contract.code_work_branch)
        code_tree = require_git(contract.code_repo_path, ["rev-parse", f"{code_head}^{{tree}}"])
        if (
            code_head != operation_input.codeCommit
            or code_tree != operation_input.codeTree
            or code_tree != operation_input.candidateTree
        ):
            return _decision(
                "direct-landing-code-evidence-conflict",
                "the accepted code commit or candidate tree changed before recovery",
                expected=expected,
                observed={"codeCommit": code_head, "candidateTree": code_tree},
            )
        live = ephemeral_git_mutation_snapshot(repository, memory_cache=True)
        observed = {"git": live.model_dump(mode="json")}
        live_evidence = _DirectLiveEvidence(
            repository=repository,
            git=live,
            expected=expected,
            observed=observed,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return _decision(
            "direct-landing-evidence-unreadable",
            "the accepted direct-landing evidence cannot be read exactly",
            expected=expected,
            observed={**observed, "errorType": type(exc).__name__},
        )
    return _classify_direct_live_evidence(record, operation_input, live_evidence)


def _classify_direct_live_evidence(
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
    live: _DirectLiveEvidence,
) -> DirectLandingRecoveryClassification:
    outputs = _direct_recovery_outputs(record, operation_input, live)
    if not _memory_state_converges(
        live.repository,
        record,
        operation_input,
        live.git,
        outputs,
    ):
        return _decision(
            "direct-landing-memory-evidence-conflict",
            "live memory Git evidence is outside the accepted or intended generation states",
            expected=live.expected,
            observed=live.observed,
        )
    state: DirectRecoveryState = "terminalizable" if outputs.memory_commit else "recoverable"
    return DirectLandingRecoveryClassification(
        state,
        expected=live.expected,
        observed=live.observed,
        memory_commit=outputs.memory_commit,
    )


def _expected_payload(
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
) -> dict[str, object]:
    return {
        "codeCommit": operation_input.codeCommit,
        "candidateTree": operation_input.candidateTree,
        "memoryAccepted": operation_input.memoryBefore.model_dump(mode="json"),
        "mutationEvidence": {
            leg: evidence.model_dump(mode="json")
            for leg, evidence in sorted(record.mutationEvidence.items())
        },
        "recoveryCommits": (
            record.recoveryCommits.model_dump(mode="json")
            if record.recoveryCommits is not None
            else None
        ),
    }


def _direct_recovery_outputs(
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
    live: _DirectLiveEvidence,
) -> _DirectRecoveryOutputs:
    """Resolve only outputs fixed by durable cells or the accepted mutation lineage."""

    recovery = record.recoveryCommits
    memory_commit = recovery.memoryContentCommit if recovery is not None else ""
    evidence = record.mutationEvidence.get("memory")
    if not memory_commit and evidence is not None:
        if evidence.state == "commit-proven":
            memory_commit = evidence.commit or ""
        elif evidence.state in {"pre-mutation", "reconciled-unchanged"} and snapshot_is_clean(
            operation_input.memoryBefore
        ):
            memory_commit = operation_input.memoryBefore.head
        else:
            memory_commit = _infer_unpublished_memory_commit(record, live)
    return _DirectRecoveryOutputs(memory_commit)


def _infer_unpublished_memory_commit(
    record: LifecycleOperationRecord,
    live: _DirectLiveEvidence,
) -> str:
    evidence = record.mutationEvidence.get("memory")
    if (
        evidence is None
        or evidence.state != "mutation-intent"
        or evidence.observed is not None
        or evidence.before is None
        or live.git.headRef != evidence.before.headRef
        or not snapshot_is_clean(live.git)
    ):
        return ""
    if _memory_commit_matches_intent(live.repository, evidence, live.git.head):
        return live.git.head
    return ""


def _memory_output_matches_evidence(
    repository: Path,
    operation_input: DirectLandingOperationInput,
    evidence: GitMutationEvidence,
    outputs: _DirectRecoveryOutputs,
    live: GitMutationSnapshot,
) -> bool:
    memory_commit = outputs.memory_commit
    if (
        not memory_commit
        or live.head != memory_commit
        or live.headRef != operation_input.memoryBefore.headRef
        or not snapshot_is_clean(live)
    ):
        return False
    if evidence.state in {"pre-mutation", "reconciled-unchanged"}:
        return bool(
            memory_commit == operation_input.memoryBefore.head
            and snapshot_is_clean(operation_input.memoryBefore)
        )
    if evidence.state == "mutation-intent":
        return _memory_commit_matches_intent(repository, evidence, memory_commit)
    return bool(
        evidence.state == "commit-proven"
        and evidence.commit is not None
        and evidence.commit == memory_commit
    )


def _memory_commit_matches_intent(
    repository: Path,
    evidence: GitMutationEvidence,
    commit: str,
) -> bool:
    before = evidence.before
    expected_tree = evidence.expectedOutputTree
    if before is None or expected_tree is None or commit == before.head:
        return False
    try:
        parent = require_git(repository, ["rev-parse", f"{commit}^"])
        tree = require_git(repository, ["rev-parse", f"{commit}^{{tree}}"])
    except RuntimeError:
        return False
    return parent == before.head and tree == expected_tree


def _memory_state_converges(
    repository: Path,
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
    live: GitMutationSnapshot,
    outputs: _DirectRecoveryOutputs,
) -> bool:
    evidence = record.mutationEvidence.get("memory")
    if evidence is None:
        return False
    if outputs.memory_commit:
        return _memory_output_matches_evidence(
            repository,
            operation_input,
            evidence,
            outputs,
            live,
        )
    if evidence.state in {"pre-mutation", "reconciled-unchanged"}:
        return live == operation_input.memoryBefore
    if evidence.state == "mutation-intent":
        return _mutation_intent_converges(
            evidence,
            _MutationIntentLive(repository=repository, git=live),
        )
    return False


def _mutation_intent_converges(
    evidence: GitMutationEvidence,
    live: _MutationIntentLive,
) -> bool:
    before = evidence.before
    if before is None:
        return False
    if live.git == before:
        return True
    if _same_precommit_base(live.git, before):
        return _precommit_intent_converges(
            evidence,
            live,
            before,
        )
    return _committed_intent_converges(
        evidence,
        live,
        before,
    )


def _precommit_intent_converges(
    evidence: GitMutationEvidence,
    live: _MutationIntentLive,
    before: GitMutationSnapshot,
) -> bool:
    expected_tree = evidence.expectedOutputTree
    if expected_tree is None:
        return False
    return bool(
        live.git.candidateTree == expected_tree
        and live.git.indexTree in {before.indexTree, expected_tree}
    )


def _committed_intent_converges(
    evidence: GitMutationEvidence,
    live: _MutationIntentLive,
    before: GitMutationSnapshot,
) -> bool:
    expected_tree = evidence.expectedOutputTree
    if expected_tree is None or live.git.head == before.head:
        return False
    try:
        parent = require_git(live.repository, ["rev-parse", f"{live.git.head}^"])
    except RuntimeError:
        return False
    return (
        live.git.headRef == before.headRef
        and parent == before.head
        and live.git.headTree == expected_tree
        and live.git.indexTree == expected_tree
        and live.git.candidateTree == expected_tree
        and snapshot_is_clean(live.git)
    )


def _same_precommit_base(live: GitMutationSnapshot, before: GitMutationSnapshot) -> bool:
    return bool(
        live.headRef == before.headRef
        and live.head == before.head
        and live.headTree == before.headTree
        and live.refLogFingerprint == before.refLogFingerprint
    )


def _decision(
    status: str,
    detail: str,
    *,
    expected: dict[str, object],
    observed: dict[str, object],
) -> DirectLandingRecoveryClassification:
    return DirectLandingRecoveryClassification(
        "developer-decision",
        status=status,
        detail=detail,
        expected=expected,
        observed=observed,
    )
