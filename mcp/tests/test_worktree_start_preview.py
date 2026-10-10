"""First-leaf start previews observe real, unpublished atomic-series plans."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
import pytest
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_cache import prepare_memory_cache
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.server import create_server
from agents_remember.observer.ambient import reset_ambient
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.activation.atomic_series_activation import observe_atomic_series
from agents_remember.worktrees.integration.integration_branch_authority import require_parent_series
from agents_remember.worktrees.modules import start as start_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.context import resolve_context
from agents_remember.worktrees.modules.startup import start_contract, start_memory
from agents_remember.worktrees.modules.startup.start_contract import build_start_contract
from agents_remember.worktrees.modules.startup.start_plan import StartContractPlan
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from mcp.shared.memory import create_connected_server_and_client_session
from preview_read_only_test_support import (
    admitted_call_days,
    assert_preview_read_only,
    attach_record,
    capture_preview_state,
    is_call_record_path,
    river_rel_of,
    transcript_rel_of,
)
from test_worktree_support import git, init_repo

REPO = "fixture-repo"
MASTER = "fresh-preview"
LEAF = "FRESH-L1"


class PreviewFixture:
    """Canonical task documents and repositories, with no pre-published series."""

    def __init__(self, root: Path, *, commanded: bool = False) -> None:
        self.root = root
        # Harness receipts live beside the observed product root, never inside its snapshot.
        self.receipts = root.parent / f"{root.name}-receipts"
        self.receipts.mkdir()
        self.coord = root / "ar-coordination"
        self.code = root / REPO
        self.base = init_repo(self.code)
        self.memory = self.coord / "memory-repos" / f"ar-{REPO}"
        self.source = "super" if commanded else "main"
        self.task_root = self.coord / "tasks" / REPO / MASTER
        self.parent_path = self.task_root / "series-contract.md"
        if commanded:
            git(self.code, "branch", "super", "main")
            self._document(
                self.coord / "tasks" / REPO / "sprint",
                id="SPRINT",
                slug="task",
                kind="master",
                orchestrates=[MASTER],
                integrationBranch="super",
            )
        if self.memory is not None:
            init_repo(self.memory)
            prepare_memory_cache(self.memory)
            git(self.memory, "add", ".gitignore")
            git(
                self.memory,
                "commit",
                "-m",
                render_memory_content_message("Attributed memory source", self.base),
            )
            if commanded:
                git(self.memory, "branch", "super", "main")
        self._document(
            self.task_root,
            id="FRESH-MASTER",
            slug="task",
            kind="master",
            executionNature="atomic",
            subTasks=[
                {"number": LEAF, "name": "First leaf", "file": "leaf.md", "status": "planning"},
                {"number": "OLD", "name": "Old leaf", "file": "old.md", "status": "abandoned"},
            ],
        )
        self._document(self.task_root, id=LEAF, slug="leaf", kind="subTask", master="task.md")
        self._document(
            self.task_root,
            id="OLD",
            slug="old",
            kind="subTask",
            master="task.md",
            status="abandoned",
        )
        settings = root / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.coord.as_posix(),
                    "workspaceRoot": root.as_posix(),
                    "repositories": {REPO: {"path": self.code.as_posix()}},
                    "providers": {},
                }
            ),
            encoding="utf-8",
        )
        self.config = McpRuntimeConfig(
            config_path=settings,
            coordination_root=self.coord,
            workspace_root=root,
            transcript_root=self.coord / "logs" / "mcp",
            repositories={REPO: RepositoryScope(repo_id=REPO, path=self.code)},
        )
        # MIK-R131: the transcript root exists before the first call, so the
        # admitted record never creates the folders that hold it.
        (self.coord / "logs" / "mcp").mkdir(parents=True, exist_ok=True)
        # Ruling L131-river: the river's lockfile is layout, not record. The
        # product only flocks it (store.py::workspace_river_lock opens it
        # "a+b" and never writes); pre-created empty, the proof requires it
        # byte-identical forever, so any content write fails loudly.
        (self.coord / "logs" / "observer" / "workspace" / "events.lock").parent.mkdir(
            parents=True, exist_ok=True
        )
        (self.coord / "logs" / "observer" / "workspace" / "events.lock").write_bytes(b"")
        self.server = create_server(self.config)
        self.arguments: dict[str, object] = {
            "repo_id": REPO,
            "task_name": MASTER,
            "worktree_name": "fresh-preview-l1",
            "leaf_id": LEAF,
            "memory_mode": "external",
            "skip_provider_setup": True,
            "dry_run": True,
        }
        if commanded:
            self.arguments["parent_task"] = "sprint"
        self.calls = 0
        self.error_calls = 0

    @staticmethod
    def _document(root: Path, **fields: object) -> None:
        write_task_doc(
            root,
            TaskDocument.model_validate(
                {
                    "title": "Preview fixture",
                    "repo": REPO,
                    "createdAt": "2026-10-03T00:00:00+00:00",
                    "status": "planning",
                    **fields,
                }
            ),
        )

    async def _call(self, name: str, arguments: dict[str, object]):
        async with create_connected_server_and_client_session(self.server._mcp_server) as client:
            return await client.call_tool(name, arguments)

    def start(self, **overrides: object):
        arguments = {**self.arguments, **overrides}
        result = anyio.run(self._call, "worktree_start", arguments)
        self.calls += 1
        if result.isError:
            self.error_calls += 1
        self.record(
            f"call-{self.calls}", {"arguments": arguments, "result": result.model_dump(mode="json")}
        )
        return result

    def record(self, name: str, value: object) -> None:
        (self.receipts / f"{name}.json").write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def mark(self) -> tuple[int, int]:
        """Call and raised-call counts for later ``*_since`` reads.

        The daily tool log records every call, while the workspace river only
        carries completed answers: a call that raised (``isError``) has its
        line but no event.
        """
        return (self.calls, self.error_calls)

    def log_tools_since(self, mark: tuple[int, int]) -> list[str]:
        """The tool of each call made since ``mark``: every call has its line."""
        return ["worktree_start"] * (self.calls - mark[0])

    def river_tools_since(self, mark: tuple[int, int]) -> list[str]:
        """The tool of each completed call since ``mark``: every completed call has its event."""
        completed = (self.calls - mark[0]) - (self.error_calls - mark[1])
        return ["worktree_start"] * completed

    def restart_session(self) -> None:
        """Start another ordinary MCP session against the same persisted fixture world."""
        reset_ambient()
        self.server = create_server(self.config)

    def plan(self) -> StartContractPlan:
        args = WorktreeArgs(
            code_repository_name=REPO,
            code_repository_root=self.code,
            workspace_root=self.root,
            coordination_root=self.coord,
            task_name=MASTER,
            leaf_id=LEAF,
            worktree_name="fresh-preview-l1",
            parent_task="sprint" if self.source == "super" else None,
            memory_mode="external",
            skip_provider_setup=True,
            dry_run=True,
        )
        plan = build_start_contract(resolve_context(args), args)
        assert isinstance(plan, StartContractPlan), plan
        return plan

    def add_leaf(self, leaf_id: str) -> None:
        master = read_task_doc(self.task_root / "task.json")
        write_task_doc(
            self.task_root,
            master.model_copy(
                update={
                    "subTasks": [
                        *master.subTasks,
                        SubTaskRef(number=leaf_id, name=leaf_id, file="next.md", status="planning"),
                    ]
                }
            ),
        )
        self._document(self.task_root, id=leaf_id, slug="next", kind="subTask", master="task.md")

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Include all coordination/provider storage, Git files, index bytes and worktree content."""
        paths: dict[str, object] = {}
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root).as_posix()
            if path.is_symlink():
                paths[relative] = ["symlink", path.readlink().as_posix()]
            elif path.is_file():
                paths[relative] = [
                    "file",
                    path.stat().st_mode,
                    path.stat().st_mtime_ns,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                ]
            else:
                paths[relative] = ["directory", path.stat().st_mode, path.stat().st_mtime_ns]
        repositories: dict[str, object] = {
            name: {
                "refs": git(repository, "for-each-ref", "--format=%(refname) %(objectname)"),
                "worktrees": git(repository, "worktree", "list", "--porcelain"),
                "index": hashlib.sha256((repository / ".git" / "index").read_bytes()).hexdigest(),
            }
            for name, repository in (("code", self.code), ("memory", self.memory))
            if repository is not None
        }
        return {"paths": paths, "repositories": repositories}


