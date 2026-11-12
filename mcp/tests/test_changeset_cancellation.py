"""MIK-R176 rules 3 and 4: reads are bounded, isolated, and end with their client.

The route family admits a bounded number of reads, runs each in its own threads, watches
the request while it runs, and never publishes or retains an abandoned answer. These
cases exercise that seam directly (L174-R1-02, L174-R1-03, L174-R1-08 and the M4-26 /
M4-27-A1 carrier conditions).
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import os
import signal
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shlex import quote
from typing import cast

import pytest
from agents_remember.kernel import git_command
from agents_remember.kernel.git_command import GitRunnerOptions, cancellable_git_commands, run_git
from agents_remember.serving.changeset import _BoundedReads, _read_off_the_loop
from agents_remember.serving.changeset_cache import ChangesetCache, cached_read
from agents_remember_test_support.testing.waits import (
    HANG_GUARD_SECONDS,
    async_wait_until,
    wait_until,
)
from fastapi import Request
from fastapi.responses import JSONResponse, Response

pytestmark = pytest.mark.evidence_unit


class _Client:
    """The one request fact the route reads: whether its client is still connected."""

    def __init__(
        self,
        *,
        disconnect_after: int | None = None,
        disconnected: threading.Event | None = None,
    ) -> None:
        self._remaining = disconnect_after
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        if self._disconnected is not None:
            return self._disconnected.is_set()
        if self._remaining is None:
            return False
        self._remaining -= 1
        return self._remaining < 0


def _client(
    *, disconnect_after: int | None = None, disconnected: threading.Event | None = None
) -> Request:
    """The route reads only ``is_disconnected``; the cast names that seam for the type check."""
    return cast(Request, _Client(disconnect_after=disconnect_after, disconnected=disconnected))


def _reads(*, threads: int = 8, pending: int = 8) -> _BoundedReads:
    return _BoundedReads(threads=threads, pending=pending, name="changeset-test")


def _fake_git(binary: Path, body: str) -> None:
    """A ``git`` first on PATH, so the read's own child is this script."""
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\n" + body)
    binary.chmod(0o755)


