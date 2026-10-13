"""MIK-R52 F03/F04: the landing chain names every foreign commit on every master route.

The step proof is pinned through its real entry point over tiny synthetic Git lines: six
foreign commits are all named (never five), a hand merge with content names its side, and
an admitted sync merge passes. Full-route evidence on the real line stays in the journal.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.series_closeout import (
    _MergeAdmission,
    _require_admitted_step,
    _SpineStep,
)
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)


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
def line(tmp_path: Path) -> Path:
    repo = tmp_path / "line"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "line")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _empty(repo: Path, message: str) -> str:
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _admission(
    *,
    heads: tuple[str, ...] = (),
    leaf_ranges: tuple[tuple[str, str, str], ...] = (),
    positions: tuple[str, ...] = (),
) -> _MergeAdmission:
    return _MergeAdmission(heads=heads, leaf_ranges=leaf_ranges, positions=positions, legacy=False)


STEP = _SpineStep(side="code", step="the test step", task_id="test-task")


def test_six_foreign_commits_are_all_named(line: Path, ports: None) -> None:
    """F03: more than five offenders are every one named, with the repair instruction."""

    base = _git(line, "rev-parse", "HEAD")
    made = [_empty(line, f"foreign {index}") for index in range(6)]
    with pytest.raises(CloseoutQueueError) as refused:
        _require_admitted_step(line, base, made[-1], STEP, _admission())
    assert refused.value.status == "atomic-series-leaf-chain-invalid"
    for commit in made:
        assert commit in refused.value.detail
    assert "worktree_sync" in refused.value.detail


def test_a_hand_merge_with_content_names_its_side(line: Path, ports: None) -> None:
    """F04: a merge smuggling its own commits refuses naming the smuggled history."""

    base = _git(line, "rev-parse", "HEAD")
    _git(line, "checkout", "-q", "-b", "side", base)
    (line / "side.txt").write_text("side\n", encoding="utf-8")
    _git(line, "add", "side.txt")
    _git(line, "commit", "-q", "-m", "side work")
    side = _git(line, "rev-parse", "side")
    _git(line, "checkout", "-q", "line")
    _git(line, "merge", "-q", "--no-ff", "-m", "hand merge", "side")
    tip = _git(line, "rev-parse", "HEAD")
    with pytest.raises(CloseoutQueueError) as refused:
        _require_admitted_step(line, base, tip, STEP, _admission())
    assert side in refused.value.detail


def test_an_admitted_sync_merge_passes(line: Path, ports: None) -> None:
    """F04: a merge of a synced official position is admitted, not refused."""

    base = _git(line, "rev-parse", "HEAD")
    _git(line, "checkout", "-q", "-b", "side", base)
    (line / "side.txt").write_text("side\n", encoding="utf-8")
    _git(line, "add", "side.txt")
    _git(line, "commit", "-q", "-m", "side work")
    side = _git(line, "rev-parse", "side")
    _git(line, "checkout", "-q", "line")
    _git(line, "merge", "-q", "--no-ff", "-m", "synced merge", "side")
    tip = _git(line, "rev-parse", "HEAD")
    _require_admitted_step(line, base, tip, STEP, _admission(heads=(tip,), positions=(side,)))


def test_a_step_inside_a_landed_leaf_range_passes(line: Path, ports: None) -> None:
    """F04: history inside a landed leaf's own base..landing range is admitted."""

    base = _git(line, "rev-parse", "HEAD")
    landed = _empty(line, "leaf landing")
    admission = _admission(leaf_ranges=(("L1", base, landed),), positions=(landed,))
    _require_admitted_step(line, base, landed, STEP, admission)
