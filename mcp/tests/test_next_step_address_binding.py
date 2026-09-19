"""A next-step hint must describe the task the response addressed, never a process cursor.

D-13 recorded the failure this lane pins: ``worktree_status`` answered two *different* addressed
contracts with the same ``nextStep.nextArgs.enclosure_path`` block -- the enclosure of an unrelated
master, taken from the globally most recently published record under
``controlplane/lifecycle-enclosures/``. ``nextStep`` is the field that says *call this next*, so a
caller that trusted it was walked into another session's enclosure.

Two independent fixes are in the tree, and this module pins both because neither had a case:

* ``application.next_step.compute_next_step`` is a pure function of the resolved contract, so the
  hint is derived from the task the caller addressed and not from any global cursor;
* ``application.tool_response.bound_next_step`` **omits** a hint whose ``nextArgs`` path fields
  contradict the response's own ``contractPath``/``enclosurePath``, so a hint that names another
  contract cannot reach a caller of an address-carrying envelope at all.

The reachable surface is stated rather than assumed: a carrier that declares no address cannot be
checked, and ``TaskDocResponse`` is the carrier that reaches this path. What the binder does with
such a carrier is **withhold** the hint -- the rule ``260918-TSIP-L6`` landed as the `T54` repair
(``bound_next_step``: *"a response that declares no contract path of its own cannot be validated,
and its guidance is withheld rather than emitted unchecked"*), and this module's fourth case pins
that, not the pre-repair pass-through it asserted before the two lines merged.

**The correction, recorded because it is this leaf's own class.** This module was written on the
``260915_knowledge-substrate`` line against the *older* binder, which returned the step unchanged
when the response declared no address (``if not response_paths: return step``). ``260918-TSIP-L6``
closed exactly that hole on the TSIP line, in a commit that landed after this module's last edit
(``562cef4c`` 17:51 -> ``7879f5b2`` 20:18), and Git merged the two textually without a conflict
marker. So the merged base held one module asserting the pass-through and another
(``test_response_address_binding.py``, the repair's own case) asserting the withholding. The
product is right and the older case was stale: a hint derived from the process-global ambient
lifecycle cannot be vouched for by a response that does not state its own address, which is the whole
of `T54`.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.application.next_step import compute_next_step
from agents_remember.application.tool_response import bound_next_step
from agents_remember.models.base import NextStep
from agents_remember.models.task_doc import TaskDocResponse
from agents_remember.models.worktree import WorktreeStatusResponse
from agents_remember.observer.lifecycle_state import LifecycleState
from agents_remember.worktrees.modules.guidance import lifecycle_guidance
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _live_state() -> LifecycleState:
    """One running lifecycle: enough for the linear half to be the branch under test."""

    return LifecycleState(
        id="01J000000000000000000000AB",
        state="running",
        phase="build",
        fleeting=False,
        started_at="2026-09-18T00:00:00+00:00",
    )


def _contract(
    root: Path, *, leaf_id: str, contract_name: str, **overrides: object
) -> WorktreeContract:
    """One addressed contract, with the closeout state its hint is derived from."""

    worktree_group = root / "worktrees" / "repo" / leaf_id
    contract = WorktreeContract(
        task_id=leaf_id,
        task_name=leaf_id,
        repo_name="repo",
        workflow_kind="light-task",
        memory_mode="disabled",
        coordination_root=root,
        task_root=root / "tasks",
        contract_path=root / "enclosures" / leaf_id / contract_name,
        task_artifact=root / "tasks" / f"{leaf_id}.md",
        worktree_group=worktree_group,
        code_repo_path=root / "repo",
        code_source_branch="main",
        code_work_branch=f"ar/{leaf_id}",
        code_base_commit="abc1234",
        code_worktree=worktree_group / leaf_id,
        leaf_id=leaf_id,
        **overrides,  # type: ignore[arg-type]
    )
    return contract


def _status_response(contract: WorktreeContract, step: NextStep | None) -> WorktreeStatusResponse:
    """An address-carrying envelope of the carrier D-13 was measured on."""

    return WorktreeStatusResponse(
        ok=True,
        repoId=contract.repo_name,
        state="ok",
        contractPath=contract.contract_path.as_posix(),
        enclosurePath=contract.contract_path.as_posix(),
        nextStep=step,
    )


def test_a_hint_naming_another_contract_is_omitted_not_forwarded(tmp_path: Path) -> None:
    """The binder's whole purpose: a contradicting address omits the hint.

    The observed value is another contract's path, which is exactly the D-13 shape. Forwarding it
    would tell the caller to call a tool against a task this response was not about; keeping it
    would be indistinguishable, at the wire, from the correct hint.
    """

    addressed = _contract(tmp_path / "a", leaf_id="leaf-a", contract_name="a.md")
    foreign = NextStep(
        summary="Continue the other task.",
        nextTool="worktree_status",
        nextArgs={"contract_path": "/tmp/coordination/enclosures/leaf-b/series-contract.md"},
    )
    response = _status_response(addressed, foreign)

    assert bound_next_step(response, foreign) is None


def test_a_hint_naming_the_addressed_contract_is_kept_unchanged(tmp_path: Path) -> None:
    """The omission is a contradiction check, not a blanket suppression of guidance."""

    addressed = _contract(tmp_path / "a", leaf_id="leaf-a", contract_name="a.md")
    own = NextStep(
        summary="Continue this task.",
        nextTool="worktree_status",
        nextArgs={"contract_path": addressed.contract_path.as_posix()},
    )
    response = _status_response(addressed, own)

    assert bound_next_step(response, own) is own


def test_two_addressed_contracts_in_sequence_yield_two_different_hints(tmp_path: Path) -> None:
    """The plan's case, driven as a sequence rather than as one asserted value.

    L14's original measurement asserted a single wrong string, which is why a re-measurement the
    same day could not reproduce it: the defect was state-dependent, left behind by another
    session's attach. A sequence of two addressed contracts is what makes the property testable --
    each hint must follow the contract that was passed, and neither response may contain the other
    contract's path anywhere.
    """

    first = _contract(tmp_path / "a", leaf_id="leaf-a", contract_name="a.md")
    second = _contract(
        tmp_path / "b",
        leaf_id="leaf-b",
        contract_name="b.md",
        closeout_status="completed",
    )
    state = _live_state()

    # The edge widens the TypedDict before it calls in (``next_step._guidance_for``): the hint
    # layer reads the guidance defensively by key, so ``compute_next_step`` declares a plain dict
    # and this case calls it the way the production path does.
    first_guidance = dict(lifecycle_guidance(first))
    second_guidance = dict(lifecycle_guidance(second))
    first_step = compute_next_step(state, first, "worktree_status", guidance=first_guidance)
    second_step = compute_next_step(state, second, "worktree_status", guidance=second_guidance)

    assert first_step is not None and second_step is not None
    assert first_step.summary != second_step.summary
    first_args = dict(first_step.nextArgs or {})
    second_args = dict(second_step.nextArgs or {})
    assert first_args["contract_path"] == first.contract_path.as_posix()
    assert second_args["contract_path"] == second.contract_path.as_posix()

    first_response = _status_response(
        first, bound_next_step(_status_response(first, first_step), first_step)
    )
    second_response = _status_response(
        second, bound_next_step(_status_response(second, second_step), second_step)
    )
    first_body = first_response.model_dump_json()
    second_body = second_response.model_dump_json()
    assert second.contract_path.as_posix() not in first_body
    assert first.contract_path.as_posix() not in second_body


def test_an_envelope_with_no_address_has_its_hint_withheld() -> None:
    """The reachable surface, stated instead of assumed: an unaddressable carrier cannot be bound.

    ``bound_next_step`` compares the hint's path arguments against the response's own address, so a
    carrier that declares neither ``contractPath`` nor ``enclosurePath`` -- ``TaskDocResponse`` is
    the one that reaches this path -- has nothing to compare against. The rule is to **withhold**
    the hint: guidance the response cannot vouch for is not forwarded unchecked, because the hint's
    source is the *process-global* ambient lifecycle and the response has just told us it is about
    a task it cannot name (`T54`, repaired by ``260918-TSIP-L6``; the same rule has its own case in
    ``test_response_address_binding.py``).

    This case pins the carrier boundary as well as the rule: the two ``model_fields`` assertions
    below are what make a future address field on ``TaskDocResponse`` a deliberate change with a
    red case rather than a silent one. **This case asserted the opposite until
    ``260918-TSIP-L10``** -- see the module docstring: it was written against the pre-repair binder
    and the two lines merged without a conflict marker, so the merged base held both rules at once.
    """

    foreign = NextStep(
        summary="Continue the session's own task.",
        nextTool="worktree_status",
        nextArgs={"contract_path": "/tmp/coordination/enclosures/leaf-b/series-contract.md"},
    )
    response = TaskDocResponse(
        ok=True,
        operation="task_doc.read_steps",
        taskId="T",
        slug="leaf",
        kind="subTask",
        status="inProgress",
        docPath="/tmp/tasks/repo/leaf/leaf.json",
        renderedPath="/tmp/tasks/repo/leaf/leaf.md",
        nextStep=foreign,
    )

    assert "contractPath" not in TaskDocResponse.model_fields
    assert "enclosurePath" not in TaskDocResponse.model_fields
    assert bound_next_step(response, foreign) is None
