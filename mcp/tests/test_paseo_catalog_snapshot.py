"""The saved-offer route reads shared data without a query or a start decision."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents_remember.cli import paseo_catalog
from agents_remember.cli.paseo_bridge import require_bridge_runtime
from agents_remember.cli.paseo_catalog_snapshot import register_saved_catalog_route
from agents_remember.cli.paseo_catalog_store import catalog_store_path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_paseo_catalog import CATALOG, FakeBridge, runtime_config


class SavedCatalogRouteTests(unittest.TestCase):
    def test_route_reads_only_and_never_decides_a_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = runtime_config(Path(directory))
            config.workspace_root.mkdir()
            config.coordination_root.mkdir()
            path = catalog_store_path(require_bridge_runtime(config).home)
            app = FastAPI()
            register_saved_catalog_route(app, config)
            bridge = FakeBridge(CATALOG)

            def files() -> dict[str, bytes]:
                return {
                    item.as_posix(): item.read_bytes()
                    for item in path.parent.rglob("*")
                    if item.is_file()
                }

            with patch.object(paseo_catalog, "bridge_call", bridge), TestClient(app) as client:
                before = files()
                empty = client.get("/api/role-launch/saved-catalog").json()
                self.assertEqual(empty["catalogDiscovery"]["source"], "unknown")
                self.assertNotIn("error", empty["catalogDiscovery"])
                self.assertEqual(files(), before)
                self.assertEqual(bridge.calls, [])

                saved = paseo_catalog.launcher_catalog(config)
                before = files()
                bridge.calls.clear()
                answer = client.get("/api/role-launch/saved-catalog").json()
                self.assertEqual(answer["catalogDiscovery"]["source"], "saved")
                self.assertNotIn("error", answer["catalogDiscovery"])
                self.assertEqual(answer["catalogDiscovery"]["readAt"], saved.discovery["readAt"])
                self.assertEqual(answer["agents"][0]["id"], saved.agents[0]["id"])
                self.assertNotIn("roleDefaults", answer)
                self.assertNotIn("execution", answer)
                self.assertEqual(files(), before)
                self.assertEqual(bridge.calls, [])
