"""Response models for orchestration helper tools."""

from __future__ import annotations

from typing import Literal

from agents_remember.controlplane.operator_inbox_records import InboxDeliveryState
from agents_remember.controlplane.orchestration_nudges import NudgeReason, NudgeState
from agents_remember.models.base import ToolResponse


class OrchestrationNudgeManagerResponse(ToolResponse):
    """``orchestration_nudge_manager``: rate-limited manager stdin nudge."""

    operation: Literal["orchestration_nudge_manager"] = "orchestration_nudge_manager"
    status: NudgeState
    reason: NudgeReason
    nudgeId: str
    entryId: str | None = None
    deliveryState: InboxDeliveryState | None = None
    deliveredToSession: str | None = None
    message: str
