"""Payload builders for the ``operator_inbox_*`` external-chat return channel."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.expectation_rows import (
    Expectation,
    ExpectationSubject,
    write_expectation_row,
)
from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    InboxAddress,
    InboxMessage,
    InboxMessageKind,
    InboxOwner,
    InboxPoster,
    InboxRouting,
    InboxSubject,
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
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    DeliveryAdmission,
    InboxDeliveryLog,
    RedeliveryFloor,
    deliver_inbox_entry,
)
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_paste import TerminalPaster

from .base import _tool_payload
from .dispatch_brief import (
    HOSTED_DELIVERY,
    HostedDelivery,
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
    address: InboxAddress,
) -> InboxAddress:
    """Completion/artifact signals address the current owner; ordinary peer messages stay explicit."""
    has_owner = (
        owner.agent_id is not None or owner.lifecycle_id is not None or owner.role is not None
    )
    if message_kind not in ("turn-report", "master-handover") or not has_owner:
        return address
    return InboxAddress(
        lifecycle_id=owner.lifecycle_id, agent_id=owner.agent_id, recipient_role=owner.role
    )


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
    address: InboxAddress,
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
        Expectation(
            kind="ack-by",
            source_id=entry.id,
            subject=ExpectationSubject(
                agent_id=address.agent_id, lifecycle_id=address.lifecycle_id
            ),
            note=(
                f"ack-by: {entry.messageKind} to "
                f"{address.recipient_role or address.agent_id or address.lifecycle_id}"
            ),
        ),
        row_id=new_ulid(),
        now=datetime.now(UTC),
        sla_seconds=expectation_sla_seconds(config, "ack-by"),
    )


def _deliver_post(
    config: McpRuntimeConfig,
    *,
    delivery: HostedDelivery,
    store: OperatorInboxStore,
    entry: OperatorInboxEntry,
) -> OperatorInboxEntry:
    if not delivery.enabled:
        return entry
    return deliver_inbox_entry(
        InboxDeliveryLog(
            store=store,
            entry=entry,
            floor=RedeliveryFloor(seconds=_redelivery_floor_seconds(config)),
        ),
        sessions=HostedSessionRuntime(
            catalog=_delivery_catalog(config, delivery.catalog),
            host=delivery.host or TerminalHost(),
        ),
        paster=delivery.paster or TerminalPaster(),
        admission=DeliveryAdmission(dispatch_gate=delivery.gate),
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
    address: InboxAddress,
    message: InboxMessage,
    poster: InboxPoster,
    delivery: HostedDelivery = HOSTED_DELIVERY,
) -> dict[str, Any]:
    catalog = _post_catalog(config, delivery.catalog)
    host = delivery.host or TerminalHost()
    delivery = replace(delivery, catalog=catalog, host=host)
    message_kind = message.message_kind
    dispatch_target = require_dispatch_target(
        message_kind=message_kind,
        agent_id=address.agent_id,
        delivery=delivery,
        host=host,
    )
    owner, routed_leaf_key = _signal_route(
        catalog, sender_agent_id=poster.sender_agent_id, message_kind=message_kind
    )
    leaf_key, seat_role, subject_agent_id = _dispatch_entry_fields(
        dispatch_target,
        routed_leaf_key=routed_leaf_key,
        sender_agent_id=poster.sender_agent_id,
    )
    # Completion/artifact messages are hierarchy signals, not arbitrary peer messages. Address
    # them to the current routed owner in the same post that attempts hosted delivery; merely
    # recording owner metadata leaves the stale caller-supplied mailbox in control and reproduces
    # the reviewer-finished/manager-never-woken halt.
    target = _post_address(owner, message_kind=message_kind, address=address)
    created_at = now_iso()
    entry = create_operator_inbox_entry(
        replace(
            message,
            subject=InboxSubject(leaf_key=leaf_key, seat_role=seat_role, agent_id=subject_agent_id),
        ),
        entry_id=new_ulid(),
        now=created_at,
        routing=InboxRouting(
            address=target,
            owner=InboxOwner(
                role=owner.role, agent_id=owner.agent_id, lifecycle_id=owner.lifecycle_id
            ),
        ),
        poster=poster,
    )
    store = _store(config)
    _persist_post(config, store, entry, dispatch_target, address=target)
    entry = _deliver_post(config, delivery=delivery, store=store, entry=entry)
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
