"""Native full/incremental oracle for a real five-component contract path."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.projections.contract_snapshot import ContractSnapshotCache
from agents_remember.serving.projections.projection_inputs import (
    ProjectionDomain,
    ProjectionInputState,
    ProjectionReaders,
    ProjectionRefresh,
    RefreshPass,
)


def test_new_canonical_contract_changes_incremental_state_exactly_like_full(tmp_path: Path) -> None:
    task = tmp_path / "tasks/r/t"
    task.mkdir(parents=True)
    (task / "task.json").write_text(
        json.dumps(
            {
                "schema": "ar-task-document/v1",
                "id": "t",
                "slug": "t",
                "title": "task",
                "kind": "light",
                "status": "planning",
                "repo": "r",
                "createdAt": "2026-10-25T00:00:00+00:00",
            }
        )
    )
    cfg = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs/mcp",
    )
    readers = ProjectionReaders(repo_surfaces=lambda _cfg, _now: ([], []), landing_state=None)
    observer = tmp_path / "obs"
    (observer / "workspace").mkdir(parents=True)
    (observer / "workspace/operator-inbox.jsonl").write_text("")
    now = datetime(2026, 10, 25, tzinfo=UTC)
    retained = ProjectionInputState()
    retained.read(
        cfg,
        readers,
        observer_root=observer,
        pass_=RefreshPass(now=now, refresh=ProjectionRefresh.full()),
    )
    contract = task / "enclosures/l/series-contract.md"
    contract.parent.mkdir(parents=True)
    contract.write_text("not a valid contract\n")
    changed = retained.read(
        cfg,
        readers,
        observer_root=observer,
        pass_=RefreshPass(
            now=now,
            refresh=ProjectionRefresh.change(
                frozenset({ProjectionDomain.TASKS}), (str(contract),), paths_complete=True
            ),
        ),
    )
    full = ProjectionInputState()
    expected = full.read(
        cfg,
        readers,
        observer_root=observer,
        pass_=RefreshPass(now=now, refresh=ProjectionRefresh.full()),
    )
    assert full._contracts is not None and contract in full._contracts.skipped
    assert retained._contracts is not None
    assert retained._contracts.skipped == full._contracts.skipped
    assert retained._contracts.contracts == full._contracts.contracts
    assert changed.enclosures == expected.enclosures
    assert changed.task_sources == expected.task_sources


def test_deleted_contract_is_absent_in_scoped_and_full_snapshots(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    task = tasks / "r/t"
    task.mkdir(parents=True)
    (task / "task.json").write_text("{}")
    contract = task / "enclosures/l/series-contract.md"
    contract.parent.mkdir(parents=True)
    contract.write_text("not a valid contract\n")
    cache = ContractSnapshotCache()
    initial = cache.build(tasks)
    assert contract in initial.skipped
    contract.unlink()

    incremental = cache.build(tasks, only=[contract])
    expected = ContractSnapshotCache().build(tasks)

    assert contract not in expected.skipped
    assert incremental.skipped == expected.skipped
    assert incremental.contracts == expected.contracts


@pytest.mark.parametrize("changed_other", [False, True])
def test_scoped_refresh_retains_unrelated_parse_failures(
    tmp_path: Path, changed_other: bool
) -> None:
    tasks = tmp_path / "tasks"
    task = tasks / "r/t"
    task.mkdir(parents=True)
    (task / "task.json").write_text("{}")
    bad = task / "enclosures/bad/series-contract.md"
    other = task / "enclosures/other/series-contract.md"
    for path in (bad, other):
        path.parent.mkdir(parents=True)
        path.write_text("not a valid contract\n")
    cache = ContractSnapshotCache()
    assert cache.build(tasks).skipped == frozenset({bad, other})

    incremental = cache.build(tasks, only=[other] if changed_other else [])
    expected = ContractSnapshotCache().build(tasks)

    assert bad in expected.skipped
    assert incremental.skipped == expected.skipped
    assert incremental.contracts == expected.contracts


@pytest.mark.parametrize("kind", ["directory", "dangling"])
def test_scoped_refresh_preserves_existing_nonregular_contract_candidates(
    tmp_path: Path, kind: str
) -> None:
    tasks = tmp_path / "tasks"
    task = tasks / "r/t"
    task.mkdir(parents=True)
    (task / "task.json").write_text("{}")
    contract = task / "enclosures/l/series-contract.md"
    contract.parent.mkdir(parents=True)
    if kind == "directory":
        contract.mkdir()
    else:
        contract.symlink_to(tmp_path / "missing-contract-target")
    cache = ContractSnapshotCache()
    assert contract in cache.build(tasks).skipped

    incremental = cache.build(tasks, only=[contract])
    expected = ContractSnapshotCache().build(tasks)

    assert contract in expected.skipped
    assert incremental.skipped == expected.skipped
    assert incremental.contracts == expected.contracts
