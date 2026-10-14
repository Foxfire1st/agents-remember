"""MIK-R52 F04: master-route offender coverage through the public route entries.

The helper-level chain proof is pinned in test_master_chain_offenders.py; the sealed packet
requires the same discriminators through the routes a landing actually takes. Each case stages
a foreign commit on a converted series fixture line and calls the public entry point —
``checkpoint_landing_result`` for the checkpoint, ``integrate_result`` for the final landing —
so removing the chain leg from either route makes it fail. A hand-edited contract cell (not an
in-memory replacement) proves the edited-contract discriminator through the same public
checkpoint entry.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.integrate import (
    checkpoint_landing_result,
    integrate_result,
)
from agents_remember.worktrees.modules.record_landing import record_landing_result
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    amend_contract,
    default_series_contract,
    load_contract,
    write_contract,
)
from test_checkpoint_landing import (
    _git,
    _set_master,
    _write_task_tree,
)
from test_knowledge_closeout_gate import (
    CODE_A,
    LEAF,
    TRACES,
    A,
    _answered,
    build_gated,
    commit,
    trace_rows,
)


@pytest.fixture
def ports() -> Any:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


def _repo(path: Path) -> Path:
    """A fixture repository whose default is ``main`` so the ``super`` source branch is a
    real PR-gated root (the public integration entry refuses a repository-default source)."""

    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "t")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "base.txt")
    _git(path, "commit", "-q", "-m", "base")
    _git(path, "branch", "super")
    _git(path, "branch", "ar/master", "super")
    _git(path, "update-ref", "refs/remotes/origin/main", _git(path, "rev-parse", "main"))
    _git(path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return path


def _converted_series(root: Path) -> WorktreeContract:
    """An atomic series fixture whose memory line holds the conversion marker and one
    closed history file on both sides, with the recorded base moved past both so the
    chain proof starts clean."""

    coordination = root / "coordination"
    code_repo = _repo(root / "code")
    memory_repo = _repo(root / "memory")
    task_root = coordination / "tasks" / "repo" / "master"
    _write_task_tree(coordination)
    contract = default_series_contract(
        ContractTask("master", "repo", coordination, "light-task", "external"),
        code=RepoBranchPlan(code_repo, "super", "ar/master", _git(code_repo, "rev-parse", "super")),
        memory=RepoBranchPlan(
            memory_repo, "super", "ar/master", _git(memory_repo, "rev-parse", "super")
        ),
        task_root=task_root,
    )
    write_contract(contract.contract_path, contract)
    memory = memory_repo
    _git(memory, "checkout", "-q", "super")
    (memory / "knowledge" / "layout.json").parent.mkdir(parents=True, exist_ok=True)
    (memory / "knowledge" / "layout.json").write_text(
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        encoding="utf-8",
    )
    history = memory / "knowledge" / "history" / "TEAM.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        canonical_text({"schema": "ar-history/v1", "leaf": "TEAM", "closed": True, "rows": []}),
        encoding="utf-8",
    )
    _git(memory, "add", "knowledge/layout.json", "knowledge/history/TEAM.json")
    _git(memory, "commit", "-q", "-m", "conversion marker and closed team history")
    marker = _git(memory, "rev-parse", "super")
    _git(memory, "checkout", "-q", "ar/master")
    _git(memory, "merge", "-q", "--ff-only", "super")
    write_contract(contract.contract_path, replace(contract, memory_base_commit=marker))
    return load_contract(contract.contract_path)


def _foreign_code_commit(contract: WorktreeContract) -> str:
    assert contract.code_repo_path is not None
    code = contract.code_repo_path
    _git(code, "checkout", "-q", "ar/master")
    (code / "foreign.txt").write_text("not a leaf landing\n", encoding="utf-8")
    _git(code, "add", "foreign.txt")
    _git(code, "commit", "-q", "-m", "hand-made commit off any leaf")
    return _git(code, "rev-parse", "ar/master")


def test_a_foreign_commit_refuses_through_the_public_checkpoint_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 master discriminator: the public checkpoint names the foreign commit."""

    contract = _converted_series(tmp_path)
    foreign = _foreign_code_commit(contract)
    result = checkpoint_landing_result(
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        load_contract(contract.contract_path),
    )
    assert result.returncode == 2, result.payload
    reason = str(result.payload.get("reason"))
    assert foreign in reason
    assert "adds history beyond the exact leaf landing chain" in reason