def _use_fake_git(binary: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")


def _gated_git(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    """Hold the real child until released; callers bound the runner with the hang guard."""
    pid_file, release, finished = (tmp_path / name for name in ("git.pid", "release", "finished"))
    binary = tmp_path / "bin" / "git"
    _fake_git(
        binary,
        f"echo $$ > {quote(str(pid_file))}\n"
        f"while [ ! -f {quote(str(release))} ]; do sleep 0.01; done\n"
        f"echo done >> {quote(str(finished))}\n",
    )
    _use_fake_git(binary, monkeypatch)
    return pid_file, release, finished


def _process_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_a_slow_read_of_one_task_does_not_delay_anothers(tmp_path, monkeypatch):
    pid_file, release, _finished = _gated_git(tmp_path, monkeypatch)
    reads = _reads()

    def slow(_cancellation: threading.Event) -> Response:
        assert (
            run_git(tmp_path, ["status"], GitRunnerOptions(timeout=HANG_GUARD_SECONDS)).returncode
            == 0
        )
        return JSONResponse({"read": "slow"})

    async def drive() -> tuple[int, int]:
        slow_read = asyncio.ensure_future(_read_off_the_loop(_client(), reads, slow))
        try:
            await async_wait_until(pid_file.exists, "the slow read's Git child to start")
            fast = await asyncio.wait_for(
                _read_off_the_loop(
                    _client(), reads, lambda _cancellation: JSONResponse({"read": "fast"})
                ),
                HANG_GUARD_SECONDS,
            )
            assert not slow_read.done(), "the fast read waited for the slow Git child"
        finally:
            release.touch()
        return fast.status_code, (await slow_read).status_code

    try:
        assert asyncio.run(drive()) == (200, 200)
    finally:
        release.touch()
        reads.shutdown()


def test_a_disconnected_read_ends_its_git_process(tmp_path, monkeypatch):
    pid_file, release, finished = _gated_git(tmp_path, monkeypatch)
    disconnected = threading.Event()
    reads = _reads(threads=2, pending=2)

    def read(_cancellation: threading.Event) -> Response:
        run_git(tmp_path, ["status"], GitRunnerOptions(timeout=HANG_GUARD_SECONDS))
        return JSONResponse({"read": "abandoned"})

    async def drive() -> int:
        pending = asyncio.create_task(
            _read_off_the_loop(_client(disconnected=disconnected), reads, read)
        )
        await async_wait_until(pid_file.exists, "the abandoned read's Git child to start")
        disconnected.set()
        return (await asyncio.wait_for(pending, HANG_GUARD_SECONDS)).status_code

    try:
        assert asyncio.run(drive()) == 499
        pid = int(pid_file.read_text().strip())
        wait_until(lambda: _process_gone(pid), "the abandoned read's Git child to be reaped")
        assert not finished.exists(), "the abandoned Git child completed instead of being killed"
    finally:
        release.touch()
        reads.shutdown()


def test_a_queued_abandoned_read_never_produces():
    """L174-R1-02/R1-03: a read whose client left while queued never runs its work."""

    reads = _reads(threads=1, pending=1)
    release = threading.Event()
    producing = threading.Event()
    produced: list[int] = []

    def blocking(_cancellation: threading.Event) -> Response:
        producing.set()
        assert release.wait(HANG_GUARD_SECONDS), "the live producer was not released"
        return JSONResponse({"read": "first"})

    def queued(_cancellation: threading.Event) -> Response:
        produced.append(1)
        return JSONResponse({"read": "queued"})

    async def drive() -> tuple[int, int]:
        first = asyncio.ensure_future(_read_off_the_loop(_client(), reads, blocking))
        await async_wait_until(producing.is_set, "the live producer to start")
        second = await _read_off_the_loop(_client(disconnect_after=1), reads, queued)
        release.set()
        first_response = await first
        return first_response.status_code, second.status_code

    try:
        first_status, second_status = asyncio.run(drive())
        assert second_status == 499
        assert produced == [], "a queued read whose client left still ran its work"
        assert first_status == 200
    finally:
        release.set()
        reads.shutdown()


def test_over_capacity_answers_an_explicit_refusal():
    """L174-R1-03: the family bounds admitted work, not only its threads."""

    reads = _reads(threads=1, pending=1)
    release = threading.Event()
    started = threading.Event()

    def blocking(_cancellation: threading.Event) -> Response:
        started.set()
        assert release.wait(HANG_GUARD_SECONDS), "the blocking producer was not released"
        return JSONResponse({"read": "blocking"})

    async def drive() -> tuple[int, int, Response]:
        first = asyncio.ensure_future(_read_off_the_loop(_client(), reads, blocking))
        await async_wait_until(started.is_set, "the blocking producer to start")
        second = asyncio.ensure_future(_read_off_the_loop(_client(), reads, blocking))
        await async_wait_until(lambda: reads.queued() == 1, "the second read to be queued")
        over = await _read_off_the_loop(
            _client(), reads, lambda _cancellation: JSONResponse({"read": "over"})
        )
        release.set()
        return (await first).status_code, (await second).status_code, over

    try:
        first_status, second_status, over = asyncio.run(drive())
        assert (first_status, second_status) == (200, 200)
        assert over.status_code == 503
        assert over.headers["retry-after"] == "1"  # an explicit refusal, not a queue
    finally:
        release.set()
        reads.shutdown()


def test_an_abandoned_successful_read_is_never_cached():
    """L174-R1-02: a success that arrives after the client left is not retained."""

    cache = ChangesetCache(capacity=8)
    reads = _reads(threads=1, pending=1)
    started = threading.Event()
    release = threading.Event()
    disconnected = threading.Event()

    def produce(cancellation: threading.Event) -> Response:
        with cache.reading(abandoned=cancellation):
            started.set()
            assert release.wait(HANG_GUARD_SECONDS), "the abandoned producer was not released"
            value = cached_read(("abandoned",), (), lambda: {"value": 1})
            return JSONResponse(value)

    async def drive() -> int:
        read = asyncio.ensure_future(
            _read_off_the_loop(_client(disconnected=disconnected), reads, produce)
        )
        await async_wait_until(started.is_set, "the abandoned producer to start")
        disconnected.set()
        response = await asyncio.wait_for(read, HANG_GUARD_SECONDS)
        release.set()
        await async_wait_until(lambda: reads._admitted == 0, "the abandoned producer to finish")
        return response.status_code

    try:
        assert asyncio.run(drive()) == 499
        assert not cache._values, "an abandoned answer entered the cache"
    finally:
        release.set()
        reads.shutdown()


def test_the_cancellation_is_scoped_to_its_read_and_restored(tmp_path, monkeypatch):
    """M4-26 (1): the event reaches only the read inside the block; leaving it restores the state.

    Proven behaviourally around a read that is *not* abandoned: the same still-set event
    ends the child inside the block and does not touch the command after it.
    """

    _pid_file, release, finished = _gated_git(tmp_path, monkeypatch)
    abandoned = threading.Event()
    abandoned.set()  # The client went away before this read's block was entered.

    assert git_command._GIT_CANCELLATION.get() is None
    with cancellable_git_commands(abandoned):
        assert git_command._GIT_CANCELLATION.get() is abandoned
        inside = run_git(tmp_path, ["status"], GitRunnerOptions(timeout=HANG_GUARD_SECONDS))
        with cancellable_git_commands(threading.Event()):
            assert git_command._GIT_CANCELLATION.get() is not abandoned
        assert git_command._GIT_CANCELLATION.get() is abandoned  # the outer block is restored
    assert git_command._GIT_CANCELLATION.get() is None  # leaving restores the prior state

    assert inside.returncode == -signal.SIGKILL, "the abandoned read's child was not killed"
    assert not finished.exists()
    release.touch()
    after = run_git(tmp_path, ["status"], GitRunnerOptions(timeout=HANG_GUARD_SECONDS))
    assert after.returncode == 0, "a later command was cancelled by another read's event"
    assert finished.read_text().strip() == "done"


def test_a_cancellation_never_reaches_another_tasks_git_child(tmp_path, monkeypatch):
    """M4-26 (2): the carrier is context-local, so a sibling task's child is never cancelled."""

    pid_file, release, marker = _gated_git(tmp_path, monkeypatch)
    abandoned = threading.Event()
    abandoned.set()

    def sibling() -> int:
        return run_git(
            tmp_path, ["status"], GitRunnerOptions(timeout=HANG_GUARD_SECONDS)
        ).returncode

    # The event is already set when the sibling starts its own child: only context-locality,
    # not the child's start order, can keep that child alive.
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with cancellable_git_commands(abandoned):
                pending = pool.submit(sibling)
                wait_until(pid_file.exists, "the sibling's Git child to start")
                own = run_git(tmp_path, ["status"], GitRunnerOptions(timeout=HANG_GUARD_SECONDS))
                assert own.returncode == -signal.SIGKILL
                assert not pending.done(), "another task's Git child was cancelled"
        finally:
            release.touch()
        assert pending.result(timeout=HANG_GUARD_SECONDS) == 0
    assert marker.read_text().strip() == "done", "another task's Git child was cancelled"

    context = contextvars.copy_context()

    def copied() -> None:
        with cancellable_git_commands(abandoned):
            assert git_command._GIT_CANCELLATION.get() is abandoned

    context.run(copied)
    assert git_command._GIT_CANCELLATION.get() is None, "the event leaked out of the context copy"


def test_the_runner_has_one_kill_owner():
    """L174-R1-08: one child/process-group kill owner, and only ``_wait_process`` uses it."""

    source = Path(git_command.__file__).read_text()
    sites = [
        line.strip()
        for line in source.splitlines()
        if "killpg(" in line or ".kill(" in line or "terminate()" in line
    ]
    assert sites == ["os.killpg(process.pid, signal.SIGKILL)"]

    owner = source[source.index("def _kill_process_group(") :]
    owner = owner[: owner.index("\ndef ", 1)]
    assert "os.killpg(process.pid, signal.SIGKILL)" in owner

    wait_process = source[source.index("def _wait_process(") :]
    wait_process = wait_process[: wait_process.index("\ndef ", 1)]
    assert "_kill_process_group(process)" in wait_process
    # No other function reaches the kill: the only calls are the ones inside the waiter.
    assert source.count("_kill_process_group(process)") == wait_process.count(
        "_kill_process_group(process)"
    )


def test_a_read_abandoned_during_finish_admits_nothing(tmp_path, monkeypatch):
    """L174-R1-02: cancellation inside the admission phase leaves the cache untouched.

    The Reviewer's counterexample is an event set after the pre-finish check: the client
    leaves while the observations and puts are running, 499 reaches the client, and one
    value is still admitted. The admission now re-checks the event before each put and
    withdraws whatever this finish admitted when it is set at any point.
    """

    cache = ChangesetCache(capacity=4)
    event = threading.Event()
    source = tmp_path / "input.txt"
    source.write_text("one\n")
    original = cache.put
    attempted: list[tuple[object, ...]] = []

    def put_then_abandon(key, kept):
        original(key, kept)
        attempted.append(key)
        event.set()  # the client leaves while finish is still admitting

    monkeypatch.setattr(cache, "put", put_then_abandon)
    with cache.reading(abandoned=event):
        cached_read((("probe",), (source,)), (source,), lambda: {"value": 1})

    assert attempted  # the admission was attempted, so this is the real race
    assert cache.retained[0] == 0  # and withdrawn again: nothing was admitted


def test_an_abandoned_queued_read_releases_its_slot_at_once():
    """L174-R1-03: the pending budget does not belong to a client that has left."""

    reads = _BoundedReads(threads=1, pending=1, name="probe")
    first = reads.try_admit()
    queued = reads.try_admit()
    assert first is not None and queued is not None
    assert reads.try_admit() is None  # the cap is real

    queued.release()  # what the poller does when that client is gone
    replacement = reads.try_admit()
    assert replacement is not None  # admitted while the live worker still runs

    queued.release()  # idempotent: a second release cannot free a slot twice
    assert reads.try_admit() is None
    first.release()
    replacement.release()
    reads.shutdown()


@pytest.mark.parametrize("population", [16, 32])
def test_the_family_bounds_admits_and_reclaims_at_over_capacity(population):
    """L174-R1-03 acceptance: fixed cap, explicit overload, reclamation, service.

    One worker and one pending place make the family's bound exactly two. With one live
    read occupying the worker, a population of requests that leave immediately is answered
    499 and leaves **no work queued**: their places are withdrawn, not deferred until an
    unrelated worker frees a thread. A read arriving afterwards is admitted and answered,
    and a second family is untouched throughout, so the bound reclaimed work without
    consuming another route's capacity.
    """

    reads = _BoundedReads(threads=1, pending=1, name="r103")
    gate = threading.Event()
    entered = threading.Event()
    disconnected = threading.Event()
    other = _BoundedReads(threads=1, pending=1, name="r103-other")

    def slow(_cancellation: threading.Event) -> Response:
        entered.set()
        assert gate.wait(HANG_GUARD_SECONDS), "the live worker was not released"
        return JSONResponse({"read": "slow"})

    def quick(_cancellation: threading.Event) -> Response:
        return JSONResponse({"read": "quick"})

    async def drive() -> tuple[list[int], int, int, int, int, int]:
        live = asyncio.ensure_future(_read_off_the_loop(_client(), reads, slow))
        await async_wait_until(entered.is_set, "the live worker to start")
        assert reads.queued() == 0

        # The first extra read takes the single pending place; the next is refused at once.
        first_extra = asyncio.ensure_future(
            _read_off_the_loop(_client(disconnected=disconnected), reads, quick)
        )
        await async_wait_until(lambda: reads.queued() == 1, "the extra read to occupy the queue")
        refused = await _read_off_the_loop(_client(), reads, quick)
        queued_with_one_waiting = reads.queued()
        disconnected.set()
        await first_extra

        abandoned = []
        for _ in range(population - 1):
            abandoned.append(await _read_off_the_loop(_client(disconnect_after=1), reads, quick))
        queued_after_abandon = reads.queued()

        # Another route family is untouched by this one's saturation.
        elsewhere = await _read_off_the_loop(_client(), other, quick)

        gate.set()
        assert (await live).status_code == 200

        # Service continues for the current task once its worker is free again.
        served = await _read_off_the_loop(_client(), reads, quick)
        return (
            [response.status_code for response in abandoned],
            refused.status_code,
            queued_with_one_waiting,
            queued_after_abandon,
            served.status_code,
            elsewhere.status_code,
        )

    try:
        statuses, refused, waiting, after, served, elsewhere = asyncio.run(drive())
        assert set(statuses) == {499}, statuses  # every abandoned read is answered
        assert refused == 503  # the overload outcome is explicit
        assert waiting == 1  # the queue holds exactly the one pending place
        assert after == 0  # and nothing abandoned stays queued
        assert served == 200  # the current task keeps being served
        assert elsewhere == 200  # and no other family paid for this one's saturation
    finally:
        gate.set()
        reads.shutdown()
        other.shutdown()


@pytest.mark.parametrize("population", [16, 32])
def test_abandoned_jobs_release_their_copied_request_contexts(population):
    """A freed admission counter must not conceal retained executor work items."""

    class Payload:
        pass

    context = contextvars.ContextVar("request-payload")
    retained = []
    reads = _reads(threads=1, pending=1)
    entered = threading.Event()
    gate = threading.Event()

    def slow(_):
        entered.set()
        assert gate.wait(HANG_GUARD_SECONDS), "the context-owning worker was not released"
        return Response()

    async def drive():
        live = asyncio.create_task(_read_off_the_loop(_client(), reads, slow))
        await async_wait_until(entered.is_set, "the context-owning worker to start")
        for _ in range(population):
            payload = Payload()
            retained.append(weakref.ref(payload))
            token = context.set(payload)
            try:
                response = await _read_off_the_loop(
                    _client(disconnect_after=0), reads, lambda _: Response()
                )
                assert response.status_code == 499
            finally:
                context.reset(token)
            del payload
        gc.collect()
        assert not any(reference() is not None for reference in retained)
        gate.set()
        assert (await live).status_code == 200

    try:
        asyncio.run(drive())
    finally:
        gate.set()
        reads.shutdown()


def test_a_running_abandoned_job_keeps_its_slot_until_its_worker_ends():
    reads = _reads(threads=1, pending=1)
    entered = threading.Event()
    gate = threading.Event()
    disconnected = threading.Event()

    def slow(_):
        entered.set()
        assert gate.wait(HANG_GUARD_SECONDS), "the abandoned running worker was not released"
        return Response()

    async def drive():
        gone = asyncio.create_task(
            _read_off_the_loop(_client(disconnected=disconnected), reads, slow)
        )
        await async_wait_until(entered.is_set, "the soon-abandoned worker to start")
        disconnected.set()
        assert (await gone).status_code == 499
        current = asyncio.create_task(_read_off_the_loop(_client(), reads, lambda _: Response()))
        await async_wait_until(lambda: reads.queued() == 1, "the current read to occupy the queue")
        excess = await _read_off_the_loop(_client(disconnect_after=0), reads, lambda _: Response())
        assert excess.status_code == 503
        gate.set()
        assert (await current).status_code == 200

    try:
        asyncio.run(drive())
    finally:
        gate.set()
        reads.shutdown()
