"""Shared mutation service for addressed and lifecycle-scoped gate decisions."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from agents_remember.controlplane.durable_store import GATE_OWNERSHIP
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.gate_policy import (
    GatePolicy,
    delegated_decision_failure_reason,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import (
    DECISION_STATES,
    GateEvidenceRef,
    GateRecord,
    GateVerdict,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore


@dataclass(frozen=True)
class GateDecisionContext:
    """Stores, policy, and clock that make one gate decision atomic in meaning."""

    store: GateStore
    inbox_store: OperatorInboxStore
    expectation_store: ExpectationRowStore | None
    policy: GatePolicy
    now: datetime


def _target_gate(store: GateStore, gate_id: str, lifecycle_id: str | None) -> GateRecord:
    gate = store.current(lifecycle_id).get(gate_id)
    if gate is None and lifecycle_id is None:
        gate = store.find(gate_id)
    if gate is None:
        raise KeyError(f"no gate {gate_id!r} on lifecycle {lifecycle_id!r}")
    return gate


def _require_undelegated_cli_decision(gate: GateRecord, policy: GatePolicy) -> None:
    if policy.rule_for(gate.kind).delegated_role is not None:
        raise ValueError(
            f"{gate.kind} is delegated by the active gate policy; pass deciding_role "
            "for an attributed orchestration decision, or leave it to the developer"
        )


def _evidence_refs(raw: list[dict[str, Any]] | None) -> list[GateEvidenceRef]:
    return [] if raw is None else [GateEvidenceRef.model_validate(entry) for entry in raw]


def _meet_verdict_expectation(
    store: ExpectationRowStore | None,
    gate: GateRecord,
    *,
    now: str,
) -> None:
    if store is None:
        return
    row = store.find_by_source(gate.id, kind="verdict-by")
    if row is not None:
        store.mark_met(row.id, now=now)


def _reclaim_gate_log(store: GateStore, lifecycle_id: str | None, *, now: datetime) -> None:
    """Reclaim terminal history only in the process that owns gate compaction."""

    if not GATE_OWNERSHIP.is_compaction_owner():
        return
    with contextlib.suppress(OSError, ValidationError):
        store.compact(lifecycle_id, now=now)


def record_gate_decision(
    context: GateDecisionContext,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    verdict: GateVerdict,
    evidence_refs: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Apply one decision and return the shared raw gate response payload."""

    if verdict.decision not in DECISION_STATES:
        raise ValueError(
            f"unknown gate decision {verdict.decision!r}; expected one of {sorted(DECISION_STATES)}"
        )
    gate = _target_gate(context.store, gate_id, lifecycle_id)
    if verdict.via == "cli" and verdict.decision != "cancel":
        _require_undelegated_cli_decision(gate, context.policy)
    timestamp = context.now.isoformat()
    updated = decide_gate(
        gate,
        verdict,
        now=timestamp,
        evidence_refs=_evidence_refs(evidence_refs),
    )
    if verdict.via == "orchestration":
        failure = delegated_decision_failure_reason(updated, context.policy)
        if failure is not None:
            raise ValueError(f"gate decision rejected by delegation policy: {failure}")
    context.store.append(updated)
    if verdict.decision == "cancel":
        context.store.delete(updated.id, updated.lifecycleId)
        context.inbox_store.delete_by_gate(updated.id)
    _meet_verdict_expectation(context.expectation_store, updated, now=timestamp)
    _reclaim_gate_log(context.store, updated.lifecycleId, now=context.now)
    return {
        "ok": True,
        "operation": "gate_decide",
        "gateId": updated.id,
        "state": updated.state,
        "decidedBy": updated.decidedBy,
        "decidedVia": updated.decidedVia,
        "decidingRole": updated.decidingRole,
        "evidenceRefs": [
            ref.model_dump(mode="json", exclude_none=True) for ref in updated.evidenceRefs
        ],
    }


def record_lifecycle_gate_decision(
    context: GateDecisionContext,
    *,
    lifecycle_id: str,
    expected_gate_id: str | None,
    verdict: GateVerdict,
    evidence_refs: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Resolve a lifecycle's latest open gate, then apply the shared decision service."""

    current = context.store.current(lifecycle_id)
    open_gates = [gate for gate in current.values() if gate.state == "open"]
    if not open_gates:
        raise KeyError(f"no open gate on lifecycle {lifecycle_id!r}")
    gate = max(open_gates, key=lambda candidate: candidate.ts)
    if expected_gate_id is not None and gate.id != expected_gate_id:
        expected = current.get(expected_gate_id)
        state = expected.state if expected is not None else "missing"
        raise KeyError(f"gate {expected_gate_id!r} is {state}; current open gate is {gate.id!r}")
    return record_gate_decision(
        context,
        gate_id=gate.id,
        lifecycle_id=lifecycle_id,
        verdict=verdict,
        evidence_refs=evidence_refs,
    )
