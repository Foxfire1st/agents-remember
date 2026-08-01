from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.git_freshness import ahead_behind
from agents_remember.kernel.memory_ledger import LedgerError, find_mapping, load_ledger
from agents_remember.worktrees.modules import provider_async
from agents_remember.worktrees.modules.git import worktree_dirty
from agents_remember.worktrees.modules.landing import landing_refs
from agents_remember.worktrees.worktree_contract import (
    CleanupStatus,
    CloseoutStatus,
    HumanReviewStatus,
    IntegrationStatus,
    MemoryMode,
    WorkflowKind,
    WorktreeContract,
)

# The guidance vocabularies, declared once, here -- this module is the state machine that
# produces every one of them, and `models.worktree` imports these aliases for the response
# boundary instead of keeping a second hand-written copy that drifted (`carryover-pending`,
# `abandoned`, `request_carryover_decision` and `memory_carryover_apply` were all emitted
# below and all rejected by the packet).
WorktreePhase = Literal[
    "worktree-started",
    "closeout-pending",
    "integration-pending",
    "integration-blocked",
    "carryover-pending",
    "cleanup-pending",
    "cleanup-completed",
    "abandoned",
]
NextOperation = Literal[
    "continue_work",
    "closeout",
    "request_integration_decision",
    "developer_decision",
    "request_carryover_decision",
    "request_cleanup_decision",
    "done",
]
NextTool = Literal[
    "worktree_status",
    "worktree_closeout_apply",
    "worktree_integrate",
    "memory_carryover_apply",
    "worktree_cleanup",
]
# The *other* users of the same nextOperation/nextTool shape: the closeout preview's commit
# gate and the four blocked-start / blocked-sync recovery payloads. Every one of them is a
# `WorktreeCommandResult` rendered as a `FlexibleToolResponse` -- none reaches
# `WorktreeSummary`, whose only producer is `lifecycle_guidance` via `worktrees.status`. They
# get their own builder and their own vocabulary rather than widening the phase machine's,
# because a wider `NextOperation` would put "requires developer approval" and "blocked on a
# stale base" back into the set the context packet's `nextOperation` claims to be.
RecoveryOperation = Literal[
    "request_commit_approval",
    "choose_memory_recovery",
    "choose_provider_setup_recovery",
    "choose_stale_base_recovery",
    "choose_memory_sync_recovery",
]
RecoveryTool = Literal["worktree_start", "worktree_sync", "worktree_closeout_apply"]


class NextGuidance(TypedDict):
    """The move out of a phase: which operation, and the tool call that performs it.

    ``nextTool`` is deliberately absent -- not blank -- when the operation needs no call
    (``done``). The wire model declares it optional for exactly that reason, so the packet
    projection reads it with ``.get`` and lets ``exclude_none`` drop it.
    """

    nextOperation: NextOperation
    nextTool: NotRequired[NextTool]
    nextArgs: NotRequired[dict[str, object]]
    nextRequiredArgs: NotRequired[list[str]]


class LifecycleGuidance(TypedDict):
    """Everything :func:`lifecycle_guidance` returns: the phase, plus the move out of it."""

    phase: WorktreePhase
    summary: str
    nextOperation: NextOperation
    nextTool: NotRequired[NextTool]
    nextArgs: NotRequired[dict[str, object]]
    nextRequiredArgs: NotRequired[list[str]]
    # Only the cleanup-pending phase carries it (05m; the dashboard renders it).
    carryoverDoneAt: NotRequired[str]


class WorktreeStatusFacts(TypedDict):
    """The local, contract-derived half of a worktree status payload.

    Snake_case because it is the tool-response shape; the camelCase packet projection in
    :mod:`agents_remember.worktrees.status` maps it onto ``WorktreeSummary``.
    """

    task_id: str
    task_name: str
    code_repository_name: str
    workflow_kind: WorkflowKind
    memory_mode: MemoryMode
    kind: str
    leaf_id: str
    enclosure_path: str
    contract_path: str
    parent_contract_path: str
    worktree_group: str
    code_worktree: str
    code_worktree_exists: bool
    code_worktree_dirty: bool
    memory_worktree: str
    memory_worktree_exists: bool
    memory_worktree_dirty: bool
    ledger_path: str
    human_review_status: HumanReviewStatus
    approved_for_commit: bool
    closeout_status: CloseoutStatus
    integration_status: IntegrationStatus
    cleanup: CleanupStatus
    lifecycle_id: str
    # Present only when the contract file carried a cell outside its vocabulary, which the
    # reader substituted for (`worktree_contract._vocabulary_cell`). Absent is the normal
    # shape -- 213 of 213 contracts on disk -- so the key is the exception report, not a flag.
    unknown_contract_cells: NotRequired[list[str]]
    providers: NotRequired[dict[str, Any]]
    freshness: NotRequired[dict[str, object]]
    landing: NotRequired[list[dict[str, object]]]


