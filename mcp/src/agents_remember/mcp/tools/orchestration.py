"""Payload builders for orchestration communication helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.orchestration_nudges import (
    NudgeReason,
    OrchestrationNudgeRecord,
    OrchestrationNudgeStore,
    nudge_message,
)
from agents_remember.observer import observer_root
from agents_remember.observer.events import Event, now_iso
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid

from .base import _tool_payload
from .operator_inbox import operator_inbox_post_payload

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


def orchestration_nudge_manager_payload(
    config: McpRuntimeConfig,
    *,
    reason: NudgeReason,
    subject: str,
    manager_agent_id: str | None = None,
    manager_lifecycle_id: str | None = None,
    subject_agent_id: str | None = None,
    subject_lifecycle_id: str | None = None,
    artifact_path: str | None = None,
    rate_limit_seconds: int = 900,
) -> dict[str, Any]:
    """Record and push a manager nudge for inactivity or a missing turn report."""
    if manager_agent_id is None and manager_lifecycle_id is None:
        raise ValueError("orchestration nudge requires manager_agent_id or manager_lifecycle_id")
    now = now_iso()
    message = nudge_message(reason, subject=subject, artifact_path=artifact_path)
    store = OrchestrationNudgeStore(observer_root(config))
    record = store.record(
        OrchestrationNudgeRecord(
            id=new_ulid(),
            ts=now,
            state="sent",
            reason=reason,
            targetAgentId=manager_agent_id,
            targetLifecycleId=manager_lifecycle_id,
            subjectAgentId=subject_agent_id,
            subjectLifecycleId=subject_lifecycle_id,
            artifactPath=artifact_path,
            message=message,
        ),
        rate_limit_seconds=rate_limit_seconds,
    )
    _log_nudge_event(config, record)
    if record.state == "rate-limited":
        return _tool_payload(
            "orchestration_nudge_manager",
            {
                "ok": True,
                "operation": "orchestration_nudge_manager",
                "status": record.state,
                "reason": reason,
                "nudgeId": record.id,
                "message": message,
            },
        )

    posted = operator_inbox_post_payload(
        config,
        lifecycle_id=manager_lifecycle_id,
        agent_id=manager_agent_id,
        sender_role="system",
        recipient_role="manager",
        message_kind="nudge",
        artifact_path=artifact_path,
        ask=f"Nudge manager about {subject}",
        response=message,
        created_by="system",
        created_via="cli",
    )
    return _tool_payload(
        "orchestration_nudge_manager",
        {
            "ok": True,
            "operation": "orchestration_nudge_manager",
            "status": record.state,
            "reason": reason,
            "nudgeId": record.id,
            "entryId": posted["entryId"],
            "deliveryState": posted.get("deliveryState"),
            "deliveredToSession": posted.get("deliveredToSession"),
            "message": message,
        },
    )


def _log_nudge_event(config: McpRuntimeConfig, record: OrchestrationNudgeRecord) -> None:
    EventStore(observer_root(config)).append(
        Event(
            id=new_ulid(),
            ts=record.ts,
            kind="orchestration.nudge",
            trust="observed",
            actor="system",
            data={
                "state": record.state,
                "reason": record.reason,
                "targetAgentId": record.targetAgentId,
                "targetLifecycleId": record.targetLifecycleId,
                "subjectAgentId": record.subjectAgentId,
                "subjectLifecycleId": record.subjectLifecycleId,
                "artifactPath": record.artifactPath,
            },
        )
    )
