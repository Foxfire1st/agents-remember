"""The gate control plane: durable, attributed decision-point records.

Slice 6a is the substrate -- the ``GateRecord`` entity and an append-only
``GateStore`` co-located with the observer event log -- plus the ``gate_*`` MCP
tools (in ``agents_remember.mcp.tools.gates``) that create, decide, wait on, and
list gates. Enforcement (mutating tools obeying gate state) and projection
surfacing land in later slices; this package owns only the honest record.
"""

from __future__ import annotations

from agents_remember.controlplane.records import (
    DECISION_STATES,
    GATE_RECORD_SCHEMA,
    DecidedVia,
    GateKind,
    GateRecord,
    GateState,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore

__all__ = [
    "DECISION_STATES",
    "GATE_RECORD_SCHEMA",
    "DecidedVia",
    "GateKind",
    "GateRecord",
    "GateState",
    "GateStore",
    "create_gate",
    "decide_gate",
]
