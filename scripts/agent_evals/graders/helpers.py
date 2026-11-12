"""HW1: native helper states, scoped turn ends, and actual wait-check delivery.

Recognized host shapes come from package_data/paseo_plugin/server/subagents.ts,
turn_events.ts and turn_ended.ts. Other shapes remain unknown. Normalized caller
assertions are checked against these source fields and cannot create evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from ..adapters.helper_wait import TERMINAL_HELPER_STATUSES
from ..adapters.models import NativeCapture, NativeEvent
from .models import Grade, native_reference, referenced_events

HelperKind = Literal[
    "helper_running",
    "helper_stopped",
    "turn_end",
    "wait_declared",
    "progress_sent",
    "progress_received",
    "progress_skipped",
    "wait_ended",
    "clock",
]


@dataclass(frozen=True, slots=True)
class HelperEvent:
    sequence: int
    kind: HelperKind
    helper_id: str | None = None
    wait_id: int | None = None
    declared_wait: bool | None = None
    at: str | None = None


@dataclass(frozen=True, slots=True)
class HelperObservation:
    capture: NativeCapture
    owner_id: str
    events: tuple[HelperEvent, ...] = ()
    complete: bool = False


def _declaration(text: str) -> bool:
    # Exact product declaration grammar on the last assistant message only.
    return any(
        line.lstrip(' \t*_>#`"-').startswith("WAITING FOR SUBAGENTS:") for line in text.splitlines()
    )


def _host_helper(event: NativeEvent, owner: str) -> HelperEvent | None:
    value = event.payload.get("payload")
    if not isinstance(value, dict):
        return None
    if value.get("kind") == "upsert":
        helper = value.get("subagent")
        if not isinstance(helper, dict) or helper.get("parentAgentId") != owner:
            return None
        if not all(
            isinstance(helper.get(key), str) and helper[key]
            for key in ("id", "status", "createdAt")
        ) or helper["status"] not in TERMINAL_HELPER_STATUSES | {"running"}:
            return None
        kind = "helper_running" if helper["status"] == "running" else "helper_stopped"
        return HelperEvent(event.sequence, kind, helper_id=helper["id"])
    if value.get("kind") == "remove" and value.get("parentAgentId") == owner:
        identity = value.get("subagentId")
        if isinstance(identity, str) and identity:
            return HelperEvent(event.sequence, "helper_stopped", helper_id=identity)
    return None


def _host_turn(event: NativeEvent, owner: str) -> HelperEvent | None:
    agent, timeline, turn = (event.payload.get(key) for key in ("agent", "timeline", "turnId"))
    if not isinstance(agent, dict) or agent.get("id") != owner:
        return None
    if not isinstance(timeline, list) or not isinstance(turn, str) or not turn:
        return None  # No invented own-turn scoping when the native boundary is missing.
    messages = [
        row
        for row in timeline
        if isinstance(row, dict)
        and row.get("turnId") == turn
        and row.get("type") == "assistant_message"
        and isinstance(row.get("text"), str)
    ]
    declared = _declaration(messages[-1]["text"]) if messages else False
    return HelperEvent(event.sequence, "turn_end", declared_wait=declared)


def _turn_log(event: NativeEvent, owner: str) -> HelperEvent | None:
    row = event.payload
    fields = ("at", "agent", "role", "task", "kind", "reason")
    if not all(isinstance(row.get(key), str) for key in fields) or row["agent"] != owner:
        return None
    ordinal = row.get("count")
    if type(ordinal) is not int or ordinal < 1:
        return None
    try:
        stamp = datetime.fromisoformat(row["at"])
        if stamp.tzinfo is None:
            return None
    except ValueError:
        return None
    names: dict[str, HelperKind] = {
        "subagent-wait-declared": "wait_declared",
        "subagent-wait-check-sent": "progress_sent",
        "subagent-wait-check-skipped": "progress_skipped",
        "subagent-wait-ended": "wait_ended",
    }
    return HelperEvent(
        event.sequence, names.get(row["kind"], "clock"), wait_id=ordinal, at=row["at"]
    )


def _user_text(payload: dict[str, Any]) -> str | None:
    if payload.get("role") != "user":
        return None
    content = payload.get("content")
    if isinstance(content, str):  # Hermes messages table content.
        return content
    if not isinstance(content, list):
        return None
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") in ("text", "input_text")
        and isinstance(block.get("text"), str)
    ]
    return "\n".join(parts)


def _progress_received(event: NativeEvent, owner: str) -> HelperEvent | None:
    text = _user_text(event.payload)
    first = text.splitlines()[0] if text else ""
    match = re.match(r"^\[ar-plugin\] subagent wait: \S+ (\S+) · ", first)
    if match and match[1] == owner:
        return HelperEvent(event.sequence, "progress_received")
    return None


def _fact(event: NativeEvent, owner: str) -> HelperEvent | None:
    if event.kind == "message":
        return _progress_received(event, owner)
    if event.kind not in ("native", "turn_end_log"):
        return None
    native_type = event.payload.get("type")
    if native_type == "agent.provider_subagents.update":
        return _host_helper(event, owner)
    if native_type == "agent.turn_ended":
        return _host_turn(event, owner)
    if native_type == "task_complete":
        text = event.payload.get("last_agent_message")
        return (
            HelperEvent(event.sequence, "turn_end", declared_wait=_declaration(text))
            if isinstance(text, str)
            else None
        )
    return _turn_log(event, owner)


@dataclass(slots=True)
class _Wait:
    sequence: int
    wait_id: int | None = None
    at: datetime | None = None
    received: bool = False
    ended: bool = False


@dataclass(slots=True)
class _State:
    running: set[str]
    started: set[str]
    waits: list[_Wait]
    failures: list[str]
    unknowns: list[str]
    helper_evidence: bool = False
    turn_ends: int = 0
    observed_until: datetime | None = None


def _wait_by_id(state: _State, identity: int | None) -> _Wait | None:
    return next((wait for wait in reversed(state.waits) if wait.wait_id == identity), None)


def _observe_wait(state: _State, fact: HelperEvent) -> None:
    stamp = datetime.fromisoformat(fact.at) if fact.at is not None else None
    if stamp is not None:
        state.observed_until = max(state.observed_until or stamp, stamp)
    if fact.kind == "wait_declared":
        pending = state.waits[-1] if state.waits and state.waits[-1].wait_id is None else None
        if pending is None:
            pending = _Wait(fact.sequence)
            state.waits.append(pending)
        pending.wait_id, pending.at = fact.wait_id, stamp
    elif fact.kind == "progress_received":
        pending = next(
            (wait for wait in reversed(state.waits) if not wait.ended and not wait.received), None
        )
        if pending is None:
            state.unknowns.append(
                f"event {fact.sequence}: received check has no captured declaration"
            )
        else:
            pending.received = True
    elif fact.kind in ("progress_skipped", "wait_ended"):
        pending = _wait_by_id(state, fact.wait_id)
        if pending is None:
            state.unknowns.append(
                f"event {fact.sequence}: wait ordinal has no captured declaration"
            )
        elif fact.kind == "progress_skipped":
            state.failures.append(f"event {fact.sequence}: declared wait check was skipped")
        else:
            pending.ended = True
            if (
                pending.at is not None
                and stamp is not None
                and stamp >= pending.at + timedelta(minutes=3)
                and not pending.received
            ):
                state.unknowns.append(
                    f"event {fact.sequence}: wait ended after check due without captured receipt"
                )


def _observe(state: _State, fact: HelperEvent) -> None:
    if fact.kind in ("helper_running", "helper_stopped"):
        state.helper_evidence = True
        if fact.helper_id is not None:
            if fact.kind == "helper_running":
                state.running.add(fact.helper_id)
                state.started.add(fact.helper_id)
            else:
                if fact.helper_id not in state.started:
                    state.unknowns.append(
                        f"event {fact.sequence}: helper stop has no captured start: {fact.helper_id}"
                    )
                state.running.discard(fact.helper_id)
    elif fact.kind == "turn_end":
        state.turn_ends += 1
        if state.running and not fact.declared_wait:
            state.failures.append(
                f"event {fact.sequence}: silent turn end with running helpers: {', '.join(sorted(state.running))}"
            )
        if fact.declared_wait:
            state.waits.append(_Wait(fact.sequence))
    else:
        _observe_wait(state, fact)


def _finish(state: _State, observation: HelperObservation) -> Grade:
    for wait in state.waits:
        if wait.received or wait.ended:
            continue
        if (
            observation.complete
            and wait.at is not None
            and state.observed_until is not None
            and state.observed_until >= wait.at + timedelta(minutes=3)
        ):
            state.failures.append(
                f"declaration at event {wait.sequence}: no received progress check in complete due window"
            )
        else:
            state.unknowns.append(
                f"declaration at event {wait.sequence}: progress-check window/receipt incomplete"
            )
    if not observation.complete:
        state.unknowns.append("native observation window is incomplete")
    if not state.helper_evidence or not state.turn_ends:
        state.unknowns.append("actual helper-state and scoped turn-end evidence are both required")
    if state.running:
        state.unknowns.append("capture ends while helpers remain running")
    refs = tuple(native_reference(event) for event in observation.capture.events)
    if state.failures:
        return Grade.produced(__file__, "HW1", "fail", (*refs, *state.failures))
    if state.unknowns:
        return Grade.produced(__file__, "HW1", "unknown", (*refs, *state.unknowns))
    return Grade.produced(__file__, "HW1", "pass", refs)


def _known_non_helper_native(event: NativeEvent, harness: str) -> bool:
    """Known Codex event_msg/response_item data retained by _codex_event.

    Session/context/usage/message/tool-result records already have separate
    normalized kinds. Hermes uses those kinds exclusively; Claude thinking
    stays inside its normalized message. Unrecognized native Claude/Hermes
    carriers, and future helper/turn-capable Codex tags, are never ignored.
    """
    if harness != "codex":
        return False
    payload = event.payload
    kind = payload.get("type")
    if kind in ("agent_reasoning", "agent_reasoning_raw_content"):
        return isinstance(payload.get("text"), str)
    if kind in ("agent_message", "user_message"):
        return isinstance(payload.get("message"), str)
    if kind == "task_started":
        return isinstance(payload.get("turn_id"), str) and bool(payload["turn_id"])
    if kind == "reasoning":
        summary = payload.get("summary")
        return isinstance(summary, list) and all(
            isinstance(part, dict)
            and part.get("type") == "summary_text"
            and isinstance(part.get("text"), str)
            for part in summary
        )
    return False


def grade_helpers(observation: HelperObservation) -> Grade:
    events, issues = referenced_events(
        observation.capture, [event.sequence for event in observation.capture.events]
    )
    state = _State(set(), set(), [], [], [*issues, *observation.capture.unknowns])
    extracted: dict[int, HelperEvent] = {}
    for event in events:
        fact = _fact(event, observation.owner_id)
        if fact is not None:
            extracted[event.sequence] = fact
            _observe(state, fact)
        elif event.kind == "native" and not _known_non_helper_native(
            event, observation.capture.harness
        ):
            state.unknowns.append(
                f"native event {event.sequence}: no supported helper/turn mapping"
            )
    for asserted in observation.events:
        if extracted.get(asserted.sequence) != asserted:
            state.unknowns.append(
                f"asserted helper event {asserted.sequence} does not match captured native fields"
            )
    return _finish(state, observation)
