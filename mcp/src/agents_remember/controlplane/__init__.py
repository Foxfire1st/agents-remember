"""Control-plane records for gates and external-chat inbox delivery.

Slice 6a is the substrate -- the ``GateRecord`` entity and an append-only
``GateStore`` co-located with the observer event log -- plus the ``gate_*`` MCP
tools (in ``agents_remember.mcp.tools.gates``) that create, decide, wait on, and
list gates. Enforcement uses the kind-generic gate policy: ``apply_gate`` (the
``applied`` transition a mutating tool writes once it consumes an approval) and
``evaluate_gate`` / ``evaluate_closeout_gate`` (the pure policy mutating tools
obey).

The external-chat inbox substrate is the pull-based counterpart: an append-only
``OperatorInboxEntry`` log plus ``OperatorInboxStore`` for posting, polling, and
consuming operator responses when agents-remember does not own the chat session.
"""

from __future__ import annotations

from agents_remember.controlplane.enforcement import (
    CloseoutGuard,
    GateGuard,
    evaluate_closeout_gate,
    evaluate_gate,
)
from agents_remember.controlplane.gate_policy import (
    DEFAULT_GATE_POLICY,
    GatePolicy,
    GatePolicyRule,
)
from agents_remember.controlplane.operator_inbox_records import (
    OPERATOR_INBOX_RECORD_SCHEMA,
    OperatorInboxEntry,
    OperatorInboxState,
    OperatorInboxVia,
    consume_operator_inbox_entry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import (
    DECISION_STATES,
    GATE_RECORD_SCHEMA,
    DecidedVia,
    GateEvidenceRef,
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
    "DEFAULT_GATE_POLICY",
    "GATE_RECORD_SCHEMA",
    "OPERATOR_INBOX_RECORD_SCHEMA",
    "CloseoutGuard",
    "DecidedVia",
    "GateEvidenceRef",
    "GateGuard",
    "GateKind",
    "GatePolicy",
    "GatePolicyRule",
    "GateRecord",
    "GateState",
    "GateStore",
    "OperatorInboxEntry",
    "OperatorInboxState",
    "OperatorInboxStore",
    "OperatorInboxVia",
    "apply_gate",
    "consume_operator_inbox_entry",
    "create_gate",
    "create_operator_inbox_entry",
    "decide_gate",
    "evaluate_closeout_gate",
    "evaluate_gate",
]
