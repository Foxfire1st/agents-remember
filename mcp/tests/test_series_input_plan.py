"""Master ref events join the same exact watch plan, without changing task-reader inputs."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import change_watcher
from agents_remember.serving.projections import series_input_plan
from agents_remember.serving.projections.series_input_plan import work_branch_ref_paths
from agents_remember.serving.projections.task_input_plan import task_input_plan
from agents_remember.serving.series_preparation_events import offer_series_changes
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    contract_to_text,
    default_contract,
)
from knowledge_index_test_support import commit_all, init_repository


def repository(path: Path) -> Path:
    init_repository(path)
    (path / "one.txt").write_text("one\n")
    commit_all(path, "one")
    return path


def master(root: Path, name: str, code: Path, memory: Path) -> Path:
    task = root / "tasks/repo" / name
    task.mkdir(parents=True)
    (task / "task.json").write_text("{}")
    contract = default_contract(
        ContractTask(name, "repo", root, "light-task", "external"),
        leaf=LeafIdentity(name),
        code=RepoBranchPlan(code, "main", "series/work", "a" * 40),
        memory=RepoBranchPlan(memory, "main", "series/memory", "b" * 40),
    )
    path = task / "series-contract.md"
    path.write_text(contract_to_text(replace(contract, kind="series", contract_path=path)))
    return path


def config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs/mcp",
    )


def test_loose_and_packed_events_name_only_their_canonical_master(
    tmp_path: Path,
) -> None:
    code = repository(tmp_path / "code")
    memory = repository(tmp_path / "memory")
    master(tmp_path, "master", code, memory)
    plan = task_input_plan(tmp_path / "tasks", include_series_refs=True)
    assert plan.complete, plan.errors
    assert plan.series_masters == (("repo", "master"),)
    expected = {
        *work_branch_ref_paths(code, "series/work"),
        *work_branch_ref_paths(memory, "series/memory"),
    }
    assert len(expected) == 4
    for path in expected:
        assert plan.series_for_paths((path,)) == frozenset({("repo", "master")})
        assert plan.series_event(path)
    assert not plan.series_for_paths((code / ".git/HEAD", code / ".git/objects/new"))
    assert not plan.series_event(code / ".git/objects/new")


def test_missing_ref_directories_are_discovered_without_recursive_git_roots(
    tmp_path: Path,
) -> None:
    code = repository(tmp_path / "code")
    memory = repository(tmp_path / "memory")
    master(tmp_path, "master", code, memory)
    plan = change_watcher.projection_watch_plan(config(tmp_path))
    assert len(plan.roots) == len(set(plan.roots))
    assert code / ".git" in plan.shallow
    assert code / ".git/refs/heads" in plan.shallow
    assert plan.task_inputs.series_event(code / ".git/refs/heads/series")
    assert plan.topology_change(code / ".git/refs/heads/series", is_directory=True)
    branch = code / ".git/refs/heads/series/work"
    branch.parent.mkdir()
    branch.write_text(require_git(code, ["rev-parse", "HEAD"]) + "\n")
    fresh = change_watcher.projection_watch_plan(config(tmp_path))
    assert branch.parent in fresh.shallow
    assert fresh.task_inputs.accepts_file(branch)
    assert branch.parent not in set(fresh.roots) - fresh.shallow


def test_ref_filter_keeps_atomic_replacements_but_rejects_other_git_churn(
    tmp_path: Path,
) -> None:
    code = repository(tmp_path / "code")
    memory = repository(tmp_path / "memory")
    master(tmp_path, "master", code, memory)
    plan = change_watcher.projection_watch_plan(config(tmp_path))
    owner = change_watcher.ProjectionInputWatcher(config(tmp_path))
    accept = owner._watch_filter(plan)
    assert change_watcher.watchfiles is not None
    event = change_watcher.watchfiles.Change.deleted
    for path in work_branch_ref_paths(code, "series/work"):
        assert accept(event, str(path))
    assert not accept(event, str(code / ".git/packed-refs.lock"))
    assert not accept(event, str(code / ".git/objects/de/adbeef"))
    assert not accept(event, str(code / ".git/HEAD"))


def test_linked_worktree_ref_paths_use_the_common_repository(tmp_path: Path) -> None:
    code = repository(tmp_path / "code")
    linked = tmp_path / "linked"
    require_git(code, ["worktree", "add", "--detach", str(linked), "HEAD"])
    assert (linked / ".git").is_file()
    expected = work_branch_ref_paths(code, "series/work")
    assert work_branch_ref_paths(linked, "series/work") == expected
    assert code / ".git/packed-refs" in expected


def test_shared_packed_refs_offer_each_owner_once(tmp_path: Path) -> None:
    code = repository(tmp_path / "code")
    memory = repository(tmp_path / "memory")
    master(tmp_path, "first", code, memory)
    master(tmp_path, "second", code, memory)
    plan = task_input_plan(tmp_path / "tasks", include_series_refs=True)
    packed = code / ".git/packed-refs"
    assert plan.series_for_paths((packed, str(packed))) == frozenset(
        {("repo", "first"), ("repo", "second")}
    )
    assert len(plan.shallow_dirs) == len(set(plan.shallow_dirs))


def test_later_master_discovery_and_task_reader_input_parity(tmp_path: Path) -> None:
    code = repository(tmp_path / "code")
    memory = repository(tmp_path / "memory")
    master(tmp_path, "first", code, memory)
    plain = task_input_plan(tmp_path / "tasks")
    watched = task_input_plan(tmp_path / "tasks", include_series_refs=True)
    assert not plain.series_masters
    assert set(plain.input_files()) == set(watched.input_files())
    master(tmp_path, "second", code, memory)
    fresh = task_input_plan(tmp_path / "tasks", include_series_refs=True)
    assert fresh.series_masters == (("repo", "first"), ("repo", "second"))
    assert set(task_input_plan(tmp_path / "tasks").input_files()) == set(fresh.input_files())
    noise = tmp_path / "tasks/repo/first/notes/reports/noise.json"
    noise.parent.mkdir(parents=True)
    noise.write_text("{}")
    assert not fresh.accepts_file(noise)


def test_ref_location_failure_is_not_a_complete_watch_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = repository(tmp_path / "code")
    memory = repository(tmp_path / "memory")
    master(tmp_path, "master", code, memory)

    def fail(*_args):
        raise OSError("ref directory unreadable")

    monkeypatch.setattr(series_input_plan, "require_git", fail)
    plan = task_input_plan(tmp_path / "tasks", include_series_refs=True)
    assert not plan.complete
    assert "ref directory unreadable" in "; ".join(plan.errors)


def test_native_ref_events_arrive_before_the_existing_replan_timer(tmp_path: Path) -> None:
    """A real loose/packed event is admitted on its own, with the refresh timer far away."""
    code = repository(tmp_path / "code")
    memory = repository(tmp_path / "memory")
    master(tmp_path, "master", code, memory)
    first = require_git(code, ["rev-parse", "HEAD"])
    require_git(code, ["update-ref", "refs/heads/series/work", first])
    (code / "one.txt").write_text("two\n")
    second = commit_all(code, "two")

    class Pacer:
        healthy = False

        def __init__(self) -> None:
            self.paths: set[str] = set()

        def set_watcher_healthy(self, healthy: bool) -> None:
            self.healthy = healthy

        def notify_change(self, domains=None, paths=frozenset()) -> None:
            del domains
            self.paths.update(paths)

    async def observed(predicate):
        async def until():
            while not predicate():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(
            until(),
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            timeout=8,
        )

    async def run() -> None:
        pacer = Pacer()
        watcher = change_watcher.ProjectionInputWatcher(config(tmp_path), refresh_seconds=60)
        task = asyncio.create_task(watcher.run(pacer))
        try:
            await observed(lambda: pacer.healthy)
            await asyncio.to_thread(
                require_git, code, ["update-ref", "refs/heads/series/work", second]
            )
            loose = str(code / ".git/refs/heads/series/work")
            await observed(lambda: loose in pacer.paths)
            await asyncio.wait_for(
                asyncio.to_thread(require_git, code, ["pack-refs", "--all"]),
                timeout=3,  # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            )
            packed = str(code / ".git/packed-refs")
            await observed(lambda: packed in pacer.paths)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(
                    task,
                    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                    timeout=3,
                )

    asyncio.run(
        # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
        asyncio.wait_for(run(), timeout=20)
    )


def test_declined_preparation_is_one_warning_per_batch(caplog) -> None:

    offered = []

    def offer(repo, master):
        offered.append((repo, master))
        return master == "ready"

    offer_series_changes(offer, (("repo", "ready"), ("repo", "busy"), ("repo", "other")))
    assert offered == [("repo", "ready"), ("repo", "busy"), ("repo", "other")]
    assert len(caplog.records) == 1
    assert "2 master offer(s)" in caplog.records[0].message
    caplog.clear()
    offer_series_changes(offer, (("repo", "ready"),))
    offer_series_changes(None, (("repo", "busy"),))
    assert not caplog.records
