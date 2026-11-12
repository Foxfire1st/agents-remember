"""Small explicit builders for lifecycle-enclosure addressability forcing."""

from __future__ import annotations

import atexit
import shutil
from pathlib import Path
from typing import Literal

from agents_remember.models.lifecycles.enclosure import (
    LifecycleEnclosureLocator,
    TerminalWorktreeAbandonArguments,
    TerminalWorktreeCleanupArguments,
)
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
    inspect_lifecycle_operation_locator,
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_contract,
    default_series_contract,
    load_contract,
)


def enclosure_contract(
    root: Path,
    *,
    kind: Literal["leaf", "series"] = "leaf",
    worktree_name: str = "enclosure-leaf",
    leaf_id: str = "L12",
) -> tuple[WorktreeContract, str]:
    coordination = root / "coordination"
    task = ContractTask(
        name="lifecycle-enclosure",
        repo_name="repo",
        coordination_root=coordination,
        workflow_kind="light-task",
        memory_mode="external",
    )
    code = RepoBranchPlan(
        repo_path=root / "repo",
        source_branch="main",
        work_branch=f"ar/{worktree_name}",
        base_commit="a" * 40,
    )
    memory = RepoBranchPlan(
        repo_path=root / "memory",
        source_branch="main",
        work_branch=f"ar/{worktree_name}",
        base_commit="b" * 40,
    )
    if kind == "series":
        contract = default_series_contract(task, code=code, memory=memory)
    else:
        contract = default_contract(
            task,
            leaf=LeafIdentity(worktree_name=worktree_name, leaf_id=leaf_id),
            code=code,
            memory=memory,
        )
    return contract, contract_publication_text(contract.contract_path, contract)


_PUBLISHED_ROOTS: list[outside_scratch.OutsideScratchRoot] = []
"""Every outside scratch root a test publication created, removed when the run ends."""


def publish_test_enclosure(contract: WorktreeContract, text: str) -> LifecycleOperationLocation:
    """Publish a test enclosure, and remember the root it allocates outside ``~/projects``.

    A publication under MIK-R207 creates the enclosure''s outside scratch root under the real
    product parent. The fixture tree is deleted by the test, so the root is this support
    module''s to remove: without this, every publishing test would leave an empty root behind
    and the suite would litter ``/var/tmp/agents-remember``.
    """

    location = publish_new_lifecycle_operation_location(contract, contract_text=text)
    record = outside_scratch.published_root_record(contract) or outside_scratch.recorded_root(
        contract
    )
    if record is not None:
        _PUBLISHED_ROOTS.append(record)
    return location


def discard_test_enclosures() -> None:
    """Remove the roots the test publications created; safe to call more than once."""

    while _PUBLISHED_ROOTS:
        record = _PUBLISHED_ROOTS.pop()
        outside_scratch.remove_root(record)


def discard_owned_roots(base: Path, parent: Path | None = None) -> list[str]:
    """Remove the roots *this* invocation created: those whose coordination root is under ``base``.

    Ownership is the coordination root the marker names, resolved under this invocation's own
    temporary base. A sibling worker of a parallel run has a different base, so its roots never
    match, and a root created by a subprocess served for one of these tests still stands under
    the base that handed it the path. Nothing is matched by name shape, by a shared parent, or by
    a root being merely absent, so a live sibling's tree and a foreign root both survive.
    """

    managed = parent if parent is not None else outside_scratch.OUTSIDE_SCRATCH_PARENT
    owned = Path(base).resolve()
    removed: list[str] = []
    for item in outside_scratch.list_roots(managed):
        marker = item.get("marker")
        if not isinstance(marker, dict):
            continue
        coordination = str(marker.get("coordinationRoot") or "")
        if not coordination:
            continue
        try:
            named = Path(coordination).resolve()
        except OSError:  # pragma: no cover - an unresolvable path is not proof of ownership
            continue
        if named != owned and owned not in named.parents:
            continue
        record = outside_scratch.record_from_marker(marker)
        if record is None:
            continue
        if outside_scratch.remove_root(record, parent=managed).get("removed"):
            removed.append(str(item["path"]))
    return removed


atexit.register(discard_test_enclosures)


def terminalize_test_enclosure(
    location: LifecycleOperationLocation,
    *,
    cleanup: bool = False,
) -> LifecycleEnclosureLocator:
    """Drive the production archive/readback/receipt seam, then cut after deletion.

    ``cleanup`` archives the generation as a *completed cleanup* rather than an abandonment:
    that is the operation a finished, landed, cleaned-up task leaves behind, and the one whose
    archive a reopen resets. The default stays abandonment, which is what the addressability
    cases beside this helper pin.
    """

    contract = _contract_for_location(location)
    result = (
        terminal_archive_required_result(
            contract,
            operation="worktree_cleanup",
            arguments=TerminalWorktreeCleanupArguments(teardown_providers=False),
            dry_run=False,
        )
        if cleanup
        else terminal_archive_required_result(
            contract,
            operation="worktree_abandon",
            arguments=TerminalWorktreeAbandonArguments(force=False),
            dry_run=False,
        )
    )
    assert result.returncode == 0, result.payload
    shutil.rmtree(location.worktree_group)
    observed = inspect_lifecycle_operation_locator(
        location.locator_path.parent.parent.parent,
        location.contract_path,
    )
    assert observed.state == "terminal-archived"
    assert observed.locator is not None
    return observed.locator


def _contract_for_location(location: LifecycleOperationLocation) -> WorktreeContract:
    return load_contract(location.contract_path)


def byte_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
