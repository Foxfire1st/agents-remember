"""The lifecycle next-step engine (task 27).

One pure function maps the projected lifecycle state to the single next move,
attached to every tool response at the ``mcp.tools.base._tool_payload`` choke
point. It marries the pre-lifecycle worktree hint system into the whole
lifecycle spine. Leaf-28 made NOTIFY-AND-CONTINUE the active turn-end model: at
each former gate moment the hint points the agent at
``lifecycle_turn_end_notification`` (notify the developer and stop, no wait),
and the next AR tool call auto-resumes the lifecycle. The ``lifecycle_gate``
stack is PARKED -- still works if raised, just no longer the hinted path -- and
the ``blocked`` branch's await/resume hint stays intact for it.

Two regimes, settled with the developer on the leaf-26 Lifecycle Flow tab
(``dashboard/src/panels/FlowTab.tsx`` -- its RUNDOWN/LINEAR structures are the
spec):

* FRONT HALF -- non-linear, prose-guided. Until the worktree contract exists
  (``LifecycleState.enclosure`` unset) the per-response hint is a stable pointer
  back to the one-time ``FRONT_HALF_RUNDOWN`` that ``lifecycle_start`` emits,
  naming the next gate to raise (``plan-approval``). Prose, not per-tool hints,
  because the research tools (``read_ar_files``/``grepai``/``cgc``) fire
  unpredictably and ``task-file-exists?`` is a routing decision, not a tool.
  (Developer-chosen S5 resolution: *ride the first gate tool* -- the front half
  is governed by the rundown; the precise per-tool chain begins at
  ``worktree_start``.)

* LINEAR HALF -- from ``worktree_start`` on (contract present). Delegate to the
  proven ``guidance.lifecycle_guidance`` state machine for the operational
  chain, overlaying a turn-end hint at the former gate moments so the agent
  notifies via ``lifecycle_turn_end_notification`` and stops rather than barreling
  past a decision point.

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


# The one-time front-half roadmap emitted by lifecycle_start. Mirrors the
# leaf-26 Lifecycle Flow tab RUNDOWN, with the developer-chosen plan-approval
# hand-off appended (S5: ride the first gate tool).
FRONT_HALF_RUNDOWN: list[str] = [
    "reframe — restate the request as agreed work, then present it for the developer's agreement (the orchestrator lifecycle: l-01-agent-lifecycles roles/orchestrator.md).",
    "research — read_ar_files · grepai · cgc (they fire unpredictably, so this half is prose-guided, "
    "not per-tool).",
    "route the event (the lens tunes it): no task doc → design one ; approved doc + code change "
    "→ build ; no code change → research-only exit ; triage may route, spawn, or escalate.",
    "task file exists? — no → task_doc first (persist the proposal + approval); worktree_start "
    "only AFTER the plan gate: task doc → branch → worktree, in that order.",
    "when the plan is ready, present it and notify via lifecycle_turn_end_notification, then "
    "stop — your next AR tool call resumes automatically; from worktree_start on, every tool "
    "response carries the next step.",
]

# Front-half generic pointer back to the rundown (every non-``decide`` call).
_FRONT_HALF_SUMMARY = (
    "Front half (non-linear): follow the lifecycle_start rundown — reframe → research → "
    "event-routing → task-file-exists? → task_doc. When the plan is ready, notify via "
    "lifecycle_turn_end_notification and stop; the linear per-tool chain begins at worktree_start."
)

# The lifecycle is a loop: a terminal lifecycle_end is the seam back to the next
# one, so the agent is reminded it can re-enter rather than treating end as a wall.
_LOOP_BACK = NextStep(
    summary=(
        "Lifecycle ended — the session is a loop, not a dead end. Start the next piece of "
        "work with lifecycle_start (fresh), or resume an existing task with worktree_attach."
    ),
)

# A raised lifecycle_gate calls ``amb.block`` (state -> "blocked"). Until the
# developer decides, the only correct next move is to wait and then resume — never
# the post-gate operational step, which would jump the open gate. This carries the
# hint chain THROUGH the gate (raise -> blocked/await -> resume -> continue).
_AWAIT_GATE = NextStep(
    summary=(
        "Blocked at a gate — awaiting the developer's decision (dashboard or chat). "
        "Do not proceed past the gate: once they decide, call lifecycle_resume, then continue."
    ),
    nextTool="lifecycle_resume",
)

# The NOTIFY-AND-CONTINUE turn-end hint (leaf-28): once the lifecycle is parked in
# awaiting-developer, the turn is the developer's. No nextTool — the agent stops; the
# next AR tool call auto-resumes the lifecycle to running at the _tool_payload choke point.
_TURN_HANDED_TO_DEVELOPER = NextStep(
    summary=(
        "Turn handed to the developer — stop here; your next AR tool call resumes "
        "automatically. No gate, no wait."
    ),
)


def compute_next_step(
    state: LifecycleState | None,
    contract: WorktreeContract | None,
    tool_name: str,
    *,
    guidance: dict[str, Any] | None = None,
) -> NextStep | None:
    """Pure: map the resolved lifecycle state to the single next move (or ``None``).

    ``contract is None`` is the front half (no worktree yet); a present contract
    is the linear half, where ``guidance`` is ``lifecycle_guidance(contract)``
    precomputed at the edge. Returns ``None`` outside any (non-terminal)
    lifecycle so a lifecycle-less response stays unchanged.
    """
    if state is None or state.is_terminal:
        # Terminal / no active lifecycle. After an explicit end, hint the loop back
        # so the agent re-enters (start fresh or resume) instead of dead-ending.
        if tool_name == "lifecycle_end":
            return _LOOP_BACK
        return None

    # Blocked at an open gate (a raised lifecycle_gate set state="blocked"): the
    # only correct next move is to await the developer's decision and resume — never
    # the post-gate operational step below, which would jump the gate. (The parked
    # gate path still works if raised; leaf-28 just no longer hints toward it.)
    if state.state == "blocked":
        return _AWAIT_GATE

    # NOTIFY-AND-CONTINUE turn end (leaf-28): the lifecycle is parked in
    # awaiting-developer (the lifecycle_turn_end_notification response itself, before
    # the next call auto-resumes). Hint the stop, not another call — nextTool=None so
    # the agent does not push past its own turn end.
    if state.state == "awaiting-developer":
        return _TURN_HANDED_TO_DEVELOPER

    # FRONT HALF — non-linear, prose-guided (no worktree contract yet).
    if contract is None:
        if state.phase == "decide":
            return NextStep(
                summary=(
                    "Decide: verify `worktree_start --dry-run` (self-fix first), then notify via "
                    "lifecycle_turn_end_notification and stop — the developer opens the worktree on "
                    "their turn; your next AR tool call resumes automatically."
                ),
                nextTool="lifecycle_turn_end_notification",
                nextArgs={"summary": "Ready to open the worktree — your call."},
            )
        return NextStep(
            summary=_FRONT_HALF_SUMMARY,
            nextTool="lifecycle_turn_end_notification",
            nextArgs={"summary": "Plan ready for your review."},
        )

    # LINEAR HALF — overlay the existing-gate raise at the gate moments...
    gate = _gate_after(tool_name, contract)
    if gate is not None:
        return gate
    # ...otherwise delegate to the proven worktree guidance state machine.
    if guidance is not None:
        return _from_guidance(guidance)
    return None


def _gate_after(tool_name: str, contract: WorktreeContract) -> NextStep | None:
    """The turn-end overlay: after a dry-run/preview, hint NOTIFY-AND-CONTINUE.

    Keyed on the just-completed tool plus the contract sub-state. Closeout uses
    distinct ``preview``/``apply`` tools; ``worktree_integrate`` and
    ``lifecycle_finalize_task`` reuse one tool with a ``dry_run`` arg, so the
    not-yet-applied contract state distinguishes the dry-run from the apply
    without inspecting args. Leaf-28: at each former gate moment the agent now
    notifies via ``lifecycle_turn_end_notification`` and stops (no gate, no wait);
    the next AR tool call auto-resumes the lifecycle. The parked ``lifecycle_gate``
    still works if raised — it is simply no longer the hinted path.
    """
    if tool_name == "worktree_closeout_preview" and not contract.approved_for_commit:
        return NextStep(
            summary=(
                "Closeout preview is ready — report it, then notify via "
                "lifecycle_turn_end_notification and stop for the developer's commit approval "
                "(an agent self-approval never satisfies it); your next AR tool call resumes "
                "automatically."
            ),
            nextTool="lifecycle_turn_end_notification",
            nextArgs={"summary": "Closeout preview ready for your commit approval."},
        )
    if (
        tool_name == "worktree_integrate"
        and contract.closeout_status == "completed"
        and contract.integration_status != "completed"
    ):
        return NextStep(
            summary=(
                "Integration dry-run verified — notify via lifecycle_turn_end_notification and "
                "stop before applying the integration; your next AR tool call resumes automatically."
            ),
            nextTool="lifecycle_turn_end_notification",
            nextArgs={"summary": "Integration dry-run verified — ready to integrate."},
        )
    if (
        tool_name == "lifecycle_finalize_task"
        and contract.integration_status == "completed"
        and contract.cleanup != "completed"
    ):
        return NextStep(
            summary=(
                "Finalize dry-run verified — notify via lifecycle_turn_end_notification and stop "
                "before reclaiming the worktrees; your next AR tool call resumes automatically."
            ),
            nextTool="lifecycle_turn_end_notification",
            nextArgs={"summary": "Finalize dry-run verified — ready to reclaim the worktrees."},
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


def next_step_for(amb: AmbientLifecycle, tool_name: str) -> dict[str, Any] | None:
    """Edge: resolve the active state + contract + guidance, then compute the hint.

    Exception-contained: the ``_tool_payload`` emission path must never raise into
    a tool call, so any failure yields no hint rather than breaking the response.
    A missing or torn contract degrades to ``contract=None`` (front-half fallback);
    a guidance failure degrades to ``guidance=None`` (the gate overlay still fires).
    The state is threaded through even when ``None`` so a terminal ``lifecycle_end``
    can still emit the loop-back hint.
    """
    try:
        state = amb.current
        contract = _load_contract(state) if state is not None else None
        guidance = _guidance_for(contract)
        step = compute_next_step(state, contract, tool_name, guidance=guidance)
        return step.model_dump(mode="json", exclude_none=True) if step is not None else None
    except Exception:
        return None


def _guidance_for(contract: WorktreeContract | None) -> dict[str, Any] | None:
    """Run the worktree guidance state machine, degrading to ``None`` on failure so
    a guidance I/O error never costs the (contract-independent) gate-overlay hint."""
    if contract is None:
        return None
    try:
        return lifecycle_guidance(contract)
    except Exception:
        return None


def _load_contract(state: LifecycleState) -> WorktreeContract | None:
    """Load the worktree contract from the promoted lifecycle's enclosure path.

    Look before leaping: a promoted lifecycle whose contract file is not yet on
    disk (the ``worktree_start --dry-run`` window) is an *expected* state, not an
    error — return ``None`` so the engine degrades to the front-half hint instead
    of swallowing it. The narrow guard then covers only the genuinely unexpected:
    a contract that exists but is torn/unparseable (e.g. a racing closeout rewrite).
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
