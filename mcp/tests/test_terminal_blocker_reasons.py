"""A cleanup blocker always names its reason, and leftover provider-runtime trees are reclaimed directly.

The contract is explicit in two places:

* ``terminal_validation._blocker`` is the only way a terminal result reports a blockage, and it
  refuses -- raising at the detecting call site -- unless both the component and a non-empty
  reason are present;
* ``worktrees/modules/filesystem.py::remove_tree`` removes a directory tree directly (including
  any leftover ``<worktree_group>/provider-runtime`` directory) and answers with an explicit
  reason whenever it reclaimed nothing.

The cases below drive ``remove_tree``, the real application tools over real Git repositories and
real worktrees, and the terminal blocker validator directly.
"""

from __future__ import annotations

import os
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
)
from agents_remember.tasks import read_task_doc
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.modules.filesystem import remove_tree
from agents_remember.worktrees.modules.terminal_validation import (
    TerminalResult,
    _blocker,
    terminal_result_blockers,
)
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_source_lineage import _git
from test_transaction_only_worktree_delivery import _public_config

pytestmark = pytest.mark.integration

PROVIDER_RUNTIME_SETUP_FILE = "setup-progress.json"
# The drift-snapshot entry a dry run reads: the producer's own preview shape, with the key
# `kernel/primitives/drift_snapshot.py::_remove_snapshot_file` actually emits.
DRIFT_PREVIEW_ENTRY = {
    "path": "/x/drift.json",
    "repository": "code",
    "branch": "leaf",
    "removed": False,
    "would_remove": True,
}
# A real run that reclaimed nothing and explained nothing -- the shape the validator's own
# contract exists to refuse, and the one case the preview allowance must NOT swallow.
DRIFT_REASONLESS_ENTRY = {
    "path": "/x/drift.json",
    "repository": "code",
    "branch": "leaf",
    "removed": False,
}


class _NoManagedCitationCache:
    """The citation guard port answering with production's own no-authority guard.

    The fixture leaf has no lifecycle-bound managed cache namespace, which is exactly the case
    the production guard resolves by yielding an authority-free guard. Only the cache reservation
    is a double, and it is the same value production yields.
    """

    def guard(
        self,
        contract: WorktreeContract,
        *,
        requested_contract_path: Path,
        completed_contract_with_remains: bool = False,
    ):
        del contract, requested_contract_path, completed_contract_with_remains
        return nullcontext(TerminalNamespaceGuard(None, None, None))


@pytest.fixture
def bound_worktree_services():
    """The bound services this module patches, so its doubles are the ones under test."""

    services = replace(build_default_worktree_services(), citation_guard=_NoManagedCitationCache())
    bind_worktree_services(services)
    try:
        yield services
    finally:
        reset_worktree_services()


def _landed_leaf(tmp_path: Path) -> WorktreeContract:
    fixture = _authority_fixture(tmp_path, external_memory=True)
    return _closed_external_leaf_worktrees(fixture, tmp_path, publish_closeout_evidence=True)


def _landed_leaf_config(tmp_path: Path, contract: WorktreeContract):
    """The MCP authority for a fixture leaf whose landing has already been applied."""

    config = _public_config(tmp_path, contract)
    applied = worktree_tools.worktree_integrate_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=False,
    )
    assert applied["state"] == "integrated", applied
    return config


def _landed_leaf_document(contract: WorktreeContract):
    resolved = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    assert resolved is not None, "the fixture leaf must own a task document"
    return read_task_doc(resolved[0])


def _finalize(config, contract: WorktreeContract):
    return worktree_tools.lifecycle_finalize_task_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
    )


def test_cleanup_removes_leftover_provider_runtime_directory_directly(
    tmp_path: Path, bound_worktree_services
) -> None:
    """Cleanup reclaims a leftover ``<group>/provider-runtime`` tree directly and finalizes."""

    closed = _landed_leaf(tmp_path)
    provider_runtime = closed.worktree_group / "provider-runtime"
    provider_runtime.mkdir(parents=True, exist_ok=True)
    (provider_runtime / PROVIDER_RUNTIME_SETUP_FILE).write_text(
        '{"state": "ok"}\n', encoding="utf-8"
    )
    config = _landed_leaf_config(tmp_path, closed)

    preview = worktree_tools.worktree_cleanup_tool(
        config,
        contract_path=closed.contract_path.as_posix(),
        dry_run=True,
    )
    assert preview["state"] == "would-cleanup", preview
    assert preview["directories"]["worktree_group"]["would_remove"] is True
    assert provider_runtime.exists()

    finalized = _finalize(config, closed)
    assert finalized["state"] == "finalized", finalized
    assert finalized["cleanup"]["state"] == "cleanup-completed", finalized["cleanup"]
    assert not provider_runtime.exists()
    assert not closed.worktree_group.exists()
    assert load_contract(closed.contract_path).cleanup == "completed"
    assert _landed_leaf_document(closed).status == "Completed"


