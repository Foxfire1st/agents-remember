"""Response models for the ``gate_*`` control-plane tools (slice 6a).

AR-owned, operation-bearing responses (strict ``ToolResponse``s, not the
persisted ``GateRecord``). ``GateKind`` / ``GateState`` reuse the record's
Literals so the response contract is as drift-proof as the record itself.
"""

from __future__ import annotations

from typing import Any

from agents_remember.controlplane.records import GateKind, GateState
from agents_remember.models.base import ToolResponse


class GateCreateResponse(ToolResponse):
    """``gate_create``: a freshly opened gate."""

    gateId: str
    kind: GateKind
    state: GateState
    lifecycleId: str | None = None


class GateDecideResponse(ToolResponse):
    """``gate_decide``: the gate's state after the recorded decision."""

    gateId: str
    state: GateState
    decidedBy: str
    decidedVia: str


class GateWaitResponse(ToolResponse):
    """``gate_wait``: the gate's state when the wait returned, and whether it timed out."""

    gateId: str
    state: GateState
    timedOut: bool


class GateListResponse(ToolResponse):
    """``gate_list``: the current (folded) gate set for a lifecycle."""

    lifecycleId: str | None = None
    gates: list[dict[str, Any]]