@pytest.mark.parametrize("commanded", [False, True])
def test_registered_first_leaf_preview_is_repeatable_without_publication(
    tmp_path: Path, commanded: bool
) -> None:
    world = PreviewFixture(tmp_path, commanded=commanded)
    mark = world.mark()
    before = capture_preview_state(world)
    world.record("before", before.snapshot)
    for _ in range(2):
        result = world.start()
        assert not result.isError, [getattr(content, "text", "") for content in result.content]
        assert result.structuredContent is not None
        payload = result.structuredContent
        assert payload["ok"], payload
        assert payload["state"] == "would-start"
        contract = payload["contract"]
        assert contract["parent_contract_path"] == world.parent_path.as_posix()
        assert contract["code_source_branch"] == f"ar/{MASTER}"
        assert contract["code_base_commit"] == git(world.code, "rev-parse", world.source)
        assert contract["memory_source_branch"] == f"ar/{MASTER}"
        assert contract["memory_base_commit"] == git(world.memory, "rev-parse", world.source)
        after = world.snapshot()
        world.record(f"after-{world.calls}", after)
        assert_preview_read_only(
            before,
            attach_record(world, after),
            expected_tools=world.log_tools_since(mark),
            expected_river_tools=world.river_tools_since(mark),
        )
        assert not world.parent_path.exists()


