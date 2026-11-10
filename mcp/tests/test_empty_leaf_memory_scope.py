"""A live leaf owns its onboarding path before its first card exists."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from agents_remember.application.memory_scope import resolve_leaf_memory_scope
from agents_remember.errors import MemoryCandidatePairError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.serving.changeset import _leaf_onboarding_root
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.memory_candidate_pair import resolve_memory_candidate_pair
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_worktree_support import open_external_contract_fixture


def _scope(contract: WorktreeContract):
    repo = RepositoryScope(
        repo_id=contract.repo_name,
        path=contract.code_repo_path,
        memory_root=contract.memory_repo_path,
    )
    config = McpRuntimeConfig(
        config_path=contract.coordination_root / "settings.json",
        coordination_root=contract.coordination_root,
        workspace_root=contract.coordination_root.parent,
        transcript_root=contract.coordination_root / "logs",
        repositories={contract.repo_name: repo},
    )
    return resolve_leaf_memory_scope(config, repo, str(contract.contract_path))


def test_a_live_leaf_without_cards_keeps_its_own_pair_scope_and_context(tmp_path: Path) -> None:
    contract = open_external_contract_fixture(tmp_path)
    assert contract.memory_worktree is not None
    onboarding = contract.memory_worktree / "onboarding"
    assert not onboarding.exists()

    pair = resolve_memory_candidate_pair(contract)
    scope = _scope(contract)
    context = contract_context(contract)

    assert pair.onboardingRoot == onboarding.resolve().as_posix()
    assert pair.memoryRoot == contract.memory_worktree.resolve().as_posix()
    assert scope.onboarding_root == onboarding
    assert scope.context.onboarding_root == onboarding
    assert context.onboarding_root == onboarding
    assert context.memory_root == contract.memory_worktree
    assert not onboarding.exists()


@pytest.mark.parametrize("removed", ["worktree", "git-marker"])
def test_a_missing_memory_worktree_still_refuses(tmp_path: Path, removed: str) -> None:
    contract = open_external_contract_fixture(tmp_path)
    assert contract.memory_worktree is not None
    if removed == "worktree":
        shutil.rmtree(contract.memory_worktree)
    else:
        (contract.memory_worktree / ".git").unlink()

    with pytest.raises(ValueError, match="the worktree was removed or never opened"):
        _scope(contract)
    with pytest.raises(MemoryCandidatePairError) as refused:
        resolve_memory_candidate_pair(contract)

    assert refused.value.field == "memoryRoot"
    assert refused.value.status == (
        "memory-candidate-pair-path-unavailable"
        if removed == "worktree"
        else "memory-candidate-pair-repository-mismatch"
    )


def test_an_empty_memory_leaf_still_has_to_belong_to_its_recorded_repository(
    tmp_path: Path,
) -> None:
    contract = open_external_contract_fixture(tmp_path)
    assert contract.memory_worktree is not None
    (contract.memory_worktree / ".git").write_text(
        (contract.code_worktree / ".git").read_text(encoding="utf-8"), encoding="utf-8"
    )

    with pytest.raises(MemoryCandidatePairError) as refused:
        resolve_memory_candidate_pair(contract)

    assert refused.value.field == "memoryRoot"
    assert refused.value.status == "memory-candidate-pair-repository-mismatch"


def test_sidecar_roots_follow_the_selected_mode_without_folder_fallback(tmp_path: Path) -> None:
    contract = open_external_contract_fixture(tmp_path)
    assert contract.memory_worktree is not None
    assert contract.memory_repo_path is not None
    leaf_root = contract.memory_worktree / "onboarding"
    official_root = contract.memory_repo_path / "onboarding"
    assert not leaf_root.exists() and not official_root.exists()

    assert _leaf_onboarding_root(contract, "working") == leaf_root
    assert _leaf_onboarding_root(contract, "committed") == official_root
    assert not leaf_root.exists() and not official_root.exists()
