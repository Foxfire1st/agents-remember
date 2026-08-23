"""One total live classifier for journal-owned protected integration refs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import run_git
from agents_remember.models.lifecycles.operation import (
    IntegrationOperationAuthority,
    LifecycleOperationRecord,
    LifecycleOperationRecoveryCommits,
)


@dataclass(frozen=True)
class IntegrationRefObservation:
    """One bounded named-ref read without raw Git diagnostics or fake object ids."""

    side: Literal["code", "memory"]
    ref: str
    object_id: str | None = None
    error_type: Literal["repository-unreadable", "ref-missing", "ref-unreadable"] | None = None

    def payload(self) -> dict[str, str]:
        result = {"side": self.side, "ref": self.ref}
        if self.object_id is not None:
            result["objectId"] = self.object_id
        if self.error_type is not None:
            result["errorType"] = self.error_type
        return result


@dataclass(frozen=True)
class IntegrationRefState:
    """Exact before/intended/observed facts for both protected ref sides."""

    state: Literal["unchanged", "intended", "conflict"]
    before: dict[str, str]
    intended: dict[str, str]
    observed: dict[str, IntegrationRefObservation]

    def observed_payload(self) -> dict[str, dict[str, str]]:
        return {key: item.payload() for key, item in self.observed.items()}

    def object_id(self, key: str) -> str | None:
        item = self.observed.get(key)
        return item.object_id if item is not None else None

    def decision_payload(self) -> dict[str, object]:
        """Return the canonical public third-ref decision surface."""

        detail = (
            "a protected source ref is missing or unreadable"
            if any(item.error_type is not None for item in self.observed.values())
            else "a protected source ref has an unexpected third object"
        )
        return {
            "state": "integration-ref-conflict",
            "reason": detail,
            "summary": detail,
            "developerDecisionRequired": True,
            "decisionSurface": detail,
            "nextAction": "developer-decision",
            "expected": {"before": self.before, "intended": self.intended},
            "observed": self.observed_payload(),
        }

    def interruption_payload(self) -> dict[str, object]:
        """Return the canonical same-generation CAS interruption surface."""

        if self.state == "conflict":
            raise ValueError("a conflicting ref state is not mechanically recoverable")
        detail = "protected refs remain at accepted or intended same-generation objects"
        return {
            "state": "integration-ref-publication-interrupted",
            "refState": self.state,
            "reason": detail,
            "summary": detail,
            "nextAction": "recover",
            "expected": {"before": self.before, "intended": self.intended},
            "observed": self.observed_payload(),
        }

    def public_payload(self) -> dict[str, object]:
        """Choose the sole public payload for this exact live ref observation."""

        return self.decision_payload() if self.state == "conflict" else self.interruption_payload()


class IntegrationRefDecisionError(RuntimeError):
    """Protected-path refusal carrying the canonical live ref classification."""

    def __init__(self, classification: IntegrationRefState) -> None:
        self.classification = classification
        super().__init__(str(classification.decision_payload()["decisionSurface"]))


class IntegrationRefPublicationInterrupted(RuntimeError):
    """A protected CAS stopped in an exact same-generation recoverable state."""

    def __init__(self, classification: IntegrationRefState) -> None:
        if classification.state == "conflict":
            raise ValueError("conflicting refs are developer decisions, not interruptions")
        self.classification = classification
        super().__init__("protected refs remain mechanically recoverable")


def require_unchanged_integration_refs(record: LifecycleOperationRecord) -> None:
    """Require exact accepted refs for a repair operation with no intended output."""

    classification = classify_integration_refs(record)
    if classification.state != "unchanged":
        raise IntegrationRefDecisionError(classification)


def classify_integration_refs(record: LifecycleOperationRecord) -> IntegrationRefState:
    """Classify live refs for one accepted integration generation."""

    authority = record.integrationAuthority
    if authority is None:
        raise RuntimeError("integrate operation has no protected-ref authority")
    return classify_integration_authority_refs(authority, record.recoveryCommits)


def classify_integration_authority_refs(
    authority: IntegrationOperationAuthority,
    commits: LifecycleOperationRecoveryCommits | None,
) -> IntegrationRefState:
    """Classify live refs against immutable operation authority and intended commits."""

    before = {"codeRef": authority.codeSourceCommit}
    observed = {
        "codeRef": _read_ref(
            "code",
            Path(authority.codeRepository),
            authority.codeSourceBranch,
        )
    }
    if authority.memoryRepository:
        before["memoryRef"] = authority.memorySourceCommit
        observed["memoryRef"] = _read_ref(
            "memory",
            Path(authority.memoryRepository),
            authority.memorySourceBranch,
        )
    intended = (
        {
            "codeRef": commits.codeCommit,
            **({"memoryRef": commits.ledgerCommit} if authority.memoryRepository else {}),
        }
        if commits is not None
        else {}
    )
    observed_objects = {key: item.object_id for key, item in observed.items()}
    state: Literal["unchanged", "intended", "conflict"]
    if any(item.error_type is not None for item in observed.values()):
        state = "conflict"
    elif observed_objects == before:
        state = "unchanged"
    elif intended and all(
        observed_objects[key] in {before[key], intended[key]} for key in observed
    ):
        state = "intended"
    else:
        state = "conflict"
    return IntegrationRefState(state, before, intended, observed)


def _read_ref(
    side: Literal["code", "memory"],
    repository: Path,
    branch: str,
) -> IntegrationRefObservation:
    """Read one canonical branch ref with stable failure categories only."""

    ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    try:
        repository_check = run_git(repository, ["rev-parse", "--git-dir"])
        if repository_check.returncode != 0:
            return IntegrationRefObservation(
                side,
                ref,
                error_type="repository-unreadable",
            )
        result = run_git(repository, ["show-ref", "--verify", "--hash", ref])
    except OSError:
        return IntegrationRefObservation(
            side,
            ref,
            error_type="repository-unreadable",
        )
    if result.returncode == 0 and result.stdout.strip():
        return IntegrationRefObservation(side, ref, object_id=result.stdout.strip())
    return IntegrationRefObservation(
        side,
        ref,
        error_type="ref-missing" if result.returncode == 1 else "ref-unreadable",
    )
