"""Tests for the dashboard serving layer (slice 04, commit 4a).

Covers the pure per-entity projection diff (``serving.delta``), the shared projector's
prime/current/subscribe fan-out (``serving.projector``), the SSE event sequence
(``serving.app.stream_events`` -- snapshot then deltas), the FastAPI app endpoints via
TestClient (``/api/state``, static mount), the shipped static-bundle resolver
(``serving.static``), and the umbrella ``agents-remember dashboard`` CLI parsing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.cli import __main__ as cli_main
from agents_remember.cli import dashboard as cli_dashboard
from agents_remember.mcp.config import ConfigError, McpRuntimeConfig
from agents_remember.observer.lifecycle_state import State
from agents_remember.observer.projection import (
    Analytics,
    EnclosureNode,
    LifecycleProjection,
    Metrics,
    ProviderNode,
    RouteCoverageNode,
    WorkspaceProjection,
)
from agents_remember.serving.app import create_app, stream_events
from agents_remember.serving.delta import DeltaEvent, diff_projection
from agents_remember.serving.projector import Projector
from agents_remember.serving.static import dashboard_static_dir

_TS = "2026-06-14T10:00:00Z"


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


def _lifecycle(ident: str, *, tokens: int = 0, state: State = "running") -> LifecycleProjection:
    return LifecycleProjection(
        id=ident,
        state=state,
        phase="build",
        fleeting=False,
        startedAt=_TS,
        lastEventTs=_TS,
        tokens=tokens,
    )


def _provider(ident: str, *, state: str = "ready") -> ProviderNode:
    return ProviderNode(id=ident, state=state)


def _enclosure(name: str) -> EnclosureNode:
    return EnclosureNode(
        enclosure=name,
        taskId="t1",
        taskName="task",
        repoName="repo",
        lifecycleId="L1",
        worktreeGroup="grp",
        humanReviewStatus="approved",
        closeoutStatus="completed",
        integrationStatus="not-started",
        cleanup="pending",
    )


def _projection(
    *,
    lifecycles: tuple[LifecycleProjection, ...] = (),
    providers: tuple[ProviderNode, ...] = (),
    enclosures: tuple[EnclosureNode, ...] = (),
    metrics: Metrics | None = None,
    analytics: Analytics | None = None,
) -> WorkspaceProjection:
    return WorkspaceProjection(
        generatedAt=_TS,
        lifecycles=list(lifecycles),
        providers=list(providers),
        enclosures=list(enclosures),
        metrics=metrics or Metrics(),
        analytics=analytics or Analytics(),
    )


class DeltaTests(unittest.TestCase):
    def test_first_tick_yields_no_deltas(self) -> None:
        self.assertEqual(diff_projection(None, _projection(lifecycles=(_lifecycle("L1"),))), [])

    def test_unchanged_yields_no_deltas(self) -> None:
        prev = _projection(lifecycles=(_lifecycle("L1"),), providers=(_provider("p"),))
        self.assertEqual(diff_projection(prev, _projection(
            lifecycles=(_lifecycle("L1"),), providers=(_provider("p"),)
        )), [])

    def test_lifecycle_added(self) -> None:
        deltas = diff_projection(_projection(), _projection(lifecycles=(_lifecycle("L1"),)))
        self.assertEqual([(d.event, d.data) for d in deltas], [("lifecycle", _lifecycle("L1"))])

    def test_lifecycle_changed(self) -> None:
        prev = _projection(lifecycles=(_lifecycle("L1", tokens=0),))
        deltas = diff_projection(prev, _projection(lifecycles=(_lifecycle("L1", tokens=9),)))
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0].event, "lifecycle")
        self.assertEqual(deltas[0].data, _lifecycle("L1", tokens=9))

    def test_lifecycle_removed(self) -> None:
        deltas = diff_projection(_projection(lifecycles=(_lifecycle("L1"),)), _projection())
        self.assertEqual(deltas, [DeltaEvent("lifecycle.removed", {"id": "L1"})])

    def test_provider_added_and_removed(self) -> None:
        prev = _projection(providers=(_provider("a"),))
        cur = _projection(providers=(_provider("b"),))
        deltas = diff_projection(prev, cur)
        self.assertIn(DeltaEvent("provider", _provider("b")), deltas)
        self.assertIn(DeltaEvent("provider.removed", {"id": "a"}), deltas)

    def test_enclosure_removed(self) -> None:
        deltas = diff_projection(_projection(enclosures=(_enclosure("e1"),)), _projection())
        self.assertEqual(deltas, [DeltaEvent("enclosure.removed", {"enclosure": "e1"})])

    def test_metrics_changed(self) -> None:
        deltas = diff_projection(_projection(), _projection(metrics=Metrics(lifecycleCount=1)))
        self.assertEqual(deltas, [DeltaEvent("metrics", Metrics(lifecycleCount=1))])

    def test_analytics_changed(self) -> None:
        cur_analytics = Analytics(routeCoverage=[RouteCoverageNode(route="r")])
        deltas = diff_projection(_projection(), _projection(analytics=cur_analytics))
        self.assertEqual(deltas, [DeltaEvent("analytics", cur_analytics)])

    def test_removals_are_sorted_for_determinism(self) -> None:
        prev = _projection(
            lifecycles=(_lifecycle("L2"), _lifecycle("L1"), _lifecycle("L3"))
        )
        deltas = diff_projection(prev, _projection())
        self.assertEqual(
            [d.data for d in deltas],
            [{"id": "L1"}, {"id": "L2"}, {"id": "L3"}],
        )


class ProjectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_prime_sets_latest(self) -> None:
        projector = Projector(_config(self.tmp), interval=100)
        await projector.prime()
        seq, latest = projector.current()
        self.assertEqual(seq, 0)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.version, 1)

    async def test_subscribe_receives_broadcast(self) -> None:
        projector = Projector(_config(self.tmp), interval=100)
        agen = projector.subscribe()
        pending = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.02)  # let subscribe() register its queue
        projector._broadcast((7, DeltaEvent("lifecycle", _lifecycle("L1"))))
        seq, delta = await asyncio.wait_for(pending, timeout=1)
        self.assertEqual(seq, 7)
        self.assertEqual(delta.event, "lifecycle")
        await agen.aclose()


class StreamEventsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_snapshot_then_delta(self) -> None:
        projector = Projector(_config(self.tmp), interval=100)
        await projector.prime()
        gen = stream_events(projector)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1)
        self.assertEqual(first.event, "snapshot")

        pending = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.02)
        projector._broadcast((1, DeltaEvent("lifecycle", _lifecycle("L1"))))
        second = await asyncio.wait_for(pending, timeout=1)
        self.assertEqual(second.event, "lifecycle")
        await gen.aclose()


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_state_endpoint_serves_projection(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["lifecycles"], [])

    def test_root_serves_placeholder(self) -> None:
        app = create_app(_config(self.tmp), interval=100)
        with TestClient(app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("MISSION CONTROL", response.text)


class StaticTests(unittest.TestCase):
    def test_static_dir_resolves_to_shipped_placeholder(self) -> None:
        static_dir = dashboard_static_dir()
        self.assertIsNotNone(static_dir)
        assert static_dir is not None
        self.assertTrue((static_dir / "index.html").is_file())


class CliTests(unittest.TestCase):
    def test_dashboard_subcommand_parsing(self) -> None:
        namespace = cli_main.build_parser().parse_args(
            ["dashboard", "--config", "/abs/settings.json", "--port", "9999"]
        )
        self.assertEqual(namespace.config, "/abs/settings.json")
        self.assertEqual(namespace.port, 9999)
        self.assertEqual(namespace.host, "127.0.0.1")
        self.assertEqual(namespace.interval, 1.0)
        self.assertIs(namespace.func, cli_dashboard.run)


class CliRunTests(unittest.TestCase):
    def _args(self) -> argparse.Namespace:
        return argparse.Namespace(
            config="/abs/settings.json", host="127.0.0.1", port=8765, interval=1.0
        )

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

    def test_run_reports_config_error(self) -> None:
        with mock.patch.object(cli_dashboard, "load_config", side_effect=ConfigError("bad")):
            result = cli_dashboard.run(self._args())
        self.assertEqual(result, 1)

    def test_main_dispatches_to_subcommand(self) -> None:
        with mock.patch.object(cli_dashboard, "run", return_value=0) as run_stub:
            result = cli_main.main(["dashboard", "--config", "/abs/settings.json"])
        self.assertEqual(result, 0)
        run_stub.assert_called_once()


if __name__ == "__main__":
    unittest.main()