def test_disabled_start_is_refused_before_any_publication(tmp_path: Path) -> None:
    """The removed opt-out mode never previews: the refusal names itself, twice identically.

    Both the preview and the real run are refused at the top of ``worktree_start``,
    before any branch, worktree, contract or task binding exists to unwind -- so the
    only change afterwards is the record of the refused calls, and no parent
    contract was published.
    """
    world = PreviewFixture(tmp_path, commanded=True)
    mark = world.mark()
    before = capture_preview_state(world)
    world.record("before", before.snapshot)
    for dry_run in (True, False):
        refused = world.start(memory_mode="disabled", dry_run=dry_run)
        assert not refused.isError, refused
        assert refused.structuredContent is not None
        payload = refused.structuredContent
        assert not payload["ok"], payload
        assert payload["state"] == "memory-opt-out-refused"
        assert payload["status"] == "memory-opt-out-refused"
        after = world.snapshot()
        world.record(f"refused-after-call-{world.calls}", after)
        assert_preview_read_only(
            before,
            attach_record(world, after),
            expected_tools=world.log_tools_since(mark),
            expected_river_tools=world.river_tools_since(mark),
        )
        assert not world.parent_path.exists()


def _assert_observer_event_kept_kind_mode(
    before_paths: dict[str, Any],
    after_paths: dict[str, Any],
    event_path: str,
) -> None:
    """F03: the admitted observer event may append, so mtime and bytes move; kind and mode must not."""
    before_entry = before_paths[event_path]
    after_entry = after_paths[event_path]
    assert isinstance(before_entry, list) and isinstance(after_entry, list)
    if before_entry[0] != after_entry[0] or before_entry[1] != after_entry[1]:
        raise AssertionError(
            f"observer event {event_path} changed kind/mode: "
            f"{before_entry!r} -> {after_entry!r}; only the event append is admitted"
        )


