"""Public forcing for the canonical lifecycle successor publication WAL."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest import mock

import pytest
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tools import (
    OperationControlRequest,
    worktree_operation_control_tool,
    worktree_status_tool,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.lifecycles.successor import LifecycleSuccessorPublicationIntent
from agents_remember.worktrees.integration.lifecycle import (
    lifecycle_operation_controls as controls_module,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    successor_publication_path,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from test_lifecycle_operation_controls_l2 import _dirty_closeout, _public_control


def _assert_one_attempt_one_successor(
    store: LifecycleOperationStore,
    pending: LifecycleSuccessorPublicationIntent,
) -> None:
    current = store.read()
    assert current is not None
    assert (current.generation, current.attempt) == (2, 1)
    assert current.fingerprint == pending.successor.fingerprint
    archive = store.path.with_name(f"{store.path.stem}.generation-1.json")
    archived = LifecycleOperationRecord.model_validate_json(archive.read_text(encoding="utf-8"))
    assert archived.successorFingerprint == current.fingerprint
    assert not store.path.with_name(f"{store.path.stem}.generation-2.json").exists()


def _successor_store_bytes(store: LifecycleOperationStore) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in (store.path, successor_publication_path(store.path))
        if path.exists()
    }


def _successor_publication_cut(
    store: LifecycleOperationStore,
    cut: str,
    sentinel: str,
):
    original_archive = LifecycleOperationStore._archive_generation
    original_write = LifecycleOperationStore._write
    original_retire = LifecycleOperationStore._retire_successor_intent

    def archive_cut(target, predecessor) -> None:
        if target.path == store.path:
            raise OSError(sentinel)
        original_archive(target, predecessor)

    def current_cut(target, successor) -> None:
        if target.path == store.path and successor.generation == 2:
            raise OSError(sentinel)
        original_write(target, successor)

    def retire_cut(target) -> None:
        if target.path == store.path:
            raise OSError(sentinel)
        original_retire(target)

    return {
        "before-archive": ("_archive_generation", archive_cut),
        "before-current": ("_write", current_cut),
        "before-wal-retire": ("_retire_successor_intent", retire_cut),
    }[cut]


def _blocking_archive_cut(store: LifecycleOperationStore, message: str):
    intent_written = Event()
    release = Event()
    original_archive = LifecycleOperationStore._archive_generation
    interrupted = False

    def cut(target, predecessor) -> None:
        nonlocal interrupted
        if target.path == store.path and not interrupted:
            interrupted = True
            intent_written.set()
            assert release.wait(timeout=10)
            raise OSError(message)
        original_archive(target, predecessor)

    return intent_written, release, cut


@pytest.mark.parametrize("cut", ["before-archive", "before-current", "before-wal-retire"])
def test_public_revision_successor_wal_recovers_every_store_publication_cut(
    tmp_path: Path,
    cut: str,
) -> None:
    private_sentinel = "PRIVATE-SUCCESSOR-WRITE-/secret/path"
    contract, _operation_input, store, record = _dirty_closeout(tmp_path)
    config = load_config(Path(record.input.configPath))
    revise = next(
        row for row in legal_operation_controls(contract, record) if row["action"] == "revise"
    )
    revise["arguments"]["code_commit_message"] = f"accepted successor at {cut}"

    target, effect = _successor_publication_cut(store, cut, private_sentinel)
    with mock.patch.object(LifecycleOperationStore, target, new=effect):
        interrupted = _public_control(config, revise)
    assert interrupted["ok"] is False
    assert interrupted["status"] == "lifecycle-successor-publication-interrupted"
    assert interrupted["nextTool"] == "worktree_operation_control"
    assert interrupted["nextArgs"]["action"] == "recover"
    wal = successor_publication_path(store.path)
    assert wal.is_file()
    pending = store.read_successor_intent()
    assert pending is not None
    assert pending.successor.generation == 2
    assert pending.successor.attempt == 1

    with mock.patch(
        "agents_remember.application.worktree_tools.git_worktree_manager.status_result",
        return_value=WorktreeCommandResult(
            0,
            {
                "contract_path": contract.contract_path.as_posix(),
                "task_name": contract.task_name,
            },
        ),
    ):
        status = worktree_status_tool(
            config,
            TaskRef(repo_id=contract.repo_name, contract_path=contract.contract_path.as_posix()),
        )
    closeout = next(row for row in status["lifecycleOperations"] if row["kind"] == "closeout")
    assert private_sentinel not in repr([interrupted, status, store.read_successor_intent()])
    assert closeout["generation"] == 2
    continuation = next(row for row in closeout["legalControls"] if row["action"] == "recover")
    with mock.patch.object(controls_module, "launch_detached_worker") as launch:
        completed = _public_control(config, continuation)
    assert completed["ok"] is True
    assert completed["lifecycleOperation"]["generation"] == 2
    assert not wal.exists()
    durable = store.read()
    assert durable is not None
    assert durable.fingerprint == pending.successor.fingerprint
    assert durable.input == pending.successor.input
    assert durable.predecessorFingerprint == pending.predecessor.fingerprint
    launch.assert_called_once()


def test_distinct_concurrent_revision_gets_existing_successor_and_executes_recovery(
    tmp_path: Path,
) -> None:
    contract, _operation_input, store, record = _dirty_closeout(tmp_path)
    config = load_config(Path(record.input.configPath))
    first = next(
        row for row in legal_operation_controls(contract, record) if row["action"] == "revise"
    )
    first["arguments"]["code_commit_message"] = "first accepted successor"
    intent_written, release_first, interrupted = _blocking_archive_cut(
        store,
        "hold exact successor WAL",
    )
    competing_started = Event()

    competing = dict(first)
    competing["arguments"] = {
        **first["arguments"],
        "code_commit_message": "different competing successor",
        "intent_note": "competing fresh approval",
    }

    def competing_revision() -> dict:
        competing_started.set()
        return _public_control(config, competing)

    with (
        mock.patch.object(LifecycleOperationStore, "_archive_generation", new=interrupted),
        mock.patch.object(controls_module, "launch_detached_worker") as launch,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first_future = pool.submit(_public_control, config, first)
        assert intent_written.wait(timeout=10)
        pending = store.read_successor_intent()
        assert pending is not None
        before_loser = _successor_store_bytes(store)
        competing_future = pool.submit(competing_revision)
        assert competing_started.wait(timeout=10)
        release_first.set()
        cut = first_future.result(timeout=10)
        refused = competing_future.result(timeout=10)

    assert cut["status"] == "lifecycle-successor-publication-interrupted"
    assert refused["ok"] is False
    assert refused["status"] == "lifecycle-successor-already-accepted"
    assert refused["expected"] == {
        "generation": pending.successor.generation,
        "fingerprint": pending.successor.fingerprint,
    }
    assert refused["observed"]["fingerprint"] != pending.successor.fingerprint
    assert refused["nextTool"] == "worktree_operation_control"
    assert _successor_store_bytes(store) == before_loser
    assert not store.path.with_name(f"{store.path.stem}.generation-1.json").exists()
    launch.assert_not_called()
    with mock.patch.object(controls_module, "launch_detached_worker") as launch:
        recovered = worktree_operation_control_tool(
            config,
            OperationControlRequest(**refused["nextArgs"]),
        )
    assert recovered["ok"] is True
    assert recovered["lifecycleOperation"]["generation"] == 2
    assert recovered["lifecycleOperation"]["status"] in {"queued", "running"}
    assert store.read_successor_intent() is None
    _assert_one_attempt_one_successor(store, pending)
    launch.assert_called_once()


def test_same_revision_input_with_changed_candidate_refuses_accepted_successor_replay(
    tmp_path: Path,
) -> None:
    contract, _operation_input, store, record = _dirty_closeout(tmp_path)
    config = load_config(Path(record.input.configPath))
    revise = next(
        row for row in legal_operation_controls(contract, record) if row["action"] == "revise"
    )
    revise["arguments"]["code_commit_message"] = "accepted input with exact candidate"
    intent_written, release_first, interrupted = _blocking_archive_cut(
        store,
        "hold accepted successor before candidate drift",
    )
    replay_started = Event()

    def replay_same_input() -> dict:
        replay_started.set()
        return _public_control(config, revise)

    with (
        mock.patch.object(LifecycleOperationStore, "_archive_generation", new=interrupted),
        mock.patch.object(controls_module, "launch_detached_worker") as launch,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first_future = pool.submit(_public_control, config, revise)
        assert intent_written.wait(timeout=10)
        pending = store.read_successor_intent()
        assert pending is not None
        (contract.code_worktree / "candidate.py").write_text("VALUE = 2\n", encoding="utf-8")
        before_replay = _successor_store_bytes(store)
        replay_future = pool.submit(replay_same_input)
        assert replay_started.wait(timeout=10)
        release_first.set()
        cut = first_future.result(timeout=10)
        refused = replay_future.result(timeout=10)

    assert cut["status"] == "lifecycle-successor-publication-interrupted"
    assert refused["ok"] is False
    assert refused["status"] == "lifecycle-successor-already-accepted"
    assert refused["expected"] == {
        "generation": pending.successor.generation,
        "fingerprint": pending.successor.fingerprint,
    }
    assert refused["observed"]["generation"] == pending.successor.generation
    assert refused["observed"]["fingerprint"] != pending.successor.fingerprint
    assert refused["nextTool"] == "worktree_operation_control"
    assert refused["nextArgs"]["action"] == "recover"
    assert _successor_store_bytes(store) == before_replay
    assert not store.path.with_name(f"{store.path.stem}.generation-1.json").exists()
    launch.assert_not_called()


def test_concurrent_same_successor_replay_converges_to_one_attempt_one_generation(
    tmp_path: Path,
) -> None:
    contract, _operation_input, store, record = _dirty_closeout(tmp_path)
    config = load_config(Path(record.input.configPath))
    revise = next(
        row for row in legal_operation_controls(contract, record) if row["action"] == "revise"
    )
    revise["arguments"]["code_commit_message"] = "one concurrently replayed successor"
    intent_written, release_first, first_archive_cut = _blocking_archive_cut(
        store,
        "forced first replay cut after accepted successor WAL",
    )
    second_started = Event()

    def second_replay() -> dict:
        second_started.set()
        return _public_control(config, revise)

    with (
        mock.patch.object(
            LifecycleOperationStore,
            "_archive_generation",
            new=first_archive_cut,
        ),
        mock.patch.object(controls_module, "launch_detached_worker") as launch,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(_public_control, config, revise)
        assert intent_written.wait(timeout=10)
        pending = store.read_successor_intent()
        assert pending is not None
        second = pool.submit(second_replay)
        assert second_started.wait(timeout=10)
        release_first.set()
        first_result = first.result(timeout=10)
        second_result = second.result(timeout=10)

    assert first_result["status"] == "lifecycle-successor-publication-interrupted"
    assert second_result["ok"] is True
    current = store.read()
    assert current is not None
    assert (current.generation, current.attempt) == (2, 1)
    assert current.fingerprint == pending.successor.fingerprint
    assert current.input == pending.successor.input
    assert store.read_successor_intent() is None
    _assert_one_attempt_one_successor(store, pending)
    launch.assert_called_once()
