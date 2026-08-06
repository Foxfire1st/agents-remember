"""Runtime process-surface readers: enclosures, gates, inbox, expectations, engine facts.

These readers describe the live worktree population -- enclosure contracts,
gate state, agent inbox pickups, expectation rows, and the enriched engine
process facts the Engine Room map renders. The git-backed ledger enrichment is
imported from the analytical readers, which own the ledger window.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.interaction_retention import (
    AGENT_PICKUP_TTL_SECONDS,
    pickup_age_seconds,
    pickup_state,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import GateRecord
from agents_remember.controlplane.store import GateStore
from agents_remember.observer.contract_snapshot import (
    ContractSnapshot,
    build_contract_snapshot,
)
from agents_remember.observer.paths import observer_logs_root
from agents_remember.observer.projection import (
    AgentPickupNode,
    EnclosureNode,
    EngineProcessFacts,
    ExpectationRowNode,
)
from agents_remember.observer.snapshots_impl._analytics import _ledger_window
from agents_remember.observer.snapshots_impl._common import (
    STATUS_PAYLOAD_TTL_SECONDS,
    _status_payload_cache,
)
from agents_remember.worktrees.modules.guidance import (
    contract_payload,
    lifecycle_guidance,
    projected_status_payload,
)
from agents_remember.worktrees.modules.landing import unobserved_landing_refs
from agents_remember.worktrees.worktree_contract import WorktreeContract

if TYPE_CHECKING:
    from agents_remember.observer.landing_state import LandingStateReader

logger = logging.getLogger(__name__)


def read_enclosures(
    coordination_root: Path, *, contracts: ContractSnapshot | None = None
) -> list[EnclosureNode]:
    """Surfaces 5/6: every active leaf enclosure contract.

    Leaf contracts live below ``enclosures/<leaf-id>/series-contract.md``. Root
    series contracts describe integration branches and are not live worktree
    processes. A malformed contract is skipped, never fatal to the projection.

    260712-PTS-L2: the projection tick passes its shared per-tick
    :class:`ContractSnapshot` so this reader adds ZERO contract parses; a
    standalone call (``contracts=None``) builds a local snapshot, preserving the
    public signature and the walk-and-skip behavior it had before.
    """
    snapshot = (
        contracts if contracts is not None else build_contract_snapshot(coordination_root / "tasks")
    )
    return [_enclosure_from_contract(contract) for contract in snapshot.contracts.values()]


def _enclosure_from_contract(contract: WorktreeContract) -> EnclosureNode:
    return EnclosureNode(
        enclosure=contract.contract_path.as_posix(),
        enclosureId=contract.leaf_id or contract.contract_path.parent.name,
        leafId=contract.leaf_id,
        taskId=contract.task_id,
        taskName=contract.task_name,
        repoName=contract.repo_name,
        taskRoot=contract.task_root.as_posix(),
        lifecycleId=contract.lifecycle_id,
        worktreeGroup=contract.worktree_group.as_posix(),
        humanReviewStatus=contract.human_review_status,
        closeoutStatus=contract.closeout_status,
        integrationStatus=contract.integration_status,
        cleanup=contract.cleanup,
        # Worktree-existence truth (L11), stat'ed here at snapshot time exactly as
        # worktree_status reports it: the tasks surface renders a leaf ONLY while a
        # worktree physically exists, so this must never be inferred from cleanup state.
        codeWorktreeExists=contract.code_worktree.exists(),
        memoryWorktreeExists=(
            contract.memory_worktree.exists() if contract.memory_worktree else False
        ),
    )


def read_gates(coordination_root: Path, *, now: datetime | None = None) -> list[GateRecord]:
    """Every lifecycle's current (folded) gate set + the workspace log (slice 6c).

    Reads the gate logs co-located with the event store under ``observer_logs_root``
    and folds each by id (last-wins), so the projection sees live gate state with no
    event machinery. A malformed log is skipped, never fatal to the tick.

    260707-HFX2-L12 F8/CS-6 D2: one directory scan + one read per gate log per tick.

    260731-EFA-L5: this tick no longer rewrites anything. It used to physically prune every
    gate log on a 30s cadence -- compaction running in the process that owns nothing here,
    racing the MCP server's appends, which is where the measured record loss came from. The
    projection output is unchanged (the same keep-filter is applied in memory by
    ``projected_current``); on-disk reclamation is ``GateStore.compact`` in the gate log's
    owner, the MCP process. The read is deliberately the TOLERANT one: a torn line must cost
    the dashboard one row for one tick, never a crashed tick -- the STRICT read is what the
    enforcement fold uses, and it still raises.

    THE SUPPRESSION IS NAMED, NOT BROAD, for the reason
    ``controlplane/gate_decisions.py::_reclaim_gate_log`` gives: ``ValidationError`` subclasses
    ``ValueError``, so a ``suppress`` written for I/O silently swallows a malformed record too, and
    this leaf narrowed that spelling there on principle. Two spellings of one decision is what the
    principle is against. Here the narrowing removes a net rather than a catch:
    ``projected_current`` is the TOLERANT read and already
    skips an unreadable row per row, so no ``ValidationError`` can reach this line, and
    ``age_seconds`` -- the only other thing in the fold that parses -- returns ``None`` on a stamp
    it cannot read rather than raising. What is left to suppress is the I/O the suppress was
    always for: a log removed or made unreadable between ``lifecycle_ids`` and the read.
    """
    store = GateStore(observer_logs_root(coordination_root))
    gates: list[GateRecord] = []
    for lifecycle_id in store.lifecycle_ids():
        with contextlib.suppress(OSError):
            gates.extend(store.projected_current(lifecycle_id, now=now).values())
    return gates


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_runtime.py:141).
def read_agent_pickups(
    coordination_root: Path, *, now: datetime
) -> list[AgentPickupNode]:  # pragma: no cover
    """Pending dashboard responses waiting for agent-side inbox consumption.

    260731-EFA-L5: ``ValidationError`` by name, and unlike ``read_gates`` above it is genuinely
    load-bearing here -- ``OperatorInboxStore._read_unlocked`` is STRICT on purpose (an inbox row
    nobody can parse is an ack nobody can account for, and ``consume`` decides on that fold), so a
    torn row really does raise out of both calls below and really must not crash the tick. Spelt
    ``ValidationError`` rather than ``ValueError`` all the same: the wide net would also swallow
    an unrelated ``ValueError`` from anywhere in the loop, which is the trap this leaf closed in
    ``application/gate_tools.py``. ``DurableStoreError`` is a ``RuntimeError`` and still propagates.
    """
    store = OperatorInboxStore(observer_logs_root(coordination_root))
    with contextlib.suppress(OSError, ValidationError):
        store.compact(now=now)
    pickups: list[AgentPickupNode] = []
    with contextlib.suppress(OSError, ValidationError):
        for entry in store.current().values():
            if entry.state != "pending":
                continue
            pickups.append(
                AgentPickupNode(
                    id=f"pickup:{entry.id}",
                    entryId=entry.id,
                    lifecycleId=entry.lifecycleId,
                    agentId=entry.agentId,
                    senderAgentId=entry.senderAgentId,
                    senderRole=entry.senderRole,
                    recipientRole=entry.recipientRole,
                    ownerRole=entry.ownerRole,
                    ownerAgentId=entry.ownerAgentId,
                    ownerLifecycleId=entry.ownerLifecycleId,
                    gateId=entry.gateId,
                    messageKind=entry.messageKind,
                    artifactPath=entry.artifactPath,
                    deliveryState=entry.deliveryState,
                    deliveredToSession=entry.deliveredToSession,
                    attemptCount=entry.attemptCount,
                    lastAttemptAt=entry.lastAttemptAt,
                    nextAttemptAt=entry.nextAttemptAt,
                    escalatedAt=entry.escalatedAt,
                    state=pickup_state(entry, now=now),
                    ageSeconds=pickup_age_seconds(entry, now=now),
                    ttlSeconds=AGENT_PICKUP_TTL_SECONDS,
                )
            )
    return sorted(pickups, key=lambda item: item.ageSeconds or 0.0, reverse=True)


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_runtime.py:189).
def read_expectation_rows(
    coordination_root: Path, *, now: datetime
) -> list[ExpectationRowNode]:  # pragma: no cover
    """Pending expectation (deadline) rows, for dashboard/architect observability (R5).

    Surfacing only: an L2 predicate reads ``ExpectationRowStore`` directly and never this
    projection (the #22 correctness half stays L2's rule; this is the visibility half).

    260731-EFA-L5 R8: ``pending_for_projection`` and not ``pending``. The strict read raises
    ``ValidationError``, which subclasses ``ValueError`` -- so the ``suppress`` below used to
    swallow ONE torn line by discarding EVERY deadline in the file, and the dashboard showed an
    operator nothing due. The tolerant read degrades per row instead, so the suppress is now
    ``OSError`` only -- it is the I/O it was always for, and keeping ``ValueError`` beside a
    tolerant read would leave the same wide net this leaf narrowed in ``application/gate_tools.py``. The
    inner ``except ValueError`` below is a different question and stays: it is the per-row parse
    of one ``dueAt``, and an unparseable deadline means "not overdue", not "drop every row".
    """
    store = ExpectationRowStore(observer_logs_root(coordination_root))
    rows: list[ExpectationRowNode] = []
    with contextlib.suppress(OSError):
        for row in store.pending_for_projection():
            try:
                due_at = datetime.fromisoformat(row.dueAt)
                overdue = now >= due_at
            except ValueError:
                overdue = False
            rows.append(
                ExpectationRowNode(
                    id=row.id,
                    kind=row.kind,
                    state=row.state,
                    sourceId=row.sourceId,
                    subjectAgentId=row.subjectAgentId,
                    subjectLifecycleId=row.subjectLifecycleId,
                    leafKey=row.leafKey,
                    dueAt=row.dueAt,
                    overdue=overdue,
                    note=row.note,
                )
            )
    return sorted(rows, key=lambda item: item.dueAt)


def read_engine_process_facts(
    coordination_root: Path,
    *,
    active_worktree_groups: set[str] | None = None,
    now: datetime | None = None,
    landing_state: LandingStateReader | None = None,
    contracts: ContractSnapshot | None = None,
) -> list[EngineProcessFacts]:
    """Slice 5e: gather one fact bundle per leaf enclosure for the Engine Room map.

    Reads the same leaf enclosure contracts as :func:`read_enclosures` (via the shared
    per-tick :class:`ContractSnapshot` when the projection passes one; a standalone call
    builds a local snapshot), but enriches each with the status-guidance facts the
    structural ``EnclosureNode`` omits (the code/memory branches, base commits, worktree
    paths, existence/dirty flags, base freshness, and provider-boot status).
    ``contract_payload`` and ``lifecycle_guidance`` are pure; only ``status_payload``
    touches git, and it is best-effort so a contract pointing at absent or fake worktrees
    degrades to ``status=None`` (rendered as missing/derived) instead of crashing the
    projection tick. A malformed contract is skipped, never fatal.
    """
    snapshot = (
        contracts if contracts is not None else build_contract_snapshot(coordination_root / "tasks")
    )
    facts: list[EngineProcessFacts] = []
    seen_status_keys: set[str] = set()
    for path, contract in snapshot.contracts.items():
        if (
            active_worktree_groups is not None
            and contract.worktree_group.name not in active_worktree_groups
        ):
            continue
        cp = contract_payload(contract)
        ledger_rows, ledger_total = _ledger_window(
            cp.get("ledger_path"),
            code_root=cp.get("code_worktree"),
            memory_root=cp.get("memory_worktree"),
        )
        status_key = str(path)
        seen_status_keys.add(status_key)
        facts.append(
            EngineProcessFacts(
                contract=cp,
                # Widened at the boundary: `EngineProcessFacts` is the projection's untyped
                # input carrier, and the reducer folds it by key name.
                guidance=dict(lifecycle_guidance(contract)),
                status=_safe_status_payload(
                    contract,
                    cache_key=status_key,
                    now=now,
                    landing_state=landing_state,
                ),
                ledger_rows=ledger_rows,
                ledger_row_count=ledger_total,
            )
        )
    # F11: keep the git-status cache bounded to the live leaf set (no unbounded growth over daemon life).
    if now is not None:
        for stale in [key for key in _status_payload_cache if key not in seen_status_keys]:
            _status_payload_cache.pop(stale, None)
    return facts


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_runtime.py:292).
def refresh_engine_process_landing(  # pragma: no cover
    facts: list[EngineProcessFacts],
    *,
    now: datetime,
    landing_state: LandingStateReader | None,
    contracts: ContractSnapshot,
) -> list[EngineProcessFacts]:
    """Refresh only the background-published landing tail of retained Engine Room facts.

    A heartbeat changes landing ages and may publish a new remote observation, but it does
    not change contract, guidance, local Git status, or ledger facts. Reusing those retained
    inputs avoids rerunning the Git-backed full Engine Room reader on every heartbeat while
    preserving the volatile landing truth on the same cadence.
    """
    contracts_by_path = {
        path.as_posix(): contract for path, contract in contracts.contracts.items()
    }
    refreshed: list[EngineProcessFacts] = []
    for fact in facts:
        status = fact.status
        contract_path = str(fact.contract.get("contract_path", ""))
        contract = contracts_by_path.get(contract_path)
        if status is None or contract is None:
            refreshed.append(fact)
            continue
        try:
            landing = (
                landing_state.current(contract, now=now)
                if landing_state is not None
                else unobserved_landing_refs(contract)
            )
        except Exception:
            logger.warning(
                "landing snapshot merge failed for %s; using retained local status",
                contract_path,
                exc_info=True,
            )
            refreshed.append(fact)
            continue
        next_status = {key: value for key, value in status.items() if key != "landing"}
        if landing is not None:
            next_status["landing"] = landing
        refreshed.append(replace(fact, status=next_status))
    return refreshed


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_runtime.py:338).
def _safe_status_payload(  # pragma: no cover
    contract: Any,
    *,
    cache_key: str | None = None,
    now: datetime | None = None,
    landing_state: LandingStateReader | None = None,
) -> dict[str, Any] | None:
    """Read cached local status, then attach immutable landing facts without remote work.

    The local git result remains TTL-cached. Landing facts are merged after that cache lookup so a
    newly published background observation is visible on the next projection tick.
    """
    value = _cached_local_status(contract, cache_key=cache_key, now=now)
    if value is None:
        return None
    moment = now or datetime.now(UTC)
    try:
        landing = (
            landing_state.current(contract, now=moment)
            if landing_state is not None
            else unobserved_landing_refs(contract)
        )
    except Exception:
        # The local status is already truthful. A malformed injected/immutable landing snapshot
        # must sacrifice only this contract's landing detail, not freeze the whole projection tick.
        logger.warning(
            "landing snapshot merge failed for %s; using local status",
            getattr(contract, "contract_path", "unknown-contract"),
            exc_info=True,
        )
        return value
    if landing is None:
        return value
    return {**value, "landing": landing}


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/snapshots_impl/_runtime.py:374).
def _cached_local_status(  # pragma: no cover
    contract: Any, *, cache_key: str | None, now: datetime | None
) -> dict[str, Any] | None:
    if cache_key is not None and now is not None:
        cached = _status_payload_cache.get(cache_key)
        if (
            cached is not None
            and 0 <= (now - cached[0]).total_seconds() < STATUS_PAYLOAD_TTL_SECONDS
        ):
            return cached[1]
    value: dict[str, Any] | None
    try:
        value = dict(projected_status_payload(contract, landing=None))
    except Exception:  # local status for one broken worktree must not fail the whole projection
        value = None
    if cache_key is not None and now is not None:
        _status_payload_cache[cache_key] = (now, value)
    return value
