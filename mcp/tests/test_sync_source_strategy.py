"""Each sync source strategy answers every question the transaction asks of its choice.

The contract's own line (``sync_source`` omitted, ``""`` in the journal) moves the base pair to the
pulled sources; the repository default keeps the pair, pins its resolved branch and names its
sources in the sync log. Plain inputs only: the repository default is read through a stub.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.worktrees import sync_source_strategy, sync_transaction_sources
from agents_remember.worktrees.sync_source_strategy import (
    CONTRACT_LINE,
    REPOSITORY_DEFAULT,
    source_strategy,
    sync_log_entry,
)
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError
from agents_remember.worktrees.sync_transaction_sources import recovered_default_branch
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncSideRecord,
    sync_side_default_source_prefix,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

CODE_BASE, MEMORY_BASE = "a" * 40, "b" * 40
CODE_TIP, MEMORY_TIP = "c" * 40, "d" * 40
CODE_HEAD, MEMORY_HEAD = "e" * 40, "f" * 40


def _contract(root: Path, **changes: object) -> WorktreeContract:
    contract = WorktreeContract(
        task_id="task",
        task_name="task",
        repo_name="repo",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=root,
        task_root=root,
        contract_path=root / "contract.md",
        task_artifact=root / "task.md",
        worktree_group=root,
        code_repo_path=root / "code",
        code_source_branch="sprint",
        code_work_branch="work",
        code_base_commit=CODE_BASE,
        code_worktree=root / "code-worktree",
        memory_repo_path=root / "memory",
        memory_source_branch="sprint-memory",
        memory_work_branch="work",
        memory_base_commit=MEMORY_BASE,
        memory_worktree=root / "memory-worktree",
    )
    return replace(contract, **changes)  # type: ignore[arg-type]


def _side(root: Path, side: str, branch: str, tip: str, head: str) -> SyncSideRecord:
    prefix = f"refs/agents-remember/sync/x/{side}"
    return SyncSideRecord.model_validate(
        {
            "side": side,
            "repository": (root / side).as_posix(),
            "worktree": (root / f"{side}-worktree").as_posix(),
            "sourceBranch": branch,
            "workBranch": "work",
            "sourceCommit": tip,
            "preSyncHead": CODE_BASE,
            "baseCommit": CODE_BASE,
            "backupRef": f"{prefix}/backup",
            "sourceBackupRef": f"{prefix}/source",
            "baseBackupRef": f"{prefix}/base",
            "plan": "merge",
            "resultHead": head,
        }
    )


def _record(root: Path, mode: str, branch: str) -> SyncOperationRecord:
    default = bool(mode)
    return SyncOperationRecord.model_validate(
        {
            "generation": 1,
            "contractPath": (root / "contract.md").as_posix(),
            "taskId": "task",
            "contractKind": "leaf",
            "codeBaseFrom": CODE_BASE,
            "memoryBaseFrom": MEMORY_BASE,
            "phase": "finalizing",
            "syncSource": mode,
            "codeSourceFrom": "sprint" if default else "",
            "memorySourceFrom": "sprint-memory" if default else "",
            "code": _side(root, "code", branch, CODE_TIP, CODE_HEAD),
            "memory": _side(root, "memory", branch, MEMORY_TIP, MEMORY_HEAD),
            "createdAt": "now",
            "updatedAt": "now",
        }
    )


@pytest.fixture
def default_is_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_source_strategy, "repository_default_branch", lambda _repo: "main")
    monkeypatch.setattr(
        sync_source_strategy, "memory_repository_default_branch", lambda _repo: "main-memory"
    )


def test_the_choice_is_made_once_from_the_request_or_the_journal() -> None:
    assert source_strategy(None) is CONTRACT_LINE
    assert source_strategy("") is CONTRACT_LINE
    assert source_strategy("repository-default") is REPOSITORY_DEFAULT
    with pytest.raises(ValueError, match="repository-default"):
        source_strategy("sprint")


@pytest.mark.usefixtures("default_is_main")
def test_the_contract_line_moves_the_pair_to_the_pulled_sources(tmp_path: Path) -> None:
    strategy = CONTRACT_LINE
    contract = _contract(tmp_path)
    record = _record(tmp_path, "", "sprint")

    assert strategy.source_branch(contract, "code") == "sprint"
    assert strategy.source_branch(contract, "memory") == "sprint-memory"
    assert strategy.bases_current(contract, record.code, record.memory) is False
    assert strategy.bases_current(
        _contract(tmp_path, code_base_commit=CODE_TIP, memory_base_commit=MEMORY_TIP),
        record.code,
        record.memory,
    )
    assert strategy.target_bases(record) == (CODE_TIP, MEMORY_TIP)
    skipped = record.model_copy(
        update={"memory": record.memory.model_copy(update={"plan": "skip"})}  # type: ignore[union-attr]
    )
    assert strategy.target_bases(skipped) == (CODE_TIP, MEMORY_BASE)
    assert strategy.log_keys(record) == {}
    assert "syncSource" not in sync_log_entry(record)
    assert strategy.pinned_ref(record, record.code) is None
    assert strategy.moved_default(record) is None
    assert strategy.journal_source_branch(record, record.code, "sprint") == "sprint"
    assert strategy.record_fields(contract) == {
        "syncSource": "",
        "codeSourceFrom": "",
        "memorySourceFrom": "",
    }
    assert (strategy.request_args(), strategy.result_keys()) == ({}, {})
    assert strategy.synced_summary.startswith("The worktree base pair now matches")

    # Finalization moves the pair and appends the entry; the undo proves and reverses both.
    assert strategy.finalization_absent(contract, record)
    assert not strategy.finalized(contract, record)
    finalized = replace(
        contract,
        code_base_commit=CODE_TIP,
        memory_base_commit=MEMORY_TIP,
        sync_log=({"at": "now", **sync_log_entry(record)},),
    )
    assert strategy.finalized(finalized, record)
    assert not strategy.finalization_absent(finalized, record)
    assert strategy.without_finalization(finalized, record) == contract
    with pytest.raises(SyncGitProofError, match="not owned by this sync generation"):
        strategy.without_finalization(replace(finalized, sync_log=()), record)


@pytest.mark.usefixtures("default_is_main")
def test_the_repository_default_keeps_the_pair_pins_and_logs_its_sources(
    tmp_path: Path,
) -> None:
    strategy = REPOSITORY_DEFAULT
    contract = _contract(tmp_path)
    record = _record(tmp_path, "repository-default", "main")

    assert strategy.source_branch(contract, "code") == "main"
    assert strategy.source_branch(contract, "memory") == "main-memory"
    assert strategy.bases_current(contract, record.code, record.memory) is True
    assert strategy.target_bases(record) == (CODE_BASE, MEMORY_BASE)
    assert strategy.log_keys(record) == {
        "syncSource": "repository-default",
        "codeSourceBranch": "main",
        "codeSourceCommit": CODE_TIP,
        "memorySourceBranch": "main",
        "memorySourceCommit": MEMORY_TIP,
    }
    prefix = sync_side_default_source_prefix(contract.contract_path, "code")
    assert strategy.pinned_ref(record, record.code) == f"{prefix}main"
    assert strategy.journal_source_branch(record, record.code, "sprint") == "main"
    with pytest.raises(SyncGitProofError) as retargeted:
        strategy.journal_source_branch(record, record.code, "elsewhere")
    assert "admitted beside the contract's source branch sprint" in str(retargeted.value)
    assert "now names elsewhere" in str(retargeted.value)
    assert str(retargeted.value).endswith("worktree_sync and resolution_action='cancel'.")
    assert strategy.record_fields(contract) == {
        "syncSource": "repository-default",
        "codeSourceFrom": "sprint",
        "memorySourceFrom": "sprint-memory",
    }
    assert strategy.request_args() == {"sync_source": "repository-default"}
    assert strategy.result_keys() == {"syncSource": "repository-default"}
    assert "repository-default line" in strategy.synced_summary

    # The recorded code default is "main", the memory default reads "main-memory": moved.
    moved = strategy.moved_default(record)
    assert moved is not None and "memory repository default is now main-memory" in moved
    on_default = record.model_copy(
        update={"memory": record.memory.model_copy(update={"sourceBranch": "main-memory"})}  # type: ignore[union-attr]
    )
    assert strategy.moved_default(on_default) is None

    # Finalization appends only the entry; the undo removes only this generation's entry.
    assert not strategy.finalization_absent(contract, record)
    assert not strategy.finalized(contract, record)
    finalized = replace(contract, sync_log=({"at": "now", **sync_log_entry(record)},))
    assert strategy.finalized(finalized, record)
    assert strategy.without_finalization(finalized, record) == contract
    assert strategy.without_finalization(contract, record) == contract


def test_inconsistent_recovery_refs_name_what_was_found_and_the_way_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract_path = tmp_path / "contract.md"
    found = {"main": CODE_TIP, "other": CODE_TIP}
    monkeypatch.setattr(sync_transaction_sources, "default_source_refs", lambda *_: found)

    with pytest.raises(SyncGitProofError) as inconsistent:
        recovered_default_branch(tmp_path, contract_path, "code", CODE_TIP)

    prefix = sync_side_default_source_prefix(contract_path, "code")
    message = str(inconsistent.value)
    assert f"found {prefix}main at {CODE_TIP}, {prefix}other at {CODE_TIP}" in message
    assert f"expected at most one ref under {prefix}, at the pinned source commit" in message
    assert message.endswith("then run worktree_sync with resolution_action='cancel'.")

    found.pop("other")
    assert recovered_default_branch(tmp_path, contract_path, "code", CODE_TIP) == "main"
    found.clear()
    assert recovered_default_branch(tmp_path, contract_path, "code", CODE_TIP) is None
