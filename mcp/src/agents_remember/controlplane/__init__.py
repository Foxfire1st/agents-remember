"""The gate control plane: durable, attributed decision-point records.

Slice 6a is the substrate -- the ``GateRecord`` entity and an append-only
``GateStore`` co-located with the observer event log -- plus the ``gate_*`` MCP
tools (in ``agents_remember.mcp.tools.gates``) that create, decide, wait on, and
list gates. Slice 6b adds enforcement: ``apply_gate`` (the ``applied`` transition
a mutating tool writes once it consumes an approval) and ``evaluate_closeout_gate``
(the pure policy ``worktree_closeout_apply`` obeys). Projection surfacing lands in
a later slice.
"""

from __future__ import annotations

from agents_remember.controlplane.enforcement import (
    CloseoutGuard,
    evaluate_closeout_gate,
)
from agents_remember.controlplane.records import (
    DECISION_STATES,
    GATE_RECORD_SCHEMA,
    DecidedVia,
    GateKind,
    GateRecord,
    GateState,
    apply_gate,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore

__all__ = [
    "DECISION_STATES",
    "GATE_RECORD_SCHEMA",
    "CloseoutGuard",
    "DecidedVia",
    "GateKind",
    "GateRecord",
    "GateState",
    "GateStore",
    "apply_gate",
    "create_gate",
    "decide_gate",
    "evaluate_closeout_gate",
]
