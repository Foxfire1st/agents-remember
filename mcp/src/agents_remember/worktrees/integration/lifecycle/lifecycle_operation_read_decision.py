"""Bounded public decision for one strict current/successor journal read failure."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.models.lifecycles.operation import (
    LifecycleOperationKind,
    LifecycleOperationProjection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationReadError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)


@dataclass(frozen=True)
class LifecycleJournalReadDecision:
    """One payload owner shared by projection and mutating-handler translation."""

    kind: LifecycleOperationKind
    error: LifecycleOperationReadError

    @property
    def status(self) -> str:
        return f"{self.kind}-lifecycle-journal-unreadable"

    @property
    def detail(self) -> str:
        return "the canonical strict lifecycle journal is unreadable or invalid"

    @property
    def expected(self) -> dict[str, object]:
        return {
            "operationKind": self.kind,
            "journalSide": self.error.side,
            **self.error.expected,
        }

    @property
    def observed(self) -> dict[str, object]:
        return {
            "journal": public_failure_evidence(
                stage="lifecycle-journal-read",
                side=self.error.side,
                name=self.error.name,
                error_type=self.error.error_type,
                observed=self.error.observed,
            )
        }

    def payload(self) -> dict[str, object]:
        return {
            "state": self.status,
            "developerDecisionRequired": True,
            "decisionSurface": self.detail,
            "nextAction": "developer-decision",
            "expected": self.expected,
            "observed": self.observed,
        }

    def projection(self) -> LifecycleOperationProjection:
        return LifecycleOperationProjection(
            kind=self.kind,
            status="unreadable",
            phase="contract-finalization",
            elapsedSeconds=0.0,
            reportPath="",
            result=self.payload(),
            failure=self.detail,
            guidance=self.detail,
            cancellable=False,
            generation=None,
            legalControls=[],
        )

    def control_error(self) -> LifecycleControlError:
        return LifecycleControlError(
            self.status,
            self.detail,
            expected=self.expected,
            observed=self.observed,
            next_action="developer-decision",
        )


def lifecycle_journal_read_decision(
    kind: LifecycleOperationKind,
    error: LifecycleOperationReadError,
) -> LifecycleJournalReadDecision:
    return LifecycleJournalReadDecision(kind, error)
