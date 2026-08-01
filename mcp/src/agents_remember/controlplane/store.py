"""Append-only gate store, co-located with the observer event substrate.

Gate snapshots live in ``<observer_root>/lifecycles/<lifecycle-id>/gates.jsonl``
beside that lifecycle's ``events.jsonl``; lifecycle-less gates go to
``<observer_root>/workspace/gates.jsonl``. Append-only and history-preserving:
:meth:`current` folds the log by gate id (last snapshot wins) into the live gate
set, so a gate's whole history stays on disk and the current state is a pure
read.

CONCURRENCY -- this log has TWO writing processes, and this is the store that
carries authority. The MCP server mints, decides, applies and deletes gates; the
dashboard raises and resolves ``agent-question`` gates for adapter-owned sessions
(``serving/hosted_interactions.py``) and its entire purpose is to decide gates for
lifecycles owned by other sessions. This docstring used to claim one writer per
file "in practice because a lifecycle is owned by one live session", borrowed from
the event store next door. That was false on both counts -- the compactor was a
third participant again, running on the dashboard's projection tick -- and the
record loss was the cost of believing it: 11.50% of appended gate snapshots lost
under ordinary two-process operation at the base commit, and 100% in the
deterministic forced-window scenario. Measured 0 lost across 10 runs of all three
scenarios once this contract was in place.

DURABILITY OF A RECORD IS NOT ATOMICITY OF A DECISION, and an earlier draft of
this docstring conflated them -- it said the ``applied`` marker is what stops one
human approval being consumed twice "so losing it re-opens the replay window",
which reads as though not losing it closed the window. It did not. Three separate
things had to be true, and only the first was:

1. The marker must survive a concurrent rewrite. That is the lock, above.
2. The marker must not be RECLAIMED AS GARBAGE. It was: ``applied`` sat in
   ``interaction_retention.PRUNE_IMMEDIATE_GATE_STATES``, so the reclaim pass
   dropped it at any age -- one later decision on the same lifecycle, an
   answered ``agent-question`` included, and the fold went back to
   permitted-gateless. Closed by :data:`~agents_remember.controlplane.
   interaction_retention.CONSUMED_APPROVAL_GATE_KINDS`.
3. The check and the write must be ONE step. They were ~100 lines and every
   commit of a closeout apart, with no lock across the pair, so two closeouts
   interleaved and both were permitted -- measured with two real processes.
   Closed by :meth:`GateStore.claim_approval`, which is now the only way to
   spend an approval and the only thing in this module that writes an
   ``applied`` snapshot for an enforcement path.

Read the three together: a record this store keeps perfectly is still worthless
if something else deletes it on a schedule, or if the decision it backs was
already made against a stale copy.

The contract is now declared rather than assumed: see
``controlplane/durable_store.py`` and :data:`~agents_remember.controlplane.durable_store.GATE_OWNERSHIP`.
Appends and rewrites exclude each other through the log's own lockfile, always
and in every process -- that is what makes the loss zero. Reclamation is named as
the MCP process's, but nothing in this module enforces that: :meth:`GateStore.compact`
takes the lock and rewrites, and a dashboard call would go through. The guard is at
the CALL SITE (``mcp/tools/gates.py``, which asks
:meth:`~agents_remember.controlplane.durable_store.StoreOwnership.is_compaction_owner`
before reclaiming) and STRUCTURALLY, in that the dashboard's projection tick no
longer rewrites this log at all. Both are advisory and neither raises; the
durability rests on the lock. Do not read ownership as more than it is.

READS ARE DELIBERATELY NOT UNIFORM (leaf 260731-EFA-L5 R8). :meth:`read` is STRICT
and raises on a torn line, because it backs :meth:`current`, :meth:`find` and
:meth:`all_current` -- the enforcement fold ``serving/hosted_interactions.py`` and
``worktrees/modules/integrate.py`` consult before a mutation. Skipping a malformed
record there could drop an ``applied`` marker and let the fold conclude the
approval was never consumed. :meth:`projected_current` is TOLERANT and is for the
dashboard projection only, which must degrade rather than crash on a 1s tick; it
never writes back what it read, so a skipped line is skipped for one tick and no
longer. Every rewrite OF THIS log reads strictly -- :meth:`GateStore.delete` and
:meth:`GateStore.compact` both filter a list :meth:`read` produced -- so a
compaction here can never be the thing that erases a gate it could not parse.
That follows from this log carrying authority and is NOT true of all six stores:
three of them rewrite from their tolerant reader and drop such a row permanently.
``durable_store.py`` names them and says why that is acceptable only for a log
nothing is decided on.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from agents_remember.controlplane.durable_store import (
    GATE_OWNERSHIP,
    append_line,
    exclusive_access,
    read_log_text,
    rewrite_lines,
)
from agents_remember.controlplane.enforcement import GateGuard, evaluate_gate
from agents_remember.controlplane.gate_policy import DEFAULT_GATE_POLICY, GatePolicy
from agents_remember.controlplane.interaction_retention import gate_keep_ids
from agents_remember.controlplane.records import GateKind, GateRecord, apply_gate


class GateStore:
    """Resolve per-lifecycle / workspace gate log paths and append snapshots."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    @property
    def root(self) -> Path:
        return self._root

    def log_path(self, lifecycle_id: str | None) -> Path:
        """The gate log a record routes to (workspace when lifecycle-less)."""
        if lifecycle_id:
            return self._root / "lifecycles" / lifecycle_id / "gates.jsonl"
        return self._root / "workspace" / "gates.jsonl"

    def append(self, record: GateRecord) -> None:
        """Append one snapshot to its log, creating parent dirs on first write."""
        GATE_OWNERSHIP.check_declared_writer()
        path = self.log_path(record.lifecycleId)
        line = record.model_dump_json(by_alias=True, exclude_none=True)
        with exclusive_access(path, GATE_OWNERSHIP):
            append_line(path, line)

    def read(self, lifecycle_id: str | None) -> list[GateRecord]:
        """Read a gate log back as validated snapshots (empty when absent).

        STRICT on purpose: a torn or unknown-major line raises rather than being skipped.
        This is the authority read -- see the module docstring.
        """
        return [
            GateRecord.model_validate_json(line)
            for line in read_log_text(self.log_path(lifecycle_id)).splitlines()
            if line.strip()
        ]

    def read_for_projection(self, lifecycle_id: str | None) -> list[GateRecord]:
        """Read a gate log skipping any torn or unknown-major line.

        TOLERANT on purpose, and never used to decide anything or to drive a rewrite: the
        dashboard projection must degrade rather than 500 an endpoint or freeze a tick.
        """
        records: list[GateRecord] = []
        for line in read_log_text(self.log_path(lifecycle_id)).splitlines():
            if not line.strip():
                continue
            try:
                records.append(GateRecord.model_validate_json(line))
            except ValidationError:
                continue
        return records

    def find(self, gate_id: str) -> GateRecord | None:
        """Resolve one gate id across the workspace log and every lifecycle log.

        The seam-decide path: a deciding seat holds only the gate id (packet-carried);
        lifecycle ids stay server-side. Last-wins fold per log, first hit returned
        (gate ids are ULIDs — collisions across logs do not occur in practice).
        """
        hit = self.current(None).get(gate_id)
        if hit is not None:
            return hit
        lifecycles_dir = self._root / "lifecycles"
        if not lifecycles_dir.is_dir():
            return None
        for log in sorted(lifecycles_dir.glob("*/gates.jsonl")):
            hit = self.current(log.parent.name).get(gate_id)
            if hit is not None:
                return hit
        return None

    def current(self, lifecycle_id: str | None) -> dict[str, GateRecord]:
        """Fold the log by gate id, last-wins -- the live gate set."""
        latest: dict[str, GateRecord] = {}
        for record in self.read(lifecycle_id):
            latest[record.id] = record
        return latest

    def all_current(self) -> dict[str, GateRecord]:
        """Fold every gate log (workspace + all lifecycles), last-wins per gate id.

        The cross-lifecycle enforcement fold: a seam gate lives on its raiser's
        lifecycle while the consuming contract anchors a different one (e.g. the
        manager-raised ``master-handover-approval`` consumed by the orchestrator's
        integration), so identity-addressed consumers need the whole workspace
        view. A gate's snapshots all live in one log (``append`` routes by
        ``record.lifecycleId``) and gate ids are ULIDs, so cross-log collisions do
        not occur in practice; within one log the fold stays last-wins.
        """
        latest: dict[str, GateRecord] = {}
        for lifecycle_id in self.lifecycle_ids():
            latest.update(self.current(lifecycle_id))
        return latest

    def claim_approval(
        self,
        lifecycle_id: str | None,
        *,
        kind: GateKind,
        now: str,
        policy: GatePolicy = DEFAULT_GATE_POLICY,
    ) -> GateGuard:
        """Consume this lifecycle's approval for ``kind``: a COMPARE-AND-SWAP, not a read.

        THIS IS THE ONLY WAY TO SPEND AN APPROVAL (leaf 260731-EFA-L5 R2). The fold, the policy
        verdict and the ``applied`` append happen inside ONE held :func:`exclusive_access`, so the
        transition ``approved -> applied`` is atomic against every other writer of this log:
        exactly one caller can see the gate approved and be the one that marks it consumed. A
        second caller that arrives while the first holds the lock reads the ``applied`` snapshot
        and is refused with the already-applied reason.

        WHY A LOCK AROUND A *DECISION* AND NOT ONLY AROUND A WRITE. Durability of a record is not
        atomicity of a decision, and this store had the first without the second. ``read`` takes no
        lock and ``append`` locks only its own byte-write, so the check-then-act pair that used to
        live in ``worktrees/modules/closeout.py`` -- ``evaluate_gate`` at one end, an ``apply_gate``
        append at the other, with the code commit, the memory quality gate, the memory commit, the
        ledger commit and ``write_contract`` in between -- interleaved freely. Measured with two
        real processes and a 0.4s body: both closeouts were permitted, two ``applied`` snapshots
        landed on disk, one approval was spent twice. Neither end was losing a record; both ends
        were reading a fold that stopped being true before it was used.

        The gateless verdict (``permitted`` with ``gate_id is None``) claims nothing and writes
        nothing: a lifecycle with no gate of this kind is governed by the chat/commit approval
        channel, exactly as before, and that additive fallback is preserved. A refusal likewise
        writes nothing -- an approval is only ever consumed on the path that was permitted to
        consume it.

        ``now`` is the caller's stamp, so the append stays a pure function of what was read.
        """
        with exclusive_access(self.log_path(lifecycle_id), GATE_OWNERSHIP):
            gates = self.current(lifecycle_id)
            guard = evaluate_gate(gates, kind=kind, policy=policy)
            if not guard.permitted or guard.gate_id is None:
                return guard
            # Re-entrant: `append` takes this same lock again on this thread, which
            # `exclusive_access` recognises rather than deadlocks on, so the write keeps its
            # declared-writer check instead of being open-coded here to dodge the nesting.
            self.append(apply_gate(gates[guard.gate_id], now=now))
            return guard

    def delete(self, gate_id: str, lifecycle_id: str | None) -> bool:
        """Physically remove one gate id from its log."""
        path = self.log_path(lifecycle_id)
        with exclusive_access(path, GATE_OWNERSHIP):
            records = self.read(lifecycle_id)
            kept = [record for record in records if record.id != gate_id]
            if len(kept) == len(records):
                return False
            self._replace(lifecycle_id, kept)
            return True

    def compact(self, lifecycle_id: str | None, *, now: datetime) -> int:
        """Prune expired or consumed interaction records from one gate log.

        The MCP process owns this: it is the process that mints and consumes gates. THIS METHOD
        DOES NOT ENFORCE THAT, and must not be read as though it did -- it takes the log's lock
        and rewrites, and a call from any process proceeds. ``rewrite_lines`` checks only that
        the lock is held (``require_lock_held``), never who is calling.

        The ownership guard is at the CALL SITE: ``mcp/tools/gates.py`` asks
        :meth:`~agents_remember.controlplane.durable_store.StoreOwnership.is_compaction_owner`
        and skips the reclaim when it is not the owner. It has to live there rather than here for
        the reason that predicate's docstring gives -- the decide path is shared code executed by
        both processes -- and note that
        :meth:`~agents_remember.controlplane.durable_store.StoreOwnership.check_declared_writer`
        could not have served as the guard in any case: the dashboard IS a declared gate writer
        (``GATE_OWNERSHIP.writers``), so it would never have raised here.

        What keeps a rewrite from this method safe, in every process, is the lock it holds across
        the read AND the rewrite -- not ownership.
        """
        path = self.log_path(lifecycle_id)
        with exclusive_access(path, GATE_OWNERSHIP):
            records = self.read(lifecycle_id)
            if not records:
                return 0
            keep_ids = gate_keep_ids(records, now=now)
            kept = [record for record in records if record.id in keep_ids]
            if len(kept) == len(records):
                return 0
            self._replace(lifecycle_id, kept)
            return len(records) - len(kept)

    def projected_current(
        self, lifecycle_id: str | None, *, now: datetime | None
    ) -> dict[str, GateRecord]:
        """The live gate set the dashboard renders: folded and keep-filtered from ONE read.

        260707-HFX2-L12 F8/CS-6 D2 kept the single read; leaf 260731-EFA-L5 removed the
        rewrite that used to ride it. The projection applies the same keep-filter in memory,
        so what the dashboard renders is unchanged, but reclaiming the log on the projection
        tick was compaction running in a process that owns nothing here -- the source of the
        measured loss. Physical reclamation is :meth:`compact`, in the MCP process.

        ``now=None`` folds without the retention filter, which is what a caller holding no
        clock has always been given -- retention is a question about a moment, and a caller
        that did not name one is not asking it.
        """
        records = self.read_for_projection(lifecycle_id)
        keep_ids = None if now is None else gate_keep_ids(records, now=now)
        latest: dict[str, GateRecord] = {}
        for record in records:
            if keep_ids is None or record.id in keep_ids:
                latest[record.id] = record
        return latest

    def lifecycle_ids(self) -> list[str | None]:
        """Lifecycle ids with gate logs, plus workspace when present."""
        ids: list[str | None] = []
        lifecycles = self._root / "lifecycles"
        if lifecycles.is_dir():
            ids.extend(
                entry.name
                for entry in sorted(lifecycles.iterdir())
                if entry.is_dir() and (entry / "gates.jsonl").is_file()
            )
        if self.log_path(None).is_file():
            ids.append(None)
        return ids

    def _replace(self, lifecycle_id: str | None, records: list[GateRecord]) -> None:
        """Rewrite one gate log. Its lock -- held by the caller across the read too -- is what
        makes this safe, and it is the ONLY thing checked: ``rewrite_lines`` verifies the lock is
        held and nothing else. No owner check happens here or anywhere below here;
        ``GATE_OWNERSHIP`` is passed so a refusal can name the store."""
        rewrite_lines(
            self.log_path(lifecycle_id),
            [record.model_dump_json(by_alias=True, exclude_none=True) for record in records],
            GATE_OWNERSHIP,
        )
