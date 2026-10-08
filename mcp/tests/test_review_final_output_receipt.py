"""ICR-R21 on exact retained code and memory trees, without a canonical dataset."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_comparison_generation import generation_directory
from agents_remember.application.review_final_output_receipt import (
    attach_closeout_receipt,
    attach_integration_receipt,
    attach_prepared_selection,
    comparison_key,
    read_final_output_receipts,
    receipt_file_name,
    select_review_comparison,
)
from agents_remember.application.review_tree_comparison import (
    ReviewTrees,
    comparison_directory,
    live_review_trees,
    tree_resolution,
)
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.models.knowledge.review_final_output_receipt import (
    FinalOutputReceipt,
    LegacyFinalOutputReceipt,
    tree_comparison_digest,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.worktree_contract import WorktreeContract

pytestmark = pytest.mark.evidence_unit


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def commit_file(root: Path, relative: str, content: str, *, code: str | None = None) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change" if code is None else f"memory\n\nCode-Commit: {code}")
    return git(root, "rev-parse", "HEAD")


def repository(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "tree receipt fixture")
    git(path, "config", "user.email", "fixture@example.invalid")
    return path


@dataclass
class TreeReceiptFixture:
    contract: WorktreeContract

    def trees(self) -> ReviewTrees:
        capture = capture_future_code_candidate(self.contract)
        trees = live_review_trees(
            self.contract.coordination_root, self.contract, capture.codeCandidateTree
        )
        assert isinstance(trees, ReviewTrees), trees
        return trees

    def record(self) -> ReviewTreeComparisonRecord:
        return self.trees().record

    def resolution(self, trees: ReviewTrees) -> ReviewCandidateResolution:
        """The landed review's own resolution of this leaf over one comparison it recorded."""

        return tree_resolution(self.contract.repo_name, self.contract, trees)

    def capture_memory(self) -> str:
        assert self.contract.memory_worktree is not None
        with TemporaryDirectory() as scratch:
            return worktree_candidate_tree(self.contract.memory_worktree, Path(scratch) / "index")

    def commit_pair(self) -> tuple[str, str]:
        code = commit_file(self.contract.code_worktree, "reviewed.py", "VALUE = 2\n")
        assert self.contract.memory_worktree is not None
        memory = commit_file(
            self.contract.memory_worktree, "notes/reviewed.md", "reviewed memory\n", code=code
        )
        return code, memory


@pytest.fixture
def tree_fixture(tmp_path: Path) -> TreeReceiptFixture:
    code = repository(tmp_path / "code")
    code_base = commit_file(code, "reviewed.py", "VALUE = 1\n")
    memory = repository(tmp_path / "memory")
    memory_base = commit_file(
        memory,
        "knowledge/layout.json",
        '{"schema":"ar-memory-layout/v2","conversion":"1"}\n',
        code=code_base,
    )
    git(code, "switch", "-q", "-c", "leaf")
    git(memory, "switch", "-q", "-c", "leaf")
    coordination = tmp_path / "coordination"
    task = coordination / "tasks" / "agents-remember" / "260101_receipt"
    task.mkdir(parents=True)
    contract = WorktreeContract(
        task_id="260101-RECEIPT",
        task_name=task.name,
        repo_name="agents-remember",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=coordination,
        task_root=task,
        contract_path=task / "enclosures" / "leaf" / "series-contract.md",
        task_artifact=task / "task.json",
        worktree_group=tmp_path / "group",
        code_repo_path=code,
        code_source_branch="main",
        code_work_branch="leaf",
        code_base_commit=code_base,
        code_worktree=code,
        memory_repo_path=memory,
        memory_source_branch="main",
        memory_work_branch="leaf",
        memory_base_commit=memory_base,
        memory_worktree=memory,
        leaf_id="260101-RECEIPT-L1",
    )
    (code / "reviewed.py").write_text("VALUE = 2\n", encoding="utf-8")
    (memory / "notes").mkdir()
    (memory / "notes/reviewed.md").write_text("reviewed memory\n", encoding="utf-8")
    return TreeReceiptFixture(contract)