class WorktreeStatusPayload(WorktreeStatusFacts, LifecycleGuidance):
    """A full ``worktree_status`` payload: the contract facts plus the guidance."""


def next_guidance(
    operation: NextOperation,
    *,
    tool: NextTool | None = None,
    args: dict[str, object] | None = None,
    required_args: list[str] | None = None,
) -> NextGuidance:
    payload: NextGuidance = {"nextOperation": operation}
    if tool:
        payload["nextTool"] = tool
    if args is not None:
        payload["nextArgs"] = args
    if required_args:
        payload["nextRequiredArgs"] = required_args
    return payload


def recovery_guidance(
    operation: RecoveryOperation,
    *,
    tool: RecoveryTool,
    args: dict[str, object],
    required_args: list[str] | None = None,
) -> dict[str, object]:
    """The same next-move keys for a payload that is a *gate or a block*, not a phase.

    Emits exactly what :func:`next_guidance` does, in the same key order, so nothing on the
    wire changes; the split is in the type, and that is the point. These callers hand their
    result to a `FlexibleToolResponse`, so widening `next_guidance` to fit them would have
    silently widened `WorktreeSummary.nextOperation` too -- and the context packet would go
    back to claiming values its own state machine can never produce. ``tool`` and ``args``
    are required here because a block that cannot say how to recover is not worth emitting.
    """
    payload: dict[str, object] = {
        "nextOperation": operation,
        "nextTool": tool,
        "nextArgs": args,
    }
    if required_args:
        payload["nextRequiredArgs"] = required_args
    return payload


def contract_next_args(contract: WorktreeContract, **extra: object) -> dict[str, object]:
    return {
        "enclosure_path": contract.contract_path.as_posix(),
        "contract_path": contract.contract_path.as_posix(),
        **extra,
    }


def contract_payload(contract: WorktreeContract) -> dict[str, object]:
    data = asdict(contract)
    for key, value in list(data.items()):
        if isinstance(value, Path):
            data[key] = value.as_posix()
        elif value is None:
            data[key] = ""
    return data


def carryover_done(contract: WorktreeContract) -> tuple[bool, str]:
    """Has the parked memory been carried into official memory? -> ``(done, carryoverDoneAt)`` (05m).

    The existing ``memory_carryover_apply`` is contract-decoupled and leaves no contract stamp, so the
    honest signal is the official ledger itself: a successful carry prepends a row to the official
    ``memory.md`` mapping the landed code commit -> the carried memory commit. We read that ledger and
    look the landed commit up with :func:`find_mapping`; the carried memory commit's ``%cI`` is the
    milestone time. Internal/disabled memory has nothing to carry, so it is reported done -- the
    carryover route + cleanup guard are external-only no-ops there.
    """
    if contract.memory_mode != "external" or contract.memory_repo_path is None:
        return (True, "")
    landed = contract.integrated_code_commit or contract.code_commit
    ledger_path = contract.memory_repo_path / "memory.md"
    if not landed or not ledger_path.exists():
        return (False, "")
    try:
        row = find_mapping(load_ledger(ledger_path), landed)
    except LedgerError:
        return (False, "")
    if row is None:
        return (False, "")
    dated = run_git(contract.memory_repo_path, ["show", "-s", "--format=%cI", row.memory_commit])
    return (True, dated.stdout.strip() if dated.returncode == 0 else "")


def lifecycle_guidance(contract: WorktreeContract) -> LifecycleGuidance:
    """Where the task stands in the worktree lifecycle, and the next move out of it.

    Read back to front: a reclaimed worktree is done, an integrated one is waiting on
    carryover/cleanup, and everything else is still working toward closeout.
    """
    return (
        _reclaimed_phase(contract)
        or _post_integration_phase(contract)
        or _pre_integration_phase(contract)
    )


