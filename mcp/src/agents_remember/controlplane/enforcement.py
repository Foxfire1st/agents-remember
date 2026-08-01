"""Gate enforcement policy: does an approved gate permit a server-side mutation?

6a recorded gates honestly; 6b made a ``closeout-approval`` gate *binding*. This
module owns the pure decision -- given a lifecycle's current gate set and the
configured gate policy, may the corresponding server-side mutation proceed? --
kept free of I/O so mutating tools share one policy and it is unit-testable
without a store.

The load-bearing rule is the **anti-self-approval** invariant: the agent's own
``gate_decide`` is attributed ``decidedBy="model"`` (see ``mcp/tools/gates.py``),
so a model-decided ``approved`` snapshot does **not** satisfy enforcement. A
human approval is always binding; a non-human orchestration approval is binding
only when the policy explicitly delegates that gate kind to the deciding role and
the decider is not the gate-owning lifecycle. A lifecycle with no gate of the
requested kind is *gateless*: the existing chat/commit gate still governs, so
legacy flows remain additive.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.controlplane.gate_policy import (
    DEFAULT_GATE_POLICY,
    GatePolicy,
    approval_failure_reason,
)
from agents_remember.controlplane.records import GateKind, GateRecord

CLOSEOUT_GATE_KIND: GateKind = "closeout-approval"


@dataclass(frozen=True)
class GateGuard:
    """The admissibility verdict for one lifecycle's gate set.

    ``permitted`` is the decision; ``reason`` is the operator/agent-facing message
    (the refusal text a blocked closeout raises); ``gate_id`` is the governing
    gate when one exists, so the caller can mark it ``applied`` on success.
    """

    kind: GateKind
    permitted: bool
    reason: str
    gate_id: str | None = None


CloseoutGuard = GateGuard


def evaluate_gate(
    gates: Mapping[str, GateRecord],
    *,
    kind: GateKind,
    policy: GatePolicy = DEFAULT_GATE_POLICY,
) -> GateGuard:
    """Decide whether a lifecycle's current gate set permits a gate kind. Pure.

    ``gates`` is the folded live set (:meth:`GateStore.current`, already last-wins
    by id). Absent a matching gate the verdict is permitted-gateless; otherwise
    the latest matching snapshot governs and only a policy-valid ``approved``
    state lets the mutation through.
    """
    relevant = [gate for gate in gates.values() if gate.kind == kind]
    if not relevant:
        return GateGuard(kind, True, f"no {kind} gate; existing approval channel governs")
    gate = max(relevant, key=lambda candidate: candidate.ts)
    if gate.state == "approved":
        failure = approval_failure_reason(gate, policy)
        if failure is None:
            return GateGuard(kind, True, f"{kind} gate approved by policy-valid decider", gate.id)
        return GateGuard(kind, False, failure, gate.id)
    if gate.state == "open":
        return GateGuard(
            kind,
            False,
            f"{kind} gate {gate.id} is open; await a policy-valid decision "
            "through the lifecycle gate channel",
            gate.id,
        )
    if gate.state == "applied":
        return GateGuard(
            kind,
            False,
            f"{kind} gate {gate.id} was already applied; open a fresh gate for a new mutation",
            gate.id,
        )
    return GateGuard(
        kind,
        False,
        f"{kind} gate {gate.id} is {gate.state}: {gate.decisionNote or gate.state}",
        gate.id,
    )


def evaluate_closeout_gate(
    gates: Mapping[str, GateRecord],
    policy: GatePolicy = DEFAULT_GATE_POLICY,
) -> CloseoutGuard:
    """Compatibility wrapper for closeout enforcement."""
    return evaluate_gate(gates, kind=CLOSEOUT_GATE_KIND, policy=policy)