def test_receipts_bind_both_delivered_trees_and_keep_both_result_keys(tree_fixture):
    contract = tree_fixture.contract
    reviewed = tree_fixture.record()
    preview = attach_prepared_selection({"ok": True}, contract)["final_comparison_selection"]
    assert preview["prepared_is_reviewed_candidate"] is True
    assert preview["comparison"]["memory_candidate"]["tree"] == reviewed.memory_candidate.tree
    code, memory = tree_fixture.commit_pair()
    results = (
        attach_closeout_receipt(
            {"ok": True, "code_commit": code, "memory_content_commit": memory}, contract
        ),
        attach_integration_receipt(
            {
                "ok": True,
                "integrated_code_commit": code,
                "integrated_memory_content_commit": memory,
            },
            contract,
        ),
    )
    reads = read_final_output_receipts(
        contract.task_root, contract.leaf_id, comparison_key(reviewed)
    )
    for phase, result, read in zip(("closeout", "integration"), results, reads, strict=True):
        block = result["final_output_receipt"]
        assert block["receipt_version"] == "ar-review-final-output-receipt/v2"
        assert block["receipt_state"] == "bound"
        assert block["code_match"] == block["memory_match"] == "matches-reviewed-input"
        assert (read.state, read.phase) == ("recorded", phase)
        assert isinstance(read.receipt, FinalOutputReceipt)
        assert read.receipt.comparison == reviewed
        assert read.receipt.delivered_memory_tree_id == git(
            contract.memory_repo_path, "rev-parse", f"{memory}^{{tree}}"
        )


def test_memory_movement_and_absent_memory_never_claim_delivered_coverage(tree_fixture):
    contract = tree_fixture.contract
    reviewed = tree_fixture.record()
    code, memory = tree_fixture.commit_pair()
    moved = commit_file(contract.memory_worktree, "notes/after.md", "after review\n", code=code)
    block = attach_closeout_receipt(
        {"ok": True, "code_commit": code, "memory_content_commit": moved}, contract
    )["final_output_receipt"]
    assert block["receipt_state"] == "moved"
    assert block["code_match"] == "matches-reviewed-input"
    assert block["memory_match"] == "differs-from-reviewed-input"
    missing = attach_closeout_receipt({"ok": True, "code_commit": code}, contract)[
        "final_output_receipt"
    ]
    assert missing["receipt_state"] == "unmeasured"
    assert select_review_comparison(contract.task_root, contract.leaf_id).comparison == reviewed
    assert memory != moved
    changed_code = commit_file(contract.code_worktree, "reviewed.py", "VALUE = 9\n")
    changed = attach_closeout_receipt(
        {"ok": True, "code_commit": changed_code, "memory_content_commit": memory}, contract
    )["final_output_receipt"]
    assert changed["code_match"] == "differs-from-reviewed-input"
    assert changed["memory_match"] == "matches-reviewed-input"
    assert changed["receipt_state"] == "moved"


def test_unknown_or_corrupt_comparisons_do_not_block_completed_transaction(tree_fixture):
    contract = tree_fixture.contract
    code, memory = tree_fixture.commit_pair()
    payload = {"ok": True, "code_commit": code, "memory_content_commit": memory}
    result = attach_closeout_receipt(dict(payload), contract)
    assert result["ok"] and result["final_output_receipt"]["selection_state"] == "no-comparison"
    reviewed = tree_fixture.record()
    (
        comparison_directory(contract.task_root, contract.leaf_id) / f"{reviewed.number}.json"
    ).write_text("broken")
    corrupt = attach_closeout_receipt(dict(payload), contract)
    assert corrupt["ok"] and corrupt["final_output_receipt"]["selection_state"] == "unreadable"


