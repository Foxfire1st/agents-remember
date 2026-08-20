"""Atomic-blocker transitions: acquisition, release, and abort (L13-R3).

An in-flight atomic block owns the sprint landing lane for its entire lifetime:
the scheduler never admits a second block concurrently, and landings by any other
master or organizational leaf are refused with the blocker-held fact and the
owning candidate identity. Acquisition reports in-flight organizational leafs as
facts — the start-anyway decision remains strategist/orchestrator judgment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.queue.closeout_queue import (
    LANE_OCCUPYING_STATES,
    ActiveAtomicBlocker,
    CloseoutQueueRequest,
    CloseoutQueueState,
)
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.atomic_series_seal import require_series_path_accepting_leaves
from agents_remember.worktrees.modules.git import branch_commit
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

from .closeout_queue_candidate_evidence import (
    atomic_master_landing_authority,
    require_atomic_master_landed,
    require_source_bases_current,
)
from .closeout_queue_errors import CloseoutQueueError, queue_task_ref
from .closeout_queue_evidence import canonical_blocker_abort
from .closeout_queue_graph import (
    QueueGraphContext,
    acquisition_facts,
    master_incomplete_predecessors,
)


def _lane_owners(state: CloseoutQueueState) -> list[Any]:
    return [
        candidate
        for candidate in state.candidates.values()
        if candidate.state in LANE_OCCUPYING_STATES
    ]


def _refusal_facts(graph: QueueGraphContext, state: CloseoutQueueState) -> str:
    lane_owners = _lane_owners(state)
    facts: dict[str, Any] = {
        "atomicBlockerOwner": (
            state.activeBlocker.master.key if state.activeBlocker is not None else None
        ),
        "ownerCandidate": lane_owners[0].taskDocumentRef.key if lane_owners else None,
        **acquisition_facts(graph, state),
    }
    return json.dumps(facts, sort_keys=True)


def _require_unsealed_blocker_series(master: ResolvedTaskDocument) -> WorktreeContract:
    path = series_contract_path(master.path.parent)
    try:
        return require_series_path_accepting_leaves(
            path,
            operation="atomic blocker acquisition",
        )
    except RuntimeError as exc:
        raise CloseoutQueueError(
            "atomic-blocker-series-sealed",
            str(exc),
        ) from exc


def _acquire_blocker(
    graph: QueueGraphContext,
    state: CloseoutQueueState,
    request: CloseoutQueueRequest,
    timestamp: str,
    actor_identity: str,
) -> CloseoutQueueState:
    master_ref = queue_task_ref(request.blocker_master_ref, "blocker_master_ref")
    master = graph.masters.get(master_ref)
    if master is None:
        raise CloseoutQueueError(
            "atomic-blocker-master-unknown", f"master is not in the sprint graph: {master_ref.key}"
        )
    if master.document.executionNature != "atomic":
        raise CloseoutQueueError(
            "atomic-blocker-nature-required", "only an atomic master can own a blocker"
        )
    require_source_bases_current(_require_unsealed_blocker_series(master))
    if state.activeBlocker is not None:
        if (
            state.activeBlocker.master == master_ref
            and state.activeBlocker.graphRevision == graph.revision
        ):
            return state
        if state.activeBlocker.master == master_ref:
            raise CloseoutQueueError(
                "atomic-blocker-graph-stale",
                "the active blocker belongs to an older graph revision; release it first",
            )
        raise CloseoutQueueError(
            "atomic-blocker-active",
            f"blocker is already held by {state.activeBlocker.master.key}; "
            f"facts={_refusal_facts(graph, state)}",
        )
    incomplete = list(master_incomplete_predecessors(graph, master_ref))
    if incomplete:
        raise CloseoutQueueError(
            "atomic-blocker-predecessors-incomplete",
            f"atomic blocker predecessors are incomplete: {[node.ref.key for node in incomplete]!r}",
        )
    lane_owners = _lane_owners(state)
    if lane_owners:
        raise CloseoutQueueError(
            "atomic-blocker-in-flight-conflict",
            "the sprint landing lane is not drained: lane-occupying candidates "
            f"{[candidate.taskDocumentRef.key for candidate in lane_owners]!r}; "
            f"facts={_refusal_facts(graph, state)}",
        )
    rationale = request.rationale.strip()
    if not rationale:
        raise CloseoutQueueError(
            "atomic-blocker-rationale-required", "blocker acquisition requires rationale"
        )
    return state.model_copy(
        update={
            "activeBlocker": ActiveAtomicBlocker(
                master=master_ref,
                graphRevision=graph.revision,
                acquiredBy=actor_identity,
                acquiredAt=timestamp,
                rationale=rationale,
            )
        }
    )


def _release_blocker(
    graph: QueueGraphContext,
    state: CloseoutQueueState,
    request: CloseoutQueueRequest,
    config: McpRuntimeConfig,
) -> CloseoutQueueState:
    if state.activeBlocker is None:
        raise CloseoutQueueError("atomic-blocker-not-active", "no atomic blocker is active")
    asserted = queue_task_ref(request.blocker_master_ref, "blocker_master_ref")
    if asserted != state.activeBlocker.master:
        raise CloseoutQueueError(
            "atomic-blocker-owner-mismatch",
            f"active blocker belongs to {state.activeBlocker.master.key}",
        )
    if any(candidate.owningMaster == asserted for candidate in state.candidates.values()):
        raise CloseoutQueueError(
            "atomic-blocker-candidates-remain",
            "the atomic master still has declared or lifecycle-owned candidates",
        )
    master = graph.masters.get(asserted)
    if master is None or master.document.status != "Completed":
        raise CloseoutQueueError(
            "atomic-blocker-master-incomplete",
            "normal blocker release requires the canonical atomic master completion edge",
        )
    require_atomic_master_landed(
        master,
        atomic_master_landing_authority(config, graph.sprint),
    )
    if not request.rationale.strip():
        raise CloseoutQueueError(
            "atomic-blocker-rationale-required", "blocker release requires rationale"
        )
    return state.model_copy(update={"activeBlocker": None})


def _abort_blocker(
    graph: QueueGraphContext, state: CloseoutQueueState, request: CloseoutQueueRequest
) -> CloseoutQueueState:
    if state.activeBlocker is None:
        raise CloseoutQueueError("atomic-blocker-not-active", "no atomic blocker is active")
    asserted = queue_task_ref(request.blocker_master_ref, "blocker_master_ref")
    if asserted != state.activeBlocker.master:
        raise CloseoutQueueError(
            "atomic-blocker-owner-mismatch",
            f"active blocker belongs to {state.activeBlocker.master.key}",
        )
    if any(candidate.owningMaster == asserted for candidate in state.candidates.values()):
        raise CloseoutQueueError(
            "atomic-blocker-candidates-remain",
            "withdraw or finish every atomic candidate before recording an abort",
        )
    canonical_blocker_abort(
        request.blocker_judgment_id,
        authority=graph.grade_authority,
        master_ref=asserted,
        graph_revision=graph.revision,
    )
    return state.model_copy(update={"activeBlocker": None})


def _boundary_recovery(blockers: list[str]) -> str:
    """Name the sync-first recovery when a boundary refusal is a stale base (L13-R2)."""

    if any(
        blocker.startswith(
            ("code-source-moved", "memory-source-moved", "ledger-base-mapping-changed")
        )
        for blocker in blockers
    ):
        return "; recovery: worktree_sync"
    return ""


def _stale_sibling_facts(state: CloseoutQueueState) -> list[dict[str, Any]]:
    """Remaining candidates whose recorded base pair no longer matches the source tips.

    A sibling whose contract cannot be read is reported with a
    ``contract-unreadable`` fact — the lane-release mechanism reports, it never
    swallows.
    """

    stale: list[dict[str, Any]] = []
    for candidate in state.candidates.values():
        try:
            contract = load_contract(Path(candidate.contractPath))
            code_tip = branch_commit(contract.code_repo_path, contract.code_source_branch)
            memory_tip = (
                branch_commit(contract.memory_repo_path, contract.memory_source_branch)
                if contract.memory_mode == "external" and contract.memory_repo_path is not None
                else None
            )
        except (ContractError, OSError, RuntimeError, ValueError):
            stale.append(
                {
                    "candidate": candidate.taskDocumentRef.key,
                    "owningMaster": candidate.owningMaster.key,
                    "fact": "contract-unreadable",
                }
            )
            continue
        code_stale = code_tip != candidate.codeBaseCommit
        memory_stale = (
            candidate.memoryBaseCommit is not None and memory_tip != candidate.memoryBaseCommit
        )
        if code_stale or memory_stale:
            stale.append(
                {
                    "candidate": candidate.taskDocumentRef.key,
                    "owningMaster": candidate.owningMaster.key,
                    "recordedCodeBase": candidate.codeBaseCommit,
                    "currentCodeSourceTip": code_tip,
                    "recordedMemoryBase": candidate.memoryBaseCommit,
                    "currentMemorySourceTip": memory_tip,
                    "recovery": "worktree_sync",
                }
            )
    return sorted(stale, key=lambda row: row["candidate"])
