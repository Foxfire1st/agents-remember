"""R3 (260707-HFX2-L4): the orphan-workers hook for a dead manager's still-running workers.

Policy (documented, not automated -- deliberately a stub/hook per the leaf's scope): when a
manager seat is respawned or found dead, its live worker seats do NOT auto re-parent to the
successor and do NOT absorb the manager's role (R4 doctrine, HFX-L6 capture hardening). They are
held under an explicit ORPHAN expectation: the grandparent (the orchestrator that owns the master)
is signaled with the orphan list and decides -- re-parent each worker to the respawned manager once
it exists, or let them run to their own turn-report/completion under no manager and be swept up at
the next leaf dispatch. A future leaf may wire an actual re-parent action; this one only detects
and surfaces the set, via the same catalog spawn-provenance trace every other predicate here reads.
"""

from __future__ import annotations

from agents_remember.controlplane.seats import SeatDirectory, SeatRow


def find_orphaned_workers(catalog: SeatDirectory, *, manager_agent_id: str) -> list[SeatRow]:
    """Every still-``running`` worker seat spawned by ``manager_agent_id`` (pure catalog read).

    Called once a manager seat is confirmed dead/respawned (``serving/supervisor.py``'s dead-
    upstream / respawn actions); the caller is responsible for the actual signal post + event.
    """
    return [
        entry
        for entry in catalog.list()
        if entry.binding_role == "worker"
        and entry.spawned_by_session == manager_agent_id
        and entry.status == "running"
    ]