def test_a_foreign_commit_refuses_through_the_public_final_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 master discriminator: the public final landing names the foreign commit."""

    contract = _converted_series(tmp_path)
    _set_master(contract, status="inProgress", row_status="abandoned")
    contract = load_contract(contract.contract_path)
    write_contract(
        contract.contract_path, amend_contract(contract, ContractCells(closeout_status="completed"))
    )
    foreign = _foreign_code_commit(contract)
    assert contract.memory_repo_path is not None
    memory_tip = _git(contract.memory_repo_path, "rev-parse", "ar/master")
    closeout_text = contract.contract_path.read_text(encoding="utf-8")
    closeout_text = re.sub(
        r"closeout:\n(?:  \w+: .*\n?)*",
        f"closeout:\n  status: completed\n  code_commit: {foreign}\n"
        f"  memory_content_commit: {memory_tip}\n",
        closeout_text,
        count=1,
    )
    closeout_text = closeout_text.replace(
        "  approved_for_commit: no", "  approved_for_commit: yes", 1
    )
    contract.contract_path.write_text(closeout_text, encoding="utf-8")
    contract = load_contract(contract.contract_path)
    assert contract.approved_for_commit and contract.code_commit == foreign
    result = integrate_result(
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        contract,
    )
    assert result.returncode == 2, result.payload
    reason = str(result.payload.get("reason"))
    assert foreign in reason
    assert "adds history beyond the exact leaf landing chain" in reason


def test_a_hand_edited_contract_cell_refuses_through_the_public_checkpoint_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 edited-contract discriminator: a hand-edited contract file (not an in-memory
    replacement) is what the public checkpoint judges; the unreadable base cell refuses
    naming the edited value, and the file keeps the edit."""

    contract = _converted_series(tmp_path)
    bogus = "d" * 40
    text = contract.contract_path.read_text(encoding="utf-8")
    edited = text.replace(
        f"  base_commit: {contract.code_base_commit}",
        f"  base_commit: {bogus}",
        1,
    )
    assert edited != text
    contract.contract_path.write_text(edited, encoding="utf-8")
    result = checkpoint_landing_result(
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        load_contract(contract.contract_path),
    )
    assert result.returncode == 2, result.payload
    assert bogus in str(result.payload.get("reason"))
    assert bogus in contract.contract_path.read_text(encoding="utf-8")


def test_a_hand_merge_names_its_side_through_the_public_checkpoint_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 merge discriminator: a hand merge smuggling a foreign side refuses through the
    public checkpoint, naming the side commit."""

    contract = _converted_series(tmp_path)
    assert contract.memory_repo_path is not None
    memory = contract.memory_repo_path
    _git(memory, "checkout", "-q", "-b", "side", _git(memory, "rev-parse", "ar/master"))
    (memory / "side.txt").write_text("side\n", encoding="utf-8")
    _git(memory, "add", "side.txt")
    _git(memory, "commit", "-q", "-m", "side work")
    side = _git(memory, "rev-parse", "side")
    _git(memory, "checkout", "-q", "ar/master")
    _git(memory, "merge", "-q", "--no-ff", "-m", "hand merge", "side")
    result = checkpoint_landing_result(
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        load_contract(contract.contract_path),
    )
    assert result.returncode == 2, result.payload
    assert side in str(result.payload.get("reason"))


def test_an_edited_history_file_refuses_through_the_public_checkpoint_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 edited-history discriminator: a closed history file changed on the line refuses
    through the public checkpoint, naming the file."""

    contract = _converted_series(tmp_path)
    assert contract.memory_repo_path is not None
    memory = contract.memory_repo_path
    history = memory / "knowledge" / "history" / "TEAM.json"
    history.write_text(
        history.read_text(encoding="utf-8").replace('"closed": true', '"closed": false', 1),
        encoding="utf-8",
    )
    _git(memory, "add", "knowledge/history/TEAM.json")
    _git(memory, "commit", "-q", "-m", "hand edit of closed history")
    result = checkpoint_landing_result(
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        load_contract(contract.contract_path),
    )
    assert result.returncode == 2, result.payload
    assert "knowledge/history/TEAM.json" in str(result.payload.get("reason"))


