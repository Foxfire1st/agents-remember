"""A proved host exit invalidates once; an unarmed or reused identity stays unknown."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from agents_remember.serving.paseo.paseo_process_record import RecordState
from agents_remember.serving.projections import host_supervisor
from agents_remember.serving.projections.host_activity import HostActivityInput
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from test_host_activity_projection import publish
from test_paseo_role_agents import runtime_config


def test_unknown_without_a_supervisor_subscription_does_not_certify_old_activity(tmp_path: Path):
    config = runtime_config(tmp_path, tmp_path / "host")
    publish(tmp_path / "host")
    bridge = Mock(return_value={"publication": {"bootId": "boot-a"}})
    assert not HostActivityInput(bridge).read(config).known
    bridge.assert_not_called()


def test_supervisor_identity_is_proved_again_after_pidfd_open(tmp_path: Path):
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) as child:
        try:
            with patch.object(
                host_supervisor,
                "inspect_record",
                side_effect=[RecordState("own", child.pid), RecordState("stale", child.pid)],
            ) as inspect:
                assert host_supervisor._open_supervisor(tmp_path) is None
                assert inspect.call_count == 2
        finally:
            child.terminate()
            child.wait()


def test_native_pidfd_exit_invalidates_once_and_releases_the_subscription(tmp_path: Path):
    async def exercise():
        subscription = host_supervisor.HostSupervisorSubscription()
        changed = asyncio.Event()
        calls = 0

        def exited():
            nonlocal calls
            calls += 1
            changed.set()

        with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]) as child:
            try:
                with patch.object(
                    host_supervisor, "inspect_record", return_value=RecordState("own", child.pid)
                ) as inspect:
                    await subscription.refresh(tmp_path, exited, None)
                assert subscription.armed
                assert inspect.call_count == 2
                child.kill()
                await asyncio.wait_for(changed.wait(), timeout=HANG_GUARD_SECONDS)
                child.wait()
                assert not subscription.armed
                await asyncio.sleep(0)
                assert calls == 1
            finally:
                subscription.close()
                if child.poll() is None:
                    child.kill()
                    child.wait()

    asyncio.run(exercise())


def test_subscription_failure_is_unknown_and_does_not_keep_the_old_handle(tmp_path: Path):
    async def exercise():
        subscription = host_supervisor.HostSupervisorSubscription()
        with patch.object(
            host_supervisor, "inspect_record", return_value=RecordState("own", os.getpid())
        ):
            await subscription.refresh(tmp_path, Mock(), None)
        assert subscription.armed
        with (
            patch.object(host_supervisor.os, "pidfd_open", side_effect=PermissionError("denied")),
            patch.object(
                host_supervisor, "inspect_record", return_value=RecordState("own", os.getpid())
            ),
        ):
            await subscription.refresh(tmp_path, Mock(), None)
        assert subscription.started
        assert not subscription.armed

    asyncio.run(exercise())