def _reclaimed_phase(contract: WorktreeContract) -> LifecycleGuidance | None:
    """Terminal phases: the worktrees are gone, by cleanup or by abandonment."""
    if contract.cleanup == "completed":
        return {
            "phase": "cleanup-completed",
            "summary": "Worktree task lifecycle is complete and cleanup has already run.",
            **next_guidance("done"),
        }
    if contract.cleanup == "abandoned":
        return {
            "phase": "abandoned",
            "summary": "Worktree abandoned — provider stack reclaimed; no further action.",
            **next_guidance("done"),
        }
    return None


def _post_integration_phase(contract: WorktreeContract) -> LifecycleGuidance | None:
    """Integration has been attempted: it blocked, or it landed and carryover/cleanup follow."""
    if contract.integration_status == "blocked":
        return {
            "phase": "integration-blocked",
            "summary": "Integration is blocked; review the conflict or non-fast-forward state with the developer before retrying.",
            **next_guidance(
                "developer_decision",
                tool="worktree_integrate",
                args=contract_next_args(contract, strategy="replay", dry_run=True),
            ),
        }
    if contract.integration_status == "completed":
        carried, carryover_done_at = carryover_done(contract)
        if not carried:
            # 05m: carryover must run while the worktree (its parked memory) still exists -- before
            # cleanup deletes it. Route the existing memory_carryover_apply (its own plan->apply gate
            # stays intact); cleanup hard-guards on this same signal.
            return {
                "phase": "carryover-pending",
                "summary": "Integration completed; carry the parked memory home before cleanup.",
                **next_guidance(
                    "request_carryover_decision",
                    tool="memory_carryover_apply",
                    args={
                        "repo_id": contract.repo_name,
                        "source_memory": contract.memory_worktree.as_posix()
                        if contract.memory_worktree
                        else "",
                        "official_code_ref": contract.integrated_code_commit
                        or contract.code_commit,
                    },
                    required_args=["intent_note"],
                ),
            }
        return {
            "phase": "cleanup-pending",
            "summary": "Carryover completed; cleanup is still pending.",
            # surfaced onto EngineProcessNode.carryoverDoneAt for the dashboard (05m; 5k renders)
            "carryoverDoneAt": carryover_done_at,
            **next_guidance(
                "request_cleanup_decision",
                tool="worktree_cleanup",
                args=contract_next_args(contract, dry_run=True),
            ),
        }
    return None


def _pre_integration_phase(contract: WorktreeContract) -> LifecycleGuidance:
    """Still working: closeout is pending, approved, or already done and awaiting integration.

    slice 09: a dirty worktree is NOT a commit-approval gate. `commit-approval-pending` is owned by the
    closeout preview (the real gate moment, set in closeout.py) and — once the slice-6 gate plane is
    adopted — by a raised `closeout-approval` gate surfaced via GateNode; it is never inferred from
    `git status`. A dirty tree falls through to the honest lifecycle-position phase below.
    """
    if contract.closeout_status == "completed":
        return {
            "phase": "integration-pending",
            "summary": "Closeout completed; integrate the task branches back into their source branches.",
            **next_guidance(
                "request_integration_decision",
                tool="worktree_integrate",
                args=contract_next_args(contract, strategy="ff-only", dry_run=True),
            ),
        }
    if contract.approved_for_commit:
        return {
            "phase": "closeout-pending",
            "summary": "Closeout approval is recorded, but closeout has not completed.",
            **next_guidance(
                "closeout",
                tool="worktree_closeout_apply",
                args=contract_next_args(contract),
                required_args=["intent_note", "code_commit_message"],
            ),
        }
    return {
        "phase": "worktree-started",
        "summary": "Worktrees are ready; continue the wrapped workflow and close out after review.",
        **next_guidance(
            "continue_work",
            tool="worktree_status",
            args=contract_next_args(contract),
        ),
    }


