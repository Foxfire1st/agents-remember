"""Startup reconciliation and notification-only host activity intake (MIK-R167)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents_remember.cli import dashboard
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.serving import change_watcher as watcher
from agents_remember.serving.change_watcher import ChangePacer
from agents_remember.serving.paseo.paseo_plugin_files import publish_role_agent
from agents_remember.serving.projections import host_activity
from agents_remember.serving.projections.projection_inputs import (
    ProjectionInputState,
    ProjectionReaders,
    ProjectionRefresh,
    RefreshPass,
)
from agents_remember.serving.projector import Projector
from test_paseo_role_agents import runtime_config

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def test_supervisor_lease_heartbeat_is_not_a_host_activity_input(tmp_path: Path) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish(home)
    publication = home / "agents-remember" / host_activity.HOST_ACTIVITY_FILE
    record = home / "paseo.pid"
    record.write_text('{"pid": 123, "heartbeat": true}')
    roots = watcher.projection_input_roots(config)
    assert home / "agents-remember" in roots
    assert record not in roots
    assert not any(root == home or root in home.parents for root in roots)
    assert watcher.projection_domains_for_paths(config, {str(publication)}) == frozenset(
        {watcher.ProjectionDomain.HOST_ACTIVITY}
    )


def test_dashboard_composition_binds_the_existing_bridge(tmp_path: Path) -> None:
    config = runtime_config(tmp_path, tmp_path / "host")
    with patch.object(
        dashboard, "bridge_call", return_value={"publication": {"bootId": "current"}}
    ) as bridge:
        collaborators = dashboard.serving_collaborators(config)
        assert collaborators.host_activity_reconcile is not None
        assert collaborators.host_activity_reconcile(config) == {
            "publication": {"bootId": "current"}
        }
        bridge.assert_called_once_with(config, "host-activity-snapshot", {})
        assert collaborators.review_trees_shutdown is not None
        collaborators.review_trees_shutdown()


def publish(home: Path, boot: str = "boot-a", **agents: object) -> None:
    directory = home / "agents-remember"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / host_activity.HOST_ACTIVITY_FILE).write_text(
        json.dumps({"bootId": boot, "complete": True, "agents": agents, "ignoredEvents": 3}),
        encoding="utf-8",
    )


def test_startup_replaces_old_activity_before_it_can_be_served(tmp_path: Path) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish(home, known={"state": "in-turn", "eventAt": "2026-10-14T00:00:00Z"})
    publish_role_agent(
        home, "known", {"role": "worker", "task": "repo/master/leaf.json", "reportPath": "/report"}
    )

    def reconcile(*_args: object) -> dict[str, object]:
        publish(home, known={"state": "idle"}, stranger={"state": "in-turn"})
        return {"publication": {"bootId": "boot-a"}}

    intake = host_activity.HostActivityInput(supervisor_armed=lambda: True)
    with patch.object(intake, "_reconcile", side_effect=reconcile) as bridge:
        snapshot = intake.read(config)
        assert snapshot.known
        assert [
            (row.task_key, row.activity.state, row.activity.eventAt) for row in snapshot.agents
        ] == [("repo/master/leaf.json", "idle", None)]
        assert snapshot.ignored_agents == 4
        publish(home, known={"state": "permission", "eventAt": NOW.isoformat()})
        changed = intake.read(config)
        assert changed.agents[0].activity.state == "permission"
        bridge.assert_called_once_with(config)


def test_a_new_host_boot_and_a_new_dashboard_each_require_the_whole_read(tmp_path: Path) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish(home)
    intake = host_activity.HostActivityInput(supervisor_armed=lambda: True)
    with patch.object(
        intake, "_reconcile", return_value={"publication": {"bootId": "boot-a"}}
    ) as bridge:
        assert intake.read(config).known
        publish(home, "boot-b")
        assert not intake.read(config).known  # stale acknowledgement cannot certify this boot
        assert bridge.call_count == 2
        assert not intake.read(config).known
        assert bridge.call_count == 2
    bridge = Mock(side_effect=PaseoBridgeFailure("offline", "host unavailable"))
    assert not host_activity.HostActivityInput(bridge, lambda: True).read(config).known
    bridge.assert_called_once()


def test_an_older_reconciliation_answer_cannot_certify_a_boot(tmp_path: Path) -> None:
    """L184-R1-F08: the reply's own reconciliation number decides, not the order it arrived in.

    A plugin answer names the reconciliation it began. If a later boot's answer is an *earlier*
    number, that reply lost the race: the file may already hold the newer publication, and the
    answer must not certify it. Before this repair only the boot id was compared, so an old-first
    reply consumed the newer answer and the activity was served from a list that never answered it.
    """

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish(home, "boot-a")
    answers = [
        {"publication": {"bootId": "boot-a", "reconcile": 5}},
        {"publication": {"bootId": "boot-b", "reconcile": 4}},
    ]
    intake = host_activity.HostActivityInput(supervisor_armed=lambda: True)
    with patch.object(intake, "_reconcile", side_effect=answers) as bridge:
        assert intake.read(config).known
        assert intake._reconciled_reconcile == 5
        publish(home, "boot-b")
        assert not intake.read(config).known
        assert bridge.call_count == 2
        assert intake._reconciled_boot is None


def test_idle_heartbeats_do_not_read_activity_or_launch_another_snapshot(tmp_path: Path) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish(home)
    bridge = Mock(return_value={"publication": {"bootId": "boot-a"}})
    state = ProjectionInputState(host_activity_reconcile=bridge, host_supervisor_armed=lambda: True)
    readers = ProjectionReaders(repo_surfaces=lambda _config, _now: ([], []))
    observer = config.coordination_root / "logs" / "observer"
    state.read(
        config,
        readers,
        observer_root=observer,
        pass_=RefreshPass(NOW, ProjectionRefresh.full()),
    )
    with patch.object(
        host_activity, "_read_publication", side_effect=AssertionError("periodic activity read")
    ):
        for seconds in range(15, 301, 15):
            inputs = state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(NOW + timedelta(seconds=seconds), ProjectionRefresh.heartbeat()),
            )
            assert inputs.host_activity.known
    bridge.assert_called_once()


def test_periodic_full_and_degraded_wakes_retain_host_without_reads(tmp_path: Path) -> None:
    config = runtime_config(tmp_path, tmp_path / "host")
    publish(tmp_path / "host")
    state = ProjectionInputState(
        host_activity_reconcile=Mock(return_value={"publication": {"bootId": "boot-a"}}),
        host_supervisor_armed=lambda: True,
    )
    readers = ProjectionReaders(repo_surfaces=lambda _config, _now: ([], []))
    observer = config.coordination_root / "logs" / "observer"
    state.read(
        config, readers, observer_root=observer, pass_=RefreshPass(NOW, ProjectionRefresh.full())
    )

    async def exercise():
        clock = [0.0]
        with patch.object(watcher, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            pacer = ChangePacer(interval=1)
            pacer.set_watcher_healthy(True)
            clock[0] = 301
            heal = await pacer.wait()
            assert heal.reason == "full"
            projector = Projector(config)
            for wake in (heal,):
                inputs = state.read(
                    config,
                    readers,
                    observer_root=observer,
                    pass_=RefreshPass(NOW, projector._next_refresh(wake)),
                )
                assert inputs.host_activity.known
            pacer.acknowledge(heal)
            pacer.confirm_full_published()
            pacer.set_watcher_healthy(False)
            clock[0] += 2
            degraded = await pacer.wait()
            assert degraded.reason == "interval"
            inputs = state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(NOW, projector._next_refresh(degraded)),
            )
            assert inputs.host_activity.known

    with (
        patch.object(
            host_activity, "_read_publication", side_effect=AssertionError("periodic host read")
        ),
        patch.object(
            host_activity,
            "read_role_agents",
            side_effect=AssertionError("periodic role-record read"),
        ),
    ):
        asyncio.run(exercise())


def test_failed_boot_reconcile_retries_only_after_a_new_publication(tmp_path: Path) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish(home)
    bridge = Mock(
        side_effect=[
            PaseoBridgeFailure("offline", "host unavailable"),
            {"publication": {"bootId": "boot-a"}},
        ]
    )
    intake = host_activity.HostActivityInput(bridge, lambda: True)
    assert not intake.read(config).known
    assert not intake.read(config).known
    assert bridge.call_count == 1
    publish(home, known={"state": "idle"})
    assert intake.read(config).known
    assert bridge.call_count == 2
