"""The mandatory invariant closeout gate (MIK-R09@v2).

Invariant work is mandatory, never report-only (D5). No route that commits a leaf's memory commits
while an item of the leaf's recomputed worklist (MIK-R08) lacks a current satisfying row in its
history file (MIK-R07), while the worklist run is ``incomplete``, or while the validator (MIK-R22)
fails; a master lands only when no entry at a path it changed is stale (MIK-R03).

* :mod:`.predicates` -- each registered kind's own satisfying rule over a stored item, and the
  currentness of invariant and family rows (rule 2);
* :mod:`.gate` -- one leaf's recompute over the exact candidate, the findings and the refusal;
* :mod:`.memo` -- the bounded memo of verdicts, keyed by the exact trees, contract, parent tip,
  task document and build, so one run's repeated evaluations reuse one recompute;
* :mod:`.direct` -- the gate at direct landing, the branch-addressed leaf closeout;
* :mod:`.landing` -- master and checkpoint landing (rule 4) and record landing (rule 3);
* :mod:`.adapter` -- the worktree layer's :class:`KnowledgeGatePort`.

Where each route calls it: the curator's memory-quality run counts the findings toward
``curatorActionableCount`` (``application/memory_quality/controller.py``); the closeout validator
refuses (``worktrees/integration/closeout/curator_coherence.py``); the closeout and direct-landing
memory commits close the leaf's history file and validate their exact tree; record, master and
checkpoint landing refuse through the port.
"""

from __future__ import annotations

from agents_remember.application.knowledge_gate.adapter import KnowledgeGate
from agents_remember.application.knowledge_gate.gate import (
    GATE_CHECK,
    GateFinding,
    GateResult,
    GateTrees,
    evaluate_leaf_gate,
    judge,
    recompute_for_gate,
)
from agents_remember.application.knowledge_gate.predicates import (
    GATE_PREDICATES,
    GateContext,
    item_open_reason,
    register_gate_predicate,
)

__all__ = [
    "GATE_CHECK",
    "GATE_PREDICATES",
    "GateContext",
    "GateFinding",
    "GateResult",
    "GateTrees",
    "KnowledgeGate",
    "evaluate_leaf_gate",
    "item_open_reason",
    "judge",
    "recompute_for_gate",
    "register_gate_predicate",
]
