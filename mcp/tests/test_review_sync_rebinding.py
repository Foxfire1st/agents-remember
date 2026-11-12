"""ICR-R22 measures complete code and memory candidates after a managed sync."""

from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.review_final_output_receipt import comparison_key
from agents_remember.application.review_sync_rebinding import (
    read_review_sync_rebinding,
    rebinding_file_name,
    rebinding_result_block,
)
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.models.knowledge.review_sync_rebinding import (
    LegacyReviewSyncRebinding,
    ReviewSyncRebinding,
    SyncKnowledgeObservation,
)
from test_review_final_output_receipt import commit_file, git, tree_fixture

__all__ = ["tree_fixture"]

pytestmark = pytest.mark.integration
SYNCED = {"operation": "worktree_sync", "ok": True, "state": "synced"}


def test_exact_pair_and_memory_only_movement_are_measured_without_relabeling(tree_fixture):
    contract = tree_fixture.contract
    reviewed = tree_fixture.record()
    current = rebinding_result_block(contract, dict(SYNCED))["review_rebinding"]
    assert current["state"] == "current" and current["covers_resolved_pair"]
    assert current["resolved_candidate_memory_tree_id"] == reviewed.memory_candidate.tree
    (contract.memory_worktree / "notes/after.md").write_text("changed after review\n")
    moved = rebinding_result_block(contract, dict(SYNCED))["review_rebinding"]
    assert moved["state"] == "moved" and not moved["covers_resolved_pair"]
    assert moved["code_match"] == "matches-reviewed-input"
    assert moved["memory_match"] == "differs-from-reviewed-input"
    read = read_review_sync_rebinding(
        contract.task_root, contract.leaf_id, comparison_key(reviewed)
    )
    assert read.state == "recorded"
    assert isinstance(read.rebinding, ReviewSyncRebinding)
    assert read.rebinding.comparison == reviewed
    assert read.rebinding.resolved_candidate_memory_tree_id == tree_fixture.capture_memory()


def test_sync_captures_parked_work_without_staging_the_real_index(tree_fixture):
    contract = tree_fixture.contract
    reviewed = tree_fixture.record()
    commit_file(contract.code_worktree, "reviewed.py", "VALUE = 3\n")
    git(contract.code_worktree, "add", "reviewed.py")
    (contract.code_worktree / "reviewed.py").write_text("VALUE = 4\n")
    (contract.memory_worktree / "notes/staged.md").write_text("staged memory\n")
    git(contract.memory_worktree, "add", "notes/staged.md")
    indexes = [
        Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index"))
        for repo in (contract.code_worktree, contract.memory_worktree)
    ]
    before = [path.read_bytes() for path in indexes]
    block = rebinding_result_block(contract, dict(SYNCED))["review_rebinding"]
    assert block["state"] == "moved"
    assert block["comparison"]["code_candidate"]["tree"] == reviewed.code_candidate.tree
    assert block["resolved_candidate_code_tree_id"] != git(
        contract.code_worktree, "rev-parse", "HEAD^{tree}"
    )
    assert [path.read_bytes() for path in indexes] == before


def test_no_movement_and_unavailable_memory_capture_never_claim_pair_coverage(tree_fixture):
    contract = tree_fixture.contract
    tree_fixture.record()
    for state in (
        "would-sync",
        "already-current",
        "sync-resolution-required",
        "sync-cancelled",
        "memory-sync-choice-required",
    ):
        block = rebinding_result_block(contract, {**SYNCED, "state": state})["review_rebinding"]
        assert not block["covers_resolved_pair"]
    missing = replace(contract, memory_worktree=contract.task_root / "removed-memory")
    block = rebinding_result_block(missing, dict(SYNCED))["review_rebinding"]
    assert block["state"] == "unmeasured" and not block["covers_resolved_pair"]
    assert block["resolved_candidate_memory_tree_id"] is None
    assert block["memory_detail"]
    absent_code = replace(contract, code_worktree=contract.task_root / "removed-code")
    source = rebinding_result_block(absent_code, dict(SYNCED))["review_rebinding"]
    assert source["state"] == "source-unmeasured" and not source["covers_resolved_pair"]


def test_historical_v1_rebinding_is_read_only_and_never_covers_a_tree_pair(tree_fixture):
    contract = tree_fixture.contract
    generation = "123e4567-e89b-12d3-a456-426614174000"
    code = git(contract.code_repo_path, "rev-parse", "HEAD")
    tree = git(contract.code_repo_path, "rev-parse", "HEAD^{tree}")
    legacy = LegacyReviewSyncRebinding(
        recorded_at="historical",
        repository_id=contract.repo_name,
        master=contract.task_name,
        leaf_id=contract.leaf_id,
        task_root=str(contract.task_root),
        contract_path=str(contract.contract_path),
        supersedes_generation_id=generation,
        supersedes_generation_index=1,
        supersedes_binding_digest="a" * 64,
        supersedes_manifest_digest="b" * 64,
        reviewed_baseline_code_tree_id=tree,
        reviewed_candidate_code_tree_id=tree,
        resolved_code_head=code,
        resolved_candidate_code_tree_id=tree,
        code_match="matches-reviewed-input",
        reviewed_knowledge_state="not-selected",
        resolved_knowledge=SyncKnowledgeObservation(
            state="not-recorded", path="historical-location", detail="no publication was recorded"
        ),
        knowledge_match="unmeasured",
        state="current",
        successor_action="historical remedy",
    )
    path = durable_reports_root(contract.task_root) / rebinding_file_name(
        contract.leaf_id, generation
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(legacy.model_dump(mode="json")))
    before = path.read_bytes()
    read = read_review_sync_rebinding(contract.task_root, contract.leaf_id, generation)
    assert read.state == "legacy-limit" and read.sha256
    assert isinstance(read.rebinding, LegacyReviewSyncRebinding)
    assert read.rebinding.resolved_code_head == code
    assert not read.covers_resolved_pair() and not read.rebinding.covers_resolved_pair()
    assert path.read_bytes() == before
