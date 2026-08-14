"""Persist and optionally deliver one operator-inbox post."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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
    signal_task_document_ref,
)
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.observer.events import now_iso
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.dispatch_brief import (
    HostedDelivery,
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
from agents_remember.serving.ports import TerminalCatalogPort
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_paste import TerminalPaster
from agents_remember.tasks.document_refs import TaskDocumentTopology

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )


@dataclass(frozen=True)
class OperatorInboxPostContext:
    """Persistence and delivery collaborators for one operator-inbox post."""

    config: McpRuntimeConfig | None
    store: OperatorInboxStore
    delivery: HostedDelivery


def _redelivery_floor_seconds(config: McpRuntimeConfig | None) -> float | None:
    if config is None:
        return None
    return load_agentic_settings(
        config.coordination_root
    ).agent_notifier.redeliver_rate_limit_seconds


def _delivery_catalog(
    config: McpRuntimeConfig | None,
    catalog: TerminalCatalogPort | None,
) -> TerminalCatalogPort:
    if catalog is not None:
        return catalog
    assert config is not None
    return TerminalCatalog(terminal_catalog_path(config.coordination_root))


def _signal_route(
    catalog: TerminalCatalogPort | None,
    topology: TaskDocumentTopology | None,
    *,
    sender_agent_id: str | None,
    message_kind: InboxMessageKind,
) -> tuple[RoutedOwner, TaskDocumentRef | None]:
    if catalog is None or topology is None:
        return RoutedOwner(), None
    document = signal_task_document_ref(catalog, sender_agent_id=sender_agent_id)
    return (
        derive_signal_owner(
            catalog,
            topology,
            sender_agent_id=sender_agent_id,
            message_kind=message_kind,
            task_document_ref=document,
        ),
        document,
    )


def _post_address(
    catalog: TerminalCatalogPort | None,
    owner: RoutedOwner,
    *,
    message_kind: InboxMessageKind,
    address: InboxAddress,
) -> InboxAddress:
    """The delivery address for one post, after N14 post-time owner re-resolution.

    Every owner-addressed post re-derives the CURRENT qualified owner from the sender's
    leaf/role identity before persisting, so a worker whose manager was replaced never
    addresses the corpse. ``dispatch-brief`` rows are exact-pinned and never rebind; a
    caller-addressed recipient that is not an owner (or cannot be proven to be one) is kept
    verbatim -- cross-agent messages are never hijacked by derivation.
    """
    has_owner = (
        owner.role is not None or owner.agent_id is not None or owner.lifecycle_id is not None
    )
    if message_kind == "dispatch-brief" or not has_owner or catalog is None:
        return address
    if message_kind == "decision-item":
        return InboxAddress(
            task_document_ref=owner.task_document_ref,
            lifecycle_id=owner.lifecycle_id,
            agent_id=owner.agent_id,
            recipient_role=owner.role,
        )
    if not _is_owner_addressed(catalog, owner, address):
        return address
    return InboxAddress(
        task_document_ref=owner.task_document_ref,
        lifecycle_id=owner.lifecycle_id,
        agent_id=owner.agent_id,
        recipient_role=owner.role,
    )


_OWNER_ADDRESS_ROLES = frozenset({"manager", "orchestrator", "architect"})


def _agent_address_is_owner(
    catalog: TerminalCatalogPort,
    owner: RoutedOwner,
    agent_id: str,
) -> bool:
    target = catalog.get(agent_id)
    if target is None:
        # The addressed seat does not exist: the derived owner is the current qualified
        # owner (the row would otherwise be born addressed to nobody).
        return True
    return target.binding_role == owner.role


def _lifecycle_address_is_owner(
    catalog: TerminalCatalogPort,
    owner: RoutedOwner,
    lifecycle_id: str,
) -> bool:
    seats = [
        entry
        for entry in catalog.list(include_terminated=True)
        if entry.lifecycle_id == lifecycle_id
    ]
    return not seats or any(entry.binding_role == owner.role for entry in seats)


def _is_owner_addressed(
    catalog: TerminalCatalogPort,
    owner: RoutedOwner,
    address: InboxAddress,
) -> bool:
    """Whether ``address`` names an owner mailbox rather than an arbitrary peer seat."""
    if address.recipient_role is not None:
        if address.recipient_role not in _OWNER_ADDRESS_ROLES:
            return False
        return (
            address.task_document_ref is None
            or address.task_document_ref == owner.task_document_ref
        )
    if address.agent_id is not None:
        return _agent_address_is_owner(catalog, owner, address.agent_id)
    if address.lifecycle_id is not None:
        return _lifecycle_address_is_owner(catalog, owner, address.lifecycle_id)
    return False


def _post_catalog(
    config: McpRuntimeConfig | None,
    supplied: TerminalCatalogPort | None,
) -> TerminalCatalogPort | None:
    if supplied is not None:
        return supplied
    if config is None:
        return None
    return TerminalCatalog(terminal_catalog_path(config.coordination_root))


def _dispatch_entry_fields(
    target: TerminalCatalogEntry | None,
    *,
    routed_task_document_ref: TaskDocumentRef | None,
    sender_agent_id: str | None,
) -> tuple[TaskDocumentRef | None, str | None, str | None]:
    if target is None:
        return routed_task_document_ref, None, sender_agent_id
    return target.binding_task_document_ref, target.binding_role, target.id


def _persist_post(
    config: McpRuntimeConfig | None,
    store: OperatorInboxStore,
    entry: OperatorInboxEntry,
    dispatch_target: TerminalCatalogEntry | None,
) -> None:
    store.append(entry)
    store.compact(now=datetime.now(UTC))
    if dispatch_target is not None and config is not None:
        start_dispatch_expectations(config, entry, dispatch_target)


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
    topology = (
        TaskDocumentTopology(context.config.coordination_root)
        if context.config is not None
        else None
    )
    dispatch_target = require_dispatch_target(
        message_kind=message.message_kind,
        agent_id=address.agent_id,
        delivery=delivery,
    )
    owner, routed_task_document_ref = _signal_route(
        catalog,
        topology,
        sender_agent_id=poster.sender_agent_id,
        message_kind=message.message_kind,
    )
    if message.message_kind == "decision-item" and catalog is not None and owner.agent_id is None:
        return {
            "ok": False,
            "operation": "operator_inbox_post",
            "status": "sprint-owner-required",
        }
    task_document_ref, seat_role, subject_agent_id = _dispatch_entry_fields(
        dispatch_target,
        routed_task_document_ref=routed_task_document_ref,
        sender_agent_id=poster.sender_agent_id,
    )
    target = _post_address(catalog, owner, message_kind=message.message_kind, address=address)
    entry = create_operator_inbox_entry(
        replace(
            message,
            subject=InboxSubject(
                task_document_ref=task_document_ref,
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
                task_document_ref=owner.task_document_ref,
                agent_id=owner.agent_id,
                lifecycle_id=owner.lifecycle_id,
            ),
        ),
        poster=poster,
    )
    _persist_post(context.config, context.store, entry, dispatch_target)
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