def _recorded(series_path: Path, *, code: str, memory: str) -> Any:
    """Run the public recorded route (dry run) against a series contract path."""

    return record_landing_result(
        WorktreeArgs(
            contract_path=series_path,
            dry_run=True,
            landed_code_commit=code,
            landed_memory_content_commit=memory,
        )
    )


def test_a_foreign_commit_refuses_through_the_public_recorded_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 recorded-master discriminator: a foreign commit on the line refuses through the
    public recorded route, naming the foreign commit (landed past it)."""

    contract = _converted_series(tmp_path)
    assert contract.code_repo_path is not None and contract.memory_repo_path is not None
    code, memory = contract.code_repo_path, contract.memory_repo_path
    _git(code, "checkout", "-q", "ar/master")
    (code / "foreign.txt").write_text("not a leaf landing\n", encoding="utf-8")
    _git(code, "add", "foreign.txt")
    _git(code, "commit", "-q", "-m", "hand-made commit on the line")
    foreign = _git(code, "rev-parse", "ar/master")
    (code / "top.txt").write_text("clean top\n", encoding="utf-8")
    _git(code, "add", "top.txt")
    _git(code, "commit", "-q", "-m", "clean top commit")
    top = _git(code, "rev-parse", "ar/master")
    _git(code, "checkout", "-q", "super")
    _git(code, "merge", "-q", "--ff-only", "ar/master")
    mem_tip = _git(memory, "rev-parse", "ar/master")
    with pytest.raises(RuntimeError) as refused:
        _recorded(contract.contract_path, code=top, memory=mem_tip)
    assert foreign in str(refused.value)


def test_a_hand_merge_refuses_through_the_public_recorded_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 recorded-master discriminator: a hand merge with a foreign side refuses through
    the public recorded route, naming the side commit (landed past the merge)."""

    contract = _converted_series(tmp_path)
    assert contract.code_repo_path is not None and contract.memory_repo_path is not None
    code, memory = contract.code_repo_path, contract.memory_repo_path
    _git(code, "checkout", "-q", "-b", "side", _git(code, "rev-parse", "ar/master"))
    (code / "side.txt").write_text("side\n", encoding="utf-8")
    _git(code, "add", "side.txt")
    _git(code, "commit", "-q", "-m", "side work")
    side = _git(code, "rev-parse", "side")
    _git(code, "checkout", "-q", "ar/master")
    _git(code, "merge", "-q", "--no-ff", "-m", "hand merge", "side")
    (code / "top.txt").write_text("clean top\n", encoding="utf-8")
    _git(code, "add", "top.txt")
    _git(code, "commit", "-q", "-m", "clean top commit")
    top = _git(code, "rev-parse", "ar/master")
    _git(code, "checkout", "-q", "super")
    _git(code, "merge", "-q", "--ff-only", "ar/master")
    mem_tip = _git(memory, "rev-parse", "ar/master")
    with pytest.raises(RuntimeError) as refused:
        _recorded(contract.contract_path, code=top, memory=mem_tip)
    assert side in str(refused.value)


