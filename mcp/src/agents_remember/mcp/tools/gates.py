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

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from agents_remember.controlplane.durable_store import GATE_OWNERSHIP
from agents_remember.controlplane.expectation_rows import (
    Expectation,
    ExpectationRowStore,
    ExpectationSubject,
    write_expectation_row,
)
from agents_remember.controlplane.gate_policy import (
    DEFAULT_GATE_POLICY,
    SEAM_GATE_KINDS,
    GatePolicy,
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
    GateAnchor,
    GateEvidenceRef,
    GateKind,
    GateRecord,
    GateRequest,
    GateVerdict,
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
from agents_remember.observer.lifecycle_state import LifecycleError, LifecycleState
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


def _gate_policy(config: McpRuntimeConfig) -> GatePolicy:
    """The active delegation policy; a config-less caller gets the human-only default."""
    return config.orchestration.gate_policy if config is not None else DEFAULT_GATE_POLICY


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
        Expectation(
            kind="verdict-by",
            source_id=gate.id,
            subject=ExpectationSubject(lifecycle_id=gate.lifecycleId),
            note=f"verdict-by: {gate.kind} gate {gate.id}",
        ),
        row_id=new_ulid(),
        now=datetime.now(UTC),
        sla_seconds=_expectation_sla_seconds(config, "verdict-by"),
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


@dataclass(frozen=True)
class GateWait:
    """How a caller waits for a gate to resolve.

    ``block=False`` is raise-and-continue: the gate is opened and its id handed on in a
    packet instead of being waited out (policy reserves that for delegated seam kinds).
    While blocked, the caller polls every ``poll_seconds`` until ``timeout_seconds``
    elapses -- ``None`` waits indefinitely. ``sleep`` / ``monotonic`` are injected so
    tests drive the loop deterministically.
    """

    block: bool = True
    timeout_seconds: float | None = GATE_RESPONSE_WAIT_TIMEOUT_SECONDS
    poll_seconds: float = GATE_RESPONSE_WAIT_POLL_SECONDS
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class InboxWatch:
    """Which operator-inbox entries end a gate wait alongside a decision on the gate itself:
    entries addressed to ``agent_id``, plus -- when ``allow_ungated_entries`` -- entries
    carrying no gate id at all (an ordinary dashboard Chat reply)."""

    agent_id: str | None = None
    allow_ungated_entries: bool = True


@dataclass(frozen=True)
class GateRaise:
    """One ``lifecycle_gate`` raise: which gate to open and what it asks of the developer.

    ``kind``, ``anchor`` and ``request`` are the gate itself, in the same three pieces the
    record layer stores them; ``ask`` is the structured question (kind, prompt, options)
    the blocked lifecycle puts to the developer while it waits.
    """

    kind: str
    anchor: GateAnchor | None = None
    request: GateRequest | None = None
    ask: dict[str, Any] | None = None


BLOCKING_GATE_WAIT = GateWait(timeout_seconds=None)
"""Block until the gate is decided, however long that takes."""

SHORT_GATE_WAIT = GateWait(timeout_seconds=30.0, poll_seconds=1.0)
"""The low-level gate poll: thirty seconds at one-second intervals."""

DEFAULT_GATE_WAIT = GateWait()
"""The compatibility wait window: five seconds between looks, up to five minutes."""

ANY_INBOX_ENTRY = InboxWatch()
"""Any pending entry on the waiting lifecycle ends the wait, gate-tied or not."""


def gate_create_payload(
    config: McpRuntimeConfig,
    *,
    kind: str,
    anchor: GateAnchor | None = None,
    request: GateRequest | None = None,
) -> dict[str, Any]:
    now = now_iso()
    store = _store(config)
    anchor = anchor or GateAnchor()
    gate_lifecycle_id = _resolve_gate_lifecycle_id(anchor.lifecycle_id)
    for current in store.current(gate_lifecycle_id).values():
        if current.state == "open":
            store.append(expire_gate(current, now=now))
    gate = create_gate(
        coerce_gate_kind(kind),
        gate_id=new_ulid(),
        now=now,
        anchor=replace(anchor, lifecycle_id=gate_lifecycle_id),
        request=request,
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


def _gating_lifecycle(current: LifecycleState | None, lifecycle_id: str | None) -> LifecycleState:
    """The active lifecycle this gate raises against; only a running, matching one may gate."""
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
    return current


def _validated_ask(
    ask: dict[str, Any] | None,
) -> tuple[str | None, str | None, list[str] | None]:
    """Type-check the free-form ``ask`` mapping into the structured ask's three inputs."""
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
    return (
        cast(str | None, ask_kind_raw),
        cast(str | None, prompt_raw),
        cast(list[str] | None, options_raw),
    )


def _require_raise_and_continue_allowed(
    config: McpRuntimeConfig, gate_kind: GateKind, enclosure: str | None
) -> None:
    """Refuse a ``wait=false`` raise that policy does not permit.

    Raise-and-continue is reserved for SEAM kinds the active policy delegates. A
    human-decided kind must keep the blocking/notify contract, and a delegated
    non-seam kind (e.g. plan-approval) keeps it too — only the seam gate (the
    master-handover-approval the manager raises for the orchestrator) returns
    immediately so the raiser can post its packet; the gate id is the hand-off.
    The raise also requires the enforcement address: `enclosure` is the master
    task name the integrate guard matches the gate by, so an addressless
    wait=false gate could only ever fail open at the enforcement rung.

    Called before the caller's expire-sweep and append, so a refused raise persists
    no orphan open gate and expires no sibling (validate-then-mutate).
    """
    if gate_kind not in SEAM_GATE_KINDS:
        raise ValueError(
            f"lifecycle_gate wait=false is reserved for delegated seam kinds; {gate_kind} blocks"
        )
    if _gate_policy(config).rule_for(gate_kind).delegated_role is None:
        raise ValueError(
            f"lifecycle_gate wait=false requires a kind the active policy delegates; "
            f"{gate_kind} is not delegated"
        )
    if enclosure is None or not enclosure.strip():
        raise ValueError(
            f"a {gate_kind} raise-and-continue requires "
            "enclosure=<master task name> — the integration guard's address"
        )


def _raised_gate_payload(gate: GateRecord, current: LifecycleState) -> dict[str, Any]:
    """The immediate ``wait=false`` response: the gate is open and its id is the hand-off."""
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


def lifecycle_gate_payload(
    config: McpRuntimeConfig,
    raised: GateRaise,
    *,
    wait: GateWait = BLOCKING_GATE_WAIT,
) -> dict[str, Any]:
    amb = require_ambient()
    anchor = raised.anchor or GateAnchor()
    current = _gating_lifecycle(amb.current, anchor.lifecycle_id)
    ask_kind, prompt, options = _validated_ask(raised.ask)
    structured_ask = build_ask(ask_kind, prompt, options)

    gate_kind = coerce_gate_kind(raised.kind)
    if not wait.block:
        _require_raise_and_continue_allowed(config, gate_kind, anchor.enclosure)
    now = now_iso()
    store = _store(config)
    for open_gate in store.current(current.id).values():
        if open_gate.state == "open":
            store.append(expire_gate(open_gate, now=now))
    gate = create_gate(
        gate_kind,
        gate_id=new_ulid(),
        now=now,
        anchor=replace(anchor, lifecycle_id=current.id),
        request=raised.request,
    )
    store.append(gate)
    _write_verdict_by_row(config, gate)
    if not wait.block:
        return _raised_gate_payload(gate, current)
    blocked = amb.block(kind=ask_kind, prompt=prompt, options=options)
    wait_result = gate_response_wait_payload(
        config,
        gate_id=gate.id,
        lifecycle_id=blocked.id,
        inbox=InboxWatch(allow_ungated_entries=False),
        wait=wait,
    )
    wait_info: dict[str, Any] = {
        "state": wait_result["state"],
        "gateId": wait_result["gateId"],
        "lifecycleId": blocked.id,
        "timedOut": wait_result["timedOut"],
        "entryCount": wait_result.get("entryCount", 0),
        "entries": wait_result.get("entries", []),
        "timeoutSeconds": wait.timeout_seconds,
        "pollSeconds": wait.poll_seconds,
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


def _reclaim_gate_log(store: GateStore, lifecycle_id: str | None) -> None:
    """Reclaim one gate log here, in the process that owns it (260731-EFA-L5 R1).

    Gate compaction used to ride the dashboard's projection tick, which owns nothing about
    gates and raced this process's appends. The MCP server mints, decides, applies and deletes
    gates, so reclamation belongs to it -- and specifically to the moment a record BECOMES
    reclaimable, which is a terminal decision. It is deliberately NOT on the read paths:
    ``gate_list`` and the wait loop stay pure reads, so moving the reclaim pass here changes
    who prunes and when, never what a caller is shown. A reclaim failure must not cost the
    caller their decision, so it is suppressed here and retried after the next decision;
    ``GateStore.compact`` itself never swallows anything.

    Guarded on ownership because this function is NOT MCP-only: the dashboard decides gates by
    calling ``gate_decide_payload`` directly (see this module's docstring), in the process that
    declared itself the dashboard. Reclaiming there is precisely the misplaced reclaim pass this
    leaf removed, so the dashboard skips it and the MCP does it on its next decision.

    THE SUPPRESSION IS NAMED, NOT BROAD. It used to catch ``ValueError``, which
    ``pydantic.ValidationError`` subclasses -- the exact widened-except trap this leaf closed
    elsewhere. The two things ``compact`` can raise are an I/O failure and a torn line, so it now
    catches those two by name; a ``DurableStoreError`` (an unlockable filesystem, a rewrite without
    the lock) is a contract violation and still propagates.

    Suppressing at all is safe here for a reason that changed under R1 and must be re-checked by
    anyone who changes it back. A skipped reclaim can only leave garbage in the log, never remove
    a record: retention no longer prunes the ``applied`` snapshot of a kind a mutating tool
    consumes (``interaction_retention.CONSUMED_APPROVAL_GATE_KINDS``), so nothing this pass does or
    fails to do can decide anything. If that ever stops being true -- if reclamation is given a
    say over an authority record again -- this suppression stops being benign and must go with it.
    """
    if not GATE_OWNERSHIP.is_compaction_owner():
        return
    with contextlib.suppress(OSError, ValidationError):
        store.compact(lifecycle_id, now=datetime.now(UTC))


def _gate_to_decide(store: GateStore, gate_id: str, lifecycle_id: str | None) -> GateRecord:
    """Resolve the gate a decision targets, by lifecycle when given and by id otherwise."""
    gate = store.current(lifecycle_id).get(gate_id)
    if gate is None and lifecycle_id is None:
        # Packet-carried gate ids: the deciding seat holds only the id; resolve it
        # across lifecycles server-side (lifecycle ids never pass through the model).
        gate = store.find(gate_id)
    if gate is None:
        raise KeyError(f"no gate {gate_id!r} on lifecycle {lifecycle_id!r}")
    return gate


def _require_undelegated_cli_decision(config: McpRuntimeConfig, gate: GateRecord) -> None:
    """An unattributed agent decision cannot stand in for a role the policy delegates."""
    if _gate_policy(config).rule_for(gate.kind).delegated_role is not None:
        raise ValueError(
            f"{gate.kind} is delegated by the active gate policy; pass deciding_role "
            "for an attributed orchestration decision, or leave it to the developer"
        )


def _meet_verdict_by_expectation(config: McpRuntimeConfig, gate: GateRecord) -> None:
    """R2 fulfillment: any terminal decision (approve/reject/cancel) meets the gate's
    verdict-by expectation row, stopping the L2 sweep from flagging it overdue."""
    if getattr(config, "coordination_root", None) is None:
        return
    expectations = _expectation_store(config)
    row = expectations.find_by_source(gate.id, kind="verdict-by")
    if row is not None:
        expectations.mark_met(row.id, now=now_iso())


def gate_decide_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    verdict: GateVerdict,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if verdict.decision not in DECISION_STATES:
        raise ValueError(
            f"unknown gate decision {verdict.decision!r}; expected one of {sorted(DECISION_STATES)}"
        )
    store = _store(config)
    gate = _gate_to_decide(store, gate_id, lifecycle_id)
    if verdict.via == "cli" and verdict.decision != "cancel":
        _require_undelegated_cli_decision(config, gate)
    updated = decide_gate(
        gate,
        replace(verdict, by=_resolve_deciding_actor(verdict.by, verdict.via)),
        now=now_iso(),
        evidence_refs=_evidence_refs(evidence_refs),
    )
    if verdict.via == "orchestration":
        failure = delegated_decision_failure_reason(updated, _gate_policy(config))
        if failure is not None:
            raise ValueError(f"gate decision rejected by delegation policy: {failure}")
    store.append(updated)
    if verdict.decision == "cancel":
        store.delete(updated.id, updated.lifecycleId)
        _inbox_store(config).delete_by_gate(updated.id)
    _meet_verdict_by_expectation(config, updated)
    _reclaim_gate_log(store, updated.lifecycleId)
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
    verdict: GateVerdict,
    expected_gate_id: str | None = None,
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
    if verdict.decision not in DECISION_STATES:
        raise ValueError(
            f"unknown gate decision {verdict.decision!r}; expected one of {sorted(DECISION_STATES)}"
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
        verdict=verdict,
        evidence_refs=evidence_refs,
    )


def gate_wait_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    wait: GateWait = SHORT_GATE_WAIT,
) -> dict[str, Any]:
    """Bounded wait until the gate leaves ``open`` (or ``timeout_seconds``).

    A simple bounded poll: a real long-poll / push wakeup lands with enforcement
    in a later slice. ``sleep`` / ``monotonic`` are injectable for deterministic
    tests.
    """
    store = _store(config)
    deadline = None if wait.timeout_seconds is None else wait.monotonic() + wait.timeout_seconds
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
        if deadline is not None and wait.monotonic() >= deadline:
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
        wait.sleep(wait.poll_seconds)


def gate_response_wait_payload(
    config: McpRuntimeConfig,
    *,
    gate_id: str,
    lifecycle_id: str | None,
    inbox: InboxWatch = ANY_INBOX_ENTRY,
    wait: GateWait = DEFAULT_GATE_WAIT,
) -> dict[str, Any]:
    """Bounded wait for either a gate decision or a dashboard Chat inbox entry.

    The lower-level helper owns the compatibility wait window: one call polls
    every five seconds for up to five minutes by default. Pass
    ``timeout_seconds=None`` for a blocking wait. Returned inbox entries are not
    consumed; call ``operator_inbox_consume`` after reading each handled entry.
    """
    gate_store = _store(config)
    inbox_store = _inbox_store(config)
    deadline = None if wait.timeout_seconds is None else wait.monotonic() + wait.timeout_seconds
    while True:
        gate = gate_store.current(lifecycle_id).get(gate_id)
        if gate is None:
            return _cancelled_wait_payload("gate_response_wait", gate_id)
        entries: list[OperatorInboxEntry] = []
        if lifecycle_id is not None or inbox.agent_id is not None:
            entries = [
                entry
                for entry in inbox_store.list_pending(
                    lifecycle_id=lifecycle_id, agent_id=inbox.agent_id
                )
                if entry.gateId == gate_id or (inbox.allow_ungated_entries and entry.gateId is None)
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
        if deadline is not None and wait.monotonic() >= deadline:
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
        wait.sleep(wait.poll_seconds)


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
