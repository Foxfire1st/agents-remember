"""Durable gate and inbox synchronization for adapter-owned interactions and completion."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import (
    GateRecord,
    apply_gate,
    create_gate,
    expire_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.errors import HarnessControlError
from agents_remember.observer.events import now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.harness_control_client import (
    read_control_transcript,
    respond_control_interaction,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot, PendingInteraction
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry


class HostedInteractionSynchronizer:
    """Project vendor questions into gates and terminal results onto their durable inbox rows."""

    def __init__(self, root: Path) -> None:
        self._gates = GateStore(root)
        self._inbox = OperatorInboxStore(root)

    def observe(self, entry: TerminalCatalogEntry, snapshot: AdapterSnapshot) -> None:
        self._sync_interaction(entry, snapshot.pending_interaction)
        self._sync_completions(entry)

    def _sync_interaction(
        self, entry: TerminalCatalogEntry, interaction: PendingInteraction | None
    ) -> None:
        matching = self._interaction_gate(entry.id, interaction.interaction_id if interaction else None)
        if interaction is None:
            if matching is not None and matching.state == "open":
                self._gates.append(expire_gate(matching, now=now_iso()))
            return
        if matching is None:
            self._gates.append(
                create_gate(
                    kind="agent-question",
                    lifecycle_id=entry.lifecycle_id,
                    gate_id=new_ulid(),
                    now=now_iso(),
                    packet={
                        "adapterInteraction": {
                            "sessionId": entry.id,
                            "interactionId": interaction.interaction_id,
                            "kind": interaction.kind,
                            "prompt": interaction.prompt,
                            "choices": list(interaction.choices),
                            "createdAt": interaction.created_at,
                            "raw": dict(interaction.raw),
                        }
                    },
                    required_decision=(
                        list(interaction.choices)
                        if interaction.choices
                        else ["approve", "reject"]
                    ),
                )
            )
            return
        if matching.state not in {
            "approved",
            "rejected",
            "revision-requested",
            "cancelled",
        }:
            return
        try:
            respond_control_interaction(
                entry,
                interaction_id=interaction.interaction_id,
                response=_gate_response(matching),
            )
        except HarnessControlError:
            return
        self._gates.append(apply_gate(matching, now=now_iso()))

    def _interaction_gate(
        self, session_id: str, interaction_id: str | None
    ) -> GateRecord | None:
        if interaction_id is None:
            return next(
                (
                    gate
                    for gate in self._gates.all_current().values()
                    if gate.state == "open"
                    and (_interaction_identity(gate) or (None, None))[0] == session_id
                ),
                None,
            )
        return next(
            (
                gate
                for gate in self._gates.all_current().values()
                if _interaction_identity(gate) == (session_id, interaction_id)
            ),
            None,
        )

    def _sync_completions(self, entry: TerminalCatalogEntry) -> None:
        try:
            transcript = read_control_transcript(entry)
        except HarnessControlError:
            return
        current = self._inbox.current()
        for item in transcript:
            request_id = item.get("requestId")
            terminal_result = item.get("terminalResult")
            if (
                not isinstance(request_id, str)
                or request_id not in current
                or not isinstance(terminal_result, Mapping)
                or current[request_id].adapterDeliveryState == "completed"
            ):
                continue
            outcome = terminal_result.get("outcome")
            completed_at = terminal_result.get("completedAt")
            correlation = item.get("vendorCorrelationId")
            self._inbox.record_adapter_completion(
                request_id,
                now=completed_at if isinstance(completed_at, str) else now_iso(),
                vendor_correlation_id=correlation if isinstance(correlation, str) else None,
                detail=f"adapter terminal result: {outcome}",
                current=current,
            )


def _interaction_identity(gate: GateRecord) -> tuple[str, str | None] | None:
    raw = gate.packet.get("adapterInteraction")
    if not isinstance(raw, dict):
        return None
    session_id = raw.get("sessionId")
    interaction_id = raw.get("interactionId")
    if not isinstance(session_id, str) or not isinstance(interaction_id, str):
        return None
    return session_id, interaction_id


def _gate_response(gate: GateRecord) -> str:
    if gate.decisionNote:
        return gate.decisionNote
    return {
        "approved": "approved",
        "rejected": "rejected",
        "revision-requested": "request revision",
        "cancelled": "cancelled",
    }[gate.state]
