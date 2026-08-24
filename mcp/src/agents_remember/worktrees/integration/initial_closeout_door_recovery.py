"""Pure classifier for an invalid canonical closeout record missing its door intent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_lifecycle_evidence_pair,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

InitialDoorRecoveryState = Literal[
    "not-applicable",
    "developer-decision",
]


@dataclass(frozen=True)
class InitialCloseoutDoorRecoveryClassification:
    """Exact read-only result shared by status and mutating recovery."""

    state: InitialDoorRecoveryState
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None

    def decision_payload(self) -> dict[str, object]:
        detail = "the closeout record cannot prove the sole pre-intent publication cut"
        public = public_lifecycle_evidence_pair(
            self.expected or {},
            self.observed or {},
        )
        return {
            "state": "closeout-initial-door-intent-missing",
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": detail,
            "expected": public.expected,
            "observed": public.observed,
        }


def classify_initial_closeout_door_recovery(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> InitialCloseoutDoorRecoveryClassification:
    """Reject a canonical closeout record that lacks its create-time door intent."""

    if (
        record.operationKind != "closeout"
        or record.doorPublication is not None
        or record.legacyMigration is not None
    ):
        return InitialCloseoutDoorRecoveryClassification("not-applicable")
    expected: dict[str, object] = {
        "operationKind": "closeout",
        "doorPublication": "create-time-claimed-intent-or-proof",
        "normalRecovery": "forbidden",
    }
    return InitialCloseoutDoorRecoveryClassification(
        "developer-decision",
        expected=expected,
        observed={
            "generation": record.generation,
            "status": record.status,
            "phase": record.phase,
            "contractDoor": (
                contract.closeout_door.model_dump(mode="json")
                if contract.closeout_door is not None
                else None
            ),
        },
    )
