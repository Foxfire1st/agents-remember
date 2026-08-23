"""Typed write-ahead intent for one lifecycle generation successor."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord


class LifecycleSuccessorPublicationIntent(BaseModel):
    """The complete accepted N+1 identity published before replacing N."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["1.0"] = "1.0"
    predecessor: LifecycleOperationRecord
    successor: LifecycleOperationRecord

    @model_validator(mode="after")
    def _require_sequential_identity(self) -> LifecycleSuccessorPublicationIntent:
        predecessor = self.predecessor
        successor = self.successor
        if predecessor.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("successor publication predecessor must be terminal")
        if predecessor.workerPid is not None or (
            predecessor.workerTermination is not None
            and predecessor.workerTermination.state != "exited"
        ):
            raise ValueError("successor predecessor retains unproven worker authority")
        for field in ("taskId", "taskName", "contractPath", "operationKind"):
            if getattr(predecessor, field) != getattr(successor, field):
                raise ValueError(f"successor publication cannot change {field}")
        if successor.generation != predecessor.generation + 1:
            raise ValueError("successor publication generation is not sequential")
        if successor.attempt != 1:
            raise ValueError("a fresh lifecycle generation starts at attempt one")
        if successor.predecessorFingerprint != predecessor.fingerprint:
            raise ValueError("successor does not name its exact predecessor fingerprint")
        if predecessor.successorFingerprint != successor.fingerprint:
            raise ValueError("predecessor does not name its exact successor fingerprint")
        return self
