"""Gate vocabulary for policy and records (kernel-owned).

Kernel is below models: the wire layer re-exports these names from here rather
than defining them, and the control-plane records import them through models.
"""

from __future__ import annotations

from typing import Literal, cast, get_args

# What needs the human/operator. Extensible: a new gate kind is one literal.
GateKind = Literal[
    "plan-approval",
    "worktree-intent",
    # `closeout-approval` IS the commit gate: closeout is the single commit-of-record for code + memory +
    # ledger (there is no separate `commit-approval`; singular commits route through closeout).
    "closeout-approval",
    "push-approval",
    "integration-approval",
    # `master-handover-approval` is the master-exit seam gate: the manager raises it with the
    # reviewer verdict attached as evidence; the orchestrator decides it on the happy path
    # (delegable, never human-pinned) — human review concentrates at the super gate.
    "master-handover-approval",
    "cleanup-approval",
    "agent-question",
    "provider-retry",
    "alarm-ack",
]
# The gate's lifecycle. ``open`` awaits a decision; ``applied`` is set once a
# mutating tool consumes an approval (a later slice writes it -- modeled here so
# the contract is stable). The rest are terminal decisions.
GateState = Literal[
    "open",
    "approved",
    "rejected",
    "revision-requested",
    "applied",
    "cancelled",
    "expired",
]

GATE_KINDS: tuple[GateKind, ...] = get_args(GateKind)


def coerce_gate_kind(raw: str) -> GateKind:
    """Validate a raw kind string against the :data:`GateKind` literals."""
    if raw not in GATE_KINDS:
        raise ValueError(f"unknown gate kind {raw!r}; expected one of {list(GATE_KINDS)}")
    return cast(GateKind, raw)
