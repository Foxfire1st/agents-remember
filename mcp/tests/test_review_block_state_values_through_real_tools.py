"""MIK-R74 rule 7: every reachable state value of the two result blocks, through the real tools.

The receipt block (``final_output_receipt``) is produced through ``worktree_closeout_apply``
and ``worktree_integrate``; the rebinding block (``review_rebinding``) through
``worktree_sync``. Each case builds its reviewed leaf the way the existing result-block
cases do -- the product records the comparison itself (``capture_future_code_candidate``
and ``live_review_trees``) -- and then asserts the exact channel matches and verdict the
real tool attaches.

Ruling 814778 point 3 narrowed the v2 receipt to measured values only
(``bound``/``moved``); a delivery without a memory commit writes no receipt. The
rebinding keeps its ``unmeasured`` match and verdict with a dedicated staged-fault
case below. A memory-disabled leaf records no tree comparison at all
(``test_a_memory_disabled_leaf_records_no_comparison``).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from agents_remember.application import review_sync_rebinding as rebinding_module
from agents_remember.application import worktree_tools
from agents_remember.application.review_final_output_receipt import comparison_key
from agents_remember.application.review_sync_rebinding import read_review_sync_rebinding
from agents_remember.application.review_tree_comparison import ReviewTrees, live_review_trees
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.models.knowledge.review_sync_rebinding import ReviewSyncRebinding
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.worktrees.modules import closeout_external
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from checkpoint_landing_test_support import close_out_leaf, commit_memory_content
from closeout_input_test_support import ensure_fixture_waiting_door
from test_closeout_queue import QueueFixture
from test_review_final_output_receipt import commit_file, git
from test_review_result_blocks_through_real_tools import _config as _sync_config
from test_review_result_blocks_through_real_tools import _record_review
from test_source_lineage import _fixture
from test_source_lineage import _git as other_git
from test_transaction_only_worktree_delivery import (
    _apply,
    _bind_task_without_review,
    _closeout_fixture,
)
from test_worktree_sync_knowledge_merge import _commit, _converted_lines

pytestmark = pytest.mark.integration


def _reviewed_closeout(tmp_path: Path) -> tuple[WorktreeContract, Any]:
    """The closeout leaf with converted memory, a seeded memory line and a recorded review."""

    contract, config = _closeout_fixture(tmp_path)
    assert contract.memory_worktree is not None and contract.memory_repo_path is not None
    layout = contract.memory_worktree / "knowledge" / "layout.json"
    layout.parent.mkdir(exist_ok=True)
    layout.write_text(
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}), encoding="utf-8"
    )
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
    return contract, config


def _closed_reviewed_leaf(tmp_path: Path) -> tuple[Any, Any]:
    """The integrate leaf LEAF-C with converted memory, reviewed and closed out."""

    fixture = QueueFixture(tmp_path, atomic_b=True, memory_mode="external")
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    commit_memory_content(series, tmp_path / "scratch", label="seed")
    fixture.author_unstarted_leaf("master-b", "LEAF-C")
    leaf = fixture.start_leaf("LEAF-C", master="master-b")
    assert leaf.memory_worktree is not None and leaf.code_worktree is not None
    (leaf.code_worktree / "reviewed.py").write_text("VALUE = 2\n", encoding="utf-8")
    (leaf.memory_worktree / "knowledge").mkdir(exist_ok=True)
    (leaf.memory_worktree / "knowledge/layout.json").write_text(
        '{"schema":"ar-memory-layout/v2","conversion":"1"}\n', encoding="utf-8"
    )
    capture = capture_future_code_candidate(leaf)
    trees = live_review_trees(leaf.coordination_root, leaf, capture.codeCandidateTree)
    assert isinstance(trees, ReviewTrees), trees
    return fixture, close_out_leaf(leaf)


def test_sync_records_current_when_the_carried_line_leaves_both_trees(
    tmp_path: Path, worktree_services: Any
) -> None:
    """An official-line move that changes no tree still carries, and the pair reads current."""

    lines = _converted_lines(tmp_path)
    contract, _reviewed = _record_review(lines)
    config = _sync_config(lines)
    git(lines.fixture.code_repo, "commit", "--allow-empty", "-q", "-m", "empty line move")
    result = worktree_tools.worktree_sync_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
        memory_sync_choice="merge-memory",
    )

    assert result["ok"] is True and result["state"] == "synced", result
    block = result["review_rebinding"]
    assert block["state"] == "current", block
    assert block["code_match"] == "matches-reviewed-input", block
    assert block["memory_match"] == "matches-reviewed-input", block
    assert block["covers_resolved_pair"] is True, block
    assert block["read_back"] == "matched", block


def test_sync_records_moved_with_matching_code_on_a_memory_only_carry(
    tmp_path: Path, worktree_services: Any
) -> None:
    """A memory-only official move carries; code matches, memory differs, verdict moved."""

    lines = _converted_lines(tmp_path)
    contract, _reviewed = _record_review(lines)
    config = _sync_config(lines)
    _commit(
        lines.fixture.memory_repo,
        {"knowledge/line-only.md": b"# line only\n"},
        "line-only memory move",
    )
    result = worktree_tools.worktree_sync_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
        memory_sync_choice="merge-memory",
    )

    assert result["ok"] is True and result["state"] == "synced", result
    block = result["review_rebinding"]
    assert block["state"] == "moved", block
    assert block["code_match"] == "matches-reviewed-input", block
    assert block["memory_match"] == "differs-from-reviewed-input", block
    assert block["covers_resolved_pair"] is False, block


def test_sync_records_no_movement_when_the_pair_already_contains_the_line(
    tmp_path: Path, worktree_services: Any
) -> None:
    """A sync that carries nothing records no-movement rather than a verdict."""

    lines = _converted_lines(tmp_path)
    contract, _reviewed = _record_review(lines)
    config = _sync_config(lines)
    result = worktree_tools.worktree_sync_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
        memory_sync_choice="merge-memory",
    )

    block = result["review_rebinding"]
    assert block["state"] == "no-movement", block
    assert block["covers_resolved_pair"] is False, block


def test_sync_records_not_measured_when_the_carry_fails(
    tmp_path: Path, worktree_services: Any
) -> None:
    """A sync the git proof refuses carries no pair, and the block says not-measured."""

    lines = _converted_lines(tmp_path)
    code_worktree = lines.fixture.contract.code_worktree
    assert code_worktree is not None
    (code_worktree / "same.py").write_text("SAME = 1\n", encoding="utf-8")
    contract, _reviewed = _record_review(lines)
    config = _sync_config(lines)
    commit_file(lines.fixture.code_repo, "same.py", "SAME = 1\n")
    result = worktree_tools.worktree_sync_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
        memory_sync_choice="merge-memory",
    )

    block = result["review_rebinding"]
    assert block["state"] == "not-measured", block
    assert block["covers_resolved_pair"] is False, block


def test_closeout_receipt_is_moved_when_its_own_memory_commit_differs(
    tmp_path: Path, worktree_services: Any
) -> None:
    """An unchanged closeout still delivers a new memory commit, so the receipt reads moved
    with matching code: the verdict follows the trees, not the absence of edits."""

    contract, config = _reviewed_closeout(tmp_path)
    with mock.patch.object(closeout_external, "leaf_gate_refusal", return_value=None):
        applied = _apply(config, contract)

    assert applied["ok"] is True and applied["state"] == "closed", applied
    receipt = applied["final_output_receipt"]
    assert receipt["state"] == "recorded" and receipt["phase"] == "closeout", receipt
    assert receipt["receipt_state"] == "moved", receipt
    assert receipt["code_match"] == "matches-reviewed-input", receipt
    assert receipt["memory_match"] == "differs-from-reviewed-input", receipt
    assert receipt["read_back"] == "matched", receipt


def test_closeout_receipt_is_moved_when_code_changes_after_review(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Late code lands in the delivered commit, so the closeout receipt reads moved."""

    contract, config = _reviewed_closeout(tmp_path)
    assert contract.code_worktree is not None
    (contract.code_worktree / "late.py").write_text("LATE = 1\n", encoding="utf-8")
    with mock.patch.object(closeout_external, "leaf_gate_refusal", return_value=None):
        applied = _apply(config, contract)

    assert applied["ok"] is True and applied["state"] == "closed", applied
    receipt = applied["final_output_receipt"]
    assert receipt["state"] == "recorded" and receipt["phase"] == "closeout", receipt
    assert receipt["receipt_state"] == "moved", receipt
    assert receipt["code_match"] == "differs-from-reviewed-input", receipt


