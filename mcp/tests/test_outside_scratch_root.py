"""MIK-R207: a task's outside scratch belongs to the task and goes with it.

Every case here is the requirement's own statement: one root per enclosure generation,
allocated at publication and recorded before the enclosure exists; handed to roles; recreated
when ageing removes it; refused when it is somebody else's or a link; deleted only with the
enclosure, or by a reconcile that proves the generation ended from retained records alone.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import stat
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.application.runtime import startup
from agents_remember.cli.role_launch_workspace import (
    _ensure_leaf_root,
    _outside_scratch_field,
)
from agents_remember.models.lifecycles.enclosure import (
    LifecycleEnclosureManifest,
    TerminalEnclosurePredecessor,
    TerminalEnclosureReceipt,
)
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.integration.lifecycle import (
    outside_scratch_reconcile,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    terminal_enclosure_archive_paths,
    validate_terminal_proof,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_binding import (
    model_text,
)
from agents_remember.worktrees.integration.terminal_cleanup_completion import (
    write_completion_record as _write_completion_record,
)
from agents_remember.worktrees.modules.cleanup_report import cleanup_report
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalResult,
    terminal_result_blockers,
)
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    contract_publication_text,
    load_contract,
    parse_contract_text,
)
from lifecycle_enclosure_test_support import (
    discard_owned_roots,
    enclosure_contract,
    publish_test_enclosure,
    terminalize_test_enclosure,
)

MANIFEST_RELATIVE = Path(".lifecycle") / "enclosure-manifest.json"


@pytest.fixture(autouse=True)
def _no_root_outlives_the_case() -> Iterator[None]:
    """A case that allocates in the product parent removes what it allocated, whatever it asserted."""

    before = {item["path"] for item in outside_scratch.list_roots()}
    yield
    for item in outside_scratch.list_roots():
        if item["path"] in before:
            continue
        marker = item.get("marker")
        if not isinstance(marker, dict):
            continue
        record = outside_scratch.record_from_marker(marker)
        if record is not None:
            outside_scratch.remove_root(record)


@pytest.fixture(autouse=True)
def _socket_room(monkeypatch: pytest.MonkeyPatch) -> None:
    """The product's parent is 22 characters; a pytest tmp_path is not.

    The socket budget is a rule about the real parent, so these cases measure the rule itself
    in `test_a_root_too_long_for_a_sandbox_socket_is_refused` and give every other case room.
    """

    monkeypatch.setattr(outside_scratch, "SOCKET_PATH_LIMIT", 4096)


def _located(
    tmp_path: Path, *, predecessor: str = ""
) -> tuple[outside_scratch.OutsideScratchRoot, WorktreeContract, Path]:
    """One contract, the root one publication of it owns, and the private parent."""

    contract, _ = enclosure_contract(tmp_path)
    parent = tmp_path / "outside"
    record = outside_scratch.record_for(contract, predecessor_generation=predecessor, parent=parent)
    return record, contract, parent


def _contract_recording(
    contract: WorktreeContract, record: outside_scratch.OutsideScratchRoot
) -> WorktreeContract:
    """The contract carrying an explicitly located record (this test's private parent)."""

    return dataclasses.replace(
        contract,
        outside_scratch_root=record.root.as_posix(),
        outside_scratch_generation=record.generation,
        outside_scratch_marker_sha256=record.marker_sha256,
    )


def test_the_generation_is_derived_and_the_root_stays_short(tmp_path: Path) -> None:
    record, contract, parent = _located(tmp_path)

    assert outside_scratch.record_for(contract, parent=parent) == record
    successor = outside_scratch.record_for(
        contract, predecessor_generation=record.generation, parent=parent
    )

    assert successor.root != record.root, "a successor generation never reuses a root"
    assert record.root.name == f"ar-{record.generation[:12]}"


def test_a_recorded_root_survives_the_contract_text(tmp_path: Path) -> None:
    contract, _ = enclosure_contract(tmp_path)
    recorded = outside_scratch.with_root_record(contract)
    text = contract_publication_text(contract.contract_path, recorded)

    parsed = parse_contract_text(text, path=contract.contract_path)
    assert parsed.outside_scratch_root == recorded.outside_scratch_root
    assert parsed.outside_scratch_generation == recorded.outside_scratch_generation
    assert parsed.outside_scratch_marker_sha256 == recorded.outside_scratch_marker_sha256
    assert outside_scratch.recorded_root(parsed) is not None

    # A retry of the same generation reuses the record instead of founding a second root.
    assert outside_scratch.with_root_record(recorded) == recorded


def test_a_pre_change_contract_records_no_root_and_materialises_nothing(tmp_path: Path) -> None:
    contract, text = enclosure_contract(tmp_path)

    assert "outside_scratch" not in text
    parsed = parse_contract_text(text, path=contract.contract_path)
    assert parsed.outside_scratch_root == ""
    assert outside_scratch.recorded_root(parsed) is None
    assert outside_scratch.removal_entry_for(parsed, dry_run=False) == {}
    assert outside_scratch.terminal_blocker(parsed, dry_run=False) == (None, [])


def test_publishing_an_enclosure_allocates_its_root_before_its_contract(
    tmp_path: Path,
) -> None:
    contract, _ = enclosure_contract(tmp_path)
    recorded = outside_scratch.with_root_record(contract)
    location = publish_test_enclosure(
        recorded, contract_publication_text(contract.contract_path, recorded)
    )
    root = Path(recorded.outside_scratch_root)
    try:
        manifest = LifecycleEnclosureManifest.model_validate_json(
            (location.worktree_group / MANIFEST_RELATIVE).read_bytes()
        )
        assert manifest.outsideScratch is not None
        assert manifest.outsideScratch.path == recorded.outside_scratch_root
        assert manifest.outsideScratch.generation == recorded.outside_scratch_generation
        assert manifest.outsideScratch.markerSha256 == recorded.outside_scratch_marker_sha256
        assert root.is_dir() and not root.is_symlink()
        assert stat.S_IMODE(root.stat().st_mode) == outside_scratch.MANAGED_MODE
        marker = outside_scratch.read_marker(root)
        assert marker is not None
        assert marker["schema"] == outside_scratch.OUTSIDE_SCRATCH_SCHEMA
        # The published manifest is the record a crash-retry must reuse, not reinvent.
        assert outside_scratch.published_root_record(recorded) == outside_scratch.record_for(
            recorded
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_pre_change_publication_writes_no_new_manifest_field(tmp_path: Path) -> None:
    contract, text = enclosure_contract(tmp_path)
    location = publish_test_enclosure(contract, text)
    try:
        published = (location.worktree_group / MANIFEST_RELATIVE).read_bytes()
        assert b"outsideScratch" not in published
        assert (
            outside_scratch.recorded_root(parse_contract_text(text, path=contract.contract_path))
            is None
        )
    finally:
        shutil.rmtree(location.worktree_group, ignore_errors=True)


def test_a_root_that_ageing_removed_is_recreated_with_the_same_digest(tmp_path: Path) -> None:
    record, _, parent = _located(tmp_path)

    created = outside_scratch.ensure_root(record, parent=parent)
    digest = outside_scratch.marker_identity(record.root)
    shutil.rmtree(record.root)
    outside_scratch.ensure_root(record, parent=parent)

    assert created["state"] == "created"
    assert outside_scratch.marker_identity(record.root) == digest == record.marker_sha256
    assert outside_scratch.ensure_root(record, parent=parent)["state"] == "present"


def test_a_root_that_is_a_link_or_escapes_the_parent_is_never_removed(tmp_path: Path) -> None:
    record, _, parent = _located(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    parent.mkdir(mode=outside_scratch.MANAGED_MODE)
    record.root.symlink_to(elsewhere, target_is_directory=True)

    entry = outside_scratch.remove_root(record, parent=parent)

    assert entry["removed"] is False
    assert entry["reason"] == "symlink"
    assert elsewhere.is_dir(), "a linked root is reported, never followed and deleted"
    with pytest.raises(outside_scratch.OutsideScratchError):
        outside_scratch.ensure_root(record, parent=parent)

    record.root.unlink()
    other = tmp_path / "other"
    other.mkdir(mode=outside_scratch.MANAGED_MODE)
    os.chmod(other, outside_scratch.MANAGED_MODE)
    escaping = outside_scratch.remove_root(record, parent=other)
    assert escaping["removed"] is False
    assert escaping["reason"] == "outside-the-managed-parent"
    absent = outside_scratch.remove_root(record, parent=tmp_path / "absent")
    assert absent["removed"] is False
    assert absent["reason"] == "parent-unusable"


def test_allocation_and_removal_refuse_a_redirected_or_foreign_parent(
    tmp_path: Path,
) -> None:
    """F05: the parent itself is validated before any mutation, at both ends of the life."""

    record, _, parent = _located(tmp_path)
    parent.mkdir(mode=outside_scratch.MANAGED_MODE, exist_ok=True)
    os.chmod(parent, outside_scratch.MANAGED_MODE)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    linked = tmp_path / "linked-parent"
    linked.symlink_to(parent, target_is_directory=True)
    with pytest.raises(outside_scratch.OutsideScratchError):
        outside_scratch.ensure_root(record, parent=linked)
    entry = outside_scratch.remove_root(record, parent=linked)
    assert entry["reason"] == "parent-unusable"
    assert not record.root.exists(), "a redirected parent allocates and removes nothing"

    os.chmod(parent, 0o755)
    try:
        with pytest.raises(outside_scratch.OutsideScratchError):
            outside_scratch.ensure_root(record, parent=parent)
        assert outside_scratch.remove_root(record, parent=parent)["reason"] == "parent-unusable"
    finally:
        os.chmod(parent, outside_scratch.MANAGED_MODE)


def test_a_conflicting_generation_tree_is_refused_and_left_intact(tmp_path: Path) -> None:
    """F05: a complete marker of another generation is a refusal, not permission to clear data."""

    record, _, parent = _located(tmp_path)
    parent.mkdir(mode=outside_scratch.MANAGED_MODE, exist_ok=True)
    os.chmod(parent, outside_scratch.MANAGED_MODE)
    record.root.mkdir(mode=outside_scratch.MANAGED_MODE)
    stranger = replace(record, generation="b" * 64)
    (record.root / outside_scratch.MARKER_NAME).write_text(
        outside_scratch.marker_text(stranger), encoding="utf-8"
    )
    sentinel = record.root / "someone-elses-work"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(outside_scratch.OutsideScratchError, match="carries the marker"):
        outside_scratch.ensure_root(record, parent=parent)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert outside_scratch.read_marker(record.root) == json.loads(
        outside_scratch.marker_text(stranger)
    )


def test_a_root_too_long_for_a_sandbox_socket_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(outside_scratch, "SOCKET_PATH_LIMIT", 108)

    record, _, parent = _located(tmp_path)

    assert outside_scratch.socket_path_bytes(record.root) >= 108
    with pytest.raises(outside_scratch.OutsideScratchError, match="platform admits"):
        outside_scratch.ensure_root(record, parent=parent)


def test_the_managed_parent_is_private_and_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "outside"
    parent.mkdir(mode=0o755)

    with pytest.raises(outside_scratch.OutsideScratchError, match="mode"):
        outside_scratch.prepare_parent(parent)

    os.chmod(parent, outside_scratch.MANAGED_MODE)
    assert outside_scratch.prepare_parent(parent) == parent

    link = tmp_path / "link"
    link.symlink_to(parent, target_is_directory=True)
    with pytest.raises(outside_scratch.OutsideScratchError, match="symlink"):
        outside_scratch.prepare_parent(link)

    real_uid = os.getuid
    monkeypatch.setattr(os, "getuid", lambda: real_uid() + 1)
    with pytest.raises(outside_scratch.OutsideScratchError, match="belongs to uid"):
        outside_scratch.prepare_parent(parent)


def test_a_recorded_root_outside_the_managed_parent_is_never_removed(tmp_path: Path) -> None:
    record, contract, _ = _located(tmp_path)
    recorded = _contract_recording(contract, record)
    outside_scratch.ensure_root(record, parent=record.root.parent)

    entry = outside_scratch.removal_entry_for(recorded, dry_run=False)["outside_scratch"]

    assert entry["removed"] is False
    assert entry["reason"] == "outside-the-managed-parent"
    assert record.root.is_dir(), "a root elsewhere on disk is somebody else's to remove"


def test_a_dry_run_reports_the_removal_it_would_make(tmp_path: Path) -> None:
    record, _, parent = _located(tmp_path)
    outside_scratch.ensure_root(record, parent=parent)

    entry = outside_scratch.remove_root(record, dry_run=True, parent=parent)

    assert entry["would_remove"] is True and entry["removed"] is False
    assert record.root.is_dir()


def test_a_root_taken_by_another_remover_is_answered_as_already_absent(
    tmp_path: Path,
) -> None:
    """Two removers race -- the reconcile of a parallel run or the system ageing -- and neither lies."""

    contract, _ = enclosure_contract(tmp_path)
    record = outside_scratch.recorded_root(outside_scratch.with_root_record(contract))
    assert record is not None
    outside_scratch.ensure_root(record)
    try:

        def taken(_path: Path) -> None:
            raise FileNotFoundError(2, "No such file or directory", str(record.root))

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(outside_scratch, "_remove_tree", taken)
            entry = outside_scratch.remove_root(record)

        assert entry == {
            "path": record.root.as_posix(),
            "removed": False,
            "reason": "already-absent",
        }
    finally:
        outside_scratch.remove_root(record)


def test_an_aged_away_root_is_not_a_blocker_and_a_remaining_one_is(tmp_path: Path) -> None:
    """MIK-R207 rule 2: the root is disposable; rule 4: a remaining root blocks the answer."""

    def result(entry: dict[str, object], *, preview: bool = False) -> TerminalResult:
        return TerminalResult(
            providers={},
            worktrees={},
            branches={},
            directories={"outside_scratch": entry},
            preview=preview,
        )

    assert terminal_result_blockers(result({"path": "/x", "reason": "already-absent"})) == []
    assert terminal_result_blockers(result({"path": "/x", "removed": True})) == []
    assert terminal_result_blockers(result({"path": "/x", "reason": "symlink"})) == [
        {"directory": "outside_scratch", "reason": "symlink"}
    ]
    preview = result({"path": "/x", "would_remove": True, "removed": False}, preview=True)
    assert terminal_result_blockers(preview) == []


def test_a_completed_retry_removes_a_root_that_remains_and_blocks_on_one_it_cannot(
    tmp_path: Path,
) -> None:
    record, contract, parent = _located(tmp_path)
    outside_scratch.ensure_root(record, parent=parent)
    recorded = _contract_recording(contract, record)

    entry, blockers = outside_scratch.terminal_blocker(recorded, dry_run=False, parent=parent)

    assert entry is not None and entry["removed"] is True
    assert blockers == []
    assert not record.root.exists()

    # A root it cannot remove is a blocker: the run never answers cleaned while it stays.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    record.root.symlink_to(elsewhere, target_is_directory=True)
    entry, blockers = outside_scratch.terminal_blocker(recorded, dry_run=False, parent=parent)
    assert blockers and blockers[0]["outsideScratch"] == record.root.as_posix()


def test_the_reconcile_deletes_only_a_root_it_can_prove_is_finished(tmp_path: Path) -> None:
    parent = tmp_path / "outside"
    parent.mkdir(mode=outside_scratch.MANAGED_MODE)

    def make(name: str) -> Path:
        root = parent / f"ar-{name:0<12}"[:15]
        root.mkdir(mode=outside_scratch.MANAGED_MODE)
        (root / outside_scratch.MARKER_NAME).write_text(
            json.dumps(
                {
                    "schema": outside_scratch.OUTSIDE_SCRATCH_SCHEMA,
                    "repository": "repo",
                    "coordinationRoot": tmp_path.as_posix(),
                    "contractPath": f"/coord/{name}/series-contract.md",
                    "generation": "a" * 64,
                    "path": root.as_posix(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return root

    finished = make("finished")
    active = make("active")
    unknown = parent / "ar-unknown"
    unknown.mkdir()
    misnamed = make("misnamed")
    (misnamed / outside_scratch.MARKER_NAME).write_text(
        json.dumps({"schema": outside_scratch.OUTSIDE_SCRATCH_SCHEMA, "path": "/elsewhere"}),
        encoding="utf-8",
    )
    linked_target = tmp_path / "target"
    linked_target.mkdir()
    linked = parent / "ar-linked"
    linked.symlink_to(linked_target, target_is_directory=True)

    marker = outside_scratch.read_marker(finished)
    assert marker is not None
    assert outside_scratch.record_from_marker(marker) == outside_scratch.OutsideScratchRoot(
        root=finished,
        generation="a" * 64,
        marker_sha256=outside_scratch.marker_identity(finished),
        repository="repo",
        coordination_root=tmp_path,
        contract_path=Path("/coord/finished/series-contract.md"),
    )

    report = outside_scratch.reconcile(
        ended=lambda marker: "/finished/" in str(marker.get("contractPath")),
        parent=parent,
    )

    assert [item["path"] for item in report["removed"]] == [finished.as_posix()]
    reasons = {item["path"]: item["reason"] for item in report["reported"]}
    assert reasons[active.as_posix()] == "generation-not-ended"
    assert reasons[unknown.as_posix()] == "marker-missing-or-unreadable"
    assert reasons[misnamed.as_posix()] == "marker-names-another-path"
    assert reasons[linked.as_posix()] == "symlink"
    assert active.is_dir() and unknown.is_dir() and linked_target.is_dir()


def _completed_enclosure(tmp_path: Path) -> tuple[WorktreeContract, Path]:
    """A published enclosure whose generation reached the completed cleanup state.

    The archive is published before the enclosure is collected, so the root outlives it; the
    surviving contract is then moved through the sanctioned cleanup progression, which is what a
    finished cleanup leaves behind.
    """

    contract, _ = enclosure_contract(tmp_path)
    recorded = outside_scratch.with_root_record(contract)
    location = publish_test_enclosure(
        recorded, contract_publication_text(recorded.contract_path, recorded)
    )
    root = Path(recorded.outside_scratch_root)
    locator = terminalize_test_enclosure(location)
    archive = validate_terminal_proof(recorded.coordination_root, recorded.contract_path, locator)
    assert archive is not None
    cell = "completed" if archive.cleanupOperation == "worktree_cleanup" else "abandoned"
    completed = amend_contract(load_contract(recorded.contract_path), ContractCells(cleanup=cell))
    recorded.contract_path.write_text(
        contract_publication_text(recorded.contract_path, completed), encoding="utf-8"
    )
    return completed, root


def test_the_terminal_owner_proves_an_ended_generation_once_its_cleanup_completes(
    tmp_path: Path,
) -> None:
    """F01: the archive alone is archive-ready; the completed contract is the proof to delete."""

    completed, root = _completed_enclosure(tmp_path)
    try:
        assert root.is_dir()

        report = outside_scratch_reconcile.reconcile_outside_scratch_roots()

        assert root.as_posix() in [item["path"] for item in report["removed"]]
        assert not root.exists()
        assert completed.cleanup in {"completed", "abandoned"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_archive_ready_generation_keeps_its_root(tmp_path: Path) -> None:
    """F01: an archive that is published before the destructive outputs never authorizes one."""

    contract, _ = enclosure_contract(tmp_path)
    recorded = outside_scratch.with_root_record(contract)
    location = publish_test_enclosure(
        recorded, contract_publication_text(recorded.contract_path, recorded)
    )
    root = Path(recorded.outside_scratch_root)
    try:
        terminalize_test_enclosure(location)
        assert root.is_dir()

        report = outside_scratch_reconcile.reconcile_outside_scratch_roots()

        assert root.as_posix() not in [item["path"] for item in report["removed"]]
        assert {"path": root.as_posix(), "reason": "generation-not-ended"} in report["reported"]
        assert root.is_dir()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_successor_proves_its_predecessor_only_through_its_completion(tmp_path: Path) -> None:
    """F06: the retained predecessor is proved by its completed cleanup, not by a gone group.

    The successor's manifest is the production record with its own predecessor link, and the
    archive and receipt are the ones the terminal owner wrote. The predecessor's cleanup then
    completes -- which is what leaves the completion record -- and its group goes away. The same
    evidence with the record removed must prove nothing, or a vanished group would be doing the
    work rather than completion.
    """

    completed, predecessor_root = _completed_enclosure(tmp_path)
    observed_locator = outside_scratch_reconcile.inspect_lifecycle_operation_locator(
        completed.coordination_root, completed.contract_path
    ).locator
    assert observed_locator is not None
    archive = validate_terminal_proof(
        completed.coordination_root,
        completed.contract_path,
        observed_locator,
    )
    assert archive is not None
    _, receipt_path = terminal_enclosure_archive_paths(
        completed.coordination_root, archive.locator.publicationRequestId
    )
    receipt = TerminalEnclosureReceipt.model_validate_json(receipt_path.read_text(encoding="utf-8"))
    link = TerminalEnclosurePredecessor(
        publicationRequestId=receipt.publicationRequestId,
        bindingFingerprint=receipt.bindingFingerprint,
        worktreeGroup=receipt.worktreeGroup,
        manifestPath=receipt.manifestPath,
        expectedManifestSha256=receipt.expectedManifestSha256,
        expectedInitialContractSha256=receipt.expectedInitialContractSha256,
        terminalArchivePath=receipt.terminalArchivePath,
        terminalArchiveSha256=receipt.terminalArchiveSha256,
        terminalReceiptPath=receipt.terminalReceiptPath,
    )
    successor_manifest_path = tmp_path / "successor" / "enclosure-manifest.json"
    successor_manifest_path.parent.mkdir(parents=True)
    successor_manifest_path.write_text(
        model_text(
            archive.manifest.model_copy(
                update={
                    "locatorId": "c" * 64,
                    "publicationRequestId": "d" * 64,
                    "publicationKind": "successor-enclosure",
                    "predecessorTerminal": link,
                }
            )
        ),
        encoding="utf-8",
    )
    live = archive.locator.model_copy(update={"manifestPath": successor_manifest_path.as_posix()})
    record_path = _write_completion_record(completed, completed, operation=archive.cleanupOperation)
    assert record_path
    shutil.rmtree(completed.worktree_group, ignore_errors=True)
    # The successor takes the address: the contract standing there is no longer the predecessor's
    # completed form, so the live address can no longer answer for the predecessor generation.
    completed.contract_path.write_text(
        contract_publication_text(
            completed.contract_path,
            amend_contract(completed, ContractCells(cleanup="pending")),
        ),
        encoding="utf-8",
    )
    try:
        proven = outside_scratch_reconcile._ended_predecessors(
            completed.coordination_root, completed.contract_path, live
        )
        assert [item.root.as_posix() for item in proven] == [predecessor_root.as_posix()]

        Path(record_path).unlink()
        assert (
            outside_scratch_reconcile._ended_predecessors(
                completed.coordination_root, completed.contract_path, live
            )
            == []
        )
    finally:
        shutil.rmtree(predecessor_root, ignore_errors=True)


def test_an_interrupted_publication_is_swept_rather_than_left_behind(tmp_path: Path) -> None:
    """F02: the partial branch is reachable -- a marker under a ``.partial-`` name is swept.

    A publication writes the marker into a staging directory and renames it into place, so the
    work an interruption leaves behind is a real marker under a dot name. A sweep that skipped
    every dot name could never reclaim it, and the branch that exists for it would be dead code.
    """

    record, _contract, parent = _located(tmp_path)
    outside_scratch.prepare_parent(parent)
    staging = parent / f"{outside_scratch.PARTIAL_PREFIX}{record.generation[:12]}-{os.getpid()}"
    staging.mkdir(mode=outside_scratch.MANAGED_MODE)
    os.chmod(staging, outside_scratch.MANAGED_MODE)
    (staging / outside_scratch.MARKER_NAME).write_text(
        outside_scratch.marker_text(record), encoding="utf-8"
    )

    page = outside_scratch.scan_roots(parent)

    assert [item["path"] for item in page["roots"]] == [staging.as_posix()]
    assert page["roots"][0]["partial"] is True

    report = outside_scratch.reconcile(ended=lambda marker: True, parent=parent)

    assert [item["path"] for item in report["removed"]] == [staging.as_posix()]
    assert report["removed"][0]["partial"] is True
    assert not staging.exists()


def test_a_refused_parent_leaves_the_reconcile_cursor_untouched(tmp_path: Path) -> None:
    """F05: the refusal covers cursor bookkeeping inside the parent, not only the root.

    The cursor is this product's own file under the managed parent. A pass over a parent that
    fails the gate must not write it, rewrite it or unlink it, and must still record its
    bookkeeping once the parent is valid again.
    """

    record, _contract, parent = _located(tmp_path)
    outside_scratch.prepare_parent(parent)
    root = record.root
    root.mkdir(mode=outside_scratch.MANAGED_MODE)
    os.chmod(root, outside_scratch.MANAGED_MODE)
    (root / outside_scratch.MARKER_NAME).write_text(
        outside_scratch.marker_text(record), encoding="utf-8"
    )
    cursor = parent / outside_scratch.RECONCILE_CURSOR
    cursor.write_text("", encoding="utf-8")

    os.chmod(parent, 0o755)
    try:
        refused = outside_scratch.reconcile(ended=lambda _marker: True, parent=parent)
        assert [entry["reason"] for entry in refused["reported"]] == ["parent-unusable"]
        assert cursor.exists(), "a refused parent keeps the cursor file it found, empty or not"
        assert cursor.read_text(encoding="utf-8") == "", (
            "a refused parent writes nothing back into its own bookkeeping"
        )
    finally:
        os.chmod(parent, outside_scratch.MANAGED_MODE)

    swept = outside_scratch.reconcile(ended=lambda _marker: True, parent=parent)
    assert [entry["path"] for entry in swept["removed"]] == [root.as_posix()]
    assert not cursor.exists(), "a completed pass over a valid parent still retires its cursor"


def test_a_partial_root_is_not_deleted_through_a_refused_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F05: the parent gate governs the partial branch too, and refusal comes before mutation.

    A partial root is this product's own interrupted publication, but the parent it sits under is
    validated first: a wrong-mode or foreign-owned parent reports every entry and deletes none.
    Under a valid parent the same root is still swept, so the recovery rule keeps its meaning.
    """

    record, _contract, parent = _located(tmp_path)
    outside_scratch.prepare_parent(parent)
    staging = parent / f"{outside_scratch.PARTIAL_PREFIX}{record.generation[:12]}-{os.getpid()}"
    staging.mkdir(mode=outside_scratch.MANAGED_MODE)
    os.chmod(staging, outside_scratch.MANAGED_MODE)
    marker = staging / outside_scratch.MARKER_NAME
    marker.write_text(outside_scratch.marker_text(record), encoding="utf-8")

    os.chmod(parent, 0o755)
    try:
        refused = outside_scratch.reconcile(ended=lambda _marker: True, parent=parent)
        assert refused["removed"] == []
        assert [entry["reason"] for entry in refused["reported"]] == ["parent-unusable"]
        assert marker.is_file(), "a partial root under a refused parent is reported, not deleted"
    finally:
        os.chmod(parent, outside_scratch.MANAGED_MODE)

    real_uid = os.getuid
    monkeypatch.setattr(os, "getuid", lambda: real_uid() + 1)
    foreign = outside_scratch.reconcile(ended=lambda _marker: True, parent=parent)
    assert foreign["removed"] == []
    assert [entry["reason"] for entry in foreign["reported"]] == ["parent-unusable"]
    assert marker.is_file(), "a foreign-owned parent is refused before the partial branch runs"
    monkeypatch.setattr(os, "getuid", real_uid)

    swept = outside_scratch.reconcile(ended=lambda _marker: True, parent=parent)
    assert [entry["path"] for entry in swept["removed"]] == [staging.as_posix()]
    assert swept["removed"][0]["partial"] is True
    assert not staging.exists()


def test_a_symlinked_partial_entry_is_reported_and_never_followed(tmp_path: Path) -> None:
    """F05: the partial prefix is not a licence to follow a link out of the parent."""

    record, _contract, parent = _located(tmp_path)
    outside_scratch.prepare_parent(parent)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / outside_scratch.MARKER_NAME).write_text(
        outside_scratch.marker_text(record), encoding="utf-8"
    )
    staging = parent / f"{outside_scratch.PARTIAL_PREFIX}{record.generation[:12]}-{os.getpid()}"
    staging.symlink_to(elsewhere, target_is_directory=True)

    report = outside_scratch.reconcile(ended=lambda _marker: True, parent=parent)

    assert report["removed"] == []
    assert [entry["reason"] for entry in report["reported"]] == ["symlink"]
    assert (elsewhere / outside_scratch.MARKER_NAME).is_file()


def test_removal_refuses_a_tree_of_another_generation(tmp_path: Path) -> None:
    """F05: the removal path reads the marker, so a foreign generation is reported, not deleted."""

    record, _contract, parent = _located(tmp_path)
    outside_scratch.prepare_parent(parent)
    outside_scratch.ensure_root(record, parent=parent)
    sentinel = record.root / "keep.txt"
    sentinel.write_text("the tree is not ours\n", encoding="utf-8")
    marker_path = record.root / outside_scratch.MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["generation"] = "f" * 64
    marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        entry = outside_scratch.remove_root(record, parent=parent)

        assert entry == {
            "path": record.root.as_posix(),
            "removed": False,
            "reason": "marker-names-another-generation",
        }
        assert sentinel.is_file()

        marker_path.unlink()
        entry = outside_scratch.remove_root(record, parent=parent)

        assert entry["removed"] is False
        assert entry["reason"] == "marker-missing-or-unreadable"
        assert sentinel.is_file()
    finally:
        shutil.rmtree(record.root, ignore_errors=True)


def test_paging_takes_every_root_in_name_order_however_the_stream_yields_them(
    tmp_path: Path,
) -> None:
    """F07: a page smaller than the parent still reaches every root; no name starves behind it."""

    parent = tmp_path / "outside"
    outside_scratch.prepare_parent(parent)
    later = parent / "ar-zzzzzzzzzzzz"
    earlier = parent / "ar-aaaaaaaaaaaa"
    later.mkdir()
    earlier.mkdir()

    first = outside_scratch.scan_roots(parent, limit=1)
    second = outside_scratch.scan_roots(parent, after=first["after"], limit=1)

    assert [Path(item["path"]).name for item in first["roots"]] == [earlier.name]
    assert first["complete"] is False
    assert [Path(item["path"]).name for item in second["roots"]] == [later.name]
    assert second["complete"] is True


def test_a_parent_larger_than_one_pass_is_walked_to_its_end(tmp_path: Path) -> None:
    """F07: more roots than one page are all reached, and the cursor is the last name taken."""

    parent = tmp_path / "outside"
    outside_scratch.prepare_parent(parent)
    names = [f"ar-{index:04d}" for index in range(outside_scratch.MAX_ROOTS + 8)]
    for name in names:
        (parent / name).mkdir()

    seen: list[str] = []
    after = ""
    passes = 0
    while True:
        page = outside_scratch.scan_roots(parent, after=after, limit=outside_scratch.MAX_ROOTS)
        seen.extend(Path(item["path"]).name for item in page["roots"])
        passes += 1
        if page["complete"]:
            break
        after = page["after"]

    assert seen == names
    assert passes == 2


def test_an_already_clean_answer_keeps_its_removal_in_the_report(tmp_path: Path) -> None:
    """F08: the already-clean path publishes ``directories: {}`` with a top-level removal.

    That is the shape the real operation emits, and reading only the nested directory shape
    dropped a removal the operation really performed.
    """

    record, contract, _parent = _located(tmp_path)
    recorded = _contract_recording(contract, record)
    payload: dict[str, object] = {
        "state": "already-clean",
        "directories": {},
        "outsideScratch": {"path": record.root.as_posix(), "removed": True},
    }

    inventory = cast("dict[str, Any]", cleanup_report(recorded, payload))

    assert inventory["removed"]["outsideScratch"] == [
        {"path": record.root.as_posix(), "removed": True}
    ]
    assert inventory["notRemoved"]["outsideScratch"] == []
    assert "outside scratch root" in inventory["summary"]
    assert "nothing was left in place" in inventory["summary"]


def test_a_sandbox_use_of_the_root_ends_with_the_enclosure(tmp_path: Path) -> None:
    """One end-to-end run: publish, a sandbox lays its tree in the root, the sweep removes it.

    The sandbox tier is represented by the shape it writes -- ``run/tmux/tmux-<uid>/default``
    and a package install below it -- because a live ``pnt_sandbox`` launch needs tmux and a
    seat; the publication, the terminal archive and the sweep are the real ones.
    """

    contract, _ = enclosure_contract(tmp_path)
    recorded = outside_scratch.with_root_record(contract)
    location = publish_test_enclosure(
        recorded, contract_publication_text(contract.contract_path, recorded)
    )
    root = Path(recorded.outside_scratch_root)
    try:
        sandbox = root / "run" / "tmux" / "tmux-1000"
        sandbox.mkdir(parents=True)
        (sandbox / "default").write_text("socket", encoding="utf-8")
        packages = root / "run" / "pi-packages"
        packages.mkdir(parents=True, exist_ok=True)
        (packages / "sentinel").write_text("installed", encoding="utf-8")

        locator = terminalize_test_enclosure(location)
        archive = validate_terminal_proof(
            recorded.coordination_root, recorded.contract_path, locator
        )
        assert archive is not None
        cell = "completed" if archive.cleanupOperation == "worktree_cleanup" else "abandoned"
        completed = amend_contract(
            load_contract(recorded.contract_path), ContractCells(cleanup=cell)
        )
        recorded.contract_path.write_text(
            contract_publication_text(recorded.contract_path, completed), encoding="utf-8"
        )
        report = outside_scratch_reconcile.reconcile_outside_scratch_roots()

        assert root.as_posix() in [item["path"] for item in report["removed"]]
        assert not root.exists(), "the sandbox tree goes with the enclosure"
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(location.worktree_group, ignore_errors=True)


def test_an_active_generation_is_reported_and_kept_by_the_reconcile(tmp_path: Path) -> None:
    contract, _ = enclosure_contract(tmp_path)
    recorded = outside_scratch.with_root_record(contract)
    location = publish_test_enclosure(
        recorded, contract_publication_text(contract.contract_path, recorded)
    )
    root = Path(recorded.outside_scratch_root)
    try:
        report = outside_scratch_reconcile.reconcile_outside_scratch_roots()

        assert {"path": root.as_posix(), "reason": "generation-not-ended"} in report["reported"]
        assert root.is_dir()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(location.worktree_group, ignore_errors=True)


def test_the_tool_server_start_sweeps_the_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every tool-server start is a reconcile trigger (rule 5), off the stdio handshake."""

    called = threading.Event()
    monkeypatch.setattr(
        startup,
        "reconcile_outside_scratch_roots",
        lambda: (called.set(), {"removed": [], "reported": []})[1],
    )

    startup.start_outside_scratch_reconcile()

    # load-independent: proves an event the thread sets at once; the bound only fails a hang.
    assert called.wait(10), "the start sweep must run in its own thread"


def test_the_handover_names_the_outside_root_only_when_one_is_recorded(
    tmp_path: Path,
) -> None:
    contract, _ = enclosure_contract(tmp_path)
    recorded = outside_scratch.with_root_record(contract)

    assert _outside_scratch_field(contract) == {"outsideScratchRoot": ""}
    assert outside_scratch.recorded_root_in_group(tmp_path / "no-group") == ""
    assert _outside_scratch_field(recorded) == {
        "outsideScratchRoot": Path(recorded.outside_scratch_root).as_posix()
    }


def test_the_next_direct_use_recreates_an_aged_away_root(tmp_path: Path) -> None:
    """F03: a recorded root that aged away comes back, empty, under its recorded identity."""

    contract, _ = enclosure_contract(tmp_path)
    recorded = outside_scratch.with_root_record(contract)
    outside_scratch.ensure_recorded_root(recorded)
    record = outside_scratch.recorded_root(recorded)
    assert record is not None
    (record.root / "run").mkdir()
    shutil.rmtree(record.root)

    field = _outside_scratch_field(recorded)

    assert field["outsideScratchRoot"] == record.root.as_posix()
    assert record.root.is_dir() and list(record.root.iterdir()) == [
        record.root / outside_scratch.MARKER_NAME
    ], "the recreated root is empty and marked, never a copy of what was deleted"
    assert outside_scratch.marker_identity(record.root) == record.marker_sha256
    assert _outside_scratch_field(recorded) == field, "a second use answers the same root"


def test_the_next_leaf_use_recreates_an_aged_away_root_from_the_manifest(
    tmp_path: Path,
) -> None:
    """F02/F03: the record is durable before the directory, and the next use restores it."""

    contract, _ = enclosure_contract(tmp_path)
    located = outside_scratch.with_root_record(contract)
    publish_test_enclosure(located, contract_publication_text(located.contract_path, located))
    record = outside_scratch.recorded_root(located)
    assert record is not None and record.root.is_dir()
    manifest = json.loads(
        (Path(located.worktree_group) / MANIFEST_RELATIVE).read_text(encoding="utf-8")
    )
    recorded_manifest = dict(manifest["outsideScratch"])
    assert recorded_manifest.pop("schemaVersion") == "1.0"
    assert recorded_manifest == record.manifest_payload()
    shutil.rmtree(record.root)  # a crash after the record, before the next use

    field = _ensure_leaf_root(located.contract_path, Path(located.worktree_group))

    assert field == record.root.as_posix()
    assert outside_scratch.marker_identity(record.root) == record.marker_sha256


def test_teardown_removes_only_this_invocations_roots(tmp_path: Path) -> None:
    """F09: ownership is the marker's coordination root under this base, nothing else."""

    parent = tmp_path / "outside"
    parent.mkdir(mode=outside_scratch.MANAGED_MODE)
    os.chmod(parent, outside_scratch.MANAGED_MODE)
    base = tmp_path / "pytest-of-user" / "pytest-700"
    mine_coordination = base / "test_mine0" / "ar-coordination"
    sibling_coordination = (
        tmp_path / "pytest-of-user" / "pytest-701" / "test_sibling0" / "ar-coordination"
    )
    mine_coordination.mkdir(parents=True)
    sibling_coordination.mkdir(parents=True)

    def root_for(coordination: Path) -> outside_scratch.OutsideScratchRoot:
        contract, _ = enclosure_contract(coordination.parent)
        contract = dataclasses.replace(
            contract,
            outside_scratch_root="",
            outside_scratch_generation="",
            outside_scratch_marker_sha256="",
        )
        return outside_scratch.record_for(contract, parent=parent)

    mine = root_for(mine_coordination)
    sibling = root_for(sibling_coordination)
    foreign = root_for(tmp_path / "elsewhere" / "ar-coordination")
    for record in (mine, sibling, foreign):
        outside_scratch.ensure_root(record, parent=parent)

    removed = discard_owned_roots(base, parent=parent)

    assert removed == [mine.root.as_posix()]
    assert not mine.root.exists()
    assert sibling.root.is_dir(), "a parallel sibling's live root is never swept"
    assert foreign.root.is_dir(), "a root outside this base is never swept"
    assert discard_owned_roots(base, parent=parent) == [], "teardown is safe to repeat"


def test_a_bounded_pass_reaches_the_roots_behind_the_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F07: a fixed limit with no cursor is a fixed point; the cursor walks the population."""

    parent = tmp_path / "outside"
    parent.mkdir(mode=outside_scratch.MANAGED_MODE)
    os.chmod(parent, outside_scratch.MANAGED_MODE)
    roots = []
    for index in range(5):
        contract, _ = enclosure_contract(tmp_path / f"world-{index}")
        record = outside_scratch.record_for(contract, parent=parent)
        outside_scratch.ensure_root(record, parent=parent)
        roots.append(record)
    monkeypatch.setattr(outside_scratch, "MAX_ROOTS", 2)

    passes: list[list[str]] = []
    for _ in range(10):
        report = outside_scratch.reconcile(ended=lambda marker: True, parent=parent)
        passes.append([str(item["path"]) for item in report["removed"]])
        if not any(record.root.exists() for record in roots):
            break

    assert all(len(one) <= 2 for one in passes), "no pass inspects more than the bound"
    reached = {path for one in passes for path in one}
    assert reached == {record.root.as_posix() for record in roots}, (
        "every root, including the trailing ones, is reached across the passes"
    )
    assert not any(record.root.exists() for record in roots)
    assert not (parent / outside_scratch.RECONCILE_CURSOR).exists(), "a finished walk clears it"


def test_a_completed_retry_reports_the_root_it_removed(tmp_path: Path) -> None:
    """F08: an already-clean retry that removed the outside root reports what it removed."""

    contract, _ = enclosure_contract(tmp_path)
    recorded = outside_scratch.with_root_record(contract)
    payload = {
        "state": "already-clean",
        "directories": {
            "outside_scratch": {
                "path": recorded.outside_scratch_root,
                "removed": True,
                "reason": "removed",
            }
        },
    }

    report = cast("dict[str, Any]", cleanup_report(recorded, payload))

    assert report["state"] == "already-clean"
    assert report["removed"]["outsideScratch"] == [
        {"path": recorded.outside_scratch_root, "removed": True}
    ]
    assert report["notRemoved"]["outsideScratch"] == []
    assert "outside scratch root" in str(report["summary"])
