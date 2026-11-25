"""MIK-R198 rule 5 on the baseline route: a stale anchor refuses adoption, a current one adopts.

The drift measurement keeps its own module (``test_converted_onboarding_drift``) and the shared
world builders, anchors and fingerprint live in ``converted_onboarding_drift_support``. This module
owns one case: accepting drift never waives a stale reference anchor, and a pair whose anchors hold
is adopted with the code commit it recorded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.memory_tools import (
    MemoryBranches,
    memory_baseline_adopt_tool,
)
from agents_remember.memory import baseline
from agents_remember.worktrees.knowledge_validation import PairedCode
from converted_onboarding_drift_support import (
    REPO_ID,
    _adoptable_converted_world,
    bound_validation_services,
)
from test_worktree_support import git


@pytest.fixture
def bound_validation() -> Any:
    """The validator binding the baseline cases need (see the support module)."""

    with bound_validation_services():
        yield


def test_stale_anchors_refuse_the_baseline_even_when_drift_is_accepted(
    tmp_path: Path, bound_validation: Any
) -> None:
    """Rule 7 with MIK-R198 rule 5: accepting drift never waives a stale reference anchor."""

    config, _code, _memory = _adoptable_converted_world(tmp_path, stale=True)
    refused = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert refused["state"] == "blocked-drift", refused

    accepted = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert accepted["ok"] is False and accepted["state"] == "blocked-invalid", accepted
    assert "R198.1-reference-anchor" in json.dumps(accepted), accepted

    repaired = _adoptable_converted_world(tmp_path / "repaired", card=True)
    adopted = memory_baseline_adopt_tool(
        repaired[0],
        repo_id=REPO_ID,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert adopted["ok"] is True and adopted["state"] == "adopted", adopted
    message = git(repaired[2], "log", "-1", "--format=%B")
    assert f"Code-Commit: {git(repaired[1], 'rev-parse', 'main')}" in message, adopted


def test_baseline_refreshes_only_the_forward_adoption_at_the_adopted_pair(
    tmp_path: Path, bound_validation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, code, memory = _adoptable_converted_world(tmp_path, card=True)
    ignore = memory / ".gitignore"
    original_ignore = ignore.read_bytes() if ignore.exists() else None
    code_commit = git(code, "rev-parse", "main")
    branches = MemoryBranches(source_branch="main", work_branch="main")
    calls: list[PairedCode] = []

    def rendering_problem(memory_root: Path, pair: PairedCode, coordination_root: Path) -> str:
        assert memory_root == memory
        assert coordination_root == config.coordination_root
        assert "/briefs/" in git(memory, "show", "HEAD:.gitignore").splitlines()
        calls.append(pair)
        return "source brief rendering failed"

    monkeypatch.setattr(baseline, "refresh_written_memory_briefs", rendering_problem)
    preview = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=branches, dry_run=True)
    assert preview["state"] == "would-adopt", preview
    assert (ignore.read_bytes() if ignore.exists() else None) == original_ignore
    assert not calls

    adopted = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=branches)
    assert adopted["ok"] and adopted["state"] == "adopted", adopted
    assert adopted["bootstrap"]["briefsProblem"] == "source brief rendering failed"
    assert calls == [PairedCode(code, code_commit)]

    replay = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=branches)
    assert replay["state"] == "already-adopted", replay
    assert calls == [PairedCode(code, code_commit)]
