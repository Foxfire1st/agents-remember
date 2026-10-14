"""Runtime process-surface readers: enclosures and engine facts.

These readers describe the live worktree population -- enclosure contracts and
the enriched engine process facts the Engine Room map renders. The git-backed
ledger enrichment is imported from the analytical readers, which own the ledger
window.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.observer.projection import EnclosureNode, EngineProcessFacts
from agents_remember.serving.projections.contract_snapshot import (
    ContractSnapshot,
    build_contract_snapshot,
)
from agents_remember.serving.projections.snapshots_impl._analytics import _ledger_window
from agents_remember.serving.projections.snapshots_impl._common import (
    STATUS_PAYLOAD_TTL_SECONDS,
    _status_payload_cache,
)
from agents_remember.worktrees.integration.lifecycle.observation.projection import (
    latest_operation_projection,
)
from agents_remember.worktrees.modules.guidance import (
    contract_payload,
    lifecycle_guidance,
    projected_status_payload,
)
from agents_remember.worktrees.modules.landing import unobserved_landing_refs
from agents_remember.worktrees.worktree_contract import WorktreeContract

if TYPE_CHECKING:
    from agents_remember.serving.projections.landing_state import LandingStateReader

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
        lifecycleOperation=latest_operation_projection(contract.contract_path),
    )


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
