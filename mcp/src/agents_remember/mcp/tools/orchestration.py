"""Payload builders for orchestration communication helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
)
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


@dataclass(frozen=True)
class NudgeTarget:
    """The manager seat a nudge is delivered to, addressed by its hosted-session agent id, its
    lifecycle id, or both. At least one must be present -- a nudge with no addressee has no
    mailbox to land in."""

    agent_id: str | None = None
    lifecycle_id: str | None = None


@dataclass(frozen=True)
class NudgeSubject:
    """What the nudge is about: the human-readable subject line that names the stalled work, the
    seat whose silence or missing turn report triggered it, and the artifact that evidences it."""

    subject: str
    agent_id: str | None = None
    lifecycle_id: str | None = None
    artifact_path: str | None = None


def orchestration_nudge_manager_payload(
    config: McpRuntimeConfig,
    *,
    reason: NudgeReason,
    target: NudgeTarget,
    subject: NudgeSubject,
    rate_limit_seconds: int = 900,
) -> dict[str, Any]:
    """Record and push a manager nudge for inactivity or a missing turn report."""
    if target.agent_id is None and target.lifecycle_id is None:
        raise ValueError("orchestration nudge requires manager_agent_id or manager_lifecycle_id")
    now = now_iso()
    message = nudge_message(reason, subject=subject.subject, artifact_path=subject.artifact_path)
    store = OrchestrationNudgeStore(observer_root(config))
    record = store.record(
        OrchestrationNudgeRecord(
            id=new_ulid(),
            ts=now,
            state="sent",
            reason=reason,
            targetAgentId=target.agent_id,
            targetLifecycleId=target.lifecycle_id,
            subjectAgentId=subject.agent_id,
            subjectLifecycleId=subject.lifecycle_id,
            artifactPath=subject.artifact_path,
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
        address=InboxAddress(
            lifecycle_id=target.lifecycle_id,
            agent_id=target.agent_id,
            recipient_role="manager",
        ),
        message=InboxMessage(
            ask=f"Nudge manager about {subject.subject}",
            response=message,
            message_kind="nudge",
            artifact_path=subject.artifact_path,
        ),
        poster=InboxPoster(created_by="system", created_via="cli", sender_role="system"),
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
