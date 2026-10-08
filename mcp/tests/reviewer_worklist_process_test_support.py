"""Controlled child replies, request clocks and executor contention for worklist tests."""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

from agents_remember_test_support.testing.waits import (
    HANG_GUARD_SECONDS,
    async_wait_until,
    wait_until,
)

WORKLIST_REPLY = """
import json, os, sys, time
r = json.load(sys.stdin)
answer = {'operation': r['operation'], 'request': r['request'], 'source': r['source'],
          'module': r['source']['packageRoot'] + '/application/reviewer_worklist_child.py',
          'pid': os.getpid(), 'document': None, 'reads': {}, 'computation': [time.monotonic(), time.monotonic()], 'error': None}
"""

LIFETIME_CHILD = """
import os, subprocess, sys
from agents_remember.kernel import reviewer_worklist_process as owner
owner.CHILD_DEADLINE_SECONDS = float(sys.argv[1])
read_end, write_end = os.pipe()
git = subprocess.Popen(['git', 'cat-file', '--batch'], stdin=read_end, cwd=sys.argv[2])
# The parent observes the Git descendant before allowing the expected alarm to be armed.
sys.stdin.read(1)
owner.arm_child_lifetime()
git.wait()
"""


@contextmanager
def held_child_script(child_script: Any, before: str, after: str) -> Any:
    """Keep a real child in flight until the test observes admission and releases it."""
    with TemporaryDirectory(prefix="worklist-reply-") as directory:
        release = Path(directory) / "release"
        gate = f"""
import time
from pathlib import Path
deadline = time.monotonic() + 30
while not Path({str(release)!r}).exists():
    if time.monotonic() >= deadline:
        raise AssertionError('worklist test did not release the child')
    time.sleep(0.01)
"""
        with child_script(before + gate + after) as children:
            try:
                yield children, release.touch
            finally:
                release.touch()


@contextmanager
def controlled_worklist_clock(owner: Any) -> Any:
    """Advance only the process owner's request clock; retain real harness hang guards."""
    clock = [100.0]
    with mock.patch.object(owner, "time", SimpleNamespace(monotonic=lambda: clock[0])):
        yield clock


@asynccontextmanager
async def busy_default_executor() -> AsyncIterator[list[asyncio.Future[None]]]:
    """Hold both executor workers and one queued job until the route has answered."""
    loop = asyncio.get_running_loop()
    release = threading.Event()
    entered = [threading.Event(), threading.Event()]
    executor = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(executor)

    def blocked(number: int) -> None:
        if number < len(entered):
            entered[number].set()
        assert release.wait(HANG_GUARD_SECONDS), "executor workers were not released"

    background = [loop.run_in_executor(None, blocked, number) for number in range(3)]
    try:
        await async_wait_until(
            lambda: all(one.is_set() for one in entered), "both executor workers"
        )
        yield background
    finally:
        release.set()
        await asyncio.gather(*background)
        executor.shutdown()


def _children(pid: int) -> list[int]:
    try:
        return [int(one) for one in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
    except FileNotFoundError:
        return []


_BLOCKED_GIT = """
import os, subprocess
from agents_remember.application import reviewer_worklist_child as child
def blocked(*args, **kwargs):
    read_end, write_end = os.pipe()
    try:
        subprocess.run(['git', 'cat-file', '--batch'], cwd=args[0].code_repo_path,
                       stdin=read_end, capture_output=True, check=True)
    finally:
        os.close(read_end); os.close(write_end)
child.leaf_worklist = blocked
child.main()
"""


def _all_children(pid: int) -> list[int]:
    found: list[int] = []
    for task in Path(f"/proc/{pid}/task").glob("*/children"):
        found += [int(one) for one in task.read_text().split()]
    return found


def _gone(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0] == "Z"
    except FileNotFoundError:
        return True


def _wait_gone(pids: list[int], seconds: float) -> bool:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if all(_gone(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return all(_gone(pid) for pid in pids)


_PARENT = """
import json, subprocess, sys
from agents_remember.kernel import reviewer_worklist_process as owner
spec = json.load(open(sys.argv[1]))
actual = subprocess.Popen
def spawn(argv, *args, **kwargs):
    if argv[1:] == ['-P', '-m', owner.CHILD_MODULE]:
        argv = [argv[0], '-P', '-c', spec['script']]
    return actual(argv, *args, **kwargs)
subprocess.Popen = spawn
print('ready', flush=True)
owner.ReviewerWorklistProcesses().compute(spec['payload'], spec['source'])
"""


def joined_requesters(threads: list[threading.Thread], children: list[Any]) -> int:
    """Observe the active-child bound while waiting for all requesters to finish."""
    most = 0

    def finished() -> bool:
        nonlocal most
        most = max(most, sum(child.poll() is None for child in children))
        return all(not thread.is_alive() for thread in threads)

    wait_until(finished, "all worklist requesters to finish")
    return most


def _alive(children: list[subprocess.Popen[Any]]) -> int:
    return sum(child.poll() is None for child in children)
