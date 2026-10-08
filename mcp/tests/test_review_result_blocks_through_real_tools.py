"""The review result blocks the real worktree tools attach, on a converted scratch leaf.

``rebinding_result_block`` and the closeout and integration receipts are unit-tested with direct
calls in ``test_review_sync_rebinding.py`` and ``test_review_final_output_receipt.py``. What those
cannot show is that the registered tools still attach the blocks to their own results. This module
runs ``worktree_sync_tool`` over a real converted leaf (a code and a memory worktree whose memory
holds converted text knowledge) and asserts the ``review_rebinding`` block the sync result carries.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.review_final_output_receipt import comparison_key
from agents_remember.application.review_sync_rebinding import read_review_sync_rebinding
from agents_remember.application.review_tree_comparison import ReviewTrees, live_review_trees
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    contract_publication_text,
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.worktree_contract import load_contract
from checkpoint_landing_test_support import close_out_leaf, commit_memory_content
from test_closeout_queue import QueueFixture
from test_review_final_output_receipt import commit_file, git
from test_worktree_sync_knowledge_merge import Lines, _converted_lines

pytestmark = pytest.mark.integration


def _config(lines: Lines):
    root = lines.fixture.root
    path = root / "mcp-settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "ar-coordination").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo-a": {}},
            }
        ),
        encoding="utf-8",
    )
    return load_config(path)


def _record_review(lines: Lines):
    contract = load_contract(lines.fixture.contract.contract_path)
    publish_new_lifecycle_operation_location(
        contract, contract_text=contract_publication_text(contract.contract_path, contract)
    )
    capture = capture_future_code_candidate(contract)
    trees = live_review_trees(contract.coordination_root, contract, capture.codeCandidateTree)
    assert isinstance(trees, ReviewTrees), trees
    return contract, trees.record


def test_the_real_sync_tool_attaches_the_rebinding_block_for_a_moved_pair(
    tmp_path: Path, worktree_services
) -> None:
    lines = _converted_lines(tmp_path)
    contract, reviewed = _record_review(lines)
    commit_file(lines.fixture.code_repo, "src/landed.py", "VALUE = 'landed'")

    result = worktree_tools.worktree_sync_tool(
        _config(lines),
        contract_path=contract.contract_path.as_posix(),
        memory_sync_choice="merge-memory",
    )

    assert result["ok"] is True and result["state"] == "synced", result
    block = result["review_rebinding"]
    assert block["state"] == "moved", block
    assert block["covers_resolved_pair"] is False
    assert block["comparison"]["code_candidate"]["tree"] == reviewed.code_candidate.tree
    assert block["code_match"] == "differs-from-reviewed-input"
    read = read_review_sync_rebinding(
        contract.task_root, contract.leaf_id, comparison_key(reviewed)
    )
    assert read.state == "recorded"
    assert block["evidence_sha256"] and block["read_back"] == "matched"


def test_the_real_integrate_tool_attaches_the_delivered_receipt_for_a_reviewed_leaf(
    tmp_path: Path,
) -> None:
    fixture = QueueFixture(tmp_path, atomic_b=True, memory_mode="external")
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    # The official memory line carries the attributed commit a review resolves its memory base from.
    commit_memory_content(series, tmp_path / "scratch", label="seed")
    fixture.author_unstarted_leaf("master-b", "LEAF-C")
    leaf = fixture.start_leaf("LEAF-C", master="master-b")
    assert leaf.memory_worktree is not None
    # The leaf's work: one code file and a converted memory tree, uncommitted when it is reviewed.
    (leaf.code_worktree / "reviewed.py").write_text("VALUE = 2\n", encoding="utf-8")
    (leaf.memory_worktree / "knowledge").mkdir(exist_ok=True)
    (leaf.memory_worktree / "knowledge/layout.json").write_text(
        '{"schema":"ar-memory-layout/v2","conversion":"1"}\n', encoding="utf-8"
    )
    capture = capture_future_code_candidate(leaf)
    trees = live_review_trees(leaf.coordination_root, leaf, capture.codeCandidateTree)
    assert isinstance(trees, ReviewTrees), trees
    reviewed_record = trees.record

    closed = close_out_leaf(leaf)
    landed = worktree_tools.worktree_integrate_tool(
        fixture.cfg, contract_path=closed.contract_path.as_posix(), strategy="ff-only"
    )

    assert landed["ok"] is True, landed
    receipt = landed["final_output_receipt"]
    assert receipt["state"] == "recorded" and receipt["phase"] == "integration", receipt
    assert receipt["receipt_version"] == "ar-review-final-output-receipt/v2"
    assert receipt["leaf_id"] == "LEAF-C"
    assert receipt["comparison"] == reviewed_record.model_dump(mode="json", by_alias=True)
    assert receipt["delivered_code_commit"] == landed["integrated_code_commit"]
    assert receipt["delivered_code_tree_id"] == git(
        leaf.code_repo_path, "rev-parse", f"{landed['integrated_code_commit']}^{{tree}}"
    )
    # The code the leaf delivered is exactly the candidate tree the review captured.
    assert receipt["code_match"] == "matches-reviewed-input"
    assert receipt["read_back"] == "matched"
    assert landed["external_git_movement"]["binding_state"] in {"bound", "not-measured", "moved"}
    destination = Path(receipt["destination"])
    assert destination.is_file()
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == receipt["sha256"]