def test_a_managed_sync_merge_passes_through_the_public_recorded_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 recorded-master positive control: a merge recorded as a managed sync head is
    admitted through the public recorded route (would-record)."""

    contract = _converted_series(tmp_path)
    assert contract.code_repo_path is not None and contract.memory_repo_path is not None
    code, memory = contract.code_repo_path, contract.memory_repo_path
    _git(code, "checkout", "-q", "super")
    (code / "official.txt").write_text("official line\n", encoding="utf-8")
    _git(code, "add", "official.txt")
    _git(code, "commit", "-q", "-m", "official line work")
    position = _git(code, "rev-parse", "super")
    _git(code, "checkout", "-q", "ar/master")
    _git(code, "merge", "-q", "--no-ff", "-m", "managed sync merge", position)
    merge = _git(code, "rev-parse", "ar/master")
    _git(code, "checkout", "-q", "super")
    _git(code, "merge", "-q", "--ff-only", "ar/master")
    mem_tip = _git(memory, "rev-parse", "ar/master")
    entry = {
        "at": "2026-10-12T00:00:00+00:00",
        "codeBaseFrom": contract.code_base_commit,
        "codeBaseTo": position,
        "memoryBaseFrom": contract.memory_base_commit,
        "memoryBaseTo": contract.memory_base_commit,
        "code": "super",
        "memory": "super",
        "codeHeadTo": merge,
        "memoryHeadTo": mem_tip,
    }
    write_contract(contract.contract_path, replace(contract, sync_log=(entry,)))
    result = _recorded(contract.contract_path, code=merge, memory=mem_tip)
    assert result.returncode == 0, result.payload
    assert result.payload.get("state") == "would-record", result.payload


def _sync_entry(contract: WorktreeContract, *, code_to: str, code_head: str, mem_head: str) -> None:
    """Journal one managed sync (official position + produced heads) on the contract file."""

    write_contract(
        contract.contract_path,
        replace(
            contract,
            sync_log=(
                {
                    "at": "2026-10-12T00:00:00+00:00",
                    "codeBaseFrom": contract.code_base_commit,
                    "codeBaseTo": code_to,
                    "memoryBaseFrom": contract.memory_base_commit,
                    "memoryBaseTo": contract.memory_base_commit,
                    "code": "super",
                    "memory": "super",
                    "codeHeadTo": code_head,
                    "memoryHeadTo": mem_head,
                },
            ),
        ),
    )


def test_a_managed_sync_merge_passes_through_the_public_checkpoint_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 checkpoint-matrix control: a sync-recorded merge is admitted through the public
    checkpoint route (side is the synced official position, merge is the recorded head)."""

    contract = _converted_series(tmp_path)
    assert contract.code_repo_path is not None and contract.memory_repo_path is not None
    code, memory = contract.code_repo_path, contract.memory_repo_path
    _git(code, "checkout", "-q", "super")
    (code / "official.txt").write_text("official line\n", encoding="utf-8")
    _git(code, "add", "official.txt")
    _git(code, "commit", "-q", "-m", "official line work")
    position = _git(code, "rev-parse", "super")
    _git(code, "checkout", "-q", "ar/master")
    _git(code, "merge", "-q", "--no-ff", "-m", "managed sync merge", position)
    merge = _git(code, "rev-parse", "ar/master")
    mem_tip = _git(memory, "rev-parse", "ar/master")
    _sync_entry(contract, code_to=position, code_head=merge, mem_head=mem_tip)
    result = checkpoint_landing_result(
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        load_contract(contract.contract_path),
    )
    assert result.returncode == 0, result.payload
    assert result.payload.get("state") == "would-checkpoint", result.payload


def _finished(contract: WorktreeContract) -> WorktreeContract:
    """Mark the fixture master finished (closeout completed + approved) so the final route
    opens. Closeout cells are pinned after staging (they must equal the judged tips)."""

    _set_master(contract, status="inProgress", row_status="abandoned")
    contract = load_contract(contract.contract_path)
    write_contract(
        contract.contract_path,
        amend_contract(contract, ContractCells(closeout_status="completed")),
    )
    closeout_text = contract.contract_path.read_text(encoding="utf-8").replace(
        "  approved_for_commit: no", "  approved_for_commit: yes", 1
    )
    contract.contract_path.write_text(closeout_text, encoding="utf-8")
    return load_contract(contract.contract_path)


def _pin_closeout_cells(contract: WorktreeContract) -> WorktreeContract:
    """Pin the closeout cells to the current line tips (the refs the final route judges)."""

    assert contract.code_repo_path is not None and contract.memory_repo_path is not None
    code_tip = _git(contract.code_repo_path, "rev-parse", "super")
    mem_tip = _git(contract.memory_repo_path, "rev-parse", "ar/master")
    closeout_text = contract.contract_path.read_text(encoding="utf-8")
    closeout_text = re.sub(
        r"closeout:\n(?:  \w+: .*\n?)*",
        f"closeout:\n  status: completed\n  code_commit: {code_tip}\n"
        f"  memory_content_commit: {mem_tip}\n",
        closeout_text,
        count=1,
    )
    contract.contract_path.write_text(closeout_text, encoding="utf-8")
    contract = load_contract(contract.contract_path)
    assert contract.approved_for_commit and contract.code_commit == code_tip
    return contract


