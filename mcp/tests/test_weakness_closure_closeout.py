"""A deferred weakness closure survives the actual paired closeout unchanged."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.writer import WriteRequest
from agents_remember.application.worktree_tool_requests import CloseoutApproval
from agents_remember.kernel.git_command import run_git
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.worktrees.modules import closeout_external
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from transaction_only_worktree_delivery_test_support import (
    MESSAGES,
    _assert_memory_attribution,
    _converted_closeout_fixture,
)
from writer_worklist_test_support import write_knowledge

pytestmark = pytest.mark.integration


def _weakness(contract: WorktreeContract, *, record_id: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "kind": "debt",
        "statement": "The base file still holds the obsolete marker.",
        "anchor": {"path": "base.txt", "locator": {"kind": "file"}},
        "seen": {"task": contract.leaf_id, "role": "worker"},
        "state": "open",
    }
    record = {"key": "obsolete-marker", "kind": "weakness", "slug": "obsolete-marker"}
    if record_id is not None:
        record["id"] = record_id
        fields.update(
            state="closed",
            closed={"task": contract.leaf_id, "reason": "The obsolete marker is removed."},
        )
    return {**record, "fields": fields}


def _write(contract: WorktreeContract, record: dict[str, Any], *, changed: bool = False) -> None:
    assert contract.memory_worktree is not None
    assert contract.leaf_id is not None
    history = []
    if changed:
        history.append(
            {
                "subject": "onboarding:base.txt",
                "disposition": "no_impact",
                "reason": "The marker change carries no invariant.",
            }
        )
    report = write_knowledge(
        WriteRequest(
            memory_root=contract.memory_worktree,
            code_root=contract.code_worktree,
            owner=Owner(task=contract.task_id, kind="leaf", id=contract.leaf_id),
            handoff_path="notes/weakness-handoff.json",
            document={"records": [record], "history": history},
            coordination_root=contract.coordination_root,
            commit=True,
        )
    )
    assert report.state == "written", report.render()


def _assert_public_closure(
    contract: WorktreeContract, root: Path, record_id: str, expected: dict[str, str]
) -> None:
    assert contract.memory_worktree is not None
    for view in ("weakness", "record"):
        answer = knowledge_read_payload(
            ReadToolRequest(
                memory_root=contract.memory_worktree.as_posix(),
                view=view,
                record_id=record_id if view == "record" else None,
                include_closed=True,
                repository_root=contract.code_worktree.as_posix(),
                code_tree_id=CodeSnapshot.capture(contract.code_worktree).tree,
            ),
            workspace_root=root.as_posix(),
            coordination_root=contract.coordination_root.as_posix(),
        )
        assert answer["ok"], answer
        if view == "weakness":
            row = next(row for row in answer["rows"] if row["id"] == record_id)
            assert row["closed"] == expected
        else:
            assert answer["facet"]["closed"] == expected


def test_closeout_attributes_a_deferred_closure_without_rewriting_its_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worktree_services: None
) -> None:
    contract, config = _converted_closeout_fixture(tmp_path)
    assert contract.memory_worktree is not None
    _write(contract, _weakness(contract))
    record_path = next(contract.memory_worktree.glob("knowledge/weaknesses/*.json"))
    record_id = json.loads(record_path.read_text(encoding="utf-8"))["id"]
    (contract.code_worktree / "base.txt").write_text("fixed\n", encoding="utf-8")
    _write(contract, _weakness(contract, record_id=record_id), changed=True)
    reviewed = record_path.read_bytes()
    closed = json.loads(reviewed)["closed"]
    assert closed == {"task": contract.leaf_id, "reason": "The obsolete marker is removed."}
    _assert_public_closure(
        contract,
        tmp_path,
        record_id,
        {**closed, "commitNote": "The code commit comes with this task's closeout."},
    )
    judged: list[str] = []
    native_judge = closeout_external._refuse_ungated_memory

    def judge(actual: WorktreeContract, code_commit: str, memory_tree: str) -> None:
        native_judge(actual, code_commit, memory_tree)
        judged.append(memory_tree)

    monkeypatch.setattr(closeout_external, "_refuse_ungated_memory", judge)
    answer = worktree_tools.worktree_closeout_apply_tool(
        config,
        contract.contract_path.as_posix(),
        MESSAGES,
        CloseoutApproval(intent_note="Publish the reviewed closure and its code repair."),
    )
    assert answer["ok"], answer
    completed = load_contract(contract.contract_path)
    memory_commit = completed.memory_content_commit
    committed_tree = run_git(contract.memory_worktree, ["rev-parse", f"{memory_commit}^{{tree}}"])
    assert committed_tree.returncode == 0, committed_tree
    assert judged and set(judged) == {committed_tree.stdout.strip()}
    _assert_memory_attribution(
        contract.memory_worktree,
        code_commit=completed.code_commit,
        content_commit=memory_commit,
        scratch=tmp_path,
    )
    relative = record_path.relative_to(contract.memory_worktree).as_posix()
    committed = run_git(contract.memory_worktree, ["show", f"{memory_commit}:{relative}"])
    assert committed.returncode == 0, committed
    assert committed.stdout.encode("utf-8") == reviewed == record_path.read_bytes()
    _assert_public_closure(
        contract, tmp_path, record_id, {**closed, "commit": completed.code_commit}
    )