def _assert_ambient_request_observes_only_tool_completion(world: PreviewFixture) -> None:
    """Retain the generic observer append while proving every startup/Git path unchanged."""
    switched = anyio.run(world._call, "switch_lifecycle", {})
    world.record("switch-lifecycle", switched.model_dump(mode="json"))
    assert not switched.isError, switched
    observer = world.coord / "logs" / "observer"
    shutil.copytree(observer, world.receipts / "observer-before")
    mark = world.mark()
    ambient_before = capture_preview_state(world)
    world.record("ambient-before", ambient_before.snapshot)
    observed = world.start(leaf_id="FRESH-L2", worktree_name="fresh-preview-l2")
    assert not observed.isError, observed
    assert observed.structuredContent is not None
    assert observed.structuredContent["state"] == "would-start"
    after_state = attach_record(world, world.snapshot())
    world.record("ambient-after", after_state.snapshot)
    shutil.copytree(observer, world.receipts / "observer-after")
    transcript_rel = transcript_rel_of(world)
    days = admitted_call_days(ambient_before, after_state)
    changed = [
        path
        for path in ambient_before.snapshot["paths"]
        if ambient_before.snapshot["paths"][path] != after_state.snapshot["paths"].get(path)
        and not is_call_record_path(path, transcript_rel, days, river_rel=river_rel_of(world))
    ]
    assert len(changed) == 1
    event_path = changed[0]
    assert event_path.startswith("ar-coordination/logs/observer/lifecycles/")
    assert event_path.endswith("/events.jsonl")
    relative = Path(event_path).relative_to("ar-coordination/logs/observer")
    before_bytes = (world.receipts / "observer-before" / relative).read_bytes()
    after_bytes = (world.receipts / "observer-after" / relative).read_bytes()
    assert after_bytes.startswith(before_bytes)
    event = json.loads(after_bytes[len(before_bytes) :])
    assert event["kind"] == "tool.completed" and event["data"]["tool"] == "worktree_start"
    _assert_observer_event_kept_kind_mode(
        ambient_before.snapshot["paths"], after_state.snapshot["paths"], event_path
    )
    assert set(ambient_before.snapshot["paths"]) <= set(after_state.snapshot["paths"])
    assert_preview_read_only(
        ambient_before,
        after_state,
        expected_tools=world.log_tools_since(mark),
        # Under the ambient lifecycle the call's event goes to that lifecycle's
        # file (proved above), not to the workspace river.
        expected_river_tools=[],
        unchanged_except=frozenset(changed),
    )
    assert ambient_before.snapshot["repositories"] == after_state.snapshot["repositories"]


def test_public_apply_re_resolves_source_and_persisted_parent_preview_stays_read_only(
    tmp_path: Path,
) -> None:
    world = PreviewFixture(tmp_path, commanded=True)
    preview = world.start()
    assert preview.structuredContent is not None and preview.structuredContent["ok"], preview
    git(world.code, "switch", "super")
    (world.code / "changed.txt").write_text("source advanced after preview\n", encoding="utf-8")
    git(world.code, "add", "changed.txt")
    git(world.code, "commit", "-m", "Advance protected source")
    new_base = git(world.code, "rev-parse", "super")
    git(world.memory, "switch", "super")
    (world.memory / "changed.md").write_text("# Advanced memory\n", encoding="utf-8")
    git(world.memory, "add", "changed.md")
    git(
        world.memory,
        "commit",
        "-m",
        render_memory_content_message("Advance memory source", new_base),
    )
    result = world.start(dry_run=False)
    assert not result.isError, result
    assert result.structuredContent is not None and result.structuredContent["ok"], result
    parent = load_contract(world.parent_path)
    leaf = load_contract(Path(result.structuredContent["contract_path"]))
    assert parent.code_base_commit == leaf.code_base_commit == new_base
    assert parent.code_source_branch == "super"
    assert require_parent_series(leaf, operation="apply preservation") == parent
    assert observe_atomic_series(parent).state == "active"
    assert leaf.code_worktree.is_dir()
    assert parent.memory_base_commit == leaf.memory_base_commit
    assert parent.memory_base_commit == git(world.memory, "rev-parse", "super")
    assert leaf.memory_worktree is not None and leaf.memory_worktree.is_dir()
    world.add_leaf("FRESH-L2")
    _assert_ambient_request_observes_only_tool_completion(world)
    world.restart_session()
    mark = world.mark()
    before = capture_preview_state(world)
    world.record("persisted-parent-before", before.snapshot)
    result = world.start(leaf_id="FRESH-L2", worktree_name="fresh-preview-l2")
    assert not result.isError, result
    assert result.structuredContent is not None
    assert result.structuredContent["state"] == "would-start"
    after = world.snapshot()
    world.record("persisted-parent-after", after)
    assert_preview_read_only(
        before,
        attach_record(world, after),
        expected_tools=world.log_tools_since(mark),
        expected_river_tools=world.river_tools_since(mark),
    )


