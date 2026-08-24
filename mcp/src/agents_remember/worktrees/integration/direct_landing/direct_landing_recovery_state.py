"""Pure live-evidence classifier for one retained direct-landing generation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerError,
    find_unique_mapping,
    ledger_to_text,
    parse_ledger_text,
    prepend_mapping,
)
from agents_remember.models.lifecycles.direct_landing import (
    DirectLandingLedgerIntent,
    DirectLandingOperationInput,
)
from agents_remember.models.lifecycles.mutation_evidence import (
    GitMutationEvidence,
    GitMutationSnapshot,
)
from agents_remember.models.lifecycles.operation import (
    LifecycleOperationRecord,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    ephemeral_git_mutation_snapshot,
)
from agents_remember.worktrees.modules.git import is_ancestor, require_git
from agents_remember.worktrees.worktree_contract import WorktreeContract

DirectRecoveryState = Literal[
    "not-applicable",
    "recoverable",
    "terminalizable",
    "developer-decision",
]

DirectLedgerMappingState = Literal["not-applicable", "absent", "exact", "conflict"]


@dataclass(frozen=True)
class DirectLandingRecoveryClassification:
    """Exact read-only decision shared by every public/protected recovery surface."""

    state: DirectRecoveryState
    status: str = ""
    detail: str = ""
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None
    memory_commit: str = ""
    ledger_commit: str = ""

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
    relative: str
    git: GitMutationSnapshot
    ledger_text: str
    expected: dict[str, object]
    observed: dict[str, object]


@dataclass(frozen=True)
class _MutationIntentLive:
    repository: Path
    git: GitMutationSnapshot
    expected_path: str | None = None
    accepted_text: str = ""
    intended_text: str = ""
    live_text: str = ""


@dataclass(frozen=True)
class _DirectRecoveryOutputs:
    """Exact durable outputs proven by journal cells or accepted Git intent."""

    memory_commit: str = ""
    ledger_commit: str = ""


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
        ledger_path = Path(operation_input.ledgerPath)
        if contract.ledger_path is None or ledger_path.resolve() != contract.ledger_path.resolve():
            raise RuntimeError("accepted ledger path differs from live contract authority")
        relative = ledger_path.resolve().relative_to(repository.resolve()).as_posix()
        live = ephemeral_git_mutation_snapshot(repository)
        ledger_text = ledger_path.read_text(encoding="utf-8")
        observed = {
            "git": live.model_dump(mode="json"),
            "ledgerSha256": _sha256(ledger_text),
        }
        live_evidence = _DirectLiveEvidence(
            repository=repository,
            relative=relative,
            git=live,
            ledger_text=ledger_text,
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
    mapping_state, mapping_observed = _direct_ledger_mapping_state(
        record,
        operation_input,
        live.ledger_text,
        outputs.memory_commit,
    )
    live.observed["ledgerMapping"] = mapping_observed
    if mapping_state == "conflict":
        return _mapping_conflict(operation_input, live, outputs)
    if not _ledger_state_converges(record, operation_input, live, outputs):
        return _decision(
            "direct-landing-ledger-evidence-conflict",
            "live ledger bytes/ref evidence is outside the accepted or intended states",
            expected=live.expected,
            observed=live.observed,
        )
    state: DirectRecoveryState = (
        "terminalizable"
        if mapping_state == "exact" or bool(outputs.memory_commit and outputs.ledger_commit)
        else "recoverable"
    )
    return DirectLandingRecoveryClassification(
        state,
        expected=live.expected,
        observed=live.observed,
        memory_commit=outputs.memory_commit,
        ledger_commit=outputs.ledger_commit,
    )


def _mapping_conflict(
    operation_input: DirectLandingOperationInput,
    live: _DirectLiveEvidence,
    outputs: _DirectRecoveryOutputs,
) -> DirectLandingRecoveryClassification:
    return _decision(
        "direct-landing-ledger-mapping-conflict",
        "the accepted code mapping is different, duplicate, or unreadable",
        expected={
            **live.expected,
            "ledgerMapping": {
                "codeCommit": operation_input.codeCommit,
                "memoryCommit": outputs.memory_commit,
            },
        },
        observed=live.observed,
    )


def _expected_payload(
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
) -> dict[str, object]:
    intent = record.directLandingLedgerIntent
    return {
        "memoryAccepted": operation_input.memoryBefore.model_dump(mode="json"),
        "ledgerAcceptedSha256": operation_input.ledgerBeforeSha256,
        "ledgerIntendedSha256": intent.intendedSha256 if intent is not None else "",
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


def _direct_ledger_mapping_state(
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
    ledger_text: str,
    memory_commit: str,
) -> tuple[DirectLedgerMappingState, dict[str, object]]:
    """Classify an existing immutable mapping only after memory proof and before intent."""

    if record.directLandingLedgerIntent is not None or not memory_commit:
        return "not-applicable", {"state": "not-applicable"}
    try:
        mapping = find_unique_mapping(parse_ledger_text(ledger_text), operation_input.codeCommit)
    except LedgerError:
        return "conflict", {"state": "unreadable", "errorType": "LedgerError"}
    if mapping is None:
        return "absent", {"state": "absent", "codeCommit": operation_input.codeCommit}
    observed: dict[str, object] = {
        "state": "exact" if mapping.memory_commit == memory_commit else "different",
        "codeCommit": mapping.code_commit,
        "memoryCommit": mapping.memory_commit,
    }
    return (
        "exact" if mapping.memory_commit == memory_commit else "conflict",
        observed,
    )


def _direct_recovery_outputs(
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
    live: _DirectLiveEvidence,
) -> _DirectRecoveryOutputs:
    """Resolve only outputs fixed by durable cells or the accepted mutation lineage."""

    recovery = record.recoveryCommits
    memory_commit = recovery.memoryContentCommit if recovery is not None else ""
    ledger_commit = recovery.ledgerCommit if recovery is not None else ""
    if not memory_commit and record.directLandingLedgerIntent is None:
        memory_commit = _infer_unpublished_memory_commit(record, operation_input, live)
    if (
        memory_commit
        and not ledger_commit
        and record.directLandingLedgerIntent is None
        and _exact_ledger_output_commit(
            operation_input,
            live,
            memory_commit,
            live.git.head,
        )
    ):
        ledger_commit = live.git.head
    return _DirectRecoveryOutputs(memory_commit, ledger_commit)


def _infer_unpublished_memory_commit(
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
    live: _DirectLiveEvidence,
) -> str:
    evidence = record.mutationEvidence.get("memory")
    if (
        evidence is None
        or evidence.state != "mutation-intent"
        or evidence.observed is not None
        or evidence.before is None
        or live.git.headRef != evidence.before.headRef
        or not _snapshot_is_clean(live.git)
    ):
        return ""
    if _memory_commit_matches_intent(live.repository, evidence, live.git.head):
        return live.git.head
    try:
        mapping = find_unique_mapping(
            parse_ledger_text(live.ledger_text),
            operation_input.codeCommit,
        )
    except LedgerError:
        return ""
    if mapping is None or not _memory_commit_matches_intent(
        live.repository,
        evidence,
        mapping.memory_commit,
    ):
        return ""
    if not _exact_ledger_output_commit(
        operation_input,
        live,
        mapping.memory_commit,
        live.git.head,
    ):
        return ""
    return mapping.memory_commit


def _memory_output_matches_evidence(
    repository: Path,
    operation_input: DirectLandingOperationInput,
    evidence: GitMutationEvidence,
    outputs: _DirectRecoveryOutputs,
    live: GitMutationSnapshot,
) -> bool:
    memory_commit = outputs.memory_commit
    expected_live_head = outputs.ledger_commit or memory_commit
    if (
        not memory_commit
        or live.head != expected_live_head
        or live.headRef != operation_input.memoryBefore.headRef
        or (outputs.ledger_commit and not _snapshot_is_clean(live))
    ):
        return False
    if evidence.state in {"pre-mutation", "reconciled-unchanged"}:
        return bool(
            memory_commit == operation_input.memoryBefore.head
            and _snapshot_is_clean(operation_input.memoryBefore)
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


def _exact_ledger_output_commit(
    operation_input: DirectLandingOperationInput,
    live: _DirectLiveEvidence,
    memory_commit: str,
    ledger_commit: str,
) -> bool:
    if (
        not memory_commit
        or not ledger_commit
        or ledger_commit == memory_commit
        or live.git.head != ledger_commit
        or live.git.headRef != operation_input.memoryBefore.headRef
        or not _snapshot_is_clean(live.git)
    ):
        return False
    intended_text = _deterministic_ledger_text(operation_input, memory_commit)
    if intended_text is None or live.ledger_text != intended_text:
        return False
    try:
        parent = require_git(live.repository, ["rev-parse", f"{ledger_commit}^"])
        return bool(
            parent == memory_commit
            and _git_blob_text(live.repository, memory_commit, live.relative)
            == operation_input.ledgerBeforeText
            and _git_blob_text(live.repository, ledger_commit, live.relative) == intended_text
            and _commit_changed_paths(live.repository, ledger_commit) == {live.relative}
        )
    except RuntimeError:
        return False


def _deterministic_ledger_text(
    operation_input: DirectLandingOperationInput,
    memory_commit: str,
) -> str | None:
    try:
        return ledger_to_text(
            prepend_mapping(
                parse_ledger_text(operation_input.ledgerBeforeText),
                operation_input.codeCommit,
                memory_commit,
            )
        )
    except LedgerError:
        return None


def _commit_changed_paths(repository: Path, commit: str) -> set[str]:
    result = run_git(
        repository,
        ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", commit],
    )
    if result.returncode != 0:
        raise RuntimeError("could not inspect direct ledger commit paths")
    return {item for item in result.stdout.split("\0") if item}


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
    if outputs.memory_commit and (outputs.ledger_commit or live.head == outputs.memory_commit):
        return _memory_output_matches_evidence(
            repository,
            operation_input,
            evidence,
            outputs,
            live,
        )
    recovery = record.recoveryCommits
    if evidence.state in {"pre-mutation", "reconciled-unchanged"}:
        return _memory_prestate_converges(record, operation_input, recovery, live)
    if evidence.state == "mutation-intent":
        return _mutation_intent_converges(
            evidence,
            _MutationIntentLive(repository=repository, git=live),
        )
    return _memory_commit_proof_converges(repository, record, evidence, recovery, live)


def _memory_prestate_converges(
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
    recovery: LifecycleOperationRecoveryCommits | None,
    live: GitMutationSnapshot,
) -> bool:
    if recovery is None or not recovery.memoryContentCommit:
        return live == operation_input.memoryBefore
    if live.head != recovery.memoryContentCommit:
        return False
    ledger = record.mutationEvidence.get("ledger")
    return _snapshot_is_clean(live) or bool(
        ledger is not None and ledger.state == "mutation-intent"
    )


def _memory_commit_proof_converges(
    repository: Path,
    record: LifecycleOperationRecord,
    evidence: GitMutationEvidence,
    recovery: LifecycleOperationRecoveryCommits | None,
    live: GitMutationSnapshot,
) -> bool:
    if (
        evidence.state != "commit-proven"
        or evidence.commit is None
        or recovery is None
        or recovery.memoryContentCommit != evidence.commit
    ):
        return False
    if live.head == evidence.commit:
        return True
    ledger = record.mutationEvidence.get("ledger")
    if ledger is None or ledger.state not in {"mutation-intent", "commit-proven"}:
        return False
    if ledger.state == "commit-proven" and ledger.commit == live.head:
        return is_ancestor(repository, evidence.commit, live.head)
    before = ledger.before
    return bool(before is not None and before.head == evidence.commit)


def _ledger_state_converges(
    record: LifecycleOperationRecord,
    operation_input: DirectLandingOperationInput,
    live: _DirectLiveEvidence,
    outputs: _DirectRecoveryOutputs,
) -> bool:
    evidence = record.mutationEvidence.get("ledger")
    if evidence is None:
        return False
    intent = record.directLandingLedgerIntent
    recovery = record.recoveryCommits
    if intent is None:
        return _ledger_without_intent_converges(operation_input, live, outputs)
    if recovery is None or not _ledger_intent_matches_recovery(record):
        return False
    return _journaled_ledger_state_converges(evidence, intent, recovery, live)


def _journaled_ledger_state_converges(
    evidence: GitMutationEvidence,
    intent: DirectLandingLedgerIntent,
    recovery: LifecycleOperationRecoveryCommits,
    live: _DirectLiveEvidence,
) -> bool:
    if evidence.state in {"pre-mutation", "reconciled-unchanged"}:
        return bool(
            live.git.head == intent.memoryCommit
            and _snapshot_is_clean(live.git)
            and live.ledger_text == intent.beforeText
            and _git_blob_text(live.repository, live.git.head, live.relative) == intent.beforeText
        )
    if evidence.state == "mutation-intent":
        return _mutation_intent_converges(
            evidence,
            _MutationIntentLive(
                repository=live.repository,
                git=live.git,
                expected_path=live.relative,
                accepted_text=intent.beforeText,
                intended_text=intent.intendedText,
                live_text=live.ledger_text,
            ),
        )
    if evidence.state != "commit-proven" or evidence.commit is None:
        return False
    return bool(
        recovery.ledgerCommit == evidence.commit
        and live.git.head == evidence.commit
        and _snapshot_is_clean(live.git)
        and live.ledger_text == intent.intendedText
        and _git_blob_text(live.repository, live.git.head, live.relative) == intent.intendedText
    )


def _ledger_without_intent_converges(
    operation_input: DirectLandingOperationInput,
    live: _DirectLiveEvidence,
    outputs: _DirectRecoveryOutputs,
) -> bool:
    if outputs.ledger_commit:
        return bool(
            outputs.memory_commit
            and live.git.head == outputs.ledger_commit
            and _exact_ledger_output_commit(
                operation_input,
                live,
                outputs.memory_commit,
                outputs.ledger_commit,
            )
        )
    if live.ledger_text != operation_input.ledgerBeforeText:
        return False
    if outputs.memory_commit:
        return bool(
            live.git.head == outputs.memory_commit
            and _snapshot_is_clean(live.git)
            and _git_blob_text(live.repository, live.git.head, live.relative) == live.ledger_text
        )
    return live.git == operation_input.memoryBefore


def _ledger_intent_matches_recovery(record: LifecycleOperationRecord) -> bool:
    intent = record.directLandingLedgerIntent
    recovery = record.recoveryCommits
    return bool(
        intent is not None
        and recovery is not None
        and intent.codeCommit == recovery.codeCommit
        and intent.memoryCommit == recovery.memoryContentCommit
        and _sha256(intent.beforeText) == intent.beforeSha256
        and _sha256(intent.intendedText) == intent.intendedSha256
    )


def _mutation_intent_converges(
    evidence: GitMutationEvidence,
    live: _MutationIntentLive,
) -> bool:
    before = evidence.before
    if before is None:
        return False
    if live.git == before and (live.expected_path is None or live.live_text == live.accepted_text):
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
    if live.expected_path is not None and (
        live.live_text != live.intended_text
        or _changed_paths(live.repository) != {live.expected_path}
    ):
        return False
    expected_tree = evidence.expectedOutputTree
    if expected_tree is None:
        return bool(
            live.expected_path is not None
            and live.git.indexTree in {before.indexTree, live.git.candidateTree}
        )
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
    if not (
        live.git.headRef == before.headRef
        and parent == before.head
        and live.git.headTree == expected_tree
        and live.git.indexTree == expected_tree
        and live.git.candidateTree == expected_tree
        and _snapshot_is_clean(live.git)
    ):
        return False
    return (
        live.expected_path is None
        or _git_blob_text(
            live.repository,
            live.git.head,
            live.expected_path,
        )
        == live.intended_text
    )


def _same_precommit_base(live: GitMutationSnapshot, before: GitMutationSnapshot) -> bool:
    return bool(
        live.headRef == before.headRef
        and live.head == before.head
        and live.headTree == before.headTree
        and live.refLogFingerprint == before.refLogFingerprint
    )


def _snapshot_is_clean(snapshot: GitMutationSnapshot) -> bool:
    return bool(
        snapshot.indexTree == snapshot.headTree
        and snapshot.candidateTree == snapshot.headTree
        and snapshot.statusFingerprint == hashlib.sha256(b"").hexdigest()
    )


def _changed_paths(repository: Path) -> set[str]:
    status = run_git(repository, ["status", "--porcelain=v1", "-z"])
    if status.returncode != 0:
        raise RuntimeError("could not inspect direct recovery state")
    return {item[3:] for item in status.stdout.split("\0") if len(item) >= 4}


def _git_blob_text(repository: Path, commit: str, relative: str) -> str:
    result = run_git(repository, ["show", f"{commit}:{relative}"])
    if result.returncode != 0:
        raise RuntimeError("could not read direct ledger blob")
    return result.stdout


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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