def test_a_hand_merge_refuses_through_the_public_final_route(tmp_path: Path, ports: None) -> None:
    """F04 final-matrix discriminator: a hand merge with a foreign side refuses through the
    public final route, naming the side commit."""

    contract = _converted_series(tmp_path)
    assert contract.code_repo_path is not None and contract.memory_repo_path is not None
    contract = _finished(contract)
    code = contract.code_repo_path
    _git(code, "checkout", "-q", "-b", "side", _git(code, "rev-parse", "ar/master"))
    (code / "side.txt").write_text("side\n", encoding="utf-8")
    _git(code, "add", "side.txt")
    _git(code, "commit", "-q", "-m", "side work")
    side = _git(code, "rev-parse", "side")
    _git(code, "checkout", "-q", "ar/master")
    _git(code, "merge", "-q", "--no-ff", "-m", "hand merge", "side")
    (code / "top.txt").write_text("clean top\n", encoding="utf-8")
    _git(code, "add", "top.txt")
    _git(code, "commit", "-q", "-m", "clean top commit")
    _git(code, "checkout", "-q", "super")
    _git(code, "merge", "-q", "--ff-only", "ar/master")
    contract = _pin_closeout_cells(contract)
    result = integrate_result(
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        load_contract(contract.contract_path),
    )
    assert result.returncode == 2, result.payload
    reason = str(result.payload.get("reason"))
    assert side in reason
    assert "adds history beyond the exact leaf landing chain" in reason


def test_a_managed_sync_merge_passes_through_the_public_final_route(
    tmp_path: Path, ports: None
) -> None:
    """F04 final-matrix control: a sync-recorded merge is admitted through the public final
    route (side is the synced official position, merge is the recorded head)."""

    contract = _converted_series(tmp_path)
    assert contract.code_repo_path is not None and contract.memory_repo_path is not None
    contract = _finished(contract)
    code, memory = contract.code_repo_path, contract.memory_repo_path
    assert memory is not None
    _git(code, "checkout", "-q", "super")
    (code / "official.txt").write_text("official line\n", encoding="utf-8")
    _git(code, "add", "official.txt")
    _git(code, "commit", "-q", "-m", "official line work")
    position = _git(code, "rev-parse", "super")
    _git(code, "checkout", "-q", "ar/master")
    _git(code, "merge", "-q", "--no-ff", "-m", "managed sync merge", position)
    merge = _git(code, "rev-parse", "ar/master")
    mem_tip = _git(memory, "rev-parse", "ar/master")
    _git(code, "checkout", "-q", "super")
    _git(code, "merge", "-q", "--ff-only", "ar/master")
    _sync_entry(contract, code_to=position, code_head=merge, mem_head=mem_tip)
    contract = _pin_closeout_cells(contract)
    result = integrate_result(
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True),
        load_contract(contract.contract_path),
    )
    assert result.returncode == 0, result.payload
    assert result.payload.get("state") == "would-integrate", result.payload


