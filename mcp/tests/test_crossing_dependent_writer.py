"""Plan a dependent's resolution through the master's open crossing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.knowledge import WriteToolRequest, knowledge_write_payload
from agents_remember.models.knowledge_files.documents import record_path
from agents_remember.worktrees.sync_transaction_authority import side_locations
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
    write_contract,
)
from knowledge_writer_test_support import (
    CODE_FILE,
    World,
    build_world,
    canonical,
    commit_all,
    git,
    tree_bytes,
    write,
)

pytestmark = pytest.mark.integration


def _open_crossing(world: World, dependent: str) -> tuple[WorktreeContract, Path, str]:
    document = "docs/landing.md"
    write(world.code, {document: "# Landing\n\nCode and memory land together.\n"})
    code_commit = commit_all(world.code, "Describe the paired landing")
    record = record_path("dependent", dependent, "landing")
    confirmation = {
        "task": "CROSSING",
        "role": "curator",
        "statement": "Record both sides of the landing.",
        "commit": code_commit,
    }
    base_record = {
        "schema": "ar-dependent/v1",
        "id": dependent,
        "revision": 1,
        "document": document,
        "stands_on": [
            {
                "kind": "file",
                "path": CODE_FILE,
                "blob": git(world.code, "rev-parse", f"HEAD:{CODE_FILE}"),
            }
        ],
        "confirmed": confirmation,
        "origin": {"task": "CROSSING", "leaf": "CROSSING-L1"},
    }
    write(world.memory, {record: canonical(base_record)})
    memory_base = commit_all(world.memory, "Record the document")
    series = default_series_contract(
        ContractTask(
            name="crossing",
            repo_name="agents-remember",
            coordination_root=world.root,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        code=RepoBranchPlan(world.code, "main", "main", code_commit),
        memory=RepoBranchPlan(world.memory, "main", "master-work", memory_base),
        task_root=world.task_root,
    )
    write_contract(series.contract_path, series)
    memory = side_locations(series, "memory")[1]
    memory.parent.mkdir(parents=True)
    git(world.memory, "worktree", "add", "-q", "-b", "master-work", str(memory))
    own = {
        **base_record,
        "revision": 2,
        "confirmed": {**confirmation, "statement": "The pair lands together."},
    }
    incoming = {
        **base_record,
        "revision": 3,
        "confirmed": {**confirmation, "statement": "Landing records both trees."},
    }
    write(memory, {record: canonical(own)})
    commit_all(memory, "Update the confirmation on the master line")
    write(world.memory, {record: canonical(incoming)})
    commit_all(world.memory, "Update the confirmation on the source line")
    with pytest.raises(AssertionError, match="fixture git merge"):
        git(memory, "merge", "--no-commit", "main")
    assert git(memory, "ls-files", "-u", "--", record)
    git(memory, "checkout", "--ours", "--", record)
    crossing = f"{series.task_id}-crossing-1"
    history = f"knowledge/history/{crossing}.json"
    write(
        memory,
        {
            history: canonical(
                {"schema": "ar-history/v1", "crossing": crossing, "closed": False, "rows": []}
            )
        },
    )
    return series, memory, crossing


def test_a_crossing_plans_the_resolution_of_a_conflicted_dependent(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    dependent = "DPD-7K3F9Q"
    series, memory, crossing = _open_crossing(world, dependent)
    before = tree_bytes(memory)
    index_before = git(memory, "ls-files", "-s")

    plan = knowledge_write_payload(
        WriteToolRequest(
            contract_path=str(series.contract_path),
            crossing=crossing,
            authorization_ref="crossing-review",
            handoff_name="resolve-dependent.json",
            handoff={
                "records": [
                    {
                        "key": "resolve-dependent",
                        "kind": "dependent",
                        "id": dependent,
                        "fields": {
                            "stands_on": [{"kind": "file", "path": CODE_FILE}],
                            "confirmed": {
                                "task": series.task_id,
                                "role": "curator",
                                "statement": "Code and memory land together.",
                            },
                        },
                    }
                ]
            },
            commit=False,
        ),
        McpRuntimeConfig(
            config_path=tmp_path / "settings.json",
            coordination_root=world.root,
            workspace_root=tmp_path,
            transcript_root=tmp_path / "logs",
        ),
    )

    assert plan["state"] == "planned", plan
    snapshot = CodeSnapshot.at_commit(world.code, "refs/heads/main")
    assert snapshot.commit == git(world.code, "rev-parse", "refs/heads/main^{commit}")
    assert plan["owner"] == crossing
    assert plan["codeTree"] == git(world.code, "rev-parse", "refs/heads/main^{tree}")
    report = json.loads(Path(plan["reportPath"]).read_text(encoding="utf-8"))
    (updated,) = report["records"]
    assert (updated["kind"], updated["id"], updated["action"], updated["revision"]) == (
        "dependent",
        dependent,
        "updated",
        4,
    )
    assert tree_bytes(memory) == before
    assert git(memory, "ls-files", "-s") == index_before
