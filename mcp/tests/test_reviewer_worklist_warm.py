"""Warm worklist children retain imports, never another exact binding's request state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from agents_remember.application.review_code_changes import tree_request
from agents_remember.kernel import reviewer_worklist_process as owner
from agents_remember.kernel.read_cancellation import ReadCancelled
from agents_remember_test_support.testing.review_git_trees import CODE_FILE, CODE_V1, World
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, wait_until
from reviewer_worklist_process_test_support import _wait_gone
from test_review_git_trees import world  # noqa: F401 - fixture
from test_review_read_latency import _live
from test_reviewer_worklist_process import _captured, _source


def _payload(fixture: World) -> dict[str, Any]:
    contract, trees = _live(fixture)
    candidate, base = _captured(contract, trees)
    return json.loads(
        json.dumps(
            {
                "contract": asdict(contract),
                "candidate": asdict(candidate),
                "base": asdict(base),
                "review": tree_request(trees),
            },
            default=lambda value: value.as_posix(),
        )
    )


def _content(answer: dict[str, Any]) -> dict[str, Any]:
    # A process identity and observation timestamps describe execution, not the answer's facts.
    return {key: value for key, value in answer.items() if key not in ("pid", "computation")}


@contextmanager
def _processes() -> Iterator[owner.ReviewerWorklistProcesses]:
    processes = owner.ReviewerWorklistProcesses()
    try:
        yield processes
    finally:
        processes.shutdown()


@contextmanager
def _warm_program(body: str) -> Iterator[list[subprocess.Popen[Any]]]:
    """Fault injection around the actual persistent main; never disable pool retention."""
    actual = subprocess.Popen
    children: list[subprocess.Popen[Any]] = []
    script = (
        "from agents_remember.application import reviewer_worklist_child as child\n"
        + body
        + "\nchild.main()\n"
    )

    def spawn(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        if argv[1:] == ["-P", "-m", owner.CHILD_MODULE]:
            process = actual([argv[0], "-P", "-c", script], *args, **kwargs)
            children.append(process)
            return process
        return actual(argv, *args, **kwargs)

    with mock.patch.object(subprocess, "Popen", spawn):
        yield children


@pytest.mark.parametrize("size", [1, 120])
def test_warm_child_matches_fresh_answers_and_does_not_leak_between_bindings(
    world: World,  # noqa: F811
    tmp_path: Path,
    size: int,
) -> None:
    world.edit()
    for number in range(size):
        (world.code_worktree / "pkg" / f"warm-{number}.py").write_text("VALUE = 1\n")
    first = _payload(world)
    source = _source()
    program = f"""
import json, os
from pathlib import Path
timers = []
set_timer = child.signal.setitimer
def recorded_timer(which, seconds):
    timers.append(seconds)
    return set_timer(which, seconds)
child.signal.setitimer = recorded_timer
run = child._run
def observed(request):
    result = run(request)
    destination = Path({str(tmp_path)!r}) / f'warm-timers-{{os.getpid()}}.jsonl'
    with destination.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(timers) + '\\n')
    return result
