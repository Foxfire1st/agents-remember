"""Landing next-operation guidance from the response's exact worktree contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.models.base import NextStep
from agents_remember.worktrees.modules.guidance import lifecycle_guidance
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


def compute_next_step(
    contract: WorktreeContract | None,
    tool_name: str,
    *,
    guidance: dict[str, Any] | None = None,
) -> NextStep | None:
    """Map the resolved contract to its next landing move, preserving stop gates."""
    if contract is None:
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


def next_step_for(contract_path: str | None, tool_name: str) -> NextStep | None:
    """Resolve the response's own contract and retain its landing approval hints."""
    contract = _load_contract(contract_path)
    return compute_next_step(contract, tool_name, guidance=_guidance_for(contract))


def approval_stop_for(contract_path: str | None, tool_name: str) -> NextStep | None:
    """Preserve the two preview stops from this operation's exact argument."""
    if tool_name not in {"worktree_closeout_preview", "worktree_integrate"}:
        return None
    contract = _load_contract(contract_path)
    return _turn_end_after(tool_name, contract) if contract is not None else None


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


def _load_contract(contract_path: str | None) -> WorktreeContract | None:
    """A missing or concurrently rewritten contract yields no derived hint."""
    if not contract_path:
        return None
    path = Path(contract_path)
    if not path.is_file():
        return None
    try:
        return load_contract(path)
    except Exception:
        return None
