"""Control-plane records for gates and external-chat inbox delivery.

Slice 6a is the substrate -- the ``GateRecord`` entity and an append-only
``GateStore`` co-located with the observer event log -- plus the ``gate_*`` MCP
application operations (in ``agents_remember.application.gate_tools``) that create, decide, wait on, and
list gates. Enforcement uses the kind-generic gate policy: ``apply_gate`` (the
``applied`` transition a mutating tool writes once it consumes an approval) and
``evaluate_gate`` / ``evaluate_closeout_gate`` (the pure policy mutating tools
obey).

The external-chat inbox substrate is the pull-based counterpart: an append-only
``OperatorInboxEntry`` log plus ``OperatorInboxStore`` for posting, polling, and
consuming operator responses when agents-remember does not own the chat session.

All six JSONL stores in this package implement ONE declared contract,
``ar-durable-store/1.0`` in :mod:`agents_remember.controlplane.durable_store`: single-owner
compaction, an UNCONDITIONAL per-log lock (every append and every rewrite, every store, every
process -- there is no store exempt and no flag that turns it off), ``schemaVersion`` with an
unknown major rejected and an unknown minor accepted, and two deliberate read policies (strict for
authority, tolerant for projection). Read that module before changing how any of them touches
disk.
"""

from __future__ import annotations

from agents_remember.controlplane.durable_store import (
    DURABLE_STORE_CONTRACT,
    SCHEMA_VERSION,
    CompactionOwnerError,
    DurableRecord,
    DurableStoreError,
    StoreOwnership,
    UnsafeLockFilesystemError,
    declare_process_role,
    declared_process_role,
)
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
    "DURABLE_STORE_CONTRACT",
    "GATE_RECORD_SCHEMA",
    "OPERATOR_INBOX_RECORD_SCHEMA",
    "SCHEMA_VERSION",
    "CloseoutGuard",
    "CompactionOwnerError",
    "DecidedVia",
    "DurableRecord",
    "DurableStoreError",
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
    "StoreOwnership",
    "UnsafeLockFilesystemError",
    "apply_gate",
    "consume_operator_inbox_entry",
    "create_gate",
    "create_operator_inbox_entry",
    "decide_gate",
    "declare_process_role",
    "declared_process_role",
    "evaluate_closeout_gate",
    "evaluate_gate",
]
