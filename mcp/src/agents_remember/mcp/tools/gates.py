"""Payload builders for the ``gate_*`` control-plane tools (slice 6a).

Each builds a :class:`GateStore` over the config's observer root, mutates the
append-only gate log, and returns the modeled response through ``_tool_payload``
-- so a gate action is itself an attributed tool call (the choke point tags it
onto the active lifecycle like any other tool).

``gate_create_payload``, ``gate_wait_payload``, and
``gate_response_wait_payload`` remain lower-level compatibility builders for
internal callers and tests. The agent-facing MCP junction is
``lifecycle_gate``; its public default blocks until a developer decision or a
gate-specific inbox response is available.

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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from agents_remember.controlplane.expectation_rows import ExpectationRowStore, write_expectation_row
from agents_remember.controlplane.gate_policy import (
    DEFAULT_GATE_POLICY,
    SEAM_GATE_KINDS,
    delegated_decision_failure_reason,
)
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
    GateEvidenceRef,
    GateRecord,
    coerce_gate_kind,
    create_gate,
    decide_gate,
    expire_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.agentic_settings import (
    DEFAULT_EXPECTATION_SLA_SECONDS,
    load_agentic_settings,
)
from agents_remember.observer import observer_root
from agents_remember.observer.ambient import ambient, build_ask, require_ambient
from agents_remember.observer.events import now_iso
from agents_remember.observer.lifecycle_state import LifecycleError
from agents_remember.observer.ulid import new_ulid

from .base import _tool_payload

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


def _store(config: McpRuntimeConfig) -> GateStore:
    return GateStore(observer_root(config))


def _inbox_store(config: McpRuntimeConfig) -> OperatorInboxStore:
    return OperatorInboxStore(observer_root(config))


def _expectation_store(config: McpRuntimeConfig) -> ExpectationRowStore:
    return ExpectationRowStore(observer_root(config))


def _expectation_sla_seconds(config: McpRuntimeConfig, kind: str) -> float:
    if getattr(config, "coordination_root", None) is None:
        return DEFAULT_EXPECTATION_SLA_SECONDS[kind]
    return load_agentic_settings(config.coordination_root).expectations.sla_for(kind)


def _write_verdict_by_row(config: McpRuntimeConfig, gate: GateRecord) -> None:
    """R2: a gate open atomically writes its ``verdict-by`` expectation row (same call, never a
    forgettable follow-up)."""
    if getattr(config, "coordination_root", None) is None:
        return
    write_expectation_row(
        _expectation_store(config),
        row_id=new_ulid(),
        now=datetime.now(UTC),
        kind="verdict-by",
        sla_seconds=_expectation_sla_seconds(config, "verdict-by"),
        source_id=gate.id,
        subject_lifecycle_id=gate.lifecycleId,
        note=f"verdict-by: {gate.kind} gate {gate.id}",
    )


def _resolve_gate_lifecycle_id(lifecycle_id: str | None) -> str:
    if lifecycle_id is not None:
        if lifecycle_id.strip():
            return lifecycle_id
        raise ValueError("gate_create lifecycle_id must be non-empty when supplied")
    amb = ambient()
    current = amb.current if amb is not None else None
    if current is None:
        raise LifecycleError("gate_create requires an active lifecycle or explicit lifecycle_id")
    return current.id


def _entry_payload(entry: OperatorInboxEntry) -> dict[str, Any]:
    return entry.model_dump(mode="json", by_alias=True, exclude_none=True)


def _decision_payload(gate: GateRecord) -> dict[str, Any]:
    return {
        "decidedBy": gate.decidedBy,
        "decidedVia": gate.decidedVia,
        "decisionNote": gate.decisionNote,
    }


def _resolve_deciding_actor(decided_by: str | None, decided_via: DecidedVia) -> str:
    if decided_via != "orchestration":
        if decided_by is None or not decided_by.strip():
            raise ValueError("gate decision actor must be non-empty")
        return decided_by
    if decided_by is not None and decided_by.strip():
        return decided_by
    amb = ambient()
    current = amb.current if amb is not None else None
    if current is None:
        raise LifecycleError("orchestration gate decisions require an active deciding lifecycle")
    return current.id


def _evidence_refs(raw: list[dict[str, Any]] | None) -> list[GateEvidenceRef]:
    if raw is None:
        return []
    return [GateEvidenceRef.model_validate(entry) for entry in raw]


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
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now = now_iso()
    store = _store(config)
    gate_lifecycle_id = _resolve_gate_lifecycle_id(lifecycle_id)
    for current in store.current(gate_lifecycle_id).values():
        if current.state == "open":
            store.append(expire_gate(current, now=now))
    gate = create_gate(
        kind=coerce_gate_kind(kind),
        lifecycle_id=gate_lifecycle_id,
        gate_id=new_ulid(),
        now=now,
        enclosure=enclosure,
        repo_id=repo_id,
        packet=packet,
        required_decision=required_decision,
        evidence_refs=evidence_refs,
    )
    store.append(gate)
    _write_verdict_by_row(config, gate)
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


def lifecycle_gate_payload(
    config: McpRuntimeConfig,
    *,
    kind: str,
    ask: dict[str, Any] | None = None,
    lifecycle_id: str | None = None,
    enclosure: str | None = None,
    repo_id: str | None = None,
    packet: dict[str, Any] | None = None,
    required_decision: list[str] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    timeout_seconds: float | None = None,
    poll_seconds: float = GATE_RESPONSE_WAIT_POLL_SECONDS,
    wait: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    amb = require_ambient()
    current = amb.current
    if current is None:
        raise LifecycleError("lifecycle_gate requires an active lifecycle")
    if lifecycle_id is not None and lifecycle_id != current.id:
        raise LifecycleError(
            f"lifecycle_gate lifecycle_id {lifecycle_id!r} does not match active "
            f"lifecycle {current.id!r}"
        )
    if current.state != "running":
        raise LifecycleError(
            f"cannot lifecycle_gate from state {current.state!r}; only running lifecycles gate"
        )

    ask_payload = ask or {}
    ask_kind_raw = ask_payload.get("kind")
    prompt_raw = ask_payload.get("prompt")
    options_raw = ask_payload.get("options")
    if ask_kind_raw is not None and not isinstance(ask_kind_raw, str):
        raise ValueError("lifecycle_gate ask.kind must be a string when supplied")
    if prompt_raw is not None and not isinstance(prompt_raw, str):
        raise ValueError("lifecycle_gate ask.prompt must be a string when supplied")
    if options_raw is not None and (
        not isinstance(options_raw, list)
        or any(not isinstance(option, str) for option in options_raw)
    ):
        raise ValueError("lifecycle_gate ask.options must be a list of strings when supplied")
    ask_kind = cast(str | None, ask_kind_raw)
    prompt = cast(str | None, prompt_raw)
    options = cast(list[str] | None, options_raw)
    structured_ask = build_ask(ask_kind, prompt, options)

    gate_kind = coerce_gate_kind(kind)
    if not wait:
        # Raise-and-continue: reserved for SEAM kinds the active policy delegates. A
        # human-decided kind must keep the blocking/notify contract, and a delegated
        # non-seam kind (e.g. plan-approval) keeps it too — only the seam gate (the
        # master-handover-approval the manager raises for the orchestrator) returns
        # immediately so the raiser can post its packet; the gate id is the hand-off.
        # The raise also requires the enforcement address: `enclosure` is the master
        # task name the integrate guard matches the gate by, so an addressless
        # wait=false gate could only ever fail open at the enforcement rung.
        # Validate-then-mutate: refuse BEFORE the expire-sweep and append below, so a
        # refused raise persists no orphan open gate and expires no sibling.
        policy = config.orchestration.gate_policy if config is not None else DEFAULT_GATE_POLICY
        if gate_kind not in SEAM_GATE_KINDS:
            raise ValueError(
                f"lifecycle_gate wait=false is reserved for delegated seam kinds; "
                f"{gate_kind} blocks"
            )
        if policy.rule_for(gate_kind).delegated_role is None:
            raise ValueError(
                f"lifecycle_gate wait=false requires a kind the active policy delegates; "
                f"{gate_kind} is not delegated"
            )
        if enclosure is None or not enclosure.strip():
            raise ValueError(
                f"a {gate_kind} raise-and-continue requires "
                "enclosure=<master task name> — the integration guard's address"
            )
    now = now_iso()
    store = _store(config)
    for open_gate in store.current(current.id).values():
        if open_gate.state == "open":
            store.append(expire_gate(open_gate, now=now))
    gate = create_gate(
        kind=gate_kind,
        lifecycle_id=current.id,
        gate_id=new_ulid(),
        now=now,
        enclosure=enclosure,
        repo_id=repo_id,
        packet=packet,
        required_decision=required_decision,
        evidence_refs=evidence_refs,
    )
    store.append(gate)
    _write_verdict_by_row(config, gate)
    if not wait:
        return _tool_payload(
            "lifecycle_gate",
            {
                "ok": True,
                "operation": "lifecycle_gate",
                "gate": {
                    "id": gate.id,
                    "kind": gate.kind,
                    "state": "open",
                    "lifecycleId": gate.lifecycleId,
                },
                "lifecycle": {
                    "id": current.id,
                    "state": current.state,
                    "phase": getattr(current, "phase", None),
                },
                "wait": {
                    "state": "raised",
                    "gateId": gate.id,
                    "lifecycleId": current.id,
                    "timedOut": False,
                    "waited": False,
                    "note": "gate raised without blocking; carry the gateId in the "
                    "handover packet — the delegated decider resolves it by id",
                },
            },
        )
    blocked = amb.block(kind=ask_kind, prompt=prompt, options=options)
    wait_result = gate_response_wait_payload(
        config,
        gate_id=gate.id,
        lifecycle_id=blocked.id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        allow_ungated_entries=False,
        sleep=sleep,
        monotonic=monotonic,
    )
    wait_info: dict[str, Any] = {
        "state": wait_result["state"],
        "gateId": wait_result["gateId"],
        "lifecycleId": blocked.id,
        "timedOut": wait_result["timedOut"],
        "entryCount": wait_result.get("entryCount", 0),
        "entries": wait_result.get("entries", []),
        "timeoutSeconds": timeout_seconds,
        "pollSeconds": poll_seconds,
    }
    for key in ("decidedBy", "decidedVia", "decisionNote"):
        if wait_result.get(key) is not None:
            wait_info[key] = wait_result[key]
    payload: dict[str, Any] = {
        "ok": True,
        "operation": "lifecycle_gate",
        "gate": {
            "id": gate.id,
            "kind": gate.kind,
            "state": wait_result["state"],
            "lifecycleId": gate.lifecycleId,
        },
        "lifecycle": {
            "id": blocked.id,
            "state": blocked.state,
            "phase": blocked.phase,
        },
        "wait": wait_info,
    }
    if structured_ask is not None:
        payload["ask"] = structured_ask
    return _tool_payload("lifecycle_gate", payload)


def gate_decide_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    decision: str,
    decided_by: str | None,
    decided_via: DecidedVia,
    deciding_role: str | None = None,
    note: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if decision not in DECISION_STATES:
        raise ValueError(
            f"unknown gate decision {decision!r}; expected one of {sorted(DECISION_STATES)}"
        )
    store = _store(config)
    gate = store.current(lifecycle_id).get(gate_id)
    if gate is None and lifecycle_id is None:
        # Packet-carried gate ids: the deciding seat holds only the id; resolve it
        # across lifecycles server-side (lifecycle ids never pass through the model).
        gate = store.find(gate_id)
    if gate is None:
        raise KeyError(f"no gate {gate_id!r} on lifecycle {lifecycle_id!r}")
    if decided_via == "cli" and decision != "cancel":
        policy = config.orchestration.gate_policy if config is not None else DEFAULT_GATE_POLICY
        if policy.rule_for(gate.kind).delegated_role is not None:
            raise ValueError(
                f"{gate.kind} is delegated by the active gate policy; pass deciding_role "
                "for an attributed orchestration decision, or leave it to the developer"
            )
    actor = _resolve_deciding_actor(decided_by, decided_via)
    evidence = _evidence_refs(evidence_refs)
    updated = decide_gate(
        gate,
        decision=decision,
        by=actor,
        via=decided_via,
        deciding_role=deciding_role,
        note=note,
        now=now_iso(),
        evidence_refs=evidence,
    )
    if decided_via == "orchestration":
        policy = config.orchestration.gate_policy if config is not None else DEFAULT_GATE_POLICY
        failure = delegated_decision_failure_reason(updated, policy)
        if failure is not None:
            raise ValueError(f"gate decision rejected by delegation policy: {failure}")
    store.append(updated)
    if decision == "cancel":
        store.delete(updated.id, updated.lifecycleId)
        _inbox_store(config).delete_by_gate(updated.id)
    if getattr(config, "coordination_root", None) is not None:
        # R2 fulfillment: any terminal decision (approve/reject/cancel) meets the gate's
        # verdict-by expectation row, stopping the L2 sweep from flagging it overdue.
        expectations = _expectation_store(config)
        row = expectations.find_by_source(updated.id, kind="verdict-by")
        if row is not None:
            expectations.mark_met(row.id, now=now_iso())
    return _tool_payload(
        "gate_decide",
        {
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
        },
    )


def gate_decide_for_lifecycle(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str,
    decision: str,
    decided_by: str | None,
    decided_via: DecidedVia,
    deciding_role: str | None = None,
    expected_gate_id: str | None = None,
    note: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
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
        raise KeyError(f"gate {expected_gate_id!r} is {state}; current open gate is {gate.id!r}")
    return gate_decide_payload(
        config,
        gate_id=gate.id,
        lifecycle_id=lifecycle_id,
        decision=decision,
        decided_by=decided_by,
        decided_via=decided_via,
        deciding_role=deciding_role,
        note=note,
        evidence_refs=evidence_refs,
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
    timeout_seconds: float | None = GATE_RESPONSE_WAIT_TIMEOUT_SECONDS,
    poll_seconds: float = GATE_RESPONSE_WAIT_POLL_SECONDS,
    allow_ungated_entries: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Bounded wait for either a gate decision or a dashboard Chat inbox entry.

    The lower-level helper owns the compatibility wait window: one call polls
    every five seconds for up to five minutes by default. Pass
    ``timeout_seconds=None`` for a blocking wait. Returned inbox entries are not
    consumed; call ``operator_inbox_consume`` after reading each handled entry.
    """
    gate_store = _store(config)
    inbox_store = _inbox_store(config)
    deadline = None if timeout_seconds is None else monotonic() + timeout_seconds
    while True:
        gate = gate_store.current(lifecycle_id).get(gate_id)
        if gate is None:
            return _cancelled_wait_payload("gate_response_wait", gate_id)
        entries: list[OperatorInboxEntry] = []
        if lifecycle_id is not None or agent_id is not None:
            entries = [
                entry
                for entry in inbox_store.list_pending(lifecycle_id=lifecycle_id, agent_id=agent_id)
                if entry.gateId == gate_id or (allow_ungated_entries and entry.gateId is None)
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
        if deadline is not None and monotonic() >= deadline:
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
    if lifecycle_id is None:
        # Ambient-defaulting (matching the other lifecycle-scoped tools): with no
        # explicit id, list the ACTIVE lifecycle's gates — so a raiser can poll its
        # own gate without ever handling a lifecycle id (they stay server-side).
        # The workspace log is the fallback only when no lifecycle is active.
        amb = ambient()
        current = amb.current if amb is not None else None
        if current is not None:
            lifecycle_id = current.id
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
