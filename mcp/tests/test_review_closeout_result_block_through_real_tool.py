"""The closeout result blocks the real ``worktree_closeout_apply`` tool attaches (ICR-R21, ICR-R23).

``final_output_receipt`` (delivered versus reviewed) and ``external_git_movement`` are built by
``review_final_output_receipt`` and ``review_external_git_movement`` and are unit-tested with direct
calls. This case drives the registered tool over a real leaf whose memory is converted text and whose
candidate was reviewed, and reads both blocks off the applied result and back from their files.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application.review_tree_comparison import ReviewTrees, live_review_trees
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.worktrees.modules import closeout_external
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from test_source_lineage import _git as git
from test_transaction_only_worktree_delivery import _apply, _closeout_fixture

pytestmark = pytest.mark.integration


def test_the_real_closeout_apply_attaches_the_delivered_receipt_and_the_boundary_statement(
    tmp_path: Path, worktree_services
) -> None:
    contract, config = _closeout_fixture(tmp_path)
    assert contract.memory_worktree is not None
    # The leaf's memory is converted text and its candidate is reviewed before it is closed out.
    layout = contract.memory_worktree / "knowledge" / "layout.json"
    layout.parent.mkdir(exist_ok=True)
    layout.write_text(
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}), encoding="utf-8"
    )
    # The official memory line carries the attributed commit a review resolves its memory base from.
    assert contract.memory_repo_path is not None
    source = contract.memory_source_branch
    seed = git(
        contract.memory_repo_path,
        "commit-tree",
        f"{source}^{{tree}}",
        "-p",
        source,
        "-m",
        render_memory_content_message("Seed memory", str(contract.code_base_commit)),
    )
    git(contract.memory_repo_path, "update-ref", f"refs/heads/{source}", seed)
    git(contract.memory_worktree, "merge", "--ff-only", seed)
    contract = replace(contract, memory_base_commit=seed)
    write_contract(contract.contract_path, contract)
    capture = capture_future_code_candidate(contract)
    trees = live_review_trees(contract.coordination_root, contract, capture.codeCandidateTree)
    assert isinstance(trees, ReviewTrees), trees

    # The invariant gate has its own cases; here it is satisfied so the closeout reaches its blocks.
    with mock.patch.object(closeout_external, "leaf_gate_refusal", return_value=None):
        applied = _apply(config, contract)

    assert applied["ok"] is True and applied["state"] == "closed", applied
    closed = load_contract(contract.contract_path)
    receipt = applied["final_output_receipt"]
    assert receipt["state"] == "recorded" and receipt["phase"] == "closeout", receipt
    assert receipt["delivered_code_commit"] == closed.code_commit
    assert receipt["comparison"] == trees.record.model_dump(mode="json", by_alias=True)
    assert receipt["code_match"] == "matches-reviewed-input"
    assert receipt["read_back"] == "matched"
    destination = Path(receipt["destination"])
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == receipt["sha256"]
    movement = applied["external_git_movement"]
    # A retained uncommitted candidate records a tree pin, not a work-branch head: the boundary is
    # stated as not measured, with its reason, and the closeout was never gated on it.
    assert movement["binding_state"] == "not-measured", movement
    assert "ordinary-append" in movement["transitions"]
    assert "tree pin" in movement["reason"] and movement["statement"]