def test_preview_handoff_is_failure_sensitive_and_cannot_supply_durable_authority(
    tmp_path: Path,
) -> None:
    world = PreviewFixture(tmp_path, commanded=True)
    planned = world.plan()
    parent = planned.preview_parent
    assert parent is not None
    mark = world.mark()
    before = capture_preview_state(world)
    world.record("handoff-before", before.snapshot)
    for operation in ("worktree_start", "worktree_attach", "worktree_sync", "worktree_closeout"):
        with pytest.raises(RuntimeError, match="requires its exact parent series contract"):
            require_parent_series(planned.contract, operation=operation, preview_parent=parent)
    with pytest.raises(RuntimeError, match="requires its exact parent series contract"):
        require_parent_series(planned.contract, operation="worktree_start", dry_run=True)
    assert (
        require_parent_series(
            planned.contract, operation="worktree_start", dry_run=True, preview_parent=parent
        )
        is parent
    )
    for foreign in (
        replace(parent, task_root=world.task_root.parent / "foreign"),
        replace(parent, code_source_branch="main"),
        replace(parent, code_work_branch="main"),
        replace(parent, memory_source_branch="main"),
        replace(parent, memory_work_branch="main"),
    ):
        with pytest.raises(RuntimeError):
            require_parent_series(
                planned.contract, operation="worktree_start", dry_run=True, preview_parent=foreign
            )
    with patch.object(
        start_module,
        "require_parent_series",
        side_effect=lambda contract, **_kw: require_parent_series(
            contract, operation="worktree_start"
        ),
    ):
        admission_removed = world.start()
    assert admission_removed.isError
    assert "worktree_start requires its exact parent series contract" in str(
        admission_removed.content
    )
    world.record("preview-admission-removed", admission_removed.model_dump(mode="json"))
    with patch.object(
        start_module, "build_start_contract", return_value=replace(planned, preview_parent=None)
    ):
        dropped = world.start()
    assert dropped.isError or not dropped.structuredContent or not dropped.structuredContent["ok"]
    world.record("dropped-handoff", dropped.model_dump(mode="json"))
    original_memory = start_memory.prepare_memory_for_start
    with patch.object(
        start_module,
        "prepare_memory_for_start",
        side_effect=lambda contract, args, **_kw: original_memory(contract, args),
    ):
        dropped_memory = world.start()
    assert dropped_memory.isError
    assert "task-derived memory source branch is missing" in str(dropped_memory.content)
    world.record("dropped-memory-handoff", dropped_memory.model_dump(mode="json"))
    after = world.snapshot()
    world.record("handoff-after", after)
    assert_preview_read_only(
        before,
        attach_record(world, after),
        expected_tools=world.log_tools_since(mark),
        expected_river_tools=world.river_tools_since(mark),
    )


def test_foreign_or_unreadable_persisted_parent_after_preview_is_never_replaced(
    tmp_path: Path,
) -> None:
    world = PreviewFixture(tmp_path, commanded=True)
    planned = world.plan()
    parent = planned.preview_parent
    assert parent is not None
    result = world.start()
    assert result.structuredContent is not None and result.structuredContent["ok"]
    for index, foreign in enumerate(
        (
            replace(parent, code_source_branch="main"),
            replace(parent, memory_source_branch="main"),
            replace(parent, task_name="foreign"),
        )
    ):
        write_contract(world.parent_path, foreign)
        mark = world.mark()
        before = capture_preview_state(world)
        world.record(f"foreign-{index}-before", before.snapshot)
        for dry_run in (True, False):
            refused = world.start(dry_run=dry_run)
            assert not refused.isError, refused
            assert refused.structuredContent is not None
            assert not refused.structuredContent["ok"]
            assert refused.structuredContent["status"] == "atomic-series-contract-edge-mismatch"
            assert refused.structuredContent["nextTool"] == "worktree_status"
            after = world.snapshot()
            world.record(f"foreign-after-call-{world.calls}", after)
            assert_preview_read_only(
                before,
                attach_record(world, after),
                expected_tools=world.log_tools_since(mark),
                expected_river_tools=world.river_tools_since(mark),
            )
    world.parent_path.write_bytes(b"\xff unreadable parent")
    mark = world.mark()
    before = capture_preview_state(world)
    world.record("unreadable-before", before.snapshot)
    for dry_run in (True, False):
        refused = world.start(dry_run=dry_run)
        assert refused.isError or (
            refused.structuredContent and not refused.structuredContent["ok"]
        )
        after = world.snapshot()
        world.record(f"unreadable-after-call-{world.calls}", after)
        assert_preview_read_only(
            before,
            attach_record(world, after),
            expected_tools=world.log_tools_since(mark),
            expected_river_tools=world.river_tools_since(mark),
        )


