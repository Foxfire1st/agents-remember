"""Control-plane records for gates.

The substrate is the ``GateRecord`` entity and an append-only ``GateStore``
co-located with the observer event log. Enforcement uses the kind-generic gate
policy: ``apply_gate`` (the ``applied`` transition a mutating tool writes once it
consumes an approval) and ``evaluate_gate`` / ``evaluate_closeout_gate`` (the pure
policy mutating tools obey).

The JSONL stores in this package implement ONE declared contract,
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
from agents_remember.controlplane.records import (
    DECISION_STATES,
    GATE_RECORD_SCHEMA,
    DecidedVia,
    GateEvidenceRef,
    GateRecord,
    apply_gate,
    create_gate,
    decide_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.primitives.gate_policy import (
    DEFAULT_GATE_POLICY,
    GatePolicy,
    GatePolicyRule,
)
from agents_remember.models.structural.gates import (
    GateKind,
    GateState,
)

__all__ = [
    "DECISION_STATES",
    "DEFAULT_GATE_POLICY",
    "DURABLE_STORE_CONTRACT",
    "GATE_RECORD_SCHEMA",
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
    "StoreOwnership",
    "UnsafeLockFilesystemError",
    "apply_gate",
    "create_gate",
    "decide_gate",
    "declare_process_role",
    "declared_process_role",
    "evaluate_closeout_gate",
    "evaluate_gate",
]
