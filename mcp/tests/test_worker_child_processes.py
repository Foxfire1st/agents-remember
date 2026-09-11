from __future__ import annotations

import os
import subprocess
import sys
import time
from unittest import mock

import pytest
from agents_remember.worktrees.integration.lifecycle.worker.child_processes import (
    DetachedWorkerChildren,
    retain_detached_worker_child,
)
from agents_remember.worktrees.integration.lifecycle.worker.termination import (
    require_linux_worker_runtime,
)


def test_retained_worker_child_is_reaped_by_its_owner() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    retain_detached_worker_child(process)

    deadline = time.monotonic() + 5
    while process.returncode is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert process.returncode == 0
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)


def test_child_registry_is_idempotent_and_refuses_numeric_pid_aliasing() -> None:
    owner = DetachedWorkerChildren()
    first = mock.Mock(pid=1234)
    alias = mock.Mock(pid=1234)
    thread = mock.Mock()
    with mock.patch(
        "agents_remember.worktrees.integration.lifecycle.worker.child_processes.threading.Thread",
        return_value=thread,
    ):
        owner.retain(first)
        owner.retain(first)
        with pytest.raises(RuntimeError, match="different retained worker"):
            owner.retain(alias)

    thread.start.assert_called_once_with()


def test_reaper_does_not_release_a_pid_now_owned_by_another_process() -> None:
    owner = DetachedWorkerChildren()
    completed = mock.Mock(pid=1234)
    successor = mock.Mock(pid=1234)
    owner._children[1234] = successor

    owner._wait_and_release(completed)

    completed.wait.assert_called_once_with()
    assert owner._children == {1234: successor}


def test_linux_worker_boundary_refuses_a_python_without_native_pidfd() -> None:
    with (
        mock.patch("sys.platform", "linux"),
        mock.patch("os.pidfd_open", None),
        mock.patch("signal.pidfd_send_signal", None),
        pytest.raises(RuntimeError, match=r"scripts/bootstrap-mcp-venv\.sh --replace"),
    ):
        require_linux_worker_runtime()


def test_non_linux_import_boundary_does_not_require_pidfd() -> None:
    with (
        mock.patch("sys.platform", "darwin"),
        mock.patch("os.pidfd_open", None),
        mock.patch("signal.pidfd_send_signal", None),
    ):
        require_linux_worker_runtime()
