"""Pure exact-state classifier for an ordinary closeout ledger mutation intent."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import ledger_to_text, parse_ledger_text, prepend_mapping
from agents_remember.models.lifecycles.mutation_evidence import (
    GitMutationEvidence,
    GitMutationSnapshot,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    ephemeral_git_mutation_snapshot,
)
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.worktree_contract import WorktreeContract

LedgerRecoveryState = Literal[
    "not-applicable",
    "accepted-before",
    "prepared-unstaged",
    "prepared-staged",
    "commit-proven-pending-publication",
    "developer-decision",
]


@dataclass(frozen=True)
class CloseoutLedgerRecoveryClassification:
    """One read-only classification consumed by projection and mutation owners."""

    state: LedgerRecoveryState
    status: str = ""
    detail: str = ""
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None
    before_text: str = ""
    intended_text: str = ""

    @property
    def mechanically_convergent(self) -> bool:
        return self.state not in {"not-applicable", "developer-decision"}

    def decision_payload(self) -> dict[str, object]:
        return {
            "state": self.status,
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": self.detail,
            "expected": dict(self.expected or {}),
            "observed": dict(self.observed or {}),
        }


class CloseoutLedgerRecoveryDecision(RuntimeError):
    """Typed protected-path refusal carrying the public classifier evidence."""

    def __init__(self, classification: CloseoutLedgerRecoveryClassification) -> None:
        self.classification = classification
        super().__init__(f"{classification.status}: {classification.detail}")


class _LedgerEvidenceFailure(RuntimeError):
    def __init__(self, evidence: dict[str, object]) -> None:
        self.evidence = evidence
        super().__init__("closeout ledger evidence is unreadable")


@dataclass(frozen=True)
class _LedgerLiveEvidence:
    repository: Path
    relative: str
    evidence: GitMutationEvidence
    live: GitMutationSnapshot
    current_text: str
    before_text: str
    intended_text: str
    expected: dict[str, object]
    observed: dict[str, object]


def classify_closeout_ledger_recovery(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> CloseoutLedgerRecoveryClassification:
    """Classify only an ordinary closeout's retained ledger mutation intent."""

    evidence = record.mutationEvidence.get("ledger")
    if (
        record.operationKind != "closeout"
        or evidence is None
        or evidence.state != "mutation-intent"
    ):
        return CloseoutLedgerRecoveryClassification("not-applicable")
    expected = _expected_facts(contract, record, evidence)
    if isinstance(expected, CloseoutLedgerRecoveryClassification):
        return expected
    before_text, intended_text, expected_payload = expected
    before = evidence.before
    assert before is not None
    observed_payload: dict[str, object] = {}
    try:
        repository, ledger_path, relative = _ledger_paths(contract, evidence)
        live = ephemeral_git_mutation_snapshot(repository)
        current_text = ledger_path.read_text(encoding="utf-8")
        observed_payload = {
            "git": live.model_dump(mode="json"),
            "ledgerSha256": _sha256(current_text),
        }
        classification = _classify_live_ledger(
            _LedgerLiveEvidence(
                repository=repository,
                relative=relative,
                evidence=evidence,
                live=live,
                current_text=current_text,
                before_text=before_text,
                intended_text=intended_text,
                expected=expected_payload,
                observed=observed_payload,
            )
        )
        if classification is not None:
            return classification
    except _LedgerEvidenceFailure as exc:
        observed_payload = {**observed_payload, "readFailure": exc.evidence}
    except (OSError, RuntimeError, ValueError) as exc:
        observed_payload = {
            **observed_payload,
            "readFailure": public_failure_evidence(
                stage="ledger-live-read",
                side="ledger",
                name=contract.ledger_path.name if contract.ledger_path is not None else "ledger",
                error_type=type(exc).__name__,
                observed={"state": "unreadable"},
            ),
        }
    return CloseoutLedgerRecoveryClassification(
        "developer-decision",
        status="closeout-ledger-recovery-conflict",
        detail="live ledger evidence is neither the accepted prestate nor the exact intended output",
        expected=expected_payload,
        observed=observed_payload,
        before_text=before_text,
        intended_text=intended_text,
    )


def _classify_live_ledger(
    facts: _LedgerLiveEvidence,
) -> CloseoutLedgerRecoveryClassification | None:
    before = facts.evidence.before
    assert before is not None
    if facts.live == before and facts.current_text == facts.before_text:
        state: LedgerRecoveryState | None = "accepted-before"
    elif (
        _prepared_base_is_exact(
            facts.repository,
            facts.relative,
            facts.evidence,
            facts.live,
        )
        and facts.current_text == facts.intended_text
        and facts.live.candidateTree == facts.evidence.expectedOutputTree
    ):
        state = _prepared_ledger_state(facts.evidence, facts.live, before.indexTree)
    elif _exact_intended_child(
        facts.repository,
        facts.relative,
        facts.evidence,
        facts.live,
        facts.intended_text,
    ):
        state = "commit-proven-pending-publication"
    else:
        state = None
    if state is None:
        return None
    return CloseoutLedgerRecoveryClassification(
        state,
        expected=facts.expected,
        observed=facts.observed,
        before_text=facts.before_text,
        intended_text=facts.intended_text,
    )


def _prepared_ledger_state(
    evidence: GitMutationEvidence,
    live: GitMutationSnapshot,
    before_index_tree: str,
) -> LedgerRecoveryState | None:
    if live.indexTree == before_index_tree:
        return "prepared-unstaged"
    if live.indexTree == evidence.expectedOutputTree:
        return "prepared-staged"
    return None


