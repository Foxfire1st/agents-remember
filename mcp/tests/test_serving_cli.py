from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

from _global_state import preserve_owned_mutable_state
from agents_remember.cli import __main__ as cli_main
from agents_remember.cli import dashboard as cli_dashboard
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
)
from agents_remember.serving.build_info import (
    ServingBuild,
    _git_worktree_dirty,
    resolve_serving_build,
)
from agents_remember.serving.delta import diff_projection
from agents_remember.serving.projections.projection_store import project_and_write
from agents_remember.serving.sim import (
    ReplayClock,
    SimError,
    SimSetup,
    build_sim,
    load_fixture,
    parse_sim_speed,
)
from agents_remember.serving.static import dashboard_static_dir
from test_serving import FIXTURE_DIR, _build_wire, _config


class BuildInfoTests(unittest.TestCase):
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_serving_cli.py:37).
    def test_resolves_commit_in_a_git_checkout(self) -> None:  # pragma: no cover
        build = resolve_serving_build()
        self.assertTrue(build.version)
        self.assertTrue(build.booted_at)
        # The test tree lives in a checkout, so the short hash resolves and rides the payload.
        self.assertIsNotNone(build.commit)
        payload = _build_wire(build)
        self.assertEqual(payload["commit"], build.commit)
        self.assertEqual(payload["bootedAt"], build.booted_at)
        # Rewritten: this used to index ``dashboardBuild`` unconditionally, which only held
        # while the fingerprint sidecar was committed alongside the bundle. Both are now
        # generated at release time, so the stamp is present-or-omitted, never fabricated.
        if build.dashboard_build is None:
            self.assertNotIn("dashboardBuild", payload)
        else:
            self.assertEqual(payload["dashboardBuild"], build.dashboard_build)

    def test_off_checkout_serves_version_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build = resolve_serving_build(anchor=Path(tmp))
        self.assertIsNone(build.commit)
        self.assertNotIn("commit", _build_wire(build))  # never a faked hash
        self.assertFalse(build.dirty)
        self.assertNotIn("dirty", _build_wire(build))  # the wheel path stays clean

    def test_payload_shape_is_camel_case(self) -> None:
        build = ServingBuild(
            version="9.9.9",
            commit="abc1234",
            booted_at="2026-07-07T05:00:00Z",
            dashboard_build="dashboard-123",
        )
        self.assertEqual(
            _build_wire(build),
            {
                "version": "9.9.9",
                "bootedAt": "2026-07-07T05:00:00Z",
                "commit": "abc1234",
                "dashboardBuild": "dashboard-123",
            },
        )

    def test_dirty_flag_is_additive_on_the_payload(self) -> None:
        clean = ServingBuild(version="9.9.9", commit="abc1234", booted_at="2026-07-07T05:00:00Z")
        self.assertNotIn("dirty", _build_wire(clean))  # omitted, never a faked "clean" fact
        dirty = ServingBuild(
            version="9.9.9", commit="abc1234", booted_at="2026-07-07T05:00:00Z", dirty=True
        )
        self.assertEqual(_build_wire(dirty)["dirty"], True)

    def test_dirty_detection_in_a_checkout(self) -> None:
        def git(root: Path, *argv: str) -> None:
            subprocess.run(
                ["git", *argv],
                cwd=root,
                check=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "--quiet")
            (root / "tracked.txt").write_text("one\n", encoding="utf-8")
            git(root, "add", "tracked.txt")
            git(
                root,
                "-c",
                "user.email=test@example.com",
                "-c",
                "user.name=test",
                "commit",
                "--quiet",
                "-m",
                "init",
            )
            clean = resolve_serving_build(anchor=root)
            self.assertIsNotNone(clean.commit)
            self.assertFalse(clean.dirty)  # a committed tree is clean

            (root / "loose.txt").write_text("untracked\n", encoding="utf-8")
            self.assertTrue(resolve_serving_build(anchor=root).dirty)  # untracked counts

            git(root, "add", "loose.txt")
            git(
                root,
                "-c",
                "user.email=test@example.com",
                "-c",
                "user.name=test",
                "commit",
                "--quiet",
                "-m",
                "track loose",
            )
            (root / "tracked.txt").write_text("two\n", encoding="utf-8")
            self.assertTrue(resolve_serving_build(anchor=root).dirty)  # tracked edits count

    def test_dirty_probe_is_tri_state_and_fails_open(self) -> None:
        # The probe must never fabricate a "clean" tree it did not verify. Proven-clean
        # is False, proven-dirty is True, and an UNPROVABLE probe (git status raises or exits
        # non-zero) fails OPEN to None -- "not proven clean", never pristine. Reverting to the
        # old fail-closed `return False` collapses both unknown cases to a fabricated clean.
        # Seam is the package's one git runner (the probe no longer spawns git itself).
        run = "agents_remember.serving.build_info.run_git"
        anchor = Path("/some/checkout")

        dirty = subprocess.CompletedProcess(args=[], returncode=0, stdout=" M edited.py\n")
        with mock.patch(run, return_value=dirty):
            self.assertIs(_git_worktree_dirty(anchor), True)

        clean = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        with mock.patch(run, return_value=clean):
            self.assertIs(_git_worktree_dirty(anchor), False)  # proven clean, distinct from None

        # git present, HEAD resolved, but `status` specifically raises (locked index, etc.).
        with mock.patch(run, side_effect=OSError("git status: index locked")):
            self.assertIsNone(_git_worktree_dirty(anchor))  # unknown, NOT a fabricated clean

        # `status` runs but exits non-zero (e.g. a transient repo error).
        failed = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="fatal")
        with mock.patch(run, return_value=failed):
            self.assertIsNone(_git_worktree_dirty(anchor))

    def test_status_failure_does_not_assert_a_pristine_tree(self) -> None:
        # End-to-end: a commit DID resolve (rev-parse ok) but `git status` failed, so the
        # stamp must serve the hash WITHOUT claiming the tree is clean. Unknown dirtiness is
        # omitted from the wire exactly like a clean tree, but the object holds None (not False)
        # so nothing internally asserts a verified-pristine tree.
        def fake_run(
            _repo: Path, arguments: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess:
            if arguments[:1] == ["rev-parse"]:
                return subprocess.CompletedProcess(arguments, 0, stdout="deadbee\n", stderr="")
            raise OSError("git status: index locked")

        with mock.patch("agents_remember.serving.build_info.run_git", side_effect=fake_run):
            build = resolve_serving_build(anchor=Path("/some/checkout"))

        self.assertEqual(build.commit, "deadbee")  # the hash resolved and rides the wire
        self.assertIsNone(build.dirty)  # dirtiness is UNKNOWN -- not the fail-closed False
        payload = _build_wire(build)
        self.assertEqual(payload["commit"], "deadbee")
        self.assertNotIn("dirty", payload)  # absence is not a pristine claim, just no warning


class StaticTests(unittest.TestCase):
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_serving_cli.py:184).
    def test_static_dir_resolves_only_a_real_built_bundle(self) -> None:  # pragma: no cover
        # Rewritten: the old assertion (never ``None``) encoded the removed contract that the
        # 28 MB generated bundle lives in version control. What survives is the honest half --
        # when resolution succeeds it points at a real build, never at an empty directory.
        # The ``None`` half is asserted deterministically in test_static.py.
        static_dir = dashboard_static_dir()
        if static_dir is None:
            self.skipTest("no frontend build in this checkout (see test_static.py)")
        self.assertTrue((static_dir / "index.html").is_file())
        self.assertTrue((static_dir / "assets").is_dir())


