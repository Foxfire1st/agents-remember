"""Pure helper observations; a dispatch receipt is not a live-roster sample.

Hermes receipts are bound by the eight retained CE05/CE06 records in
tests/fixtures/helper-wait-real-dispatches.json. Host carriers follow
paseo_plugin/server/subagents.ts. No process-local roster or Hermes ACP re-entry
carrier is present in those sources: these remain explicitly unobservable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..artifacts import json_digest
from .models import NativeCapture, NativeEvent

_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_HELPER_TOOLS = {"delegate_task", "spawn_agent", "wait_agent", "send_input", "close_agent"}


def _reference(event: NativeEvent) -> dict[str, Any]:
    return {
        "source": event.source,
        "source_sha256": event.source_sha256,
        "sequence": event.sequence,
        "session_id": event.session_id,
        "native_timestamp": event.native_timestamp,
        "payload_sha256": json_digest(event.payload),
    }


def _object(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _owner(capture: NativeCapture) -> str | None:
    identities = set()
    for record in capture.raw_records:
        actor = _object(record.get("paseoActorIdentity"))
        persistence = _object(actor.get("persistence"))
        if persistence.get("sessionId") == capture.session_id and isinstance(actor.get("id"), str):
            identities.add(actor["id"])
    return next(iter(identities)) if len(identities) == 1 else None


def _identities(value: object) -> list[str]:
    return value if isinstance(value, list) and all(isinstance(x, str) and x for x in value) else []


@dataclass
class _Facts:
    capture: NativeCapture
    calls: dict[tuple[str, str, str], NativeEvent] = field(default_factory=dict)
    helpers: dict[str, dict[str, Any]] = field(default_factory=dict)
    unknowns: list[dict[str, Any]] = field(default_factory=list)
    turns: dict[str, dict[str, Any]] = field(default_factory=dict)
    dispatches: set[tuple[str, str]] = field(default_factory=set)
    sources: set[tuple[str, str]] = field(init=False)
    owner: str | None = field(init=False)

    def __post_init__(self) -> None:
        self.sources = {(source.path, source.sha256) for source in self.capture.source_files}
        self.owner = _owner(self.capture)

    def unknown(self, event: NativeEvent, reason: str) -> None:
        self.unknowns.append({"reason": reason, **_reference(event)})

    def helper(self, identity: str) -> dict[str, Any]:
        return self.helpers.setdefault(
            identity,
            {
                "id": identity,
                "delegation_ids": [],
                "dispatches": [],
                "observations": [],
                "observed_status": "unknown",
                "liveness": "unobservable",
            },
        )

    def consume(self, event: NativeEvent) -> None:
        if (
            event.source,
            event.source_sha256,
        ) not in self.sources or event.session_id != self.capture.session_id:
            self.unknown(event, "event lacks exact capture source/session binding")
            return
        if event.kind == "tool_call":
            self.call(event)
        elif event.kind == "tool_result":
            self.result(event)
        elif event.payload.get("type") == "agent.provider_subagents.update":
            self.host_update(event)
        elif event.payload.get("type") == "agent.turn_ended":
            self.turn(event)
        else:
            self.other(event)

    def call(self, event: NativeEvent) -> None:
        name = event.payload.get("name")
        if name == "delegate_task":
            identity = event.payload.get("call_id")
            if isinstance(identity, str) and identity:
                self.calls[(event.source, event.source_sha256, identity)] = event
            else:
                self.unknown(event, "delegate_task call has no native call_id")
        elif isinstance(name, str) and name.rsplit(".", 1)[-1] in _HELPER_TOOLS:
            self.unknown(event, f"unsupported helper tool carrier: {name}")

    def result(self, event: NativeEvent) -> None:
        identity = event.payload.get("call_id")
        call = (
            self.calls.get((event.source, event.source_sha256, identity))
            if isinstance(identity, str)
            else None
        )
        if call is None:
            if event.payload.get("name") == "delegate_task":
                self.unknown(event, "delegate_task result has no preceding source-bound call")
            return
        if call.sequence >= event.sequence:
            self.unknown(event, "delegate_task result does not follow its call")
            return
        output = _object(event.payload.get("output"))
        ids, delegation = _identities(output.get("subagent_ids")), output.get("delegation_id")
        if not (
            output.get("status") == "dispatched"
            and output.get("mode") == "background"
            and isinstance(delegation, str)
            and delegation
            and ids
        ):
            self.unknown(event, "unsupported delegate_task result shape; no live state inferred")
            return
        for child in ids:
            helper = self.helper(child)
            if (child, delegation) not in self.dispatches:
                self.dispatches.add((child, delegation))
                helper["delegation_ids"].append(delegation)
                helper["dispatches"].append({"call": _reference(call), **_reference(event)})

    def host_update(self, event: NativeEvent) -> None:
        payload = _object(event.payload.get("payload"))
        helper = _object(payload.get("subagent"))
        parent = (
            helper.get("parentAgentId")
            if payload.get("kind") == "upsert"
            else payload.get("parentAgentId")
        )
        if self.owner is None:
            self.unknown(event, "host helper update lacks exact owner binding")
        elif parent != self.owner:
            return
        elif payload.get("kind") == "upsert":
            self.host_status(event, helper)
        elif payload.get("kind") == "remove" and isinstance(payload.get("subagentId"), str):
            self.observe(event, payload["subagentId"], "removed")
        elif payload.get("kind") != "timeline":
            self.unknown(event, f"unsupported host helper update kind: {payload.get('kind')!r}")

    def host_status(self, event: NativeEvent, helper: dict[str, Any]) -> None:
        if not all(
            isinstance(helper.get(key), str) and helper[key]
            for key in ("id", "status", "createdAt")
        ):
            self.unknown(event, "host helper upsert has incomplete native identity/status fields")
            return
        status = helper["status"]
        self.observe(event, helper["id"], status)
        if status not in _TERMINAL | {"running"}:
            self.unknown(event, f"unclassified host helper status: {status!r}")

    def observe(self, event: NativeEvent, identity: str, status: str) -> None:
        helper = self.helper(identity)
        if status != "removed" or helper["observed_status"] not in _TERMINAL:
            helper["observed_status"] = status
        helper["observations"].append(
            {"status": status, "carrier": "host-provider-update", **_reference(event)}
        )

    def turn(self, event: NativeEvent) -> None:
        payload = event.payload
        if self.owner is not None and _object(payload.get("agent")).get("id") == self.owner:
            identity = payload.get("turnId")
            if isinstance(identity, str) and identity:
                self.turns.setdefault(identity, {"turn_id": identity, **_reference(event)})

    def other(self, event: NativeEvent) -> None:
        kind = event.payload.get("type")
        if isinstance(kind, str) and ("subagent" in kind or kind.startswith("collab_")):
            self.unknown(event, f"unsupported helper native carrier: {kind}")
        content = event.payload.get("content")
        if (
            event.kind == "message"
            and event.payload.get("role") == "user"
            and isinstance(content, str)
            and content.startswith(
                ("[ASYNC DELEGATION COMPLETE", "[ASYNC DELEGATION BATCH COMPLETE")
            )
        ):
            self.unknown(event, "completion-looking user text has no admitted re-entry carrier")

    def snapshot(self) -> dict[str, Any]:
        helpers = list(self.helpers.values())
        return {
            "schema": "ar-helper-wait-snapshot/v1",
            "helpers": helpers,
            "pending_ids": [h["id"] for h in helpers if h["observed_status"] not in _TERMINAL],
            "running_ids": [],
            "observed_running_ids": [h["id"] for h in helpers if h["observed_status"] == "running"],
            "terminal_ids": [h["id"] for h in helpers if h["observed_status"] in _TERMINAL],
            "liveness": "unobservable",
            "liveness_basis": "no admitted process-local live-roster carrier in NativeCapture",
            "reentries": [],
            "continuations": list(self.turns.values())[1:],
            "continuation_basis": "later scoped actor turn ends; helper causality unproved",
            "unknowns": self.unknowns,
        }


def helper_wait_snapshot(capture: NativeCapture) -> dict[str, Any]:
    """Reduce this finite capture in linear time, with no I/O or persistent state.

    Pending means unresolved in the capture. Host running observations are not
    process-local live-roster evidence. Unsupported re-entry shapes stay unknown.
    """
    facts = _Facts(capture)
    for event in capture.events:
        facts.consume(event)
    return facts.snapshot()
