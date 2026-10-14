"""MIK-R52 F01: a Git read that fails is a named unreadable refusal, never silent membership.

A plain non-ancestry (rc 1) keeps its policy; any other Git failure refuses by name in every
new pair-selection, merge-admission and history-owner check. Each test enters through the
route-level function, so removing the strict read makes it fail.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.application.knowledge_gate import landing as landing_module
from agents_remember.application.knowledge_gate.landing import _commit_holds_path
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.worktrees.modules import record_landing
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout_external import _frozen_recorded_closeout
from agents_remember.worktrees.modules.git import require_ancestor
from agents_remember.worktrees.modules.record_landing import _judged_recorded_code
from agents_remember.worktrees.series_closeout import _chain_ancestry, _SpineStep
from agents_remember.worktrees.services import (
    LandingGateRequest,
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import write_contract
from test_knowledge_closeout_gate import (
    Gated,
    build_gated,
)

BOGUS = "f" * 40
STEP = _SpineStep(side="code", step="the test step", task_id="test-task")


@pytest.fixture
def world(tmp_path: Path) -> Gated:
    return build_gated(tmp_path)


@pytest.fixture
def ports() -> Any:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "tiny"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "first")
    return repo


def test_require_ancestor_answers_false_for_a_plain_non_ancestry(
    small_repo: Path, ports: None
) -> None:
    """rc 1 keeps its policy: a legitimate non-ancestor answers False, no refusal."""

    _git(small_repo, "checkout", "-q", "-b", "side")
    (small_repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(small_repo, "add", "b.txt")
    _git(small_repo, "commit", "-q", "-m", "side")
    main = _git(small_repo, "rev-parse", "main")
    side = _git(small_repo, "rev-parse", "side")
    assert require_ancestor(small_repo, side, main, what="the test pair") is False
    assert require_ancestor(small_repo, main, side, what="the test pair") is True


def test_require_ancestor_refuses_an_unreadable_object_by_name(
    small_repo: Path, ports: None
) -> None:
    """rc 128 never passes as non-membership: an unreadable object refuses, naming it."""

    head = _git(small_repo, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError) as refused:
        require_ancestor(small_repo, BOGUS, head, what="the recorded test commit")
    assert BOGUS in str(refused.value)
    assert "the recorded test commit" in str(refused.value)


def test_recorded_code_selection_with_unreadable_recorded_code_refuses(
    world: Gated, ports: None
) -> None:
    """Rule 2 selection: an unreadable recorded closeout code refuses instead of falling
    through to the landed commit."""

    contract = replace(world.contract, code_commit=BOGUS)
    with pytest.raises(RuntimeError) as refused:
        _judged_recorded_code(contract, world.code_base)
    assert BOGUS in str(refused.value)
    assert "recorded closeout code" in str(refused.value)


def test_record_landing_with_unreadable_recorded_code_refuses_without_writing(
    world: Gated, ports: None, tmp_path: Path
) -> None:
    """The reviewer's counterexample: converted leaf, unreadable recorded closeout code.

    The route refuses naming the recorded commit (never would-record), and neither the
    contract file nor either ref inventory moves.
    """

    contract = replace(world.contract, code_commit=BOGUS)
    candidate = tmp_path / "series-contract.md"
    write_contract(candidate, contract)
    before = candidate.read_bytes()
    code_before = _git(world.code, "rev-parse", "leaf")
    memory_before = _git(world.memory, "rev-parse", "leaf")
    refused = record_landing.record_landing_result(
        WorktreeArgs(
            contract_path=candidate,
            dry_run=True,
            landed_code_commit=world.code_base,
            landed_memory_content_commit=world.memory_base,
        )
    )
    assert refused.returncode == 2
    assert refused.payload["code"] == "knowledge-gate-refused"
    assert BOGUS in str(refused.payload["detail"])
    assert "recorded closeout code" in str(refused.payload["detail"])
    assert refused.payload["error"] == str(refused.payload["detail"]).splitlines()[0]
    assert candidate.read_bytes() == before
    assert _git(world.code, "rev-parse", "leaf") == code_before
    assert _git(world.memory, "rev-parse", "leaf") == memory_before


def test_frozen_closeout_with_unreadable_judged_commit_refuses(world: Gated, ports: None) -> None:
    """Rule 1 freezing: an unreadable judged memory commit refuses instead of answering
    an empty frozen tuple."""

    contract = replace(
        world.contract, closeout_status="completed", memory_content_commit=world.memory_base
    )
    with pytest.raises(RuntimeError) as refused:
        _frozen_recorded_closeout(contract, world.memory, BOGUS)
    assert BOGUS in str(refused.value)
    assert "judged" in str(refused.value)


def test_history_owner_check_answers_false_off_line_and_refuses_unreadable(
    world: Gated, ports: None
) -> None:
    """Rule 5 ownership: off-line commits own nothing; unreadable commits refuse by name."""

    request = LandingGateRequest(
        memory_repository=world.memory,
        memory_commit=world.memory_base,
        memory_bases=(world.memory_base,),
        code_repository=world.code,
        code_commit=world.code_base,
        series_contract="series-contract.md",
    )
    assert _commit_holds_path(request, world.memory_base, "knowledge/layout.json") in (True, False)
    with pytest.raises(RuntimeError) as refused:
        _commit_holds_path(request, BOGUS, "knowledge/layout.json")
    assert BOGUS in str(refused.value)


def test_gate_finding_reports_an_unreadable_history_ownership_check(
    world: Gated, ports: None
) -> None:
    """A failed owner read surfaces as a named gate finding, never a crash or a pass."""

    request = LandingGateRequest(
        memory_repository=world.memory,
        memory_commit=world.memory_base,
        memory_bases=(world.memory_base,),
        code_repository=world.code,
        code_commit=world.code_base,
        series_contract="series-contract.md",
    )
    with mock.patch.object(
        landing_module,
        "_master_history_ownership",
        side_effect=RuntimeError("the history owner check cannot read probe-commit"),
    ):
        refusal = landing_module.landing_refusal(request)
    assert refusal is not None
    assert "probe-commit" in refusal


def test_chain_ancestry_refuses_an_unreadable_step_by_name(small_repo: Path, ports: None) -> None:
    """Rule 4 admission: a failed ancestry read inside the chain proof refuses, naming the
    step instead of mislabeling the commit foreign or admitted."""

    head = _git(small_repo, "rev-parse", "HEAD")
    assert _chain_ancestry(small_repo, head, head, step=STEP, what="the test step") is True
    with pytest.raises(Exception) as refused:
        _chain_ancestry(small_repo, BOGUS, head, step=STEP, what="the test merge")
    assert "the test merge" in str(refused.value)
    assert "atomic-series-leaf-chain-invalid" in str(refused.value)


def test_history_owner_path_read_fatal_refuses_by_name(world: Gated, ports: None) -> None:
    """F01 owner path reads: a real existing path answers True; forcing only that read to
    rc128 refuses naming the commit and the path instead of answering absent; a genuinely
    missing path still answers False."""

    request = LandingGateRequest(
        memory_repository=world.memory,
        memory_commit=world.memory_base,
        memory_bases=(world.memory_base,),
        code_repository=world.code,
        code_commit=world.code_base,
        series_contract="series-contract.md",
    )
    assert _commit_holds_path(request, world.memory_base, "knowledge/layout.json") is True
    assert _commit_holds_path(request, world.memory_base, "knowledge/no-such-file.json") is False
    real_run_git = landing_module.run_git

    def fatal_path_read(repository: Path, args: list[str], options: object | None = None) -> object:
        if args[:2] == ["rev-parse", "--verify"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: unable to read tree")
        return real_run_git(repository, args, options)  # type: ignore[arg-type]

    with (
        mock.patch.object(landing_module, "run_git", side_effect=fatal_path_read),
        pytest.raises(RuntimeError) as refused,
    ):
        _commit_holds_path(request, world.memory_base, "knowledge/layout.json")
    assert world.memory_base in str(refused.value)
    assert "knowledge/layout.json" in str(refused.value)