def _public_leaf(tmp_path: Path) -> WorktreeContract:
    """A finished leaf enclosure over the gated world, ready for public leaf integration.

    Parent series (main -> master) plus leaf (master -> leaf) give the lineage and target
    proof real branch ancestry; the closeout cells pin the leaf tips until a test hand-edits
    them in the contract file.
    """

    world = build_gated(tmp_path / "leaf")
    _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(world, *TRACES))
    for repo in (world.code, world.memory):
        _git(repo, "branch", "ar/gate_case", "main")
        _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "main"))
        _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    answered_mem = commit(world.memory, {})
    assert _git(world.memory, "rev-parse", "leaf") == answered_mem
    sprint_root = world.root / "tasks" / "agents-remember" / "gate_case"
    sprint_root.mkdir(parents=True)
    (sprint_root / "task.md").write_text("# task\n", encoding="utf-8")
    (sprint_root / "task.json").write_text(
        json.dumps(
            {
                "schema": "ar-task-document/v1",
                "id": "GATE-CASE",
                "slug": "gate_case",
                "title": "Gate case",
                "kind": "master",
                "repo": "agents-remember",
                "createdAt": "2026-10-12T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    parent = default_series_contract(
        ContractTask("gate_case", "agents-remember", world.root, "light-task", "external"),
        code=RepoBranchPlan(world.code, "main", "ar/gate_case", world.code_base),
        memory=RepoBranchPlan(world.memory, "main", "ar/gate_case", world.memory_base),
        task_root=sprint_root,
    )
    write_contract(parent.contract_path, parent)
    leaf_root = sprint_root / "enclosures" / LEAF
    leaf_root.mkdir(parents=True)
    leaf = default_series_contract(
        ContractTask(
            "gate_leaf",
            "agents-remember",
            world.root,
            "light-task",
            "external",
            parent_task_name="gate_case",
            parent_contract_path=parent.contract_path,
        ),
        code=RepoBranchPlan(world.code, "ar/gate_case", "leaf", world.code_base),
        memory=RepoBranchPlan(world.memory, "ar/gate_case", "leaf", world.memory_base),
        task_root=sprint_root,
    )
    leaf = replace(
        leaf,
        kind="leaf",
        leaf_id=LEAF,
        contract_path=leaf_root / "series-contract.md",
        task_artifact=sprint_root / "task.md",
        memory_worktree=world.memory,
        ledger_path=world.memory / "memory.md",
    )
    write_contract(leaf.contract_path, leaf)
    publish_new_lifecycle_operation_location(
        leaf, contract_text=leaf.contract_path.read_text(encoding="utf-8")
    )
    leaf = load_contract(leaf.contract_path)
    write_contract(
        leaf.contract_path,
        amend_contract(leaf, ContractCells(closeout_status="completed")),
    )
    code_tip = _git(world.code, "rev-parse", "leaf")
    mem_tip = _git(world.memory, "rev-parse", "leaf")
    closeout_text = leaf.contract_path.read_text(encoding="utf-8")
    closeout_text = re.sub(
        r"closeout:\n(?:  \w+: .*\n?)*",
        f"closeout:\n  status: completed\n  code_commit: {code_tip}\n"
        f"  memory_content_commit: {mem_tip}\n",
        closeout_text,
        count=1,
    )
    closeout_text = closeout_text.replace(
        "  approved_for_commit: no", "  approved_for_commit: yes", 1
    )
    leaf.contract_path.write_text(closeout_text, encoding="utf-8")
    leaf = load_contract(leaf.contract_path)
    assert leaf.approved_for_commit and leaf.code_commit == code_tip
    assert leaf.kind == "leaf" and leaf.leaf_id == LEAF
    return leaf


def test_public_leaf_integration_passes_on_the_unedited_closeout_cells(
    tmp_path: Path, ports: None
) -> None:
    """F04 leaf-integration control: the finished fixture leaf passes the public final route
    (would-integrate), so a refusal after a hand edit is attributable to the edit."""

    leaf = _public_leaf(tmp_path)
    result = integrate_result(
        WorktreeArgs(contract_path=leaf.contract_path, dry_run=True),
        load_contract(leaf.contract_path),
    )
    assert result.returncode == 0, result.payload
    assert result.payload.get("state") == "would-integrate", result.payload


def test_a_hand_edited_leaf_closeout_cell_refuses_through_public_leaf_integration(
    tmp_path: Path, ports: None
) -> None:
    """F04 leaf-integration discriminator: a hand-edited closeout code cell (a stale change
    the closeout never judged) refuses through the public final route instead of landing
    unjudged; the refusal carries the stale anchor the edit broke."""

    leaf = _public_leaf(tmp_path)
    assert leaf.code_repo_path is not None
    stale = commit(leaf.code_repo_path, {A: CODE_A.replace("return value", "return -value")})
    assert _git(leaf.code_repo_path, "rev-parse", "leaf") == stale
    closeout_text = leaf.contract_path.read_text(encoding="utf-8")
    edited = re.sub(
        r"(  code_commit: )[0-9a-f]{40}",
        rf"\g<1>{stale}",
        closeout_text,
        count=1,
    )
    assert edited != closeout_text
    leaf.contract_path.write_text(edited, encoding="utf-8")
    reloaded = load_contract(leaf.contract_path)
    assert reloaded.code_commit == stale
    result = integrate_result(
        WorktreeArgs(contract_path=leaf.contract_path, dry_run=True),
        reloaded,
    )
    assert result.returncode == 2, result.payload
    reason = str(result.payload.get("reason"))
    assert "RLZ-A00001" in reason and "stale" in reason