def test_invalid_public_leaf_source_base_and_protected_ref_stay_refused_without_writes(
    tmp_path: Path,
) -> None:
    world = PreviewFixture(tmp_path, commanded=True)
    mark = world.mark()
    before = capture_preview_state(world)
    world.record("before-refusals", before.snapshot)
    for arguments in (
        {"leaf_id": "FOREIGN-LEAF"},
        {"source_branch": "main"},
        {"source_branch": "0" * 40},
        {"work_branch": "main"},
        {"work_branch": f"ar/{MASTER}"},
    ):
        refused = world.start(**arguments)
        assert refused.isError or (
            refused.structuredContent and not refused.structuredContent["ok"]
        )
        after = world.snapshot()
        world.record(f"refused-after-call-{world.calls}", after)
        assert_preview_read_only(
            before,
            attach_record(world, after),
            expected_tools=world.log_tools_since(mark),
            expected_river_tools=world.river_tools_since(mark),
        )
    git(world.code, "branch", f"ar/{MASTER}", "super")
    mark = world.mark()
    occupied = capture_preview_state(world)
    world.record("occupied-before", occupied.snapshot)
    refused = world.start()
    assert refused.isError
    assert "series branch exists without its task-bound contract" in str(refused.content)
    after = world.snapshot()
    world.record("occupied-after", after)
    assert_preview_read_only(
        occupied,
        attach_record(world, after),
        expected_tools=world.log_tools_since(mark),
        expected_river_tools=world.river_tools_since(mark),
    )
    assert not world.parent_path.exists()


def test_organizational_direct_super_preview_keeps_its_distinct_parent_route(
    tmp_path: Path,
) -> None:
    world = PreviewFixture(tmp_path, commanded=True)
    master = read_task_doc(world.task_root / "task.json")
    write_task_doc(world.task_root, master.model_copy(update={"executionNature": "organizational"}))
    sprint_root = world.coord / "tasks" / REPO / "sprint"
    sprint = read_task_doc(sprint_root / "task.json").model_dump(by_alias=True)
    sprint["executionGraph"] = {
        "nodes": [{"ref": {"repository": REPO, "path": f"{MASTER}/task.json"}}],
        "edges": [],
    }
    write_task_doc(sprint_root, TaskDocument.model_validate(sprint))
    planned = world.plan()
    assert planned.preview_parent is None
    mark = world.mark()
    before = capture_preview_state(world)
    world.record("organizational-before", before.snapshot)
    result = world.start()
    assert not result.isError, result
    assert result.structuredContent is not None
    assert result.structuredContent["state"] == "would-start"
    contract = result.structuredContent["contract"]
    assert contract["code_source_branch"] == contract["memory_source_branch"] == "super"
    assert not contract["parent_contract_path"]
    after = world.snapshot()
    world.record("organizational-after", after)
    assert_preview_read_only(
        before,
        attach_record(world, after),
        expected_tools=world.log_tools_since(mark),
        expected_river_tools=world.river_tools_since(mark),
    )


