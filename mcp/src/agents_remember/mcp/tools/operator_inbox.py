"""Payload builders for the ``operator_inbox_*`` external-chat return channel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    OperatorInboxVia,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.observer import observer_root
from agents_remember.observer.events import now_iso
from agents_remember.observer.ulid import new_ulid

from .base import _tool_payload

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


def _store(config: McpRuntimeConfig) -> OperatorInboxStore:
    return OperatorInboxStore(observer_root(config))


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
) -> dict[str, Any]:
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
    )
    _store(config).append(entry)
    return _tool_payload(
        "operator_inbox_post",
        {
            "ok": True,
            "operation": "operator_inbox_post",
            "entryId": entry.id,
            "state": entry.state,
            "lifecycleId": entry.lifecycleId,
            "agentId": entry.agentId,
            "gateId": entry.gateId,
        },
    )


def operator_inbox_poll_payload(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str | None,
    agent_id: str | None,
) -> dict[str, Any]:
    entries = _store(config).list_pending(lifecycle_id=lifecycle_id, agent_id=agent_id)
    return _tool_payload(
        "operator_inbox_poll",
        {
            "ok": True,
            "operation": "operator_inbox_poll",
            "lifecycleId": lifecycle_id,
            "agentId": agent_id,
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
    entry, consumed_now = _store(config).consume(
        entry_id,
        now=now_iso(),
        consumed_by=consumed_by,
        consumed_via=consumed_via,
    )
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
