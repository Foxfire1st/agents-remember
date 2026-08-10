"""Application operations for the ``operator_inbox_*`` return channel."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    InboxAddress,
    InboxMessage,
    InboxPoster,
    OperatorInboxEntry,
    OperatorInboxVia,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.operator_inbox_transitions import mark_superseded
from agents_remember.models.application_requests import OperatorInboxPostRequest
from agents_remember.observer import observer_root
from agents_remember.observer.events import now_iso
from agents_remember.serving.dispatch_brief import HOSTED_DELIVERY, HostedDelivery
from agents_remember.serving.operator_inbox_posts import (
    OperatorInboxPostContext,
    post_operator_inbox_entry,
)


def _result(_tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the raw use-case result for the MCP adapter to finalize."""
    return payload


if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )


def _store(config: McpRuntimeConfig) -> OperatorInboxStore:
    return OperatorInboxStore(observer_root(config))


def _entry_payload(entry: OperatorInboxEntry) -> dict[str, Any]:
    return entry.model_dump(mode="json", by_alias=True, exclude_none=True)


def operator_inbox_post_tool(
    config: McpRuntimeConfig,
    *,
    address: InboxAddress,
    message: InboxMessage,
    poster: InboxPoster,
    delivery: HostedDelivery | None = None,
) -> dict[str, Any]:
    return _result(
        "operator_inbox_post",
        post_operator_inbox_entry(
            OperatorInboxPostContext(
                config=config,
                store=_store(config),
                delivery=delivery or HOSTED_DELIVERY,
            ),
            address=address,
            message=message,
            poster=poster,
        ),
    )


def post_operator_inbox(
    config: McpRuntimeConfig,
    request: OperatorInboxPostRequest,
    delivery: HostedDelivery | None = None,
) -> dict[str, Any]:
    """Compose flat transport fields into one operator-inbox post use case."""
    return operator_inbox_post_tool(
        config,
        address=InboxAddress(
            lifecycle_id=request.lifecycle_id,
            agent_id=request.agent_id,
            recipient_role=request.recipient_role,
        ),
        message=InboxMessage(
            ask=request.ask,
            response=request.response,
            message_kind=request.message_kind,
            gate_id=request.gate_id,
            artifact_path=request.artifact_path,
        ),
        poster=InboxPoster(
            created_by=request.created_by,
            created_via=request.created_via,
            sender_agent_id=request.sender_agent_id,
            sender_role=request.sender_role,
        ),
        delivery=replace(delivery or HOSTED_DELIVERY, enabled=request.deliver_to_hosted),
    )


def operator_inbox_poll_tool(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str | None,
    agent_id: str | None,
    recipient_role: AgentRole | None = None,
    include_terminal: bool = False,
) -> dict[str, Any]:
    entries = _store(config).list_for_mailbox(
        lifecycle_id=lifecycle_id,
        agent_id=agent_id,
        recipient_role=recipient_role,
        include_terminal=include_terminal,
    )
    return _result(
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


def operator_inbox_consume_tool(
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
    return _result(
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


def operator_inbox_supersede_tool(
    config: McpRuntimeConfig,
    *,
    entry_id: str,
    reason: str,
    superseded_by: str = "model",
) -> dict[str, Any]:
    """Explicitly supersede one pending command (R11): terminal, visible, never a false ack."""
    entry, superseded_now = mark_superseded(
        _store(config),
        entry_id,
        now=now_iso(),
        reason=reason,
        superseded_by=superseded_by,
    )
    return _result(
        "operator_inbox_supersede",
        {
            "ok": True,
            "operation": "operator_inbox_supersede",
            "entryId": entry.id,
            "state": entry.state,
            "supersededNow": superseded_now,
            "terminalAt": entry.terminalAt,
            "terminalReason": entry.terminalReason,
            "supersededBy": entry.supersededBy,
        },
    )