child._run = observed
"""
    with _warm_program(program), _processes() as warm, _processes() as fresh:
        initial = warm.compute(first, source)
        assert initial["error"] is None and initial["document"] is not None
        expected = fresh.compute(first, source)
        assert _content(initial) == _content(expected)
        initial["document"]["mutated_by_caller"] = True
        repeated = warm.compute(first, source)
        assert repeated["pid"] == initial["pid"] != expected["pid"] != os.getpid()
        assert _content(repeated) == _content(expected)
        # A different code tree has a different exact request binding in the same process.
        (world.code_worktree / CODE_FILE).write_text(
            CODE_V1.replace("return value", "return value + 9")
        )
        second = _payload(world)
        assert second["candidate"] != first["candidate"]
        with _processes() as fresh_second:
            expected_second = fresh_second.compute(second, source)
        changed = warm.compute(second, source)
        assert changed["pid"] == repeated["pid"]
        assert _content(changed) == _content(expected_second)
        returned = warm.compute(first, source)
        assert _content(returned) == _content(expected)
        assert len(warm._idle) == 1 and not warm._active and not warm._flights
        pid = returned["pid"]
        snapshots = [
            json.loads(line)
            for line in (tmp_path / f"warm-timers-{pid}.jsonl").read_text().splitlines()
        ]
        deadline = owner.CHILD_DEADLINE_SECONDS
        # Every job rearms its deadline, and the preceding job clears it before becoming idle.
        assert snapshots[0] == [deadline, 0, deadline]
        assert snapshots[1] == [deadline, 0, deadline, 0, deadline]
        assert snapshots[2] == [deadline, 0, deadline, 0, deadline, 0, deadline]
        assert snapshots[3] == [deadline, 0, deadline, 0, deadline, 0, deadline, 0, deadline]
        assert len(snapshots) == 4
    assert _wait_gone([pid], HANG_GUARD_SECONDS)


def test_warm_child_fault_is_not_replayed_and_next_request_starts_clean(
    world: World,  # noqa: F811
    subtests: Any,
) -> None:
    world.edit()
    payload, source = _payload(world), _source()
    for fault in ("crash", "malformed", "source"):
        with subtests.test(fault=fault):
            _fault_is_not_replayed(fault, payload, source)


def _fault_is_not_replayed(fault: str, payload: dict[str, Any], source: dict[str, Any]) -> None:
    program = f"""
import json, os, sys
serve = child._serve
def controlled(line):
    request = json.loads(line)
    if request['payload'].get('probe') == 'fail':
        if {fault!r} == 'crash':
            os._exit(7)
        if {fault!r} == 'malformed':
            sys.stdout.write('{{"not": "a reply"}}\\n')
            sys.stdout.flush()
            return
        child.runtime_source_digest = lambda root: 'sha256:changed'
    serve(line)
child._serve = controlled
"""
    with _warm_program(program) as children, _processes() as processes:
        before = processes.compute(payload, source)
        error = owner.WorklistBuildMismatch if fault == "source" else owner.WorklistProcessError
        with pytest.raises(error):
            processes.compute({**payload, "probe": "fail"}, source)
        assert _wait_gone([before["pid"]], HANG_GUARD_SECONDS)
        assert not processes._idle and not processes._active
        after = processes.compute(payload, source)
        assert after["pid"] != before["pid"] and len(children) == 2
        assert _content(after) == _content(before)


def test_warm_child_last_requester_cancellation_discards_the_process(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    world.edit()
    payload, source = _payload(world), _source()
    started = tmp_path / "started"
    program = f"""
import json, threading
from pathlib import Path
serve = child._serve
def controlled(line):
    if json.loads(line)['payload'].get('probe') == 'block':
        Path({str(started)!r}).write_text('started')
        threading.Event().wait()
    serve(line)
child._serve = controlled
"""
    cancelled = threading.Event()
    failures: list[BaseException] = []
    with _warm_program(program), _processes() as processes:
        initial = processes.compute(payload, source)

        def request() -> None:
            try:
                with owner.worklist_request(cancelled):
                    processes.compute({**payload, "probe": "block"}, source)
            except ReadCancelled as failure:  # the requester left: never a worklist failure
                failures.append(failure)

        thread = threading.Thread(target=request)
        thread.start()
        try:
            wait_until(started.exists, "the warm child starts its second request")
            cancelled.set()
        finally:
            cancelled.set()
            thread.join(timeout=HANG_GUARD_SECONDS)
        assert not thread.is_alive() and len(failures) == 1
        assert isinstance(failures[0], ReadCancelled)
        assert _wait_gone([initial["pid"]], HANG_GUARD_SECONDS)
        recovered = processes.compute(payload, source)
        assert recovered["pid"] != initial["pid"]
        assert _content(recovered) == _content(initial)


def test_warm_children_remain_within_two_slots_and_shutdown_reaps_idle_processes(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    world.edit()
    payload, source = _payload(world), _source()
    release = tmp_path / "release"
    program = f"""