def test_abandon_removes_leftover_provider_runtime_directory_directly(
    tmp_path: Path, bound_worktree_services
) -> None:
    """Abandon reclaims a leftover ``<group>/provider-runtime`` tree directly."""

    closed = _landed_leaf(tmp_path)
    assert closed.memory_worktree is not None
    assert closed.memory_source_branch is not None
    _git(closed.code_worktree, "reset", "--hard", closed.code_source_branch)
    _git(closed.memory_worktree, "reset", "--hard", closed.memory_source_branch)
    config = _public_config(tmp_path, closed)
    provider_runtime = closed.worktree_group / "provider-runtime"
    provider_runtime.mkdir(parents=True, exist_ok=True)
    (provider_runtime / PROVIDER_RUNTIME_SETUP_FILE).write_text(
        '{"state": "ok"}\n', encoding="utf-8"
    )

    preview = worktree_tools.worktree_abandon_tool(
        config,
        contract_path=closed.contract_path.as_posix(),
        dry_run=True,
        force=False,
    )
    assert preview["state"] == "would-abandon", preview
    assert preview["directories"]["worktree_group"]["would_remove"] is True
    assert provider_runtime.exists()

    abandoned = worktree_tools.worktree_abandon_tool(
        config,
        contract_path=closed.contract_path.as_posix(),
        dry_run=False,
        force=False,
    )
    assert abandoned["state"] == "abandoned", abandoned
    assert abandoned["directories"]["worktree_group"]["removed"] is True
    assert not provider_runtime.exists()
    assert not closed.worktree_group.exists()
    assert load_contract(closed.contract_path).cleanup == "abandoned"


def test_an_unremovable_provider_runtime_directory_blocks_cleanup_with_its_reason(
    tmp_path: Path, bound_worktree_services
) -> None:
    """A genuine permission failure on ``<group>/provider-runtime`` blocks cleanup with a reason."""

    closed = _landed_leaf(tmp_path)
    config = _landed_leaf_config(tmp_path, closed)
    provider_runtime = closed.worktree_group / "provider-runtime"
    provider_runtime.mkdir(parents=True, exist_ok=True)
    (provider_runtime / PROVIDER_RUNTIME_SETUP_FILE).write_text(
        '{"state": "ok"}\n', encoding="utf-8"
    )
    os.chmod(provider_runtime, 0o500)

    try:
        refused = _finalize(config, closed)
    finally:
        os.chmod(provider_runtime, 0o700)

    assert refused["state"] == "cleanup-blocked", refused
    assert refused["cleanup"]["state"] == "blocked", refused["cleanup"]
    entry = refused["cleanup"]["directories"]["worktree_group"]
    assert entry["path"] == provider_runtime.as_posix()
    assert entry["removed"] is False
    assert str(provider_runtime) in str(entry["reason"])
    assert any(
        blocker.get("directory") == "worktree_group"
        and str(provider_runtime) in str(blocker.get("reason", ""))
        for blocker in refused["cleanup"]["blockers"]
    ), refused["cleanup"]["blockers"]
    assert _landed_leaf_document(closed).status != "Completed"
    assert load_contract(closed.contract_path).cleanup == "pending"
    assert provider_runtime.exists()


def test_an_unnameable_blocker_reason_is_refused_at_its_own_source() -> None:
    """A producer whose reason is not operator language is a defect that fails loudly."""

    for unnameable in (None, "", "   ", 17):
        with pytest.raises(RuntimeError) as refusal:
            _blocker({"directory": "provider_runtime"}, unnameable)

        assert "provider_runtime" in str(refusal.value)
        assert "carries no reason" in str(refusal.value)


def test_remove_tree_answers_with_a_reason_whenever_it_reclaimed_nothing(
    tmp_path: Path,
) -> None:
    """``worktrees/modules/filesystem.py::remove_tree`` handles absent, preview, removal, and errors."""

    absent = tmp_path / "already-gone"
    assert remove_tree(absent, dry_run=False) == {
        "path": absent.as_posix(),
        "removed": False,
        "reason": "already-absent",
    }

    present = tmp_path / "present"
    (present / "data").mkdir(parents=True)
    (present / "data" / "file").write_text("content\n", encoding="utf-8")
    assert remove_tree(present, dry_run=True) == {
        "path": present.as_posix(),
        "removed": False,
        "would_remove": True,
    }
    assert present.exists()

    assert remove_tree(present, dry_run=False) == {"path": present.as_posix(), "removed": True}
    assert not present.exists()

    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "file").write_text("stuck\n", encoding="utf-8")
    os.chmod(locked, 0o500)
    try:
        failed = remove_tree(locked, dry_run=False)
    finally:
        os.chmod(locked, 0o700)
    assert failed["path"] == locked.as_posix()
    assert failed["removed"] is False
    assert str(failed["reason"]).startswith("permission denied")