def test_source_forgery_is_refused_and_receipts_are_leaf_scoped(tree_fixture):
    contract = tree_fixture.contract
    reviewed = tree_fixture.record()
    code, memory = tree_fixture.commit_pair()
    result = attach_closeout_receipt(
        {"ok": True, "code_commit": code, "memory_content_commit": memory}, contract
    )
    path = Path(result["final_output_receipt"]["destination"])
    data = json.loads(path.read_text())
    data["comparison"]["recorded_at"] = "forged"
    data["comparison_digest"] = tree_comparison_digest(
        ReviewTreeComparisonRecord.model_validate(data["comparison"])
    )
    path.write_text(json.dumps(data))
    (forged,) = read_final_output_receipts(
        contract.task_root, contract.leaf_id, comparison_key(reviewed), phases=("closeout",)
    )
    assert forged.state == "unreadable"
    forged_bytes = path.read_bytes()
    (elsewhere,) = read_final_output_receipts(
        contract.task_root, "another-leaf", comparison_key(reviewed), phases=("closeout",)
    )
    assert elsewhere.state == "not-recorded"
    assert path.read_bytes() == forged_bytes


def test_retained_successor_labels_previous_receipt_without_rewriting_it(tree_fixture):
    contract = tree_fixture.contract
    reviewed = tree_fixture.record()
    code, memory = tree_fixture.commit_pair()
    result = attach_closeout_receipt(
        {"ok": True, "code_commit": code, "memory_content_commit": memory}, contract
    )
    path = Path(result["final_output_receipt"]["destination"])
    before = path.read_bytes()
    (contract.code_worktree / "next.py").write_text("NEXT = 1\n")
    successor = tree_fixture.record()
    (read,) = read_final_output_receipts(
        contract.task_root, contract.leaf_id, comparison_key(reviewed), phases=("closeout",)
    )
    assert read.superseded_by == (successor,)
    assert path.read_bytes() == before
    assert successor.number > reviewed.number
    foreign = replace(contract, memory_repo_path=contract.code_repo_path)
    assert (
        attach_closeout_receipt({"ok": True, "code_commit": code}, foreign)["final_output_receipt"][
            "state"
        ]
        == "not-recorded"
    )


def test_historical_v1_receipt_remains_readable_without_claiming_memory_tree_proof(tree_fixture):
    contract = tree_fixture.contract
    generation = "123e4567-e89b-12d3-a456-426614174000"
    code = git(contract.code_repo_path, "rev-parse", "HEAD")
    tree = git(contract.code_repo_path, "rev-parse", "HEAD^{tree}")
    legacy = LegacyFinalOutputReceipt(
        phase="closeout",
        recorded_at="historical",
        repository_id=contract.repo_name,
        master=contract.task_name,
        leaf_id=contract.leaf_id,
        task_root=str(contract.task_root),
        contract_path=str(contract.contract_path),
        generation_id=generation,
        generation_index=1,
        binding_digest="a" * 64,
        manifest_digest="b" * 64,
        reviewed_baseline_code_tree_id=tree,
        reviewed_candidate_code_tree_id=tree,
        delivered_code_commit=code,
        delivered_code_tree_id=tree,
        code_match="matches-reviewed-input",
        memory_output_state="not-recorded",
        reviewed_knowledge_state="not-selected",
        published_knowledge_state="not-recorded",
        published_knowledge_path="historical-location",
        published_knowledge_detail="no publication was recorded",
        knowledge_match="not-comparable",
        state="bound",
    )
    path = durable_reports_root(contract.task_root) / receipt_file_name(
        contract.leaf_id, generation, "closeout"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(legacy.model_dump(mode="json")))
    before = path.read_bytes()
    (read,) = read_final_output_receipts(
        contract.task_root, contract.leaf_id, generation, phases=("closeout",)
    )
    assert read.state == "legacy-limit" and read.sha256
    assert isinstance(read.receipt, LegacyFinalOutputReceipt)
    assert read.receipt.delivered_code_tree_id == tree
    assert "tree-pair coverage is unavailable" in read.receipt.statement()
    generation_directory(contract.task_root, contract.leaf_id, generation).mkdir(parents=True)
    result = attach_closeout_receipt({"ok": True, "code_commit": code}, contract)
    assert result["final_output_receipt"]["selection_state"] == "legacy-limit"
    assert path.read_bytes() == before