def base_freshness(contract: WorktreeContract) -> dict[str, object] | None:
    """Fetch-free mid-task base drift check (issue #54).

    Compares the recorded base commits against the current local source branch
    tips — no network, safe for the worktree_status polling loop. Local source
    branches move mid-task when a parallel cycle lands (PR merge ff's code main,
    carryover advances memory main), which is exactly the signal that the
    worktree should pull the official line in via worktree_sync. Remote-only
    movement is covered by the fetching checkpoints (context_packet
    include_freshness and the worktree_start preflight) and by worktree_sync's
    own fetch.
    """
    sides: dict[str, dict[str, object]] = {}
    targets: list[tuple[str, Path, str, str]] = [
        ("code", contract.code_repo_path, contract.code_base_commit, contract.code_source_branch)
    ]
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_base_commit
        and contract.memory_source_branch
    ):
        targets.append(
            (
                "memory",
                contract.memory_repo_path,
                contract.memory_base_commit,
                contract.memory_source_branch,
            )
        )
    for side, repo, base, branch in targets:
        if not repo.exists():
            continue
        counts = ahead_behind(repo, base, branch)
        if counts is None:
            continue
        _, behind = counts
        sides[side] = {"baseCommit": base, "sourceBranch": branch, "baseBehindSource": behind}
    if not sides:
        return None
    behind_any = any(side["baseBehindSource"] for side in sides.values())
    payload: dict[str, object] = {
        "state": "behind-official" if behind_any else "current",
        **sides,
    }
    if behind_any:
        payload["syncHint"] = {
            "tool": "worktree_sync",
            "args": contract_next_args(contract, dry_run=True),
        }
    return payload


def _status_payload_with_landing(
    contract: WorktreeContract, landing: list[dict[str, object]] | None
) -> WorktreeStatusPayload:
    guidance = lifecycle_guidance(contract)
    facts: WorktreeStatusFacts = {
        "task_id": contract.task_id,
        "task_name": contract.task_name,
        "code_repository_name": contract.repo_name,
        "workflow_kind": contract.workflow_kind,
        "memory_mode": contract.memory_mode,
        "kind": contract.kind,
        "leaf_id": contract.leaf_id,
        "enclosure_path": contract.contract_path.as_posix(),
        "contract_path": contract.contract_path.as_posix(),
        "parent_contract_path": contract.parent_contract_path.as_posix()
        if contract.parent_contract_path
        else "",
        "worktree_group": contract.worktree_group.as_posix(),
        "code_worktree": contract.code_worktree.as_posix(),
        "code_worktree_exists": contract.code_worktree.exists(),
        "code_worktree_dirty": worktree_dirty(contract.code_worktree),
        "memory_worktree": contract.memory_worktree.as_posix() if contract.memory_worktree else "",
        "memory_worktree_exists": contract.memory_worktree.exists()
        if contract.memory_worktree
        else False,
        "memory_worktree_dirty": worktree_dirty(contract.memory_worktree),
        "ledger_path": contract.ledger_path.as_posix() if contract.ledger_path else "",
        "human_review_status": contract.human_review_status,
        "approved_for_commit": contract.approved_for_commit,
        "closeout_status": contract.closeout_status,
        "integration_status": contract.integration_status,
        "cleanup": contract.cleanup,
        "lifecycle_id": contract.lifecycle_id,
    }
    if contract.unknown_cells:
        # The one place a degraded read becomes visible to whoever called a worktree tool:
        # the phase below was computed from the substituted values, and this says so.
        facts["unknown_contract_cells"] = list(contract.unknown_cells)
    providers = provider_async.provider_setup_status(contract)
    if providers is not None:
        facts["providers"] = providers
    freshness = base_freshness(contract)
    if freshness is not None:
        facts["freshness"] = freshness
    if landing is not None:
        facts["landing"] = landing
    # Merged rather than `.update`d so the checker sees the result as one payload: the
    # guidance keys keep coming last, exactly as they did, and the phase / next-move
    # Literals are now type-checked against the wire vocabulary at this seam.
    return {**facts, **guidance}


def projected_status_payload(
    contract: WorktreeContract, *, landing: list[dict[str, object]] | None
) -> WorktreeStatusPayload:
    """Build status from local facts plus an already-observed landing snapshot."""
    return _status_payload_with_landing(contract, landing)


def status_payload(contract: WorktreeContract) -> WorktreeStatusPayload:
    """Build an interactive status response, including a fresh remote landing observation."""
    return _status_payload_with_landing(contract, landing_refs(contract))
