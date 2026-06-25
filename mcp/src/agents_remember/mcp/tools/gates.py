"""Payload builders for the ``gate_*`` control-plane tools (slice 6a).

Each builds a :class:`GateStore` over the config's observer root, mutates the
append-only gate log, and returns the modeled response through ``_tool_payload``
-- so a gate action is itself an attributed tool call (the choke point tags it
onto the active lifecycle like any other tool).

Attribution rule: the MCP server registers ``gate_decide`` with
``decided_by="model"`` / ``decided_via="cli"`` -- the agent records its own
decisions honestly and *cannot* claim to be the developer. The dashboard serving
layer (a later slice) calls :func:`gate_decide_payload` directly with
``decided_by="developer"`` / ``decided_via="dashboard"``. Enforcement (the
mutating tools requiring a developer-attributed approval) is what makes that
distinction binding; this slice only records it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.interaction_retention import (
    GATE_RESPONSE_WAIT_POLL_SECONDS,
    GATE_RESPONSE_WAIT_TIMEOUT_SECONDS,
    delete_after_wait,
)
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import (
    DECISION_STATES,
    DecidedVia,
    GateRecord,
    coerce_gate_kind,
    create_gate,
    decide_gate,
    expire_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.observer import observer_root
from agents_remember.observer.events import now_iso
from agents_remember.observer.ulid import new_ulid

from .base import _tool_payload

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


def _store(config: McpRuntimeConfig) -> GateStore:
    return GateStore(observer_root(config))


def _inbox_store(config: McpRuntimeConfig) -> OperatorInboxStore:
    return OperatorInboxStore(observer_root(config))


def _entry_payload(entry: OperatorInboxEntry) -> dict[str, Any]:
    return entry.model_dump(mode="json", by_alias=True, exclude_none=True)


def _decision_payload(gate: GateRecord) -> dict[str, Any]:
    return {
        "decidedBy": gate.decidedBy,
        "decidedVia": gate.decidedVia,
        "decisionNote": gate.decisionNote,
    }


def _cancelled_wait_payload(operation: str, gate_id: str) -> dict[str, Any]:
    return _tool_payload(
        operation,
        {
            "ok": True,
            "operation": operation,
            "gateId": gate_id,
            "state": "cancelled",
            "timedOut": False,
            "entryCount": 0,
            "entries": [],
        },
    )


def gate_create_payload(
    config: McpRuntimeConfig,
    *,
    kind: str,
    lifecycle_id: str | None,
    enclosure: str | None = None,
    repo_id: str | None = None,
    packet: dict[str, Any] | None = None,
    required_decision: list[str] | None = None,
) -> dict[str, Any]:
    now = now_iso()
    store = _store(config)
    if lifecycle_id is not None:
        for current in store.current(lifecycle_id).values():
            if current.state == "open":
                store.append(expire_gate(current, now=now))
    gate = create_gate(
        kind=coerce_gate_kind(kind),
        lifecycle_id=lifecycle_id,
        gate_id=new_ulid(),
        now=now,
        enclosure=enclosure,
        repo_id=repo_id,
        packet=packet,
        required_decision=required_decision,
    )
    store.append(gate)
    return _tool_payload(
        "gate_create",
        {
            "ok": True,
            "operation": "gate_create",
            "gateId": gate.id,
            "kind": gate.kind,
            "state": gate.state,
            "lifecycleId": gate.lifecycleId,
        },
    )


def gate_decide_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    decision: str,
    decided_by: str,
    decided_via: DecidedVia,
    note: str | None = None,
) -> dict[str, Any]:
    if decision not in DECISION_STATES:
        raise ValueError(
            f"unknown gate decision {decision!r}; expected one of {sorted(DECISION_STATES)}"
        )
    store = _store(config)
    gate = store.current(lifecycle_id).get(gate_id)
    if gate is None:
        raise KeyError(f"no gate {gate_id!r} on lifecycle {lifecycle_id!r}")
    updated = decide_gate(
        gate,
        decision=decision,
        by=decided_by,
        via=decided_via,
        note=note,
        now=now_iso(),
    )
    store.append(updated)
    if decision == "cancel":
        store.delete(updated.id, lifecycle_id)
        _inbox_store(config).delete_by_gate(updated.id)
    return _tool_payload(
        "gate_decide",
        {
            "ok": True,
            "operation": "gate_decide",
            "gateId": updated.id,
            "state": updated.state,
            "decidedBy": updated.decidedBy,
            "decidedVia": updated.decidedVia,
        },
    )


def gate_decide_for_lifecycle(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str,
    decision: str,
    decided_by: str,
    decided_via: DecidedVia,
    expected_gate_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Decide the lifecycle's latest still-open gate -- the dashboard's write path.

    The dashboard targets a *lifecycle*, not a gate id (gate projection, which
    would hand the UI a specific id, lands in a later slice), so this resolves the
    newest ``open`` gate on the lifecycle and decides it. The serving layer calls
    it with ``decided_by="developer"`` / ``decided_via="dashboard"`` -- the
    un-forgeable counterpart to the agent's ``decided_by="model"`` path through
    :func:`gate_decide_payload`, which server-side closeout enforcement makes
    binding. Raises ``KeyError`` when the lifecycle has no open gate.
    """
    if decision not in DECISION_STATES:
        raise ValueError(
            f"unknown gate decision {decision!r}; expected one of {sorted(DECISION_STATES)}"
        )
    store = _store(config)
    current = store.current(lifecycle_id)
    open_gates = [gate for gate in current.values() if gate.state == "open"]
    if not open_gates:
        raise KeyError(f"no open gate on lifecycle {lifecycle_id!r}")
    gate = max(open_gates, key=lambda candidate: candidate.ts)
    if expected_gate_id is not None and gate.id != expected_gate_id:
        expected = current.get(expected_gate_id)
        state = expected.state if expected is not None else "missing"
        raise KeyError(
            f"gate {expected_gate_id!r} is {state}; current open gate is {gate.id!r}"
        )
    updated = decide_gate(
        gate,
        decision=decision,
        by=decided_by,
        via=decided_via,
        note=note,
        now=now_iso(),
    )
    store.append(updated)
    if decision == "cancel":
        store.delete(updated.id, lifecycle_id)
        _inbox_store(config).delete_by_gate(updated.id)
    return _tool_payload(
        "gate_decide",
        {
            "ok": True,
            "operation": "gate_decide",
            "gateId": updated.id,
            "state": updated.state,
            "decidedBy": updated.decidedBy,
            "decidedVia": updated.decidedVia,
        },
    )


