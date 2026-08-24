"""Focused forcing for same-address terminal-to-successor enclosure publication."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application.worktree_tools import worktree_abandon_tool
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
    reserve_new_lifecycle_operation_location,
    resolve_lifecycle_operation_location,
    resume_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules import abandon as abandon_module
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_contract,
    load_contract,
    write_contract,
)
from lifecycle_enclosure_test_support import (
    enclosure_contract,
    publish_test_enclosure,
    terminalize_test_enclosure,
)
from test_lifecycle_operations import _contract


def _terminal_abandoned_predecessor(tmp_path: Path):
    predecessor, _ = enclosure_contract(
        tmp_path,
        worktree_name="old-generation",
        leaf_id="L12",
    )
    predecessor = replace(predecessor, cleanup="abandoned", lifecycle_id="LC-OLD")
    text = contract_publication_text(predecessor.contract_path, predecessor)
    location = publish_test_enclosure(predecessor, text)
    terminal = terminalize_test_enclosure(location)
    write_contract(predecessor.contract_path, predecessor)
    return predecessor, terminal


def _successor(tmp_path: Path, worktree_name: str = "new-generation"):
    successor, _ = enclosure_contract(
        tmp_path,
        worktree_name=worktree_name,
        leaf_id="L12",
    )
    successor = replace(successor, lifecycle_id="LC-NEW")
    return successor, contract_publication_text(successor.contract_path, successor)


def _real_successor(predecessor: WorktreeContract) -> tuple[WorktreeContract, str]:
    successor = default_contract(
        ContractTask(
            name=predecessor.task_name,
            repo_name=predecessor.repo_name,
            coordination_root=predecessor.coordination_root,
            workflow_kind=predecessor.workflow_kind,
            memory_mode=predecessor.memory_mode,
            parent_task_name=predecessor.parent_task_name,
            parent_contract_path=predecessor.parent_contract_path,
        ),
        leaf=LeafIdentity(
            worktree_name="durable-lifecycle-successor",
            leaf_id=predecessor.leaf_id,
            lifecycle_id="LC-SUCCESSOR",
        ),
        code=RepoBranchPlan(
            repo_path=predecessor.code_repo_path,
            source_branch=predecessor.code_source_branch,
            work_branch="feature/l23-successor",
            base_commit=predecessor.code_base_commit,
        ),
    )
    return successor, contract_publication_text(successor.contract_path, successor)


def test_fresh_generation_keeps_the_predecessor_absent_from_canonical_bytes(
    tmp_path: Path,
) -> None:
    contract, text = enclosure_contract(tmp_path)
    location = publish_test_enclosure(contract, text)

    assert '"predecessorTerminal"' not in location.locator_path.read_text(encoding="utf-8")
    assert '"predecessorTerminal"' not in location.manifest_path.read_text(encoding="utf-8")


def test_terminal_predecessor_reserves_and_resumes_one_linked_successor(
    tmp_path: Path,
) -> None:
    predecessor, terminal = _terminal_abandoned_predecessor(tmp_path)
    successor, text = _successor(tmp_path)

    reserved = reserve_new_lifecycle_operation_location(
        successor,
        contract_text=text,
        predecessor_contract=predecessor,
    )

    assert reserved.state == "reserved"
    assert reserved.publicationKind == "successor-enclosure"
    assert reserved.predecessorTerminal is not None
    assert reserved.predecessorTerminal.publicationRequestId == terminal.publicationRequestId
    assert Path(reserved.predecessorTerminal.terminalArchivePath).is_file()

    location = resume_new_lifecycle_operation_location(successor, contract_text=text)

    assert location.locator.state == "addressable"
    assert location.manifest.predecessorTerminal == reserved.predecessorTerminal
    assert resume_new_lifecycle_operation_location(successor, contract_text=text) == location


def test_real_abandon_completion_is_the_successor_admission_boundary(tmp_path: Path) -> None:
    predecessor = _contract(tmp_path)
    config = load_config(tmp_path / "settings.json")

    with mock.patch.object(
        abandon_module,
        "write_contract",
        side_effect=RuntimeError("forced cut before terminal contract publication"),
    ):
        cut = worktree_abandon_tool(
            config,
            contract_path=predecessor.contract_path.as_posix(),
            dry_run=False,
            force=False,
        )

    assert cut["ok"] is False
    assert cut["state"] == "abandon-blocked"
    active_predecessor = load_contract(predecessor.contract_path)
    assert active_predecessor.cleanup == "pending"
    successor, text = _real_successor(active_predecessor)
    with pytest.raises(LifecycleOperationLocationError) as refused:
        reserve_new_lifecycle_operation_location(
            successor,
            contract_text=text,
            predecessor_contract=active_predecessor,
        )
    assert refused.value.status == "operation-location-successor-mismatch"

    completed = worktree_abandon_tool(
        config,
        contract_path=predecessor.contract_path.as_posix(),
        dry_run=False,
        force=False,
    )

    assert completed["ok"] is True
    terminal_predecessor = load_contract(predecessor.contract_path)
    assert terminal_predecessor.cleanup == "abandoned"
    reserved = reserve_new_lifecycle_operation_location(
        successor,
        contract_text=text,
        predecessor_contract=terminal_predecessor,
    )
    assert reserved.state == "reserved"
    location = resume_new_lifecycle_operation_location(successor, contract_text=text)
    assert location.locator.state == "addressable"


def test_terminal_predecessor_refuses_unproved_and_conflicting_successors(
    tmp_path: Path,
) -> None:
    predecessor, _ = _terminal_abandoned_predecessor(tmp_path)
    successor, text = _successor(tmp_path)

    with pytest.raises(LifecycleOperationLocationError) as unproved:
        reserve_new_lifecycle_operation_location(successor, contract_text=text)
    assert unproved.value.status == "operation-location-successor-proof-required"

    reserve_new_lifecycle_operation_location(
        successor,
        contract_text=text,
        predecessor_contract=predecessor,
    )
    conflicting, conflicting_text = _successor(tmp_path, "different-generation")
    with pytest.raises(LifecycleOperationLocationError) as conflict:
        reserve_new_lifecycle_operation_location(
            conflicting,
            contract_text=conflicting_text,
            predecessor_contract=predecessor,
        )
    assert conflict.value.status == "operation-location-conflict"


def test_successor_refuses_a_nonrestartable_predecessor_contract(tmp_path: Path) -> None:
    predecessor, _ = _terminal_abandoned_predecessor(tmp_path)
    successor, text = _successor(tmp_path)
    nonrestartable = replace(predecessor, cleanup="completed")

    with pytest.raises(LifecycleOperationLocationError) as refused:
        reserve_new_lifecycle_operation_location(
            successor,
            contract_text=text,
            predecessor_contract=nonrestartable,
        )

    assert refused.value.status == "operation-location-successor-mismatch"


def test_successor_refuses_tampered_terminal_archive_bytes(tmp_path: Path) -> None:
    predecessor, terminal = _terminal_abandoned_predecessor(tmp_path)
    successor, text = _successor(tmp_path)
    Path(terminal.terminalArchivePath or "").write_bytes(b"tampered")

    with pytest.raises(LifecycleOperationLocationError) as refused:
        reserve_new_lifecycle_operation_location(
            successor,
            contract_text=text,
            predecessor_contract=predecessor,
        )

    assert refused.value.status == "operation-location-terminal-proof-mismatch"


def test_successor_refuses_missing_or_mismatched_terminal_receipt(tmp_path: Path) -> None:
    predecessor, terminal = _terminal_abandoned_predecessor(tmp_path)
    successor, text = _successor(tmp_path)
    receipt = Path(terminal.terminalReceiptPath or "")
    receipt.write_text('{"state":"terminal-archived"}\n', encoding="utf-8")

    with pytest.raises(LifecycleOperationLocationError) as refused:
        reserve_new_lifecycle_operation_location(
            successor,
            contract_text=text,
            predecessor_contract=predecessor,
        )

    assert refused.value.status == "operation-location-terminal-proof-invalid"


def test_successor_refuses_terminal_archive_outside_the_canonical_external_address(
    tmp_path: Path,
) -> None:
    predecessor, terminal = _terminal_abandoned_predecessor(tmp_path)
    successor, text = _successor(tmp_path)
    locator_path = (
        successor.coordination_root
        / "controlplane"
        / "lifecycle-enclosures"
        / f"{terminal.locatorId}.json"
    )
    inside = Path(terminal.worktreeGroup) / "terminal-archive.json"
    changed = terminal.model_copy(update={"terminalArchivePath": inside.as_posix()})
    locator_path.write_text(changed.model_dump_json(indent=2) + "\n", encoding="utf-8")

    with pytest.raises(LifecycleOperationLocationError) as refused:
        reserve_new_lifecycle_operation_location(
            successor,
            contract_text=text,
            predecessor_contract=predecessor,
        )

    assert refused.value.status == "operation-location-terminal-proof-mismatch"


def test_successor_locator_cannot_change_the_manifest_predecessor_link(
    tmp_path: Path,
) -> None:
    predecessor, _ = _terminal_abandoned_predecessor(tmp_path)
    successor, text = _successor(tmp_path)
    reserve_new_lifecycle_operation_location(
        successor,
        contract_text=text,
        predecessor_contract=predecessor,
    )
    location = resume_new_lifecycle_operation_location(successor, contract_text=text)
    assert location.locator.predecessorTerminal is not None
    changed_predecessor = location.locator.predecessorTerminal.model_copy(
        update={"publicationRequestId": "0" * 64}
    )
    changed_locator = location.locator.model_copy(
        update={"predecessorTerminal": changed_predecessor}
    )
    location.locator_path.write_text(
        changed_locator.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(LifecycleOperationLocationError) as refused:
        resolve_lifecycle_operation_location(
            successor.coordination_root,
            successor.contract_path,
        )

    assert refused.value.status == "operation-location-mismatch"