import json, time
from pathlib import Path
serve = child._serve
def controlled(line):
    slot = json.loads(line)['payload'].get('slot')
    if slot is not None:
        Path({str(tmp_path)!r}, f'started-{{slot}}').touch()
        while not Path({str(release)!r}).exists():
            time.sleep(0.01)
    serve(line)
child._serve = controlled
"""
    answers: list[dict[str, Any]] = []
    failures: list[BaseException] = []
    with _warm_program(program) as children, _processes() as processes:

        def request(slot: int) -> None:
            try:
                answers.append(processes.compute({**payload, "slot": slot}, source))
            except owner.WorklistProcessError as error:
                failures.append(error)

        threads = [threading.Thread(target=request, args=(slot,)) for slot in range(3)]
        try:
            for thread in threads:
                thread.start()
            wait_until(
                lambda: len(list(tmp_path.glob("started-*"))) == 2 and len(processes._queue) == 1,
                "two running warm children with the third request queued",
            )
            assert len(children) == 2 and len(processes._active) == 2
        finally:
            release.touch()
            for thread in threads:
                thread.join(timeout=HANG_GUARD_SECONDS)
        assert not failures and len(answers) == 3
        assert all(not thread.is_alive() for thread in threads)
        assert len(children) == 2 and len(processes._idle) == 2 and not processes._active
        assert len({answer["pid"] for answer in answers}) == 2
        pids = [process.pid for process in children]
    assert _wait_gone(pids, HANG_GUARD_SECONDS)


def test_warm_child_matches_fresh_environment_and_working_directory(
    world: World,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world.edit()
    payload, source = _payload(world), _source()
    next_directory = tmp_path / "next-directory"
    next_directory.mkdir()
    program = """
import os
run = child._run
def observed(request):
    result = run(request)
    result['document']['_test_environment'] = os.environ.get('GIT_CONFIG_COUNT')
    result['document']['_test_cwd'] = os.getcwd()
    return result
child._run = observed
"""
    # GIT_CONFIG_COUNT is a real Git input. Zero adds no settings while demonstrating that a
    # changed process environment must not reuse the earlier interpreter or its in-flight job.
    with _warm_program(program), _processes() as warm:
        first = warm.compute(payload, source)
        monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
        second = warm.compute(payload, source)
        with _processes() as fresh:
            assert _content(second) == _content(fresh.compute(payload, source))
        assert first["pid"] != second["pid"]
        assert _wait_gone([first["pid"]], HANG_GUARD_SECONDS)
        monkeypatch.chdir(next_directory)
        third = warm.compute(payload, source)
        with _processes() as fresh:
            assert _content(third) == _content(fresh.compute(payload, source))
        assert third["pid"] != second["pid"]
        assert _wait_gone([second["pid"]], HANG_GUARD_SECONDS)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux parent-death contract")
def test_killed_dashboard_reaps_a_parked_child_after_its_request_thread_has_ended(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    world.edit()
    source = _source()
    spec = tmp_path / "parent-spec.json"
    spec.write_text(json.dumps({"payload": _payload(world), "source": source}))
    program = """
import json, sys, threading
from agents_remember.kernel.reviewer_worklist_process import ReviewerWorklistProcesses
spec = json.load(open(sys.argv[1]))
owner = ReviewerWorklistProcesses()
first = owner.compute(spec['payload'], spec['source'])
second = owner.compute(spec['payload'], spec['source'])
assert first['pid'] == second['pid']
print(second['pid'], flush=True)
threading.Event().wait()
"""
    parent = subprocess.Popen(
        [sys.executable, "-P", "-c", program, str(spec)],
        env=owner._environment(source),
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline())
        parent.kill()  # No Python shutdown handler runs; the kernel owns this cleanup.
        parent.wait(timeout=HANG_GUARD_SECONDS)
        assert _wait_gone([child_pid], HANG_GUARD_SECONDS)
    finally:
        parent.kill()
        parent.wait(timeout=HANG_GUARD_SECONDS)
        if parent.stdout is not None:
            parent.stdout.close()
