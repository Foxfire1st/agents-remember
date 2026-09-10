"""Named pins for the closeout rules that survive the door/operation/journal cut.

After the plane is gone these rules ARE closeout. Each test here names the rule
it pins:

* R1 the trifecta -- code, memory and ledger, all three, always
* R2 a valid nonblank shaped commit message on each enabled leg
* R3 correct ancestry before closeout passes

R2 is enforced as a side effect of ``normalize_closeout_input`` and
``resolve_closeout_plan`` rather than by a standalone guard, so these tests pin
that side effect at the boundary where it happens.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.models.closeout.input import (
    CloseoutCorrectedCall,
    CloseoutLegPlan,
    CloseoutMessageInput,
    ResolvedCloseoutPlan,
)
from agents_remember.worktrees.closeout_input import (
    CloseoutInputError,
    normalize_closeout_input,
)
from agents_remember.worktrees.integration import integration_ref_transaction
from agents_remember.worktrees.modules.closeout import _validate_closeout_source_heads
from agents_remember.worktrees.modules.integrate import _integration_replay_requirements
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_worktree_support import git, init_repo

CORRECTED_CALL = CloseoutCorrectedCall(tool="worktree_closeout_apply", arguments={})


def _contract(*, kind: str = "leaf", memory_mode: str = "external") -> WorktreeContract:
    return WorktreeContract(
        task_id="task-1",
        task_name="task-one",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode=memory_mode,  # type: ignore[arg-type]
        coordination_root=Path("/coordination"),
        task_root=Path("/coordination/tasks/repo-a/leaf"),
        contract_path=Path("/coordination/tasks/repo-a/leaf/enclosures/a/series-contract.md"),
        task_artifact=Path("/coordination/tasks/repo-a/leaf/task.md"),
        worktree_group=Path("/coordination/worktrees/repo-a/leaf"),
        code_repo_path=Path("/repos/repo-a"),
        code_source_branch="main",
        code_work_branch="ar/task-one",
        code_base_commit="a" * 40,
        code_worktree=Path("/coordination/worktrees/repo-a/leaf/code"),
        kind=kind,
    )


def _enabled_plan(*, kind: str = "leaf", memory_mode: str = "external") -> ResolvedCloseoutPlan:
    enabled = CloseoutLegPlan(state="enabled", reason="fixture pin")
    return ResolvedCloseoutPlan(
        route="worktree",
        contractKind=kind,  # type: ignore[arg-type]
        memoryMode=memory_mode,  # type: ignore[arg-type]
        code=enabled,
        memory=enabled,
        ledger=enabled,
    )


@pytest.mark.parametrize("leg", ["code", "memory", "ledger"])
@pytest.mark.parametrize("supplied", [None, "", "   ", "\n\t "])
def test_r2_each_enabled_leg_requires_a_nonblank_commit_message(leg, supplied) -> None:
    """R2: a blank or absent message refuses on its leg, and every blank leg is reported.

    Whitespace-only counts as absent: the value is stripped before the check.
    The refusal reports *all* enabled legs still lacking intent, so supplying one
    leg cannot mask another.
    """

    contract = _contract()
    messages = CloseoutMessageInput(**{leg: supplied})
    with pytest.raises(CloseoutInputError) as raised:
        normalize_closeout_input(
            contract,
            messages,
            route="worktree",
            corrected_call=CORRECTED_CALL,
            resolved_plan=_enabled_plan(),
        )
    reported = [(field.leg, field.code) for field in raised.value.invalid_fields]
    assert (leg, f"enabled-{leg}-message-required") in reported
    assert reported == [
        (name, f"enabled-{name}-message-required") for name in ("code", "memory", "ledger")
    ]


def test_r2_all_three_blank_legs_refuse_together() -> None:
    """R2: every enabled leg is checked, not just the first."""

    with pytest.raises(CloseoutInputError) as raised:
        normalize_closeout_input(
            _contract(),
            CloseoutMessageInput(),
            route="worktree",
            corrected_call=CORRECTED_CALL,
            resolved_plan=_enabled_plan(),
        )
    assert sorted(field.leg for field in raised.value.invalid_fields) == [
        "code",
        "ledger",
        "memory",
    ]


def test_r2_supplied_messages_are_shape_normalized_and_carried_on_every_leg() -> None:
    """R2: a nonblank message is accepted and stripped for each leg."""

    effective = normalize_closeout_input(
        _contract(),
        CloseoutMessageInput(
            code="  commit code  ", memory=" commit memory ", ledger="commit ledger"
        ),
        route="worktree",
        corrected_call=CORRECTED_CALL,
        resolved_plan=_enabled_plan(),
    )
    # Read the message through the canonical accessor: it narrows the
    # enabled/not-applicable leg union and raises if a leg is not applicable, which
    # is exactly the invariant this test pins. Reading `.message` off the union
    # directly is what Pyright flagged, because NotApplicableCloseoutLeg has none.
    assert (
        effective.message_for("code"),
        effective.message_for("memory"),
        effective.message_for("ledger"),
    ) == (
        "commit code",
        "commit memory",
        "commit ledger",
    )
    assert (effective.code.state, effective.memory.state, effective.ledger.state) == (
        "enabled",
        "enabled",
        "enabled",
    )


def test_r1_the_trifecta_is_required_as_a_whole_never_partially() -> None:
    """R1: closeout never proceeds with a subset of code, memory and ledger.

    The refusal is all-or-nothing: supplying two of the three enabled legs still
    refuses, and the refusal names exactly the missing leg. There is no code path
    in which an enabled leg is silently dropped.
    """

    with pytest.raises(CloseoutInputError) as raised:
        normalize_closeout_input(
            _contract(),
            CloseoutMessageInput(code="code", memory="memory"),
            route="worktree",
            corrected_call=CORRECTED_CALL,
            resolved_plan=_enabled_plan(),
        )
    assert [(field.leg, field.code) for field in raised.value.invalid_fields] == [
        ("ledger", "enabled-ledger-message-required")
    ]

    effective = normalize_closeout_input(
        _contract(),
        CloseoutMessageInput(code="code", memory="memory", ledger="ledger"),
        route="worktree",
        corrected_call=CORRECTED_CALL,
        resolved_plan=_enabled_plan(),
    )
    assert {leg: getattr(effective, leg).state for leg in ("code", "memory", "ledger")} == {
        "code": "enabled",
        "memory": "enabled",
        "ledger": "enabled",
    }


# --------------------------------------------------------------------------
# R3 and R4 are git facts. Both are pinned against a real repository: R3 on the
# source heads closeout validates, R4 on the is_ancestor replay requirement.
# --------------------------------------------------------------------------


def _git_repo(tmp_path: Path):
    repo = tmp_path / "repo-a"
    init_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "ar/task-one")
    (repo / "file.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    return git, repo, base, candidate


def _git_contract(repo: Path, **overrides: object) -> WorktreeContract:
    values: dict[str, object] = {
        "code_repo_path": repo,
        "code_source_branch": "main",
        "code_work_branch": "ar/task-one",
        "memory_mode": "internal",
    }
    values.update(overrides)
    return replace(_contract(), **values)  # type: ignore[arg-type]


def test_r3_closeout_ancestry_passes_when_the_source_is_still_at_the_recorded_base(
    tmp_path: Path,
) -> None:
    """R3: the commit graph is what closeout claims -- source at base, candidate ahead."""

    _git, repo, base, candidate = _git_repo(tmp_path)
    contract = _git_contract(repo, code_base_commit=base, code_commit=candidate)

    _validate_closeout_source_heads(contract)  # does not raise


def test_r3_closeout_refuses_when_the_source_branch_moved(tmp_path: Path) -> None:
    """R3: a source branch that is not the recorded base refuses closeout."""

    git, repo, base, candidate = _git_repo(tmp_path)
    (repo / "elsewhere.txt").write_text("moved\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "source moved")
    contract = _git_contract(repo, code_base_commit=base, code_commit=candidate)

    with pytest.raises(RuntimeError, match="code source branch moved"):
        _validate_closeout_source_heads(contract)


def test_r4_integration_replay_requirement_is_git_ancestry_not_a_record(tmp_path: Path) -> None:
    """R4: the moved-parent refusal is an is_ancestor read of the live source tip.

    It does not consult a door, a claim or an operation record -- only the leaf's
    candidate commit and the branch it is landing onto.
    """

    git, repo, base, candidate = _git_repo(tmp_path)
    unmoved = _integration_replay_requirements(
        _git_contract(repo, code_base_commit=base, code_commit=candidate)
    )
    assert unmoved.code_replay_required is False

    (repo / "moved.txt").write_text("moved\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "parent moved")
    moved = _integration_replay_requirements(
        _git_contract(repo, code_base_commit=base, code_commit=candidate)
    )
    assert moved.code_replay_required is True


def test_r4_no_crash_recovery_path_exists_after_a_torn_ref_move() -> None:
    """R4: after a crash between the two ref moves there is no recovery entry point.

    Mid-crash integration-ref recovery was removed as a capability: its only
    input was the journaled expected pre-move ref value, and with the journal gone
    that value has no durable source. The operator-visible behaviour is defined
    instead of undefined -- re-run ``worktree_integrate``, which reads the live
    refs through the replay requirement and either proceeds or returns
    ``blocked-non-ff`` (pinned by
    ``test_r4_integration_replay_requirement_is_git_ancestry_not_a_record`` and by
    ``test_public_integration_ref_movement_refuses_before_pair_merge``).
    """

    assert not hasattr(integration_ref_transaction, "recover_integration_ref")
    assert not hasattr(integration_ref_transaction, "refresh_recovered_checkout")
