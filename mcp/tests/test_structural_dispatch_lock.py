"""Cross-process exclusion and bounded reclamation for structural dispatch locks."""

from __future__ import annotations

import fcntl
import multiprocessing
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving import structural_dispatch


def _hold_process_lock(root: str, held: object, release: object) -> None:
    document = TaskDocumentRef(repository="repo", path="sprint/task.json")
    with structural_dispatch.exclusive_structural_dispatch_lock(Path(root), document, "architect"):
        held.set()  # type: ignore[attr-defined]
        if not release.wait(timeout=5):  # type: ignore[attr-defined]
            raise TimeoutError("parent did not release the process lock")


def test_structural_dispatch_lock_excludes_a_second_process(tmp_path: Path) -> None:
    document = TaskDocumentRef(repository="repo", path="sprint/task.json")
    context = multiprocessing.get_context("fork")
    held = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_process_lock, args=(str(tmp_path), held, release))
    process.start()
    try:
        assert held.wait(timeout=5)
        slot = structural_dispatch._seat_lock_slot(document, "architect")
        path = structural_dispatch._seat_lock_path(tmp_path, slot)
        with path.open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:  # pragma: no cover - the defect is the absence of kernel exclusion
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                raise AssertionError("a second process acquired the occupied seat lock")
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


def test_process_lock_map_reclaims_after_the_last_waiter_drains() -> None:
    slot = 17
    held = threading.Event()
    release = threading.Event()

    def first() -> None:
        with structural_dispatch._exclusive_process_slot(slot):
            held.set()
            assert release.wait(timeout=5)

    def waiter() -> None:
        assert held.wait(timeout=5)
        with structural_dispatch._exclusive_process_slot(slot):
            return

    first_thread = threading.Thread(target=first)
    waiter_thread = threading.Thread(target=waiter)
    first_thread.start()
    waiter_thread.start()
    try:
        for _attempt in range(100):
            with structural_dispatch._PROCESS_LOCKS_GUARD:
                users = structural_dispatch._PROCESS_LOCKS.get(slot, (None, 0))[1]
            if users == 2:
                break
            threading.Event().wait(0.01)
        assert users == 2
    finally:
        release.set()
        first_thread.join(timeout=5)
        waiter_thread.join(timeout=5)
    assert not first_thread.is_alive()
    assert not waiter_thread.is_alive()
    with structural_dispatch._PROCESS_LOCKS_GUARD:
        assert slot not in structural_dispatch._PROCESS_LOCKS


def test_lock_path_failure_is_a_typed_structural_refusal(tmp_path: Path) -> None:
    document = TaskDocumentRef(repository="repo", path="sprint/task.json")
    with (
        mock.patch.object(Path, "open", side_effect=OSError("read only")),
        pytest.raises(
            structural_dispatch.StructuralDispatchLockError,
            match="structural seat lock is unavailable",
        ),
        structural_dispatch.exclusive_structural_dispatch_lock(
            tmp_path,
            document,
            "architect",
        ),
    ):
        raise AssertionError("the unavailable lock must not yield")


def test_duplicate_pinned_briefs_refuse_instead_of_selecting_one() -> None:
    document = TaskDocumentRef(repository="repo", path="sprint/task.json")
    entry = SimpleNamespace(
        messageKind="dispatch-brief",
        agentId="occupant",
        taskDocumentRef=document,
        recipientRole="architect",
    )
    store = mock.Mock()
    store.current.return_value = {"first": entry, "second": entry}

    with pytest.raises(
        structural_dispatch.StructuralDispatchError,
        match="multiple pinned dispatch briefs",
    ):
        structural_dispatch.pinned_dispatch_brief(
            store,
            document=document,
            role="architect",
            occupant_id="occupant",
        )


def test_historical_seat_churn_stays_inside_the_fixed_stripe_namespace(
    tmp_path: Path,
) -> None:
    assert structural_dispatch._LOCK_STRIPE_COUNT == 4096
    with mock.patch.object(structural_dispatch, "_LOCK_STRIPE_COUNT", 4):
        for index in range(40):
            document = TaskDocumentRef(
                repository="repo",
                path=f"master/leaf-{index}.json",
            )
            with structural_dispatch.exclusive_structural_dispatch_lock(
                tmp_path, document, "worker"
            ):
                pass

    locks = tuple((tmp_path / "runtime" / "structural-seat-locks").glob("*.lock"))
    assert 1 <= len(locks) <= 4
    assert {lock.stat().st_size for lock in locks} == {0}
    with structural_dispatch._PROCESS_LOCKS_GUARD:
        assert structural_dispatch._PROCESS_LOCKS == {}
