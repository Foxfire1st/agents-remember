"""Task-state refusals that answer instead of raising (MIK-R111 rule 7).

A check that fails because of the state of the task -- an earlier step not
completed, a contract of the wrong kind, a named commit not where the tool
needs it -- is answered ``ok: false`` with a ``code``, one sentence, and the
next step where exactly one exists. It is never raised: a raise loses ``ok``,
the code and the next step at the tool boundary. A raise stays only for a
state that no sequence of tool calls produces (caller bugs, I/O failures);
each caller documents those beside its conversions.

The refusal is built before anything is written, from the checks the step
already ran in the order it ran them: the sentence is the check's own
sentence, unchanged.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

from agents_remember.kernel.primitives.gate_vocab import STORED_GATE_WAY_OUT
from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class RefusalNext:
    """The one step that follows a refusal, where exactly one exists.

    A step that is no tool call of this server (an architect-ruled CLI
    conversion) carries ``tool`` None and its call as separate ``argv`` values:
    the summary expresses the governed step and no tool is invented for it. The
    builder carries those values in ``nextArgs`` and the runnable shown line,
    made from the same values with the standard shell quoting, in ``command``.
    Every refusal that names a tool keeps the exact payload shape it has today.
    """

    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    argv: tuple[str, ...] = ()


def state_refusal_result(
    contract: WorktreeContract,
    *,
    state: str,
    code: str,
    error: str,
    next: RefusalNext | None = None,
) -> WorktreeCommandResult:
    """Answer one task-state precondition as a refusal result.

    ``state`` names the state the step is in (it agrees with the refusal:
    an answer that names a blocker carries a blocked state). ``code`` is a
    short ASCII identifier for the precondition. ``error`` is the check's
    own one sentence. ``next`` names the one call that follows, only where
    exactly one exists.
    """

    payload: dict[str, object] = {
        "taskId": contract.task_id,
        "taskName": contract.task_name,
        "lifecycleId": contract.lifecycle_id,
        "contractPath": contract.contract_path.as_posix(),
        "state": state,
        "code": code,
        "error": error,
        "summary": error,
    }
    if next is not None:
        step: dict[str, object] = {"summary": next.summary or error}
        if next.tool is not None:
            step["nextTool"] = next.tool
            if next.args:
                step["nextArgs"] = dict(next.args)
        if next.argv:
            step["nextArgs"] = list(next.argv)
            step["command"] = shlex.join(next.argv)
        payload["nextStep"] = step
    return WorktreeCommandResult(2, payload)


def _slug(reason: str) -> str:
    """A short ASCII code fragment from a producer reason."""
    slug = "-".join(
        part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in reason).split()
    )
    return slug[:40] or "blocked"


def shaped_terminal_blockers(
    blockers: list[dict[str, object]] | tuple[dict[str, object], ...],
) -> list[dict[str, object]]:
    """Give every terminal-preflight blocker its code and one sentence.

    Worktree preflight names ``{worktree, reason}``; branch preflight names
    ``{branch, name, reason}``. The code prefixes the component so a caller
    can tell blockages apart; the sentence names what blocks and what
    resolves it. The producer's original keys ride along unchanged.
    """
    return [_shaped_terminal_blocker(blocker) for blocker in blockers]


def _shaped_terminal_blocker(blocker: dict[str, object]) -> dict[str, object]:
    reason = blocker.get("reason")
    reason_text = reason.strip() if isinstance(reason, str) and reason.strip() else "unready"
    if "worktree" in blocker:
        name = str(blocker.get("worktree"))
        if reason_text == "dirty":
            code = "worktree-dirty"
            sentence = (
                f"The {name} worktree has uncommitted changes; commit, discard, "
                "or rerun with force=true."
            )
        else:
            code = "worktree-unready"
            sentence = f"The {name} worktree is not ready ({reason_text[:200]})."
        return {**blocker, "code": code, "sentence": sentence}
    name = str(blocker.get("name", blocker.get("branch", "branch")))
    if isinstance(blocker.get("unmergedCommits"), int):
        code = "branch-unmerged-commits"
        sentence = (
            f"Branch {name} holds unmerged commits; merge or delete it, or rerun with force=true."
        )
    elif reason_text == "unready":
        code = "branch-unready"
        sentence = f"Branch {name} is not ready."
    else:
        code = f"branch-{_slug(reason_text)}"
        sentence = f"Branch {name} is blocked ({reason_text[:200]})."
    return {**blocker, "code": code, "sentence": sentence}


def provider_setup_blocked(
    contract: WorktreeContract,
    *,
    summary: str,
) -> WorktreeCommandResult:
    """Answer a teardown while provider setup still runs, dry run or real.

    The liveness read is side-effect free, so the dry run evaluates it too:
    a dry run never promises a cleanup the real call would refuse. The
    caller's own sentence is kept byte-identical; the code and the poll
    step are added around it.
    """
    return WorktreeCommandResult(
        2,
        {
            **status_payload(contract),
            "state": "blocked",
            "code": "provider-setup-running",
            "error": summary,
            "summary": summary,
            "nextStep": {
                "summary": "Poll worktree_status until providers are terminal, then retry.",
                "nextTool": "worktree_status",
                "nextArgs": {"contract_path": contract.contract_path.as_posix()},
            },
        },
    )


def closeout_queue_refusal(
    contract: WorktreeContract,
    error: CloseoutQueueError,
    *,
    next_args: dict[str, object],
) -> WorktreeCommandResult:
    """Answer a series authority check as a refusal, preserving its code and sentence.

    ``CloseoutQueueError.status`` is the check's code and ``detail`` its
    sentence; both ride unchanged. The next call exists only where exactly
    one follows: a completed master integrates, an abandoned master is
    abandoned. An incomplete or invalid master names its repair in the
    sentence instead.
    """
    queue_next = {
        "atomic-series-checkpoint-master-complete": ("worktree_integrate", dict(next_args)),
        "atomic-series-closeout-master-abandoned": ("worktree_abandon", dict(next_args)),
    }.get(error.status)
    return state_refusal_result(
        contract,
        state="blocked",
        code=error.status,
        error=error.detail,
        next=(
            RefusalNext(
                tool=queue_next[0],
                args=queue_next[1],
                summary=error.detail,
            )
            if queue_next is not None
            else None
        ),
    )


def terminal_state_refusal(
    contract: WorktreeContract,
    error: RuntimeError,
) -> WorktreeCommandResult | None:
    """Answer a known terminal precondition, or ``None`` to let it raise.

    The mapped messages are reachable task states with the repair in their
    sentence: an uncompleted or unabandoned master, unretired children, a
    missing carryover, a mis-spelled or protected branch. Anything else —
    contract-kind guards the route entry refuses first, capability and permit
    internals, contract-changed races — stays a raise: failing closed on an
    unrecognized check keeps a new check from silently becoming a generic
    refusal with an invented code.
    """
    message = str(error)
    for needle, code, at_start in (
        (
            "worktree_cleanup refused: atomic master task must be Completed",
            "master-not-completed",
            True,
        ),
        ("atomic series terminal mutation requires every child leaf", "children-not-retired", True),
        ("cleanup requires the exact landed memory mapping", "carryover-required", True),
        (
            "worktree_abandon refused: an atomic master must be declared abandoned",
            "abandon-master-not-terminal",
            True,
        ),
        (
            "worktree_abandon refused: this master already landed work",
            "abandon-landed-master",
            True,
        ),
        ("must use exact task-owned spelling", "branch-not-task-owned", False),
        ("is not its task-owned atomic branch", "branch-not-task-owned", False),
        ("checked out, expected", "worktree-checkout-mismatch", False),
        ("does not belong to its recorded repository", "worktree-foreign-repository", False),
        ("protected parent ref cannot be retired", "protected-ref", False),
        ("coordination.worktree_group cell of", "worktree-group-mismatch", False),
    ):
        if message.startswith(needle) if at_start else needle in message:
            return state_refusal_result(contract, state="blocked", code=code, error=message)
    return None


def handover_gate_blocked(
    contract: WorktreeContract,
    *,
    summary: str,
    gate_id: str,
    reason: str,
    handover_gate: dict[str, object] | None = None,
) -> WorktreeCommandResult:
    """Answer an integration the master-handover-approval gate blocks.

    The dry run enforces the same gate the real run enforces: a dry run
    never promises ``would-integrate``/``would-checkpoint`` the real call
    would refuse. The verdict facts (gate id, reason, gate payload) ride
    the answer so the refusal stays actionable. No tool decides a stored
    gate (MIK-R113): the answer names no next tool, and its sentence ends
    with the way out.
    """
    sentence = (
        f"Master-handover-approval gate {gate_id} is undecided ({reason}). {STORED_GATE_WAY_OUT}."
    )
    status = {
        key: value
        for key, value in status_payload(contract).items()
        if key not in ("nextTool", "nextArgs", "nextRequiredArgs")
    }
    payload: dict[str, object] = {
        **status,
        "state": "handover-gate-blocked",
        "code": "handover-gate-blocked",
        "error": summary,
        "summary": summary,
        "gateId": gate_id,
        "reason": reason,
        "blockers": [{"code": "handover-gate-blocked", "sentence": sentence}],
        "nextOperation": "review_handover_gate",
        "nextStep": {"summary": summary, "nextOperation": "review_handover_gate"},
    }
    if handover_gate is not None:
        payload["handover_gate"] = handover_gate
    return WorktreeCommandResult(2, payload)


def _integrate_state_refusal(
    contract: WorktreeContract,
    error: RuntimeError,
    next_args: dict[str, object],
) -> WorktreeCommandResult:
    """Answer a failed integration precondition instead of raising (MIK-R111 rule 7).

    Every check ``validate_integrate_contract`` runs precedes the first write,
    so the refusal is made before anything is written, in the check's order
    and with its sentence. The closeout precondition keeps its own code and
    the one call that follows; every other precondition shares
    ``integration-blocked`` with its own sentence. The next-call arguments
    arrive precomputed so this builder stays free of guidance imports.
    """

    message = str(error)
    if message.startswith("integration requires closeout.status completed"):
        if contract.kind == "series":
            return state_refusal_result(
                contract,
                state="blocked",
                code="closeout-incomplete",
                error=message,
                next=RefusalNext(
                    tool="worktree_checkpoint_landing",
                    args=dict(next_args),
                    summary=(
                        "Land the open master with worktree_checkpoint_landing, "
                        "then integrate again."
                    ),
                ),
            )
        return state_refusal_result(
            contract,
            state="blocked",
            code="closeout-incomplete",
            error=message,
            next=RefusalNext(
                tool="worktree_closeout_preview",
                args=dict(next_args),
                summary="Close out the task, then integrate again.",
            ),
        )
    return state_refusal_result(
        contract,
        state="blocked",
        code="integration-blocked",
        error=message,
    )


def task_stage_refusal(contract: WorktreeContract, error: ValueError) -> WorktreeCommandResult:
    """Answer an invalid task/parent binding before terminal mutation."""
    return state_refusal_result(
        contract,
        state="blocked",
        code="task-stage-invalid",
        error=str(error),
        next=RefusalNext(summary="Repair the named task document or parent row, then retry."),
    )