def test_public_partial_bootstrap_preview_is_read_only_then_apply_recovers_same_journal(
    tmp_path: Path,
) -> None:
    world = PreviewFixture(tmp_path, commanded=True)
    original = start_contract._require_bootstrap_ref
    journal = world.coord / "logs" / "worktree-series-bootstrap" / REPO / f"{MASTER}.json"
    retained_journal = None
    for memory_exists in (False, True):

        def interrupt_memory(
            ref: start_contract._BootstrapRef,
            *,
            authority: object | None = None,
            memory_created: bool = memory_exists,
        ):
            if ref.repository == world.memory and not memory_created:
                raise RuntimeError("test interruption before memory protected-ref creation")
            original(ref, authority=authority)
            if ref.repository == world.memory:
                raise RuntimeError("test interruption after memory protected-ref creation")

        with patch.object(start_contract, "_require_bootstrap_ref", side_effect=interrupt_memory):
            interrupted = world.start(dry_run=False)
        assert interrupted.isError
        assert not world.parent_path.exists() and journal.is_file()
        if retained_journal is None:
            retained_journal = journal.read_bytes()
            (world.receipts / "actual-bootstrap-journal.json").write_bytes(retained_journal)
        assert journal.read_bytes() == retained_journal
        assert git(world.code, "rev-parse", f"ar/{MASTER}") == world.base
        named_ref = f"refs/heads/ar/{MASTER}"
        memory_refs = git(world.memory, "for-each-ref", "--format=%(refname)").splitlines()
        assert (named_ref in memory_refs) == memory_exists
        if memory_exists:
            assert git(world.memory, "rev-parse", named_ref) == git(
                world.memory, "rev-parse", "super"
            )
        state = "existing" if memory_exists else "would-create"
        mark = world.mark()
        before = capture_preview_state(world)
        world.record(f"journal-before-{state}", before.snapshot)
        for _ in range(2):
            preview = world.start()
            assert not preview.isError, preview
            assert preview.structuredContent is not None and preview.structuredContent["ok"]
            assert preview.structuredContent["memory"]["memorySourceBranch"] == {
                "state": state,
                "branch": f"ar/{MASTER}",
            }
            after = world.snapshot()
            world.record(f"journal-after-call-{world.calls}", after)
            assert_preview_read_only(
                before,
                attach_record(world, after),
                expected_tools=world.log_tools_since(mark),
                expected_river_tools=world.river_tools_since(mark),
            )
    recovered = world.start(dry_run=False)
    assert not recovered.isError, recovered
    assert recovered.structuredContent is not None and recovered.structuredContent["ok"]
    parent = load_contract(world.parent_path)
    leaf = load_contract(Path(recovered.structuredContent["contract_path"]))
    assert require_parent_series(leaf, operation="recovered apply") == parent
    assert observe_atomic_series(parent).state == "active"
    assert parent.code_base_commit == leaf.code_base_commit == world.base
    assert parent.memory_base_commit == leaf.memory_base_commit
    assert not journal.exists()


def _today_transcript_names(world: PreviewFixture) -> tuple[Path, Path]:
    """Today's line file and detail folder in one fixture's transcript root."""
    transcript = world.coord / "logs" / "mcp"
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    return transcript / f"ar-tools-{day}.jsonl", transcript / f"ar-tools-{day}"