class CliTests(unittest.TestCase):
    def test_dashboard_subcommand_parsing(self) -> None:
        namespace = cli_main.build_parser().parse_args(
            ["dashboard", "--config", "/abs/settings.json", "--port", "9999"]
        )
        self.assertEqual(namespace.config, "/abs/settings.json")
        self.assertEqual(namespace.port, 9999)
        self.assertEqual(namespace.host, "127.0.0.1")
        self.assertEqual(namespace.interval, 1.0)
        self.assertIsNone(namespace.heartbeat)
        self.assertIs(namespace.func, cli_dashboard.run)


class CliRunTests(unittest.TestCase):
    def setUp(self) -> None:
        state = preserve_owned_mutable_state()
        state.__enter__()
        self.addCleanup(state.__exit__, None, None, None)

    def _args(self, **overrides: object) -> argparse.Namespace:
        base = {
            "config": "/abs/settings.json",
            "host": "127.0.0.1",
            "port": 8765,
            "interval": 1.0,
            "heartbeat": None,
            "reload": False,
            "sim": None,
            "sim_speed": "1",
            "daemon": False,
            "status": False,
            "stop": False,
            "no_access_log": False,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_run_launches_server(self) -> None:
        with (
            mock.patch.object(cli_dashboard, "load_config", return_value=object()) as load,
            mock.patch.object(cli_dashboard, "create_app", return_value="APP") as create,
            mock.patch("uvicorn.run") as serve,
        ):
            result = cli_dashboard.run(self._args())
        self.assertEqual(result, 0)
        load.assert_called_once_with("/abs/settings.json")
        create.assert_called_once()
        serve.assert_called_once()
        # A bounded graceful-shutdown window must be passed so an open SSE
        # stream cannot make SIGTERM hang the process forever (port released, zombie survives).
        _, kwargs = serve.call_args
        self.assertEqual(
            kwargs["timeout_graceful_shutdown"],
            cli_dashboard.DASHBOARD_GRACEFUL_SHUTDOWN_SECONDS,
        )
        self.assertGreater(kwargs["timeout_graceful_shutdown"], 0)

    def test_run_reports_config_error(self) -> None:
        with mock.patch.object(cli_dashboard, "load_config", side_effect=ConfigError("bad")):
            result = cli_dashboard.run(self._args())
        self.assertEqual(result, 1)

    def test_run_reload_launches_the_dev_factory(self) -> None:
        with (
            mock.patch.object(cli_dashboard, "load_config", return_value=object()),
            mock.patch.object(cli_dashboard, "create_app") as create,
            mock.patch("uvicorn.run") as serve,
        ):
            result = cli_dashboard.run(self._args(reload=True))
        self.assertEqual(result, 0)
        # --reload passes an import-string factory so uvicorn's reloader can re-import on
        # change; the app object is never pre-built in this branch.
        create.assert_not_called()
        serve.assert_called_once()
        args, kwargs = serve.call_args
        self.assertEqual(args[0], "agents_remember.cli.dashboard:_dev_app")
        self.assertTrue(kwargs["factory"])
        self.assertTrue(kwargs["reload"])
        # The reload dev path shuts down on the same bounded graceful window.
        self.assertEqual(
            kwargs["timeout_graceful_shutdown"],
            cli_dashboard.DASHBOARD_GRACEFUL_SHUTDOWN_SECONDS,
        )

    def test_run_reload_with_sim_is_rejected(self) -> None:
        with mock.patch.object(cli_dashboard, "load_config", return_value=object()):
            result = cli_dashboard.run(self._args(reload=True, sim="/fix"))
        self.assertEqual(result, 1)

    def test_dev_app_factory_builds_from_env(self) -> None:
        with (
            mock.patch.object(cli_dashboard, "load_config", return_value=object()) as load,
            mock.patch.object(cli_dashboard, "create_app", return_value="APP") as create,
            mock.patch.dict(
                os.environ,
                {
                    cli_dashboard._DEV_CONFIG_ENV: "/abs/settings.json",
                    cli_dashboard._DEV_INTERVAL_ENV: "2.5",
                },
                clear=False,
            ),
        ):
            app = cli_dashboard._dev_app()
        self.assertEqual(app, "APP")
        load.assert_called_once_with("/abs/settings.json")
        _, kwargs = create.call_args
        self.assertEqual(kwargs["cadence"].interval, 2.5)

    def test_main_dispatches_to_subcommand(self) -> None:
        with mock.patch.object(cli_dashboard, "run", return_value=0) as run_stub:
            result = cli_main.main(["dashboard", "--config", "/abs/settings.json"])
        self.assertEqual(result, 0)
        run_stub.assert_called_once()


class SimFixtureTests(unittest.TestCase):
    def test_load_fixture_is_sorted(self) -> None:
        events = load_fixture(FIXTURE_DIR)
        self.assertEqual(len(events), 8)
        self.assertEqual(events[0].kind, "lifecycle.started")
        self.assertEqual([e.id for e in events[:3]], ["sim-e1", "sim-e2", "sim-e3"])

    def test_load_fixture_missing_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(load_fixture(Path(empty)), [])

    def test_parse_sim_speed(self) -> None:
        self.assertEqual(parse_sim_speed("paused"), 0.0)
        self.assertEqual(parse_sim_speed("10"), 10.0)
        with self.assertRaises(SimError):
            parse_sim_speed("fast")
        with self.assertRaises(SimError):
            parse_sim_speed("-1")

    def test_replay_clock_paused_is_frozen(self) -> None:
        start = datetime(2026, 6, 14, 9, 0, tzinfo=UTC)
        self.assertEqual(ReplayClock(start, speed=0.0).now(), start)

    def test_replay_clock_advances_from_start(self) -> None:
        start = datetime(2026, 6, 14, 9, 0, tzinfo=UTC)
        self.assertGreaterEqual(ReplayClock(start, speed=10.0).now(), start)


class SimReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.config = _config(Path(self._dir.name))

    def tearDown(self) -> None:
        self._dir.cleanup()

    @staticmethod
    def _at(second: int) -> datetime:
        return datetime(2026, 6, 14, 9, 0, second, tzinfo=UTC)

    def _build_sim(self) -> SimSetup:
        """Build a sim and close its throwaway root when the test ends.

        ``build_sim`` hands the caller a live ``TemporaryDirectory``: the CLI holds it for
        the server's lifetime, so the function cannot close it itself. A test that drops the
        setup without closing it leaves ``/tmp/ar-dashboard-sim-*`` to the finaliser.
        """
        sim = build_sim(self.config, FIXTURE_DIR, speed=1.0)
        self.addCleanup(sim.temp_dir.cleanup)
        return sim

    def test_build_sim_overrides_root_to_a_fresh_dir(self) -> None:
        sim = self._build_sim()
        self.assertNotEqual(sim.config.coordination_root, self.config.coordination_root)
        self.assertEqual(sim.config.coordination_root, Path(sim.temp_dir.name))
        self.assertTrue(sim.config.coordination_root.is_dir())

    def test_build_sim_empty_fixture_raises(self) -> None:
        with tempfile.TemporaryDirectory() as empty, self.assertRaises(SimError):
            build_sim(self.config, Path(empty), speed=1.0)

    def test_feeder_is_progressive(self) -> None:
        sim = self._build_sim()
        before_any = datetime(2026, 6, 14, 8, 59, tzinfo=UTC)
        self.assertEqual(sim.feeder.feed(before_any), 0)
        self.assertEqual(sim.feeder.feed(self._at(10)), 3)  # e1, e2, e3
        self.assertEqual(sim.feeder.remaining, 5)
        projection = project_and_write(sim.config, now=self._at(10))
        self.assertEqual(len(projection.lifecycles), 1)
        lifecycle = projection.lifecycles[0]
        self.assertEqual(lifecycle.id, "sim-replay-lifecycle")
        self.assertEqual(lifecycle.phase, "build")
        self.assertEqual(lifecycle.state, "running")
        self.assertFalse(lifecycle.fleeting)

    def test_replay_drives_state_transitions(self) -> None:
        sim = self._build_sim()
        sim.feeder.feed(self._at(10))
        before = project_and_write(sim.config, now=self._at(10))
        sim.feeder.feed(self._at(30))  # through tool.completed + lifecycle.blocked
        after = project_and_write(sim.config, now=self._at(30))
        self.assertEqual(after.lifecycles[0].state, "blocked")
        self.assertEqual(after.lifecycles[0].tokens, 1200)
        self.assertIn("lifecycle", [d.event for d in diff_projection(before, after)])

    def test_replay_is_deterministic(self) -> None:
        moment = self._at(30)
        dumps = []
        for _ in range(2):
            sim = self._build_sim()
            sim.feeder.feed(moment)
            dumps.append(project_and_write(sim.config, now=moment).model_dump(by_alias=True))
        self.assertEqual(dumps[0], dumps[1])


class CliSimTests(unittest.TestCase):
    def setUp(self) -> None:
        state = preserve_owned_mutable_state()
        state.__enter__()
        self.addCleanup(state.__exit__, None, None, None)
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _args(self, **overrides: object) -> argparse.Namespace:
        base = {
            "config": "/abs/settings.json",
            "host": "127.0.0.1",
            "port": 8765,
            "interval": 1.0,
            "heartbeat": None,
            "reload": False,
            "sim": None,
            "sim_speed": "1",
            "daemon": False,
            "status": False,
            "stop": False,
            "no_access_log": False,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_sim_args_parse(self) -> None:
        namespace = cli_main.build_parser().parse_args(
            ["dashboard", "--config", "/abs/settings.json", "--sim", "/fix", "--sim-speed", "10"]
        )
        self.assertEqual(namespace.sim, "/fix")
        self.assertEqual(namespace.sim_speed, "10")

    def test_run_sim_launches_with_clock_and_feeder(self) -> None:
        config = _config(self.tmp)
        with (
            mock.patch.object(cli_dashboard, "load_config", return_value=config),
            mock.patch.object(cli_dashboard, "create_app", return_value="APP") as create,
            mock.patch("uvicorn.run") as serve,
        ):
            result = cli_dashboard.run(self._args(sim=str(FIXTURE_DIR), sim_speed="10"))
        self.assertEqual(result, 0)
        serve.assert_called_once()
        _, kwargs = create.call_args
        self.assertIsNotNone(kwargs["replay"].now)
        self.assertIsNotNone(kwargs["replay"].before_tick)

    def test_run_sim_bad_speed_returns_1(self) -> None:
        config = _config(self.tmp)
        with mock.patch.object(cli_dashboard, "load_config", return_value=config):
            result = cli_dashboard.run(self._args(sim=str(FIXTURE_DIR), sim_speed="bogus"))
        self.assertEqual(result, 1)

    def test_run_sim_empty_fixture_returns_1(self) -> None:
        config = _config(self.tmp)
        empty = self.tmp / "empty"
        empty.mkdir()
        with mock.patch.object(cli_dashboard, "load_config", return_value=config):
            result = cli_dashboard.run(self._args(sim=str(empty), sim_speed="1"))
        self.assertEqual(result, 1)
