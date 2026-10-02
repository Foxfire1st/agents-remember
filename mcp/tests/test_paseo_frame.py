from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents_remember.cli import orca_task_routes, paseo_frame
from agents_remember.cli.paseo_frame import (
    HostFrameFacts,
    frame_descriptor,
    normalise_origin,
)
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoEmbedEntry,
    PaseoRuntimeSettings,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from fastapi import FastAPI
from fastapi.testclient import TestClient

SERVER_ID = "srv_frameTest"
LOCAL = PaseoEmbedEntry("http://127.0.0.1:9797", "http://127.0.0.1:6820")
REMOTE = PaseoEmbedEntry("https://box.tailnet.ts.net", "https://box.tailnet.ts.net:8443/")


def runtime_settings(root: Path) -> PaseoRuntimeSettings:
    return PaseoRuntimeSettings(
        install_prefix=root / "prefix",
        home=root / "home",
        listen="127.0.0.1:6820",
        version="0.11.0-beta.2",
        providers={},
        embed=(LOCAL, REMOTE),
    )


def runtime_config(root: Path, settings: PaseoRuntimeSettings | None) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings" / "ar.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
        paseo_runtime=settings,
    )


def reachable(_config: McpRuntimeConfig) -> HostFrameFacts:
    return HostFrameFacts(reachable=True, server_id=SERVER_ID, projects_workspace_id="wks_projects")


class PaseoFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_frame_base_url_comes_from_the_embed_list_for_the_asking_origin(self) -> None:
        settings = runtime_settings(self.root)
        config = runtime_config(self.root, settings)

        local = frame_descriptor(config, "http://127.0.0.1:9797", host=reachable)
        remote = frame_descriptor(config, "https://box.tailnet.ts.net", host=reachable)

        # Each listed origin gets its own base URL: a fixed loopback value would fail the second.
        self.assertEqual(
            local,
            {
                "available": True,
                "frameBaseUrl": "http://127.0.0.1:6820",
                "serverId": SERVER_ID,
                "projectsWorkspaceId": "wks_projects",
                "projectsWorkspaceDetail": None,
            },
        )
        self.assertEqual(remote["frameBaseUrl"], "https://box.tailnet.ts.net:8443")
        # No secret: nothing of the daemon home (credential, key pair) is in the answer.
        self.assertNotIn(settings.home.as_posix(), json.dumps([local, remote]))

        asked: list[Path] = []

        def recording(asked_config: McpRuntimeConfig) -> HostFrameFacts:
            asked.append(asked_config.workspace_root)
            return reachable(asked_config)

        for origin in ("http://localhost:9797", "http://127.0.0.1:9798", "http://evil.test", None):
            with self.subTest(origin=origin):
                answer = frame_descriptor(config, origin, host=recording)
                self.assertEqual(answer["available"], False)
                self.assertEqual(answer["reason"], "origin-not-listed")
                self.assertIn("embedded chat is not configured for this address", answer["detail"])
                self.assertNotIn("frameBaseUrl", answer)
        self.assertEqual(asked, [], "an unlisted origin must not even reach the runtime")
        frame_descriptor(config, "http://127.0.0.1:9797", host=recording)
        self.assertEqual(asked, [config.workspace_root])

    def test_unavailable_frame_names_not_configured_or_unreachable(self) -> None:
        unconfigured = frame_descriptor(
            runtime_config(self.root, None), "http://127.0.0.1:9797", host=reachable
        )
        self.assertEqual(unconfigured["available"], False)
        self.assertEqual(unconfigured["reason"], "not-configured")
        self.assertIn("no Paseo runtime configured", unconfigured["detail"])

        config = runtime_config(self.root, runtime_settings(self.root))

        def refusing(_config: McpRuntimeConfig) -> HostFrameFacts:
            raise RuntimeError("bridge timeout")

        cases = {
            "connection refused": lambda _config: HostFrameFacts(
                reachable=False, detail="connection refused"
            ),
            "bridge timeout": refusing,
            "server id": lambda _config: HostFrameFacts(reachable=True),
        }
        for expected, host in cases.items():
            with self.subTest(expected=expected):
                down = frame_descriptor(config, "http://127.0.0.1:9797", host=host)
                self.assertEqual(down["available"], False)
                self.assertEqual(down["reason"], "unreachable")
                self.assertIn(expected, down["detail"])
                self.assertNotIn("frameBaseUrl", down)

    def test_frame_stays_available_without_a_projects_workspace(self) -> None:
        config = runtime_config(self.root, runtime_settings(self.root))
        answer = frame_descriptor(
            config,
            "http://127.0.0.1:9797",
            host=lambda _config: HostFrameFacts(
                reachable=True, server_id=SERVER_ID, detail="workspace call timed out"
            ),
        )
        # The frame then opens the application without naming a workspace.
        self.assertEqual(answer["available"], True)
        self.assertEqual(answer["serverId"], SERVER_ID)
        self.assertIsNone(answer["projectsWorkspaceId"])
        self.assertEqual(answer["projectsWorkspaceDetail"], "workspace call timed out")

    def test_route_reads_the_dashboard_origin_from_the_request(self) -> None:
        config = runtime_config(self.root, runtime_settings(self.root))
        app = FastAPI()
        orca_task_routes.register_orca_task_routes(app, config)
        cases = [
            # (base URL the browser used, extra headers, expected frame base URL or None)
            ("http://127.0.0.1:9797", {}, "http://127.0.0.1:6820"),
            ("http://localhost:9797", {}, None),
            ("https://box.tailnet.ts.net", {}, "https://box.tailnet.ts.net:8443"),
            ("https://BOX.tailnet.ts.net:443", {}, "https://box.tailnet.ts.net:8443"),
            # A cross-origin caller is judged by its own origin, not by the host it called.
            ("http://127.0.0.1:9797", {"Origin": "http://evil.test"}, None),
            ("http://localhost:9797", {"Origin": "http://127.0.0.1:9797"}, "http://127.0.0.1:6820"),
        ]
        with patch.object(paseo_frame, "host_frame_facts", reachable):
            for base_url, headers, expected in cases:
                with self.subTest(base_url=base_url, headers=headers):
                    response = TestClient(app, base_url=base_url).get(
                        "/api/orca/frame", headers=headers
                    )
                    self.assertEqual(response.status_code, 200)
                    answer = response.json()
                    if expected is None:
                        self.assertEqual(answer["reason"], "origin-not-listed")
                    else:
                        self.assertEqual(answer["frameBaseUrl"], expected)
                        self.assertEqual(answer["serverId"], SERVER_ID)

        # Until host_frame_facts is wired to the bridge, the route says so instead of framing.
        unwired = TestClient(app, base_url="http://127.0.0.1:9797").get("/api/orca/frame").json()
        self.assertEqual(unwired["reason"], "unreachable")
        self.assertIn("not wired to the Paseo bridge", unwired["detail"])

    def test_origins_compare_without_case_or_default_ports(self) -> None:
        self.assertEqual(normalise_origin("HTTP://LocalHost:80"), "http://localhost")
        self.assertEqual(normalise_origin("https://box.ts.net:443/"), "https://box.ts.net")
        self.assertEqual(normalise_origin("http://[::1]:9797"), "http://[::1]:9797")
        for value in (None, "", "null", "ftp://host", "http://", "http://host:notaport"):
            with self.subTest(value=value):
                self.assertIsNone(normalise_origin(value))


if __name__ == "__main__":
    unittest.main()
