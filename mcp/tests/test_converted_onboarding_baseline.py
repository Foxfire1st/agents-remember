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