def test_admitted_record_rejects_a_file_beside_the_line_file(tmp_path: Path) -> None:
    """MIK-R131 rule 6: a file with another name beside the line file fails the proof."""
    world = PreviewFixture(tmp_path)
    before = capture_preview_state(world)
    (world.coord / "logs" / "mcp" / "other.txt").write_text("not the record\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape("other.txt")):
        assert_preview_read_only(before, capture_preview_state(world), expected_tools=[])


def test_admitted_record_rejects_a_rewritten_earlier_line(tmp_path: Path) -> None:
    """MIK-R131 rule 6: changing an earlier line of the line file fails the proof."""
    world = PreviewFixture(tmp_path)
    line_file, _ = _today_transcript_names(world)
    line_file.write_text(
        "\n"
        + json.dumps({"detail": None, "tool": "worktree_start"}, sort_keys=True)
        + "\n"
        + json.dumps({"detail": None, "tool": "worktree_start"}, sort_keys=True),
        encoding="utf-8",
    )
    before = capture_preview_state(world)
    line_file.write_text(
        line_file.read_text(encoding="utf-8").replace("worktree_start", "another-tool", 1),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="does not grow by appending"):
        assert_preview_read_only(before, capture_preview_state(world), expected_tools=[])


def test_admitted_record_rejects_a_detail_file_no_line_names(tmp_path: Path) -> None:
    """MIK-R131 rule 6: a new detail file that no new line names fails the proof."""
    world = PreviewFixture(tmp_path)
    before = capture_preview_state(world)
    _, detail_dir = _today_transcript_names(world)
    detail_dir.mkdir(parents=True, exist_ok=True)
    (detail_dir / "rogue-detail.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape("rogue-detail.json")):
        assert_preview_read_only(before, capture_preview_state(world), expected_tools=[])


def test_admitted_record_rejects_a_rewritten_earlier_event(tmp_path: Path) -> None:
    """Ruling L131-river rule 6: an earlier event of the river that was changed fails the proof."""
    world = PreviewFixture(tmp_path)
    river_file = world.coord / "logs" / "observer" / "workspace" / "events.jsonl"
    river_file.write_text(
        "\n".join(
            json.dumps(
                {"kind": "tool.completed", "data": {"tool": "worktree_start", "ok": True}},
                sort_keys=True,
            )
            for _ in range(2)
        )
        + "\n",
        encoding="utf-8",
    )
    before = capture_preview_state(world)
    river_file.write_text(
        river_file.read_text(encoding="utf-8").replace('"worktree_start"', '"another-tool"', 1),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="does not grow by appending"):
        assert_preview_read_only(before, capture_preview_state(world), expected_tools=[])


def test_admitted_record_rejects_a_stale_day_line_file(tmp_path: Path) -> None:
    """L131-R1-F01: a record-shaped line file from another day is not the call's record."""
    world = PreviewFixture(tmp_path)
    before = capture_preview_state(world)
    (world.coord / "logs" / "mcp" / "ar-tools-2000-01-01.jsonl").write_text(
        "\n" + json.dumps({"detail": None, "tool": "worktree_start"}, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match=re.escape("ar-tools-2000-01-01.jsonl")):
        assert_preview_read_only(before, capture_preview_state(world), expected_tools=[])


def test_admitted_record_rejects_a_stale_day_detail_folder(tmp_path: Path) -> None:
    """L131-R1-F01: a record-shaped detail folder from another day is not the call's record."""
    world = PreviewFixture(tmp_path)
    before = capture_preview_state(world)
    stale = world.coord / "logs" / "mcp" / "ar-tools-2000-01-01"
    stale.mkdir(parents=True)
    (stale / "stale-detail.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape("ar-tools-2000-01-01")):
        assert_preview_read_only(before, capture_preview_state(world), expected_tools=[])


def test_admitted_record_rejects_a_directory_touch_without_entry(tmp_path: Path) -> None:
    """L131-R1-F02: a directory mtime change with no admitted entry creation fails the proof."""
    world = PreviewFixture(tmp_path)
    before = capture_preview_state(world)
    transcript = world.coord / "logs" / "mcp"
    moment = time.time_ns() + 5_000_000_000
    os.utime(transcript, ns=(moment, moment))
    with pytest.raises(AssertionError, match="without an admitted entry"):
        assert_preview_read_only(before, capture_preview_state(world), expected_tools=[])


def test_observer_event_mode_change_is_rejected(tmp_path: Path) -> None:
    """L131-R1-F03: a mode change of the admitted observer event file fails; its append passes."""
    world = PreviewFixture(tmp_path)
    switched = anyio.run(world._call, "switch_lifecycle", {})
    assert not switched.isError, switched
    world.add_leaf("FRESH-L2")
    before = capture_preview_state(world)
    observed = world.start(leaf_id="FRESH-L2", worktree_name="fresh-preview-l2")
    assert not observed.isError, observed
    assert observed.structuredContent is not None
    assert observed.structuredContent["state"] == "would-start"
    matches = sorted((world.coord / "logs" / "observer" / "lifecycles").rglob("events.jsonl"))
    assert len(matches) == 1
    event_path = matches[0].relative_to(world.root).as_posix()
    after = attach_record(world, world.snapshot())
    _assert_observer_event_kept_kind_mode(
        before.snapshot["paths"], after.snapshot["paths"], event_path
    )
    current_mode = os.stat(world.root / event_path).st_mode & 0o777
    new_mode = 0o600 if current_mode != 0o600 else 0o644
    os.chmod(world.root / event_path, new_mode)
    with pytest.raises(AssertionError, match="kind/mode"):
        _assert_observer_event_kept_kind_mode(
            before.snapshot["paths"],
            attach_record(world, world.snapshot()).snapshot["paths"],
            event_path,
        )
