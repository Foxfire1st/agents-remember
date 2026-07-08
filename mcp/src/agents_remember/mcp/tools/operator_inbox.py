"""Payload builders for the ``operator_inbox_*`` external-chat return channel."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.expectation_rows import ExpectationRowStore, write_expectation_row
from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    InboxMessageKind,
    OperatorInboxEntry,
    OperatorInboxVia,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.signal_routing import RoutedOwner, derive_signal_owner
from agents_remember.kernel.agentic_settings import (
    DEFAULT_EXPECTATION_SLA_SECONDS,
    load_agentic_settings,
)
from agents_remember.observer import observer_root
from agents_remember.observer.events import now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.inbox_delivery import deliver_inbox_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.serving.terminal_paste import TerminalPaster

from .base import _tool_payload

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


def _store(config: McpRuntimeConfig) -> OperatorInboxStore:
    return OperatorInboxStore(observer_root(config))


def _expectation_store(config: McpRuntimeConfig) -> ExpectationRowStore:
    return ExpectationRowStore(observer_root(config))


def _expectation_sla_seconds(config: McpRuntimeConfig | None, kind: str) -> float:
    """The configured SLA for ``kind`` (``orchestration.expectations.defaults``), or the
    documented default when ``config`` carries none (or is absent, e.g. an injected-store test)."""
    if config is None:
        return DEFAULT_EXPECTATION_SLA_SECONDS[kind]
    return load_agentic_settings(config.coordination_root).expectations.sla_for(kind)


def _entry_payload(entry: OperatorInboxEntry) -> dict[str, Any]:
    return entry.model_dump(mode="json", by_alias=True, exclude_none=True)


def operator_inbox_post_payload(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str | None,
    agent_id: str | None,
    ask: str,
    response: str,
    created_by: str,
    created_via: OperatorInboxVia,
    gate_id: str | None = None,
    sender_agent_id: str | None = None,
    sender_role: AgentRole | None = None,
    recipient_role: AgentRole | None = None,
    message_kind: InboxMessageKind = "message",
    artifact_path: str | None = None,
    deliver_to_hosted: bool = True,
    terminal_catalog: TerminalCatalog | None = None,
    terminal_host: TerminalHost | None = None,
    terminal_paster: TerminalPaster | None = None,
) -> dict[str, Any]:
    catalog = terminal_catalog or (
        TerminalCatalog(terminal_catalog_path(config.coordination_root)) if config is not None else None
    )
    owner = derive_signal_owner(catalog, sender_agent_id=sender_agent_id, message_kind=message_kind) if (
        catalog is not None
    ) else RoutedOwner()
    entry = create_operator_inbox_entry(
        entry_id=new_ulid(),
        now=now_iso(),
        lifecycle_id=lifecycle_id,
        agent_id=agent_id,
        gate_id=gate_id,
        ask=ask,
        response=response,
        created_by=created_by,
        created_via=created_via,
        sender_agent_id=sender_agent_id,
        sender_role=sender_role,
        recipient_role=recipient_role,
        message_kind=message_kind,
        artifact_path=artifact_path,
        owner_role=owner.role,
        owner_agent_id=owner.agent_id,
        owner_lifecycle_id=owner.lifecycle_id,
    )
    store = _store(config)
    store.append(entry)
    store.compact(now=datetime.now(UTC))
    # R2: the signal-post -> ack-by expectation row, written atomically in the SAME call as the
    # post (never a follow-up step a caller could forget). consume=ack fulfills it.
    if config is not None:
        write_expectation_row(
            _expectation_store(config),
            row_id=new_ulid(),
            now=datetime.now(UTC),
            kind="ack-by",
            sla_seconds=_expectation_sla_seconds(config, "ack-by"),
            source_id=entry.id,
            subject_agent_id=agent_id,
            subject_lifecycle_id=lifecycle_id,
            note=f"ack-by: {message_kind} to {recipient_role or agent_id or lifecycle_id}",
        )
    if deliver_to_hosted:
        entry = deliver_inbox_entry(
            store=store,
            catalog=catalog or TerminalCatalog(terminal_catalog_path(config.coordination_root)),
            host=terminal_host or TerminalHost(),
            paster=terminal_paster or TerminalPaster(),
            entry=entry,
            submit=True,
        )
    return _tool_payload(
        "operator_inbox_post",
        {
            "ok": True,
            "operation": "operator_inbox_post",
            "entryId": entry.id,
            "state": entry.state,
            "lifecycleId": entry.lifecycleId,
            "agentId": entry.agentId,
            "senderAgentId": entry.senderAgentId,
            "senderRole": entry.senderRole,
            "recipientRole": entry.recipientRole,
            "ownerRole": entry.ownerRole,
            "ownerAgentId": entry.ownerAgentId,
            "ownerLifecycleId": entry.ownerLifecycleId,
            "gateId": entry.gateId,
            "messageKind": entry.messageKind,
            "artifactPath": entry.artifactPath,
            "deliveryState": entry.deliveryState,
            "deliveredAt": entry.deliveredAt,
            "deliveredToSession": entry.deliveredToSession,
            "deliveryDetail": entry.deliveryDetail,
        },
    )


def operator_inbox_poll_payload(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str | None,
    agent_id: str | None,
    recipient_role: AgentRole | None = None,
) -> dict[str, Any]:
    entries = _store(config).list_pending(
        lifecycle_id=lifecycle_id,
        agent_id=agent_id,
        recipient_role=recipient_role,
    )
    return _tool_payload(
        "operator_inbox_poll",
        {
            "ok": True,
            "operation": "operator_inbox_poll",
            "lifecycleId": lifecycle_id,
            "agentId": agent_id,
            "recipientRole": recipient_role,
            "entryCount": len(entries),
            "entries": [_entry_payload(entry) for entry in entries],
        },
    )


def operator_inbox_consume_payload(
    config: McpRuntimeConfig,
    *,
    entry_id: str,
    consumed_by: str,
    consumed_via: OperatorInboxVia,
) -> dict[str, Any]:
    store = _store(config)
    entry, consumed_now = store.consume(
        entry_id,
        now=now_iso(),
        consumed_by=consumed_by,
        consumed_via=consumed_via,
    )
    store.delete(entry.id)
    if consumed_now and config is not None:
        # R1: consume=ack is the ONLY terminal delivery outcome; it also fulfills the signal's
        # ack-by expectation row (R2), so redelivery/backoff and the deadline sweep both stop.
        expectations = _expectation_store(config)
        row = expectations.find_by_source(entry.id, kind="ack-by")
        if row is not None:
            expectations.mark_met(row.id, now=now_iso())
    return _tool_payload(
        "operator_inbox_consume",
        {
            "ok": True,
            "operation": "operator_inbox_consume",
            "entryId": entry.id,
            "state": entry.state,
            "consumedNow": consumed_now,
            "consumedAt": entry.consumedAt,
        },
    )
