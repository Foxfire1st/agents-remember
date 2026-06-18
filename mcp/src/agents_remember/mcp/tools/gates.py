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

from agents_remember.controlplane.records import (
    DECISION_STATES,
    DecidedVia,
    coerce_gate_kind,
    create_gate,
    decide_gate,
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
    gate = create_gate(
        kind=coerce_gate_kind(kind),
        lifecycle_id=lifecycle_id,
        gate_id=new_ulid(),
        now=now_iso(),
        enclosure=enclosure,
        repo_id=repo_id,
        packet=packet,
        required_decision=required_decision,
    )
    _store(config).append(gate)
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