def test_a_drift_snapshot_preview_is_not_read_as_an_unreclaimed_result() -> None:
    """`T62`: the preview crashed on the output its own producer emits -- and must not."""

    preview_shape = TerminalResult(
        worktrees={},
        branches={},
        directories={},
        drift_snapshots={"code": dict(DRIFT_PREVIEW_ENTRY)},
        preview=True,
    )
    assert terminal_result_blockers(preview_shape) == []

    # The control that locates the fault: the IDENTICAL shape passes in the worktree
    # collection, which excludes "the producer is malformed" and pins it to the drift branch.
    worktree_control = TerminalResult(
        worktrees={"code": dict(DRIFT_PREVIEW_ENTRY)},
        branches={},
        directories={},
        drift_snapshots=None,
        preview=True,
    )
    assert terminal_result_blockers(worktree_control) == []


def test_a_real_reasonless_drift_snapshot_still_refuses_rather_than_passing_as_preview() -> None:
    """The validator's own contract survives the preview allowance, measured not asserted."""

    reasonless = TerminalResult(
        worktrees={},
        branches={},
        directories={},
        drift_snapshots={"code": dict(DRIFT_REASONLESS_ENTRY)},
        preview=False,
    )
    with pytest.raises(RuntimeError) as refusal:
        terminal_result_blockers(reasonless)

    assert "driftSnapshot=code" in str(refusal.value)
    assert "carries no reason" in str(refusal.value)


def test_the_cleanup_preview_of_a_task_with_a_drift_snapshot_reports_no_blocker(
    tmp_path: Path, bound_worktree_services
) -> None:
    """The level that was missing: the same defect driven through the tool, not the validator.

    `T62` was found live by an ordinary ``lifecycle_finalize_task(dry_run=True)`` on a task whose
    code branch had a drift snapshot -- an artifact that only exists once something has written
    one, which is why three earlier leaves' previews ran clean. This case creates that file at
    the product's own resolved path and drives ``worktree_cleanup(dry_run=True)`` in preview
    mode: it must answer ``would-cleanup`` with no blockers, change nothing, and leave the
    snapshot file in place.
    """

    closed = _landed_leaf(tmp_path)
    config = _landed_leaf_config(tmp_path, closed)
    snapshot = drift_snapshot_path(
        closed.coordination_root,
        repository=closed.code_worktree.name,
        branch=closed.code_work_branch,
    )
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text('{"schema": "ar-drift-snapshot/v1"}\n', encoding="utf-8")
    assert snapshot.exists(), "the preview case needs the snapshot to exist"

    preview = worktree_tools.worktree_cleanup_tool(
        config,
        contract_path=closed.contract_path.as_posix(),
        dry_run=True,
    )

    assert preview["state"] == "would-cleanup", preview
    assert preview.get("blockers") in (None, []), preview.get("blockers")
    # A preview is not a result: nothing it reported as reclaimable may actually be gone.
    assert snapshot.exists(), "the preview removed the drift snapshot it only planned to"
    assert closed.code_worktree.exists(), "the preview removed a worktree"
    assert load_contract(closed.contract_path).cleanup == "pending"


def test_cleanup_preview_names_role_report_link_blocker(
    tmp_path: Path, bound_worktree_services
) -> None:
    closed = _landed_leaf(tmp_path)
    config = _landed_leaf_config(tmp_path, closed)
    local_reports = closed.worktree_group / "role-reports"
    local_reports.mkdir()
    destination = tmp_path / "linked-evidence.txt"
    destination.write_text("evidence outside task folder", encoding="utf-8")
    bad_report = local_reports / "linked-evidence.txt"
    bad_report.symlink_to(destination)

    def cleanup(*, dry_run: bool):
        return worktree_tools.worktree_cleanup_tool(
            config,
            contract_path=closed.contract_path.as_posix(),
            dry_run=dry_run,
        )

    preview = cleanup(dry_run=True)
    assert not preview["ok"] and str(bad_report) in str(preview.get("blockers")), (
        f"cleanup preview hid the linked role report blocker: {preview}"
    )
    result = cleanup(dry_run=False)
    assert not result["ok"] and str(bad_report) in str(result.get("blockers")), result
    assert bad_report.is_symlink()
    assert closed.code_worktree.exists()
    assert load_contract(closed.contract_path).cleanup == "pending"
