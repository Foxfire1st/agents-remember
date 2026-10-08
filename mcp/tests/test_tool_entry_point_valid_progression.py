"""Valid public worktree progression, separate from the sweep's intentional invalid guards."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.worktrees.worktree_contract import load_contract
from checkpoint_landing_test_support import accumulate_master_line, checkpoint
from test_closeout_queue import QueueFixture
from tool_entry_point_world import MASTER, REPO, EntryPointWorld, _git


@pytest.mark.integration
def test_valid_public_progression_keeps_the_invalid_sweep_inputs_separate(tmp_path: Path) -> None:
    """Close/integrate/record/clean a real leaf and checkpoint a separate real atomic fixture."""

    world = EntryPointWorld()
    try:
        world.build()
        assert world.benign()["worktree_record_landing"]["landed_code_commit"] == "deadbeef"
        kind, parent = world.call(
            "task_doc",
            {
                "repo_id": REPO,
                "task_name": MASTER,
                "slug": "1_leaf",
                "operation": "set_field",
                "fields": {"master": "task.json"},
            },
        )
        assert kind == "RETURNED" and parent is not None and parent.get("ok"), parent
        _public(
            world,
            "worktree_closeout_preview",
            {
                "contract_path": str(world.contract),
                "code_commit_message": "Valid fixture code",
                "memory_commit_message": "Valid fixture memory",
            },
            tmp_path,
        )
        _public(
            world,
            "worktree_closeout_apply",
            {
                "contract_path": str(world.contract),
                "code_commit_message": "Valid fixture code",
                "memory_commit_message": "Valid fixture memory",
                "intent_note": "Approve the disposable valid-progression fixture only",
            },
            tmp_path,
        )
        _closed(world, tmp_path)
        _public(world, "worktree_integrate", {"contract_path": str(world.contract)}, tmp_path)
        closed = load_contract(world.contract)
        commit = _git(closed.code_repo_path, "rev-parse", closed.code_source_branch)
        _public(
            world,
            "worktree_record_landing",
            {
                "contract_path": str(world.contract),
                "landed_code_commit": commit,
                "landed_memory_content_commit": closed.memory_content_commit,
            },
            tmp_path,
        )
        _public(world, "worktree_cleanup", {"contract_path": str(world.contract)}, tmp_path)
    finally:
        world.close()

    atomic = QueueFixture(tmp_path / "atomic", atomic_b=True, memory_mode="external")
    series = load_contract(atomic.tasks / "master-b" / "series-contract.md")
    assert json.loads((series.task_root / "task.json").read_text())["executionNature"] == "atomic"
    accumulate_master_line(atomic, series, tmp_path / "atomic-pair", label="valid-checkpoint")
    preview = checkpoint(atomic, series, dry_run=True)
    TOOL_RESPONSE_MODELS["worktree_checkpoint_landing"].model_validate(preview)
    (tmp_path / "checkpoint-preview.json").write_text(json.dumps(preview, default=str))
    assert preview["ok"] and preview["state"] == "would-checkpoint", preview
    result = checkpoint(atomic, series, dry_run=False)
    TOOL_RESPONSE_MODELS["worktree_checkpoint_landing"].model_validate(result)
    (tmp_path / "checkpoint-applied.json").write_text(json.dumps(result, default=str))
    assert result["ok"] and result["state"] == "checkpointed", result


def _public(world: EntryPointWorld, tool: str, arguments: dict[str, str], out: Path) -> None:
    kind, payload = world.call(tool, arguments)
    (out / (tool + ".json")).write_text(json.dumps({"kind": kind, "payload": payload}, default=str))
    assert kind == "RETURNED" and payload is not None, payload
    TOOL_RESPONSE_MODELS[tool].model_validate(payload)
    assert payload.get("ok") is True, payload


def _closed(world: EntryPointWorld, out: Path) -> None:
    """Observe the public operation until its actual completion before integration."""

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        kind, status = world.call(
            "worktree_status", {"repo_id": REPO, "contract_path": str(world.contract)}
        )
        with (out / "closeout-status.jsonl").open("a") as stream:
            stream.write(json.dumps({"kind": kind, "payload": status}, default=str) + "\n")
        assert kind == "RETURNED" and status is not None, status
        if status.get("closeout_status") == "completed":
            return
        time.sleep(0.05)
    pytest.fail("the public fixture closeout did not complete within its bound")
