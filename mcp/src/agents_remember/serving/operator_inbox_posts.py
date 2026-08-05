"""Persist and optionally deliver one operator-inbox post."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.expectation_rows import (
    Expectation,
    ExpectationSubject,
    write_expectation_row,
)
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxMessageKind,
    InboxOwner,
    InboxPoster,
    InboxRouting,
    InboxSubject,
    OperatorInboxEntry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.operator_inbox_transitions import RedeliveryFloor
from agents_remember.controlplane.signal_routing import (
    RoutedOwner,
    derive_signal_owner,
    signal_leaf_key,
)
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.observer.events import now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.dispatch_brief import (
    HostedDelivery,
    expectation_sla_seconds,
    expectation_store,
    fulfill_dispatch_expectation,
    require_dispatch_target,
    start_dispatch_expectations,
)
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    DeliveryAdmission,
    InboxDeliveryLog,
    deliver_inbox_entry,
)
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_paste import TerminalPaster

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


@dataclass(frozen=True)
class OperatorInboxPostContext:
    """Persistence and delivery collaborators for one operator-inbox post."""

    config: McpRuntimeConfig | None
    store: OperatorInboxStore
    delivery: HostedDelivery


def _redelivery_floor_seconds(config: McpRuntimeConfig | None) -> float | None:
    if config is None:
        return None
    return load_agentic_settings(config.coordination_root).supervisor.redeliver_rate_limit_seconds


def _delivery_catalog(
    config: McpRuntimeConfig | None,
    catalog: TerminalCatalog | None,
) -> TerminalCatalog:
    if catalog is not None:
        return catalog
    assert config is not None
    return TerminalCatalog(terminal_catalog_path(config.coordination_root))


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
    has_owner = (
        owner.agent_id is not None or owner.lifecycle_id is not None or owner.role is not None
    )
    if message_kind not in ("turn-report", "master-handover") or not has_owner:
        return address
    return InboxAddress(
        lifecycle_id=owner.lifecycle_id,
        agent_id=owner.agent_id,
        recipient_role=owner.role,
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
    if dispatch_target is not None and config is not None:
        start_dispatch_expectations(config, entry, dispatch_target)
    if config is None:
        return
    write_expectation_row(
        expectation_store(config),
        Expectation(
            kind="ack-by",
            source_id=entry.id,
            subject=ExpectationSubject(
                agent_id=address.agent_id,
                lifecycle_id=address.lifecycle_id,
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
    config: McpRuntimeConfig | None,
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


def post_operator_inbox_entry(
    context: OperatorInboxPostContext,
    *,
    address: InboxAddress,
    message: InboxMessage,
    poster: InboxPoster,
) -> dict[str, Any]:
    """Create, persist, deliver, and describe one post through the shared real owner."""

    catalog = _post_catalog(context.config, context.delivery.catalog)
    host = context.delivery.host or TerminalHost()
    delivery = replace(context.delivery, catalog=catalog, host=host)
    dispatch_target = require_dispatch_target(
        message_kind=message.message_kind,
        agent_id=address.agent_id,
        delivery=delivery,
        host=host,
    )
    owner, routed_leaf_key = _signal_route(
        catalog,
        sender_agent_id=poster.sender_agent_id,
        message_kind=message.message_kind,
    )
    leaf_key, seat_role, subject_agent_id = _dispatch_entry_fields(
        dispatch_target,
        routed_leaf_key=routed_leaf_key,
        sender_agent_id=poster.sender_agent_id,
    )
    target = _post_address(owner, message_kind=message.message_kind, address=address)
    entry = create_operator_inbox_entry(
        replace(
            message,
            subject=InboxSubject(
                leaf_key=leaf_key,
                seat_role=seat_role,
                agent_id=subject_agent_id,
            ),
        ),
        entry_id=new_ulid(),
        now=now_iso(),
        routing=InboxRouting(
            address=target,
            owner=InboxOwner(
                role=owner.role,
                agent_id=owner.agent_id,
                lifecycle_id=owner.lifecycle_id,
            ),
        ),
        poster=poster,
    )
    _persist_post(context.config, context.store, entry, dispatch_target, address=target)
    if delivery.enabled:
        entry = _deliver_post(
            context.config,
            delivery=delivery,
            store=context.store,
            entry=entry,
        )
    if dispatch_target is not None and context.config is not None:
        fulfill_dispatch_expectation(context.config, entry)
    return {
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
    }