def test_closeout_receipt_is_moved_for_a_memory_change_with_matching_code(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Late memory lands in the delivered memory tree while code matches: still moved."""

    contract, config = _reviewed_closeout(tmp_path)
    assert contract.memory_worktree is not None
    (contract.memory_worktree / "late.md").write_text("# late\n", encoding="utf-8")
    with mock.patch.object(closeout_external, "leaf_gate_refusal", return_value=None):
        applied = _apply(config, contract)

    assert applied["ok"] is True and applied["state"] == "closed", applied
    receipt = applied["final_output_receipt"]
    assert receipt["state"] == "recorded" and receipt["phase"] == "closeout", receipt
    assert receipt["receipt_state"] == "moved", receipt
    assert receipt["code_match"] == "matches-reviewed-input", receipt
    assert receipt["memory_match"] == "differs-from-reviewed-input", receipt


def test_integration_receipt_is_bound_when_both_trees_match(tmp_path: Path) -> None:
    """An unchanged integration delivers exactly the reviewed pair: verdict bound."""

    fixture, closed = _closed_reviewed_leaf(tmp_path)
    landed = worktree_tools.worktree_integrate_tool(
        fixture.cfg, contract_path=closed.contract_path.as_posix(), strategy="ff-only"
    )

    assert landed["ok"] is True, landed
    receipt = landed["final_output_receipt"]
    assert receipt["state"] == "recorded" and receipt["phase"] == "integration", receipt
    assert receipt["receipt_state"] == "bound", receipt
    assert receipt["code_match"] == "matches-reviewed-input", receipt
    assert receipt["memory_match"] == "matches-reviewed-input", receipt
    assert receipt["read_back"] == "matched", receipt


def test_integration_receipt_is_moved_when_code_changes_after_review(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Late code lands in the integrated commit, so the integration receipt reads moved."""

    fixture = QueueFixture(tmp_path, atomic_b=True, memory_mode="external")
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    commit_memory_content(series, tmp_path / "scratch", label="seed")
    fixture.author_unstarted_leaf("master-b", "LEAF-C")
    leaf = fixture.start_leaf("LEAF-C", master="master-b")
    assert leaf.memory_worktree is not None and leaf.code_worktree is not None
    (leaf.code_worktree / "reviewed.py").write_text("VALUE = 2\n", encoding="utf-8")
    (leaf.memory_worktree / "knowledge").mkdir(exist_ok=True)
    (leaf.memory_worktree / "knowledge/layout.json").write_text(
        '{"schema":"ar-memory-layout/v2","conversion":"1"}\n', encoding="utf-8"
    )
    capture = capture_future_code_candidate(leaf)
    trees = live_review_trees(leaf.coordination_root, leaf, capture.codeCandidateTree)
    assert isinstance(trees, ReviewTrees), trees
    (leaf.code_worktree / "late.py").write_text("LATE = 1\n", encoding="utf-8")
    closed = close_out_leaf(leaf)
    landed = worktree_tools.worktree_integrate_tool(
        fixture.cfg, contract_path=closed.contract_path.as_posix(), strategy="ff-only"
    )

    assert landed["ok"] is True, landed
    receipt = landed["final_output_receipt"]
    assert receipt["state"] == "recorded" and receipt["phase"] == "integration", receipt
    assert receipt["receipt_state"] == "moved", receipt
    assert receipt["code_match"] == "differs-from-reviewed-input", receipt


def test_sync_records_unmeasured_when_the_memory_capture_faults(
    tmp_path: Path, worktree_services: Any
) -> None:
    """One staged fault at the memory-candidate capture seam: the sync completes on the
    pair, then the memory tree capture raises, so the recorded rebinding measures the
    code channel and leaves the memory channel unmeasured.

    The fault is staged by replacing exactly one function --
    ``application.review_sync_rebinding.worktree_candidate_tree``, the tree capture that
    ``_resolved_memory`` calls -- with a stub that raises ``OSError``. Nothing else of
    the rebinding is replaced: the official line carries (an empty commit, so both
    resolved trees still match), the registered ``worktree_sync`` tool runs, and the
    assertions below read the tool's result block and the record the tool wrote back
    from its file."""

    lines = _converted_lines(tmp_path)
    contract, reviewed = _record_review(lines)
    config = _sync_config(lines)
    git(lines.fixture.code_repo, "commit", "--allow-empty", "-q", "-m", "empty line move")

    def _faulted_capture(*args: Any, **kwargs: Any) -> str:
        raise OSError("staged capture fault: memory candidate unreadable")

    with mock.patch.object(rebinding_module, "worktree_candidate_tree", _faulted_capture):
        result = worktree_tools.worktree_sync_tool(
            config,
            contract_path=contract.contract_path.as_posix(),
            memory_sync_choice="merge-memory",
        )

    assert result["ok"] is True and result["state"] == "synced", result
    block = result["review_rebinding"]
    assert block["code_match"] == "matches-reviewed-input", block
    assert block["memory_match"] == "unmeasured", block
    assert block["state"] == "unmeasured", block
    assert block["covers_resolved_pair"] is False, block
    assert "could not be captured" in block["memory_detail"], block
    read = read_review_sync_rebinding(
        contract.task_root, contract.leaf_id, comparison_key(reviewed)
    )
    assert read.state == "recorded", read
    assert isinstance(read.rebinding, ReviewSyncRebinding), read.rebinding
    assert read.rebinding.state == "unmeasured", read.rebinding
    assert read.rebinding.code_match == "matches-reviewed-input", read.rebinding
    assert read.rebinding.memory_match == "unmeasured", read.rebinding
    assert read.rebinding.memory_detail == block["memory_detail"], read.rebinding


def test_a_memory_disabled_leaf_records_no_comparison(
    tmp_path: Path, worktree_services: Any
) -> None:
    """A leaf without memory holds no tree comparison, so closeout, integrate and sync
    have nothing to measure on it."""

    fixture = _fixture(tmp_path, external_memory=False, selected_profile=False)
    contract = fixture.leaf_contract
    assert contract.memory_worktree is None
    assert contract.code_worktree is not None
    contract.code_worktree.parent.mkdir(parents=True, exist_ok=True)
    other_git(
        fixture.code_repo,
        "worktree",
        "add",
        contract.code_worktree.as_posix(),
        contract.code_work_branch,
    )
    (contract.code_worktree / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    ensure_fixture_waiting_door(contract, force_synthetic=True)
    contract = load_contract(contract.contract_path)
    _bind_task_without_review(contract)
    capture = capture_future_code_candidate(contract)
    trees = live_review_trees(contract.coordination_root, contract, capture.codeCandidateTree)

    assert trees is None