def gate_wait_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Bounded wait until the gate leaves ``open`` (or ``timeout_seconds``).

    A simple bounded poll: a real long-poll / push wakeup lands with enforcement
    in a later slice. ``sleep`` / ``monotonic`` are injectable for deterministic
    tests.
    """
    store = _store(config)
    deadline = monotonic() + timeout_seconds
    while True:
        gate = store.current(lifecycle_id).get(gate_id)
        if gate is None:
            raise KeyError(f"no gate {gate_id!r} on lifecycle {lifecycle_id!r}")
        if gate.state != "open":
            return _tool_payload(
                "gate_wait",
                {
                    "ok": True,
                    "operation": "gate_wait",
                    "gateId": gate.id,
                    "state": gate.state,
                    "timedOut": False,
                    **_decision_payload(gate),
                },
            )
        if monotonic() >= deadline:
            return _tool_payload(
                "gate_wait",
                {
                    "ok": True,
                    "operation": "gate_wait",
                    "gateId": gate.id,
                    "state": gate.state,
                    "timedOut": True,
                    **_decision_payload(gate),
                },
            )
        sleep(poll_seconds)


def gate_response_wait_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    agent_id: str | None = None,
    timeout_seconds: float = GATE_RESPONSE_WAIT_TIMEOUT_SECONDS,
    poll_seconds: float = GATE_RESPONSE_WAIT_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Bounded wait for either a gate decision or a dashboard Chat inbox entry.

    The helper owns the normal wait window: one call polls every five seconds
    for up to five minutes by default. Returned inbox entries are not consumed;
    call ``operator_inbox_consume`` after reading each handled entry.
    """
    gate_store = _store(config)
    inbox_store = _inbox_store(config)
    deadline = monotonic() + timeout_seconds
    while True:
        gate = gate_store.current(lifecycle_id).get(gate_id)
        if gate is None:
            return _cancelled_wait_payload("gate_response_wait", gate_id)
        entries: list[OperatorInboxEntry] = []
        if lifecycle_id is not None or agent_id is not None:
            entries = [
                entry
                for entry in inbox_store.list_pending(lifecycle_id=lifecycle_id, agent_id=agent_id)
                if entry.gateId in (None, gate_id)
            ]
        if gate.state != "open" or entries:
            payload = _tool_payload(
                "gate_response_wait",
                {
                    "ok": True,
                    "operation": "gate_response_wait",
                    "gateId": gate.id,
                    "state": gate.state,
                    "timedOut": False,
                    "entryCount": len(entries),
                    "entries": [_entry_payload(entry) for entry in entries],
                    **_decision_payload(gate),
                },
            )
            if gate.state != "open" and delete_after_wait(gate):
                gate_store.delete(gate.id, lifecycle_id)
            return payload
        if monotonic() >= deadline:
            return _tool_payload(
                "gate_response_wait",
                {
                    "ok": True,
                    "operation": "gate_response_wait",
                    "gateId": gate.id,
                    "state": gate.state,
                    "timedOut": True,
                    "entryCount": 0,
                    "entries": [],
                    **_decision_payload(gate),
                },
            )
        sleep(poll_seconds)


def gate_list_payload(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str | None,
) -> dict[str, Any]:
    gates = _store(config).current(lifecycle_id)
    return _tool_payload(
        "gate_list",
        {
            "ok": True,
            "operation": "gate_list",
            "lifecycleId": lifecycle_id,
            "gates": [
                gate.model_dump(mode="json", by_alias=True, exclude_none=True)
                for gate in gates.values()
            ],
        },
    )
