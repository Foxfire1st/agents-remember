"""Application service for lifecycle next-step decisions (task 27).

One pure function maps the lifecycle a process adopted to the single next move,
attached to every tool response at the ``mcp.tools.base._tool_payload`` choke
point. A lifecycle exists from ``worktree_start`` or ``worktree_attach`` on, with
its worktree contract: the hint delegates to the ``guidance.lifecycle_guidance``
state machine for the operational chain and overlays a turn-end hint at the
decision points, so the agent stops (presents the packet in prose and ends the
turn) rather than barreling past one.

The engine stays pure: ``compute_next_step`` receives already-resolved inputs
(state, the loaded contract or ``None``, the just-completed tool name, and the
precomputed guidance dict). All I/O -- loading the contract and running guidance
-- lives at the edge in ``next_step_for``, which is exception-contained because
the ``_tool_payload`` emission path must never raise into a tool call.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.models.base import NextStep
from agents_remember.worktrees.modules.guidance import lifecycle_guidance
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

if TYPE_CHECKING:
    from agents_remember.observer.ambient import AmbientLifecycle
    from agents_remember.observer.lifecycle_state import LifecycleState


def compute_next_step(
    state: LifecycleState | None,
    contract: WorktreeContract | None,
    tool_name: str,
    *,
    guidance: dict[str, Any] | None = None,
) -> NextStep | None:
    """Pure: map the resolved lifecycle state to the single next move (or ``None``).

    ``guidance`` is ``lifecycle_guidance(contract)`` precomputed at the edge. Returns
    ``None`` outside any (non-terminal) lifecycle and for a lifecycle whose contract
    could not be loaded, so such a response stays unchanged.
    """
    if state is None or state.is_terminal or contract is None:
        return None
    return _linear_half_step(tool_name, contract, guidance)


def _linear_half_step(
    tool_name: str, contract: WorktreeContract, guidance: dict[str, Any] | None
) -> NextStep | None:
    """The turn-end overlay, else the worktree guidance state machine."""
    turn_end = _turn_end_after(tool_name, contract)
    if turn_end is not None:
        return turn_end
    if guidance is not None:
        return _from_guidance(guidance)
    return None


def _turn_end_after(tool_name: str, contract: WorktreeContract) -> NextStep | None:
    """The turn-end overlay: after a dry-run/preview, hint the stop.

    Keyed on the just-completed tool plus the contract sub-state. Closeout uses
    distinct ``preview``/``apply`` tools; ``worktree_integrate`` and
    ``lifecycle_finalize_task`` reuse one tool with a ``dry_run`` arg, so the
    not-yet-applied contract state distinguishes the dry-run from the apply
    without inspecting args. At each of these decision points the agent stops;
    the next turn resumes.
    """
    if tool_name == "worktree_closeout_preview" and not contract.approved_for_commit:
        return NextStep(
            summary=(
                "Closeout preview is ready — report it, then stop for the developer's "
                "commit approval (an agent self-approval never satisfies it)."
            ),
        )
    if (
        tool_name == "worktree_integrate"
        and contract.closeout_status == "completed"
        and contract.integration_status != "completed"
    ):
        return NextStep(
            summary=(
                "Integration dry-run verified — stop before applying the integration; "
                "present the plan for approval."
            ),
        )
    if (
        tool_name == "lifecycle_finalize_task"
        and contract.integration_status == "completed"
        and contract.cleanup != "completed"
    ):
        return NextStep(
            summary=(
                "Finalize dry-run verified — stop before reclaiming the worktrees; "
                "present the plan for approval."
            ),
        )
    return None


def _from_guidance(guidance: dict[str, Any]) -> NextStep:
    """Adapt a ``lifecycle_guidance`` dict into the shared ``NextStep`` shape."""
    next_args = guidance.get("nextArgs")
    required = guidance.get("nextRequiredArgs")
    return NextStep(
        summary=str(guidance.get("summary", "")),
        nextOperation=_opt_str(guidance.get("nextOperation")),
        nextTool=_opt_str(guidance.get("nextTool")),
        nextArgs=next_args if isinstance(next_args, dict) else None,
        nextRequiredArgs=list(required) if isinstance(required, list) else None,
    )


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def next_step_for(amb: AmbientLifecycle, tool_name: str) -> NextStep | None:
    """Edge: resolve the active state + contract + guidance, then compute the hint.

    Returns the MODEL, not a dump of it. The hint is a declared field of the response
    envelope (``models.base.ResponseModel.nextStep``), so serializing it is the choke
    point's single ``model_dump`` -- this edge used to dump it here, which is what made
    the hint a key written into an already-dumped, already-token-counted dict.

    Exception-contained: the ``_tool_payload`` emission path must never raise into
    a tool call, so any failure yields no hint rather than breaking the response.
    A missing or torn contract degrades to ``contract=None`` (no hint); a guidance
    failure degrades to ``guidance=None`` (the turn-end overlay still fires).
    """
    try:
        state = amb.current
        contract = _load_contract(state) if state is not None else None
        guidance = _guidance_for(contract)
        return compute_next_step(state, contract, tool_name, guidance=guidance)
    except Exception:
        return None


def _guidance_for(contract: WorktreeContract | None) -> dict[str, Any] | None:
    """Run the worktree guidance state machine, degrading to ``None`` on failure so
    a guidance I/O error never costs the (guidance-independent) turn-end hint."""
    if contract is None:
        return None
    try:
        # Widened deliberately: this hint layer reads the guidance defensively by key and
        # never re-emits its vocabulary, so it takes the payload rather than the contract.
        return dict(lifecycle_guidance(contract))
    except Exception:
        return None


def _load_contract(state: LifecycleState) -> WorktreeContract | None:
    """Load the worktree contract from the adopted lifecycle's enclosure path.

    Look before leaping: a contract file that is not on disk yields ``None`` (no
    hint) without raising. The narrow guard then covers only the genuinely
    unexpected: a contract that exists but is torn/unparseable (e.g. a racing
    closeout rewrite).
    """
    if not state.enclosure:
        return None
    path = Path(state.enclosure)
    if not path.is_file():
        return None
    try:
        return load_contract(path)
    except Exception:
        return None
