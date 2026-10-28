"""Draft native reader/digest oracles for the shared task input plan; not yet executed."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore, queue_store_paths
from agents_remember.controlplane.held_store_discovery import (
    EXECUTIONS_DIRECTORY,
    RECEIPT_WALK_DEPTH,
    ReceiptWalk,
    receipt_roots,
)
from agents_remember.controlplane.held_store_layout import HELD_QUEUE_FILE, HeldAuthorityReader
from agents_remember.kernel.primitives.observer_paths import LANDING_FINAL_BASENAME
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.projections.contract_snapshot import ContractSnapshotCache
from agents_remember.serving.projections.landing_state import LandingStateRefresher
from agents_remember.serving.projections.snapshots_impl import _closeout_queue, _common
from agents_remember.serving.projections.task_input_plan import task_input_plan
from agents_remember.tasks.task_paths import iter_leaf_enclosure_contracts
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    contract_to_text,
    default_contract,
)


def put(path: Path, text: str = "{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def holder(root: Path) -> Path:
    return root / "notes/reports" / EXECUTIONS_DIRECTORY


def test_plan_covers_paths_the_native_readers_really_open(tmp_path: Path) -> None:
    task = tmp_path / "tasks/r/t"
    document = put(task / "task.json", json.dumps({"schema": "ar-task-document/v1"}))
    leaf = put(task / "leaf.json", "not-json")
    hidden_leaf = put(task / ".retained.json", "not-json")
    landing_contract = replace(
        default_contract(
            ContractTask("t", "r", tmp_path, "light-task", "external"),
            leaf=LeafIdentity("l"),
            code=RepoBranchPlan(tmp_path / "code", "feat", "work", "a" * 40),
            memory=RepoBranchPlan(tmp_path / "memory", "mem-main", "work", "b" * 40),
        ),
        cleanup="completed",
        closeout_status="completed",
    )
    contract = put(landing_contract.contract_path, contract_to_text(landing_contract))
    # The final belongs to this completed arc, after its last contract write.
    now = datetime.now(UTC)
    landing_facts = [
        {"kind": kind, "label": label, "state": state, "factState": "observed"}
        for kind, label, state in (
            ("origin-feat", "origin/feat", "pushed"),
            ("origin-main", "origin/main", "planned"),
            ("origin-mem-main", "origin/mem-main", "pushed"),
            ("pr", "PR #128", "merged"),
        )
    ]
    landing_final = put(
        contract.parent / LANDING_FINAL_BASENAME,
        json.dumps({"frozenAt": now.isoformat(), "facts": landing_facts}),
    )
    landing = LandingStateRefresher(
        McpRuntimeConfig(
            config_path=tmp_path / "settings.json",
            coordination_root=tmp_path,
            workspace_root=tmp_path,
            transcript_root=tmp_path / "logs/mcp",
        )
    )
    store = CloseoutQueueStore(tmp_path, TaskDocumentRef(repository="r", path="t/task.json"))
    put(store.state_path)
    ordinary = put(task / "notes/reports/noise.json")
    # D561 admits canonical live task and fixed coordination holders only.
    receipt_paths = [
        put(holder(task) / "worker/sessions/one.json"),
        put(holder(tmp_path) / "worker/sessions/two.json"),
    ]
    excluded_receipts = [
        put(holder(tmp_path / "tasks/r/0_archive/old") / "worker/sessions/three.json"),
        put(holder(tmp_path / "tasks/odd/level/place") / "worker/sessions/four.json"),
    ]
    ignored = put(holder(task) / "history/never.json")
    plan = task_input_plan(tmp_path / "tasks")
    assert plan.complete, plan.errors
    consumed: set[Path] = set()
    original = Path.read_text
    original_bytes = Path.read_bytes

    def read(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> str:
        consumed.add(path)
        return original(path, encoding=encoding, errors=errors, newline=newline)

    def read_bytes(path: Path) -> bytes:
        consumed.add(path)
        return original_bytes(path)

    with (
        mock.patch.object(Path, "read_text", read),
        mock.patch.object(Path, "read_bytes", read_bytes),
    ):
        _common._iter_task_document_payloads(tmp_path / "tasks", now=None)
        ContractSnapshotCache().build(tmp_path / "tasks")
        assert landing.current(landing_contract, now=now) == landing_facts
        store.read_raw(timestamp="2026-10-25T00:00:00+00:00")
        authority = HeldAuthorityReader()
        for _ in range(100):
            authority.read(tmp_path)
            if not authority.pending:
                break
        else:
            pytest.fail("small native receipt fixture did not complete discovery")
        authority.reset()
    assert {
        document,
        leaf,
        hidden_leaf,
        contract,
        landing_final,
        store.state_path,
        *receipt_paths,
    } <= consumed
    assert ordinary not in consumed and ignored not in consumed
    assert not consumed.intersection(excluded_receipts)
    for path in consumed:
        # Existing fixed watcher roots keep covering taskless receipts and held queue.
        if path.is_relative_to(tmp_path / "tasks"):
            assert plan.accepts_file(path), f"native reader opened unplanned input {path}"
        else:
            assert path == tmp_path / "temp" / HELD_QUEUE_FILE or path == receipt_paths[1]


def test_input_files_is_fresh_and_report_noise_never_becomes_semantic(tmp_path: Path) -> None:
    task = tmp_path / "tasks/r/t"
    first = put(task / "task.json")
    report = put(task / "notes/reports/report.json")
    plan = task_input_plan(tmp_path / "tasks")
    before = set(plan.input_files())
    assert first in before and report not in before
    second = put(task / "another-leaf.json")
    assert plan.accepts_file(second)
    assert second in set(plan.input_files()), "plan must not freeze a file listing"
    assert task_input_plan(tmp_path / "tasks") == plan, "file writes must not change watch topology"
    assert not plan.accepts_file(report)
    assert not plan.accepts_file(task / "notes/report.md")


def test_archive_task_and_receipt_inputs_are_excluded(tmp_path: Path) -> None:
    archived = tmp_path / "tasks/r/0_archive/old"
    doc = put(archived / "task.json")
    contract = put(archived / "enclosures/l/series-contract.md")
    receipt = put(holder(archived) / "worker/sessions/request.json")
    plan = task_input_plan(tmp_path / "tasks")
    assert not plan.accepts_file(doc)
    assert not plan.accepts_file(contract)
    assert receipt.parent.parent.parent not in receipt_roots(tmp_path)
    assert not plan.accepts_file(receipt)


@pytest.mark.parametrize("parts", [(), ("r",), ("r", "t")])
@pytest.mark.parametrize("symlink_leaf", [False, True])
def test_contract_admission_matches_native_owner_layouts(
    tmp_path: Path, parts: tuple[str, ...], symlink_leaf: bool
) -> None:
    tasks = tmp_path / "tasks"
    task = tasks.joinpath(*parts)
    put(task / "task.json")
    leaf = task / "enclosures/l"
    if symlink_leaf:
        target = tmp_path / "linked-enclosure"
        target.mkdir()
        leaf.parent.mkdir(parents=True)
        leaf.symlink_to(target, target_is_directory=True)
    contract = put(leaf / "series-contract.md")
    decoy = put(task / "notes/evidence/enclosures/copy/series-contract.md")
    expected = set(iter_leaf_enclosure_contracts(tasks))
    assert contract in expected and decoy not in expected
    plan = task_input_plan(tasks)
    assert plan.complete, plan.errors
    assert all(plan.accepts_file(path) for path in expected)
    assert expected <= set(plan.input_files())
    assert not plan.accepts_file(decoy)


def test_receipt_root_discovery_only_uses_live_canonical_task_holders(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    task = tasks / "r/t"
    put(task / "task.json")
    kept = put(holder(task) / "receipt.json")
    top = put(holder(tmp_path) / "receipt.json")
    excluded = [
        put(holder(task / "nested") / "receipt.json"),
        put(holder(tasks / "r/0_archive/old") / "receipt.json"),
        put(holder(tasks / "r") / "receipt.json"),
        put(holder(tasks / "r/deep/nested/task") / "receipt.json"),
    ]
    roots = set(receipt_roots(tmp_path))
    assert roots == {holder(task), holder(tmp_path)}
    plan = task_input_plan(tasks)
    assert plan.complete, plan.errors
    assert plan.accepts_file(kept)
    assert not plan.accepts_file(top), (
        "fixed coordination holder belongs to its existing watch root"
    )
    assert not any(plan.accepts_file(path) for path in excluded)


def test_receipt_content_skip_rules_match_native_walk(tmp_path: Path) -> None:
    root = holder(tmp_path / "tasks/r/t")
    kept = put(root / ".retained/receipt.json")
    skipped = [put(root / name / "receipt.json") for name in ("history", "message-bindings")]
    discovery_skipped = put(holder(tmp_path / "tasks/r/t/nested") / "receipt.json")
    walk = ReceiptWalk(root)
    native: set[Path] = set()
    while not walk.done:
        native.update(Path(path) for path in walk.take(256))
    assert walk.complete and kept in native
    assert not native.intersection(skipped)
    plan = task_input_plan(tmp_path / "tasks")
    assert plan.complete, plan.errors
    assert plan.accepts_file(kept)
    assert not any(plan.accepts_file(path) for path in skipped)
    assert not plan.accepts_file(discovery_skipped)


def test_receipt_depth_exhaustion_is_incomplete_like_native_walk(tmp_path: Path) -> None:
    root = holder(tmp_path / "tasks/r/t")
    too_deep = root.joinpath(*[str(i) for i in range(RECEIPT_WALK_DEPTH)])
    put(too_deep / "receipt.json")
    walk = ReceiptWalk(root)
    while not walk.done:
        walk.take(256)
    assert not walk.complete
    plan = task_input_plan(tmp_path / "tasks")
    assert not plan.complete
    assert plan.errors


def test_creating_receipt_depth_boundary_requests_replan_then_refuses(tmp_path: Path) -> None:
    root = holder(tmp_path / "tasks/r/t")
    deepest = root.joinpath(*[str(i) for i in range(RECEIPT_WALK_DEPTH - 1)])
    put(deepest / "receipt.json")
    plan = task_input_plan(tmp_path / "tasks")
    assert plan.complete, plan.errors
    boundary = deepest / "new-depth"
    assert plan.topology_change(boundary, is_directory=True)
    boundary.mkdir()
    current = task_input_plan(tmp_path / "tasks")
    assert not current.complete
    assert current.errors


def test_absent_receipt_chain_and_removed_planned_directory_trigger_topology(
    tmp_path: Path,
) -> None:
    task = tmp_path / "tasks/r/t"
    put(task / "task.json")
    plan = task_input_plan(tmp_path / "tasks")
    assert plan.topology_candidate(task / "notes")
    receipt = put(holder(task) / "worker/sessions/request.json")
    new_plan = task_input_plan(tmp_path / "tasks")
    assert new_plan.accepts_file(receipt)
    expected = {directory.path for directory in new_plan.directories}
    assert holder(task) in expected
    receipt.unlink()
    receipt.parent.rmdir()
    assert new_plan.topology_candidate(receipt.parent), "deleted topology must not require stat"


def test_late_enclosure_is_admitted_after_topology_replan(tmp_path: Path) -> None:
    task = tmp_path / "tasks/r/t"
    put(task / "task.json")
    plan = task_input_plan(tmp_path / "tasks")
    assert plan.topology_candidate(task / "enclosures")
    contract = put(task / "enclosures/new-leaf/series-contract.md")
    current = task_input_plan(tmp_path / "tasks")
    assert current.complete, current.errors
    assert current != plan
    assert current.accepts_file(contract)
    assert contract in set(current.input_files())


@pytest.mark.parametrize("name", ["task.json", "series-contract.md"])
def test_contract_recognition_files_trigger_topology_even_when_not_directories(
    tmp_path: Path, name: str
) -> None:
    tasks = tmp_path / "tasks"
    contract = put(tasks / "r/t/enclosures/l/series-contract.md")
    original = task_input_plan(tasks)
    assert original.accepts_file(contract)
    marker = tasks / "r" / name
    assert original.topology_change(marker, is_directory=False)
    put(marker)
    changed = task_input_plan(tasks)
    assert changed != original
    assert contract not in set(iter_leaf_enclosure_contracts(tasks))
    assert not changed.accepts_file(contract)
    assert changed.topology_change(marker, is_directory=False)
    marker.unlink()
    assert task_input_plan(tasks) == original


def test_admission_and_topology_predicates_do_not_read_the_filesystem(tmp_path: Path) -> None:
    task = tmp_path / "tasks/r/t"
    doc = put(task / "task.json")
    plan = task_input_plan(tmp_path / "tasks")
    with mock.patch.object(Path, "stat", side_effect=AssertionError("predicate performed I/O")):
        assert plan.accepts_file(doc)
        assert not plan.accepts_file(task / "notes/report.json")
        assert plan.topology_candidate(task / "notes")


def test_digest_ignores_reports_and_build_artifacts_but_tracks_inputs(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    doc = put(tasks / "r/t/task.json", '{"title":"before"}')
    state, build = queue_store_paths(tmp_path, TaskDocumentRef(repository="r", path="t/task.json"))
    put(state)
    report = put(tasks / "r/t/notes/reports/report.json")
    put(build)
    first = _closeout_queue.tasks_tree_digest(tasks)
    assert first is not None
    report.write_text('{"ordinary":"changed"}')
    build.write_text('{"offside":"changed"}')
    assert _closeout_queue.tasks_tree_digest(tasks) == first
    before = doc.stat()
    doc.write_text('{"title":"after!"}')
    os.utime(doc, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert doc.stat().st_size == before.st_size
    changed = _closeout_queue.tasks_tree_digest(tasks, paths=[doc])
    assert changed is not None and changed != first
    assert changed == _closeout_queue.tasks_tree_digest(tasks)
    assert task_input_plan(tmp_path / "tasks").accepts_file(doc)


@pytest.mark.parametrize(
    "relative",
    [
        "task.json",
        "enclosures/l/series-contract.md",
        f"enclosures/l/{LANDING_FINAL_BASENAME}",
        "artifacts/closeout-candidates.json",
        f"notes/reports/{EXECUTIONS_DIRECTORY}/worker/sessions/receipt.json",
    ],
)
def test_each_native_input_family_changes_the_digest(tmp_path: Path, relative: str) -> None:
    tasks = tmp_path / "tasks"
    task = tasks / "r/t"
    put(task / "task.json")
    path = put(task / relative, "before")
    first = _closeout_queue.tasks_tree_digest(tasks)
    assert first is not None
    path.write_text("after!")
    changed = _closeout_queue.tasks_tree_digest(tasks, paths=[path])
    assert changed is not None and changed != first
    assert changed == _closeout_queue.tasks_tree_digest(tasks)


def test_digest_does_not_spend_input_budget_on_ordinary_reports(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    put(tasks / "r/t/task.json")
    for number in range(12):
        put(tasks / f"r/t/notes/reports/{number}.json")
    # Scale the cap instead of creating 100,000 disposable report files.
    with mock.patch.object(_closeout_queue, "_TASKS_DIGEST_FILE_CAP", 2):
        assert _closeout_queue.tasks_tree_digest(tasks) is not None


def test_digest_still_refuses_too_many_semantic_inputs(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    for number in range(3):
        put(tasks / f"r/t/leaf-{number}.json")
    with mock.patch.object(_closeout_queue, "_TASKS_DIGEST_FILE_CAP", 2):
        assert _closeout_queue.tasks_tree_digest(tasks) is None


def test_discovery_refusal_never_becomes_a_complete_empty_plan(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    put(tasks / "r/t/task.json")
    successful = _closeout_queue.tasks_tree_digest(tasks)
    assert successful is not None
    with mock.patch("os.scandir", side_effect=PermissionError("injected discovery failure")):
        plan = task_input_plan(tasks)
        assert not plan.complete
        assert plan.errors
        assert _closeout_queue.tasks_tree_digest(tasks) is None
    assert _closeout_queue.tasks_tree_digest(tasks) == successful


def test_entry_classification_failure_makes_discovery_incomplete(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    put(tasks / "r/t/task.json")
    blocked = tasks / "r"
    original = os.scandir

    @contextmanager
    def scanned(path: str | os.PathLike[str]) -> Iterator[Iterator[os.DirEntry[str]]]:
        with original(path) as entries:

            def observed_entries() -> Iterator[os.DirEntry[str]]:
                for entry in entries:
                    if Path(entry.path) != blocked:
                        yield entry
                        continue
                    damaged = mock.Mock(wraps=entry)
                    damaged.name = entry.name
                    damaged.path = entry.path
                    damaged.is_dir.side_effect = PermissionError("injected entry classification")
                    yield damaged

            yield observed_entries()

    with mock.patch("os.scandir", scanned):
        plan = task_input_plan(tasks)
        assert not plan.complete
        assert plan.errors
        assert _closeout_queue.tasks_tree_digest(tasks) is None


def test_digest_read_failure_cannot_reuse_last_success(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    doc = put(tasks / "r/t/task.json")
    successful = _closeout_queue.tasks_tree_digest(tasks)
    assert successful is not None
    original = Path.stat

    def fail(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == doc:
            raise PermissionError("injected semantic-input stat failure")
        return original(path, follow_symlinks=follow_symlinks)

    with mock.patch.object(Path, "stat", fail):
        assert _closeout_queue.tasks_tree_digest(tasks) is None
    assert _closeout_queue.tasks_tree_digest(tasks) == successful


def test_absent_task_root_keeps_complete_parent_discovery(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    plan = task_input_plan(tasks)
    assert plan.complete, plan.errors
    assert plan.shallow_dirs == (tmp_path,)
    assert list(plan.input_files()) == []
    assert plan.topology_change(tasks, is_directory=True)
    document = put(tasks / "r/t/task.json")
    current = task_input_plan(tasks)
    assert current.complete, current.errors
    assert current.accepts_file(document)
    assert document in set(current.input_files())


def test_unreadable_task_root_is_not_treated_as_absent(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    original = Path.stat

    def denied(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == tasks:
            raise PermissionError("task root stat denied")
        return original(path, follow_symlinks=follow_symlinks)

    with mock.patch.object(Path, "stat", denied):
        plan = task_input_plan(tasks)
    assert not plan.complete
    assert any("task root stat denied" in error for error in plan.errors)
