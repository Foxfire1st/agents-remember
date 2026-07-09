"""Durable cooldown records for supervisor-owned pane/liveness signals."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.controlplane.inbox_backoff import require_redelivery_floor_seconds
from agents_remember.controlplane.operator_inbox_records import AgentRole, InboxDeliveryState

SUPERVISOR_SIGNAL_SCHEMA = "ar-supervisor-signal/v1"
SupervisorSignalState = Literal["sent"]


class SupervisorSignalRecord(BaseModel):
    """One supervisor signal actually posted to an owner inbox."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SUPERVISOR_SIGNAL_SCHEMA, alias="schema")
    id: str
    ts: str
    state: SupervisorSignalState = "sent"
    targetAgentId: str | None = None
    targetLifecycleId: str | None = None
    targetRole: AgentRole | None = None
    leafKey: str | None = None
    findingKind: str
    detail: str
    deliveryState: InboxDeliveryState


class SupervisorSignalCooldownStore:
    """Append-only signal log used as the supervisor's cross-sweep cooldown memory."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    def log_path(self) -> Path:
        return self._root / "workspace" / "supervisor-signals.jsonl"

    def read(self) -> list[SupervisorSignalRecord]:
        path = self.log_path()
        if not path.exists():
            return []
        return [
            SupervisorSignalRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def append(self, record: SupervisorSignalRecord) -> None:
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json(by_alias=True, exclude_none=True) + "\n")

    def last_sent(
        self,
        *,
        target_agent_id: str | None,
        target_lifecycle_id: str | None,
        target_role: AgentRole | None,
        leaf_key: str | None,
        finding_kind: str,
        detail: str,
    ) -> SupervisorSignalRecord | None:
        matches = [
            record
            for record in self.read()
            if record.state == "sent"
            and record.targetAgentId == target_agent_id
            and record.targetLifecycleId == target_lifecycle_id
            and record.targetRole == target_role
            and record.leafKey == leaf_key
            and record.findingKind == finding_kind
            and record.detail == detail
        ]
        return max(matches, key=lambda record: record.ts, default=None)

    def in_cooldown(
        self,
        *,
        target_agent_id: str | None,
        target_lifecycle_id: str | None,
        target_role: AgentRole | None,
        leaf_key: str | None,
        finding_kind: str,
        detail: str,
        now: datetime,
        cooldown_seconds: float,
    ) -> bool:
        floor = require_redelivery_floor_seconds(
            cooldown_seconds, owner="supervisor signal cooldown"
        )
        previous = self.last_sent(
            target_agent_id=target_agent_id,
            target_lifecycle_id=target_lifecycle_id,
            target_role=target_role,
            leaf_key=leaf_key,
            finding_kind=finding_kind,
            detail=detail,
        )
        if previous is None:
            return False
        try:
            elapsed = (now - datetime.fromisoformat(previous.ts)).total_seconds()
        except ValueError:
            return False
        return elapsed < floor