def _expected_facts(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    evidence: GitMutationEvidence,
) -> tuple[str, str, dict[str, object]] | CloseoutLedgerRecoveryClassification:
    before = evidence.before
    commits = record.recoveryCommits
    try:
        repository, _ledger_path, relative = _ledger_paths(contract, evidence)
        if before is None or evidence.expectedOutputTree is None or commits is None:
            raise _LedgerEvidenceFailure(
                public_failure_evidence(
                    stage="ledger-journal-authority",
                    side="journal",
                    name="current-operation.json",
                    error_type="MissingEvidence",
                    observed={"state": "incomplete"},
                )
            )
        if not commits.codeCommit or not commits.memoryContentCommit:
            raise _LedgerEvidenceFailure(
                public_failure_evidence(
                    stage="ledger-recovery-commits",
                    side="journal",
                    name="current-operation.json",
                    error_type="MissingEvidence",
                    observed={"state": "incomplete"},
                )
            )
        before_text = _git_blob_text(repository, before.head, relative)
        intended_text = ledger_to_text(
            prepend_mapping(
                parse_ledger_text(before_text),
                commits.codeCommit,
                commits.memoryContentCommit,
            )
        )
        expected = {
            "acceptedGit": before.model_dump(mode="json"),
            "acceptedLedgerSha256": _sha256(before_text),
            "intendedOutputTree": evidence.expectedOutputTree,
            "intendedLedgerSha256": _sha256(intended_text),
            "codeCommit": commits.codeCommit,
            "memoryCommit": commits.memoryContentCommit,
        }
        return before_text, intended_text, expected
    except _LedgerEvidenceFailure as exc:
        failure = exc.evidence
    except (OSError, RuntimeError, ValueError) as exc:
        failure = public_failure_evidence(
            stage="ledger-authority-read",
            side="ledger",
            name=contract.ledger_path.name if contract.ledger_path is not None else "ledger",
            error_type=type(exc).__name__,
            observed={"state": "unreadable"},
        )
    return CloseoutLedgerRecoveryClassification(
        "developer-decision",
        status="closeout-ledger-recovery-authority-missing",
        detail="the journal cannot reconstruct its exact accepted and intended ledger states",
        expected={
            "acceptedGit": before.model_dump(mode="json") if before is not None else None,
            "intendedOutputTree": evidence.expectedOutputTree or "",
        },
        observed={"authorityFailure": failure},
    )


def _ledger_paths(
    contract: WorktreeContract,
    evidence: GitMutationEvidence,
) -> tuple[Path, Path, str]:
    repository = contract.memory_worktree
    ledger_path = contract.ledger_path
    if repository is None or ledger_path is None:
        raise _LedgerEvidenceFailure(
            public_failure_evidence(
                stage="ledger-authority",
                side="ledger",
                name="ledger",
                error_type="MissingAuthority",
                observed={"state": "missing"},
            )
        )
    if repository.resolve() != Path(evidence.repository).resolve():
        raise _LedgerEvidenceFailure(
            public_failure_evidence(
                stage="ledger-authority",
                side="memory",
                name=repository.name,
                error_type="AuthorityMismatch",
                observed={"state": "mismatched"},
            )
        )
    try:
        relative = ledger_path.resolve().relative_to(repository.resolve()).as_posix()
    except ValueError as exc:
        raise _LedgerEvidenceFailure(
            public_failure_evidence(
                stage="ledger-authority",
                side="ledger",
                name=ledger_path.name,
                error_type="AuthorityMismatch",
                observed={"state": "outside-repository"},
            )
        ) from exc
    return repository, ledger_path, relative


def _prepared_base_is_exact(
    repository: Path,
    relative: str,
    evidence: GitMutationEvidence,
    live,
) -> bool:
    before = evidence.before
    if before is None:
        return False
    return bool(
        live.headRef == before.headRef
        and live.head == before.head
        and live.headTree == before.headTree
        and live.refLogFingerprint == before.refLogFingerprint
        and _changed_paths(repository) == {relative}
    )


def _exact_intended_child(
    repository: Path,
    relative: str,
    evidence: GitMutationEvidence,
    live,
    intended_text: str,
) -> bool:
    before = evidence.before
    expected_tree = evidence.expectedOutputTree
    if before is None or expected_tree is None or live.head == before.head:
        return False
    try:
        head_text = _git_blob_text(repository, live.head, relative)
        parent = require_git(repository, ["rev-parse", f"{live.head}^"])
    except RuntimeError:
        return False
    return bool(
        live.headRef == before.headRef
        and parent == before.head
        and live.headTree == expected_tree
        and live.indexTree == expected_tree
        and live.candidateTree == expected_tree
        and not _changed_paths(repository)
        and head_text == intended_text
    )


def _changed_paths(repository: Path) -> set[str]:
    status = run_git(repository, ["status", "--porcelain=v1", "-z"])
    if status.returncode != 0:
        raise _LedgerEvidenceFailure(
            public_failure_evidence(
                stage="ledger-git-status",
                side="memory",
                name=repository.name,
                error_type="GitCommandError",
                observed={"state": "unreadable"},
            )
        )
    return {item[3:] for item in status.stdout.split("\0") if len(item) >= 4}


def _git_blob_text(repository: Path, commit: str, relative: str) -> str:
    result = run_git(repository, ["show", f"{commit}:{relative}"])
    if result.returncode != 0:
        raise _LedgerEvidenceFailure(
            public_failure_evidence(
                stage="ledger-git-blob-read",
                side="ledger",
                name=Path(relative).name,
                error_type="GitCommandError",
                observed={"state": "unreadable"},
            )
        )
    return result.stdout


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
