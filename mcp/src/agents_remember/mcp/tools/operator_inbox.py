"""Payload builders for the ``operator_inbox_*`` external-chat return channel."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.expectation_rows import write_expectation_row
from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    InboxMessageKind,
    OperatorInboxEntry,
    OperatorInboxVia,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.signal_routing import (
    RoutedOwner,
    derive_signal_owner,
    signal_leaf_key,
)
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.observer import observer_root
from agents_remember.observer.events import now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.dispatch_brief import DispatchBriefGate
from agents_remember.serving.inbox_delivery import deliver_inbox_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_paste import TerminalPaster

from .base import _tool_payload
from .dispatch_brief import (
    DispatchReadinessCheck,
    expectation_sla_seconds,
    expectation_store,
    fulfill_dispatch_expectation,
    require_dispatch_target,
    start_dispatch_expectations,
)

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


def _store(config: McpRuntimeConfig) -> OperatorInboxStore:
    return OperatorInboxStore(observer_root(config))


def _redelivery_floor_seconds(config: McpRuntimeConfig | None) -> float | None:
    if config is None:
        return None
    return load_agentic_settings(config.coordination_root).supervisor.redeliver_rate_limit_seconds


def _delivery_catalog(
    config: McpRuntimeConfig,
    catalog: TerminalCatalog | None,
) -> TerminalCatalog:
    if catalog is not None:
        return catalog
    return TerminalCatalog(terminal_catalog_path(config.coordination_root))


def _entry_payload(entry: OperatorInboxEntry) -> dict[str, Any]:
    return entry.model_dump(mode="json", by_alias=True, exclude_none=True)


def _signal_route(
    catalog: TerminalCatalog | None,
    *,
    sender_agent_id: str | None,
    message_kind: InboxMessageKind,
) -> tuple[RoutedOwner, str | None]:
    if catalog is None:
        return RoutedOwner(), None
    return (
        derive_signal_owner(
            catalog,
            sender_agent_id=sender_agent_id,
            message_kind=message_kind,
        ),
        signal_leaf_key(catalog, sender_agent_id=sender_agent_id),
    )


def _post_address(
    owner: RoutedOwner,
    *,
    message_kind: InboxMessageKind,
    lifecycle_id: str | None,
    agent_id: str | None,
    recipient_role: AgentRole | None,
) -> tuple[str | None, str | None, AgentRole | None]:
    """Completion/artifact signals address the current owner; ordinary peer messages stay explicit."""
    has_owner = (
        owner.agent_id is not None or owner.lifecycle_id is not None or owner.role is not None
    )
    if message_kind not in ("turn-report", "master-handover") or not has_owner:
        return lifecycle_id, agent_id, recipient_role
    return owner.lifecycle_id, owner.agent_id, owner.role


def _post_catalog(
    config: McpRuntimeConfig | None,
    supplied: TerminalCatalog | None,
) -> TerminalCatalog | None:
    if supplied is not None:
        return supplied
    if config is None:
        return None
    return TerminalCatalog(terminal_catalog_path(config.coordination_root))


def _dispatch_entry_fields(
    target: TerminalCatalogEntry | None,
    *,
    routed_leaf_key: str | None,
    sender_agent_id: str | None,
) -> tuple[str | None, str | None, str | None]:
    if target is None:
        return routed_leaf_key, None, sender_agent_id
    return target.binding_leaf_key, target.binding_role, target.id


def _persist_post(
    config: McpRuntimeConfig | None,
    store: OperatorInboxStore,
    entry: OperatorInboxEntry,
    dispatch_target: TerminalCatalogEntry | None,
    *,
    target_agent_id: str | None,
    target_lifecycle_id: str | None,
    target_role: AgentRole | None,
) -> None:
    store.append(entry)
    store.compact(now=datetime.now(UTC))
    if dispatch_target is not None:
        assert config is not None
        start_dispatch_expectations(config, entry, dispatch_target)
    if config is None:
        return
    write_expectation_row(
        expectation_store(config),
        row_id=new_ulid(),
        now=datetime.now(UTC),
        kind="ack-by",
        sla_seconds=expectation_sla_seconds(config, "ack-by"),
        source_id=entry.id,
        subject_agent_id=target_agent_id,
        subject_lifecycle_id=target_lifecycle_id,
        note=f"ack-by: {entry.messageKind} to {target_role or target_agent_id or target_lifecycle_id}",
    )


def _deliver_post(
    config: McpRuntimeConfig,
    *,
    deliver_to_hosted: bool,
    store: OperatorInboxStore,
    catalog: TerminalCatalog | None,
    host: TerminalHost,
    paster: TerminalPaster | None,
    entry: OperatorInboxEntry,
    dispatch_gate: DispatchBriefGate | None,
) -> OperatorInboxEntry:
    if not deliver_to_hosted:
        return entry
    return deliver_inbox_entry(
        store=store,
        catalog=_delivery_catalog(config, catalog),
        host=host,
        paster=paster or TerminalPaster(),
        entry=entry,
        submit=True,
        redelivery_floor_seconds=_redelivery_floor_seconds(config),
        dispatch_gate=dispatch_gate,
    )


def _finish_dispatch(
    config: McpRuntimeConfig,
    target: TerminalCatalogEntry | None,
    entry: OperatorInboxEntry,
) -> None:
    if target is not None:
        fulfill_dispatch_expectation(config, entry)


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
    dispatch_readiness: DispatchReadinessCheck | None = None,
    dispatch_gate: DispatchBriefGate | None = None,
) -> dict[str, Any]:
    catalog = _post_catalog(config, terminal_catalog)
    host = terminal_host or TerminalHost()
    dispatch_target = require_dispatch_target(
        message_kind=message_kind,
        agent_id=agent_id,
        deliver_to_hosted=deliver_to_hosted,
        catalog=catalog,
        host=host,
        readiness=dispatch_readiness,
    )
    owner, routed_leaf_key = _signal_route(
        catalog, sender_agent_id=sender_agent_id, message_kind=message_kind
    )
    leaf_key, seat_role, subject_agent_id = _dispatch_entry_fields(
        dispatch_target,
        routed_leaf_key=routed_leaf_key,
        sender_agent_id=sender_agent_id,
    )
    # Completion/artifact messages are hierarchy signals, not arbitrary peer messages. Address
    # them to the current routed owner in the same post that attempts hosted delivery; merely
    # recording owner metadata leaves the stale caller-supplied mailbox in control and reproduces
    # the reviewer-finished/manager-never-woken halt.
    target_lifecycle_id, target_agent_id, target_role = _post_address(
        owner,
        message_kind=message_kind,
        lifecycle_id=lifecycle_id,
        agent_id=agent_id,
        recipient_role=recipient_role,
    )
    created_at = now_iso()
    entry = create_operator_inbox_entry(
        entry_id=new_ulid(),
        now=created_at,
        lifecycle_id=target_lifecycle_id,
        agent_id=target_agent_id,
        gate_id=gate_id,
        ask=ask,
        response=response,
        created_by=created_by,
        created_via=created_via,
        sender_agent_id=sender_agent_id,
        sender_role=sender_role,
        recipient_role=target_role,
        message_kind=message_kind,
        artifact_path=artifact_path,
        leaf_key=leaf_key,
        seat_role=seat_role,
        subject_agent_id=subject_agent_id,
        owner_role=owner.role,
        owner_agent_id=owner.agent_id,
        owner_lifecycle_id=owner.lifecycle_id,
    )
    store = _store(config)
    _persist_post(
        config,
        store,
        entry,
        dispatch_target,
        target_agent_id=target_agent_id,
        target_lifecycle_id=target_lifecycle_id,
        target_role=target_role,
    )
    entry = _deliver_post(
        config,
        deliver_to_hosted=deliver_to_hosted,
        store=store,
        catalog=catalog,
        host=host,
        paster=terminal_paster,
        entry=entry,
        dispatch_gate=dispatch_gate,
    )
    _finish_dispatch(config, dispatch_target, entry)
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
            "adapterDeliveryState": entry.adapterDeliveryState,
            "adapterRequestId": entry.adapterRequestId,
            "adapterVendorCorrelationId": entry.adapterVendorCorrelationId,
            "adapterAcceptedAt": entry.adapterAcceptedAt,
            "adapterCompletedAt": entry.adapterCompletedAt,
            "adapterDeliveryDetail": entry.adapterDeliveryDetail,
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
    if consumed_now and config is not None:
        # R1: consume=ack is the ONLY terminal delivery outcome; it also fulfills the signal's
        # ack-by expectation row (R2), so redelivery/backoff and the deadline sweep both stop.
        expectations = expectation_store(config)
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
