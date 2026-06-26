"""Closeout-gate enforcement policy: does an approved gate permit this closeout? (slice 6b).

6a recorded gates honestly; 6b makes a ``closeout-approval`` gate *binding*. This
module owns the pure decision -- given a lifecycle's current gate set, may its
closeout proceed? -- kept free of I/O so the mutating worktree tool and any future
caller share one policy and it is unit-testable without a store.

The load-bearing rule is the **anti-self-approval** invariant: the agent's own
``gate_decide`` is attributed ``decidedBy="model"`` (see ``mcp/tools/gates.py``),
so a model-decided ``approved`` snapshot does **not** satisfy enforcement. Only a
``decidedBy="developer"`` approval -- which only the dashboard serving path writes
-- is binding. A lifecycle with no closeout-approval gate is *gateless*: the chat
commit gate (``--approved`` + note) still governs, so every pre-6b flow is
unbroken and enforcement is purely additive.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.controlplane.records import GateRecord

CLOSEOUT_GATE_KIND = "closeout-approval"
BINDING_DECIDER = "developer"


@dataclass(frozen=True)
class CloseoutGuard:
    """The closeout admissibility verdict for one lifecycle's gate set.

    ``permitted`` is the decision; ``reason`` is the operator/agent-facing message
    (the refusal text a blocked closeout raises); ``gate_id`` is the governing
    gate when one exists, so the caller can mark it ``applied`` on success.
    """

    permitted: bool
    reason: str
    gate_id: str | None = None


def evaluate_closeout_gate(gates: Mapping[str, GateRecord]) -> CloseoutGuard:
    """Decide whether a lifecycle's current gate set permits closeout. Pure.

    ``gates`` is the folded live set (:meth:`GateStore.current`, already last-wins
    by id). Absent a closeout-approval gate the verdict is permitted-gateless;
    otherwise the latest closeout-approval snapshot governs and only a
    developer-decided ``approved`` state lets closeout through.
    """
    relevant = [gate for gate in gates.values() if gate.kind == CLOSEOUT_GATE_KIND]
    if not relevant:
        return CloseoutGuard(True, "no closeout-approval gate; chat commit approval governs")
    gate = max(relevant, key=lambda candidate: candidate.ts)
    if gate.state == "approved" and gate.decidedBy == BINDING_DECIDER:
        return CloseoutGuard(True, "closeout-approval gate approved by the developer", gate.id)
    if gate.state == "approved":
        return CloseoutGuard(
            False,
            f"closeout gate {gate.id} was approved by {gate.decidedBy!r}, not the developer; "
            "a self-approval is not binding -- a developer must approve it via the dashboard",
            gate.id,
        )
    if gate.state == "open":
        return CloseoutGuard(
            False,
            f"closeout gate {gate.id} is open; await the developer's decision "
            "through the lifecycle gate channel -- do not self-approve",
            gate.id,
        )
    if gate.state == "applied":
        return CloseoutGuard(
            False,
            f"closeout gate {gate.id} was already applied; open a fresh gate for a new closeout",
            gate.id,
        )
    return CloseoutGuard(
        False,
        f"closeout gate {gate.id} is {gate.state}: {gate.decisionNote or gate.state}",
        gate.id,
    )
