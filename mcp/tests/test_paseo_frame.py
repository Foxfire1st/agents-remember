from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.cli import orca_task_routes, paseo_frame
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.cli.paseo_frame import (
    HostFrameFacts,
    frame_descriptor,
    host_frame_facts,
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
# Written the way a person may write it: the lookup compares origins, not their spelling.
SPELLED = PaseoEmbedEntry("HTTPS://Desk.Example:443", "https://frames.example")


def runtime_settings(root: Path) -> PaseoRuntimeSettings:
    return PaseoRuntimeSettings(
        install_prefix=root / "prefix",
        home=root / "home",
        listen="127.0.0.1:6820",
        version="0.11.0-beta.2",
        providers={},
        embed=(LOCAL, REMOTE, SPELLED),
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


class FakeBridge:
    """Stands in for ``bridge_call``: answers the two commands the frame route uses."""

    def __init__(self, **replies: Any) -> None:
        # Per command: a reply to return, or a failure to raise. Unset commands answer healthily.
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(
        self, _config: McpRuntimeConfig, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((command, payload))
        reply = self.replies.get(command.replace("-", "_"))
        if isinstance(reply, Exception):
            raise reply
        if reply is not None:
            return reply
        if command == "runtime-info":
            return {"serverId": SERVER_ID}
        return {"serverId": SERVER_ID, "workspace": {"id": "wks_real", "directory": payload["cwd"]}}


# The smallest client package the bridge script can load: it connects and knows its server id.
FAKE_CLIENT = {
    "package.json": json.dumps(
        {
            "name": "@getpaseo/client",
            "type": "module",
            "exports": {
                ".": {"default": "./dist/index.js"},
                "./internal/daemon-client": {"default": "./dist/daemon-client.js"},
            },
        }
    ),
    "dist/index.js": "export function createPaseoApi() { return { dispose: async () => {} } }\n",
    "dist/daemon-client.js": """
import { readFileSync } from 'node:fs'
const scenario = JSON.parse(readFileSync(process.env.FAKE_RUNTIME_SCENARIO, 'utf8'))
export class DaemonClient {
  constructor(config) { this.config = config; this.state = { status: 'idle' } }
  async connect() {
    if (scenario.connectError) throw new Error(scenario.connectError)
    if (this.config.url !== scenario.url) throw new Error('unexpected url ' + this.config.url)
    this.state = { status: 'connected' }
  }
  async close() { this.state = { status: 'disposed' } }
  getConnectionState() { return this.state }
  getLastServerInfoMessage() { return { serverId: scenario.serverId, version: '0.11.0-beta.2' } }
}
""",
}


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
        spelled = frame_descriptor(config, "https://desk.example", host=reachable)
        self.assertEqual(spelled["frameBaseUrl"], "https://frames.example")
        # No secret: nothing of the daemon home (credential, key pair) is in the answer.
        self.assertNotIn(settings.home.as_posix(), json.dumps([local, remote]))

        asked: list[Path] = []

        def recording(asked_config: McpRuntimeConfig) -> HostFrameFacts:
            asked.append(asked_config.workspace_root)
            return reachable(asked_config)

        unlisted = (
            "http://localhost:9797",
            "http://127.0.0.1:9798",
            "http://evil.test",
            # Look-alikes that begin with, or contain, a listed origin.
            "http://127.0.0.1:97970",
            "http://127.0.0.1:9797.evil.test",
            "https://box.tailnet.ts.net.evil.test",
            "https://evil.test/?https://box.tailnet.ts.net",
            None,
        )
        for origin in unlisted:
            with self.subTest(origin=origin):
                answer = frame_descriptor(config, origin, host=recording)
                self.assertEqual(
                    answer,
                    {
                        "available": False,
                        "reason": "origin-not-listed",
                        "detail": f"{origin or 'the origin of this request'} is not a dashboard "
                        "origin in paseoRuntime.embed",
                    },
                )
        self.assertEqual(asked, [], "an unlisted origin must not even reach the runtime")
        frame_descriptor(config, "http://127.0.0.1:9797", host=recording)
        self.assertEqual(asked, [config.workspace_root])

    def test_unavailable_frame_names_not_configured_or_unreachable(self) -> None:
        unconfigured = frame_descriptor(
            runtime_config(self.root, None), "http://127.0.0.1:9797", host=reachable
        )
        self.assertEqual(
            unconfigured,
            {
                "available": False,
                "reason": "not-configured",
                "detail": f"{self.root / 'settings' / 'ar.json'} has no paseoRuntime block",
            },
        )

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
                # The reason is the state; the detail does not say it a second time.
                self.assertNotIn("unreachable", down["detail"])

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
            # An Origin header that names no origin (a sandboxed or redirected caller sends
            # "null") is not replaced by the host that was called.
            ("http://127.0.0.1:9797", {"Origin": "null"}, None),
            ("http://127.0.0.1:9797", {"Sec-Fetch-Site": "same-origin"}, "http://127.0.0.1:6820"),
            ("http://127.0.0.1:9797", {"Sec-Fetch-Site": "same-site"}, "http://127.0.0.1:6820"),
            ("http://127.0.0.1:9797", {"Sec-Fetch-Site": "none"}, "http://127.0.0.1:6820"),
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

    def test_origins_compare_without_case_or_default_ports(self) -> None:
        self.assertEqual(normalise_origin("HTTP://LocalHost:80"), "http://localhost")
        self.assertEqual(normalise_origin("https://box.ts.net:443/"), "https://box.ts.net")
        self.assertEqual(normalise_origin("http://[::1]:9797"), "http://[::1]:9797")
        for value in (None, "", "null", "ftp://host", "http://", "http://host:notaport"):
            with self.subTest(value=value):
                self.assertIsNone(normalise_origin(value))


class WiredHostFactsTests(unittest.TestCase):
    """The registered route asking the runtime through a fake bridge."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = runtime_config(self.root, runtime_settings(self.root))

    def ask(self, bridge: FakeBridge, config: McpRuntimeConfig | None = None) -> dict[str, Any]:
        app = FastAPI()
        orca_task_routes.register_orca_task_routes(app, config or self.config)
        with patch.object(paseo_frame, "bridge_call", bridge):
            response = TestClient(app, base_url="http://127.0.0.1:9797").get("/api/orca/frame")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_server_id_and_projects_workspace_come_from_the_bridge(self) -> None:
        bridge = FakeBridge()
        answer = self.ask(bridge)

        projects = (self.root / "projects").resolve().as_posix()
        self.assertEqual(
            answer,
            {
                "available": True,
                "frameBaseUrl": "http://127.0.0.1:6820",
                "serverId": SERVER_ID,
                "projectsWorkspaceId": "wks_real",
                "projectsWorkspaceDetail": None,
            },
        )
        # The identity first, then the launch's reuse-or-create call for the Projects folder,
        # which exists afterwards as it does for a launch. Nothing else is asked of the runtime.
        self.assertEqual(
            bridge.calls, [("runtime-info", {}), ("workspace-open", {"cwd": projects})]
        )
        self.assertTrue((self.root / "projects").is_dir())
        # No cache: a second request asks the runtime again.
        self.ask(bridge)
        self.assertEqual(len(bridge.calls), 4)

    def test_a_failed_workspace_call_leaves_the_frame_available(self) -> None:
        elsewhere = {"serverId": SERVER_ID, "workspace": {"id": "wks_other", "directory": "/other"}}
        cases = {
            "Directory not found": PaseoBridgeFailure("paseo_call_failed", "Directory not found"),
            "ran out of time": PaseoBridgeFailure(
                "paseo_bridge_timeout", "The Paseo bridge call workspace-open ran out of time."
            ),
            "the workspace of /other": elsewhere,
            "unreadable workspace": {"serverId": SERVER_ID},
        }
        for expected, reply in cases.items():
            with self.subTest(expected=expected):
                answer = self.ask(FakeBridge(workspace_open=reply))
                self.assertEqual(answer["available"], True)
                self.assertEqual(answer["serverId"], SERVER_ID)
                self.assertIsNone(answer["projectsWorkspaceId"])
                self.assertIn(expected, answer["projectsWorkspaceDetail"])

    def test_a_failed_identity_call_is_unreachable_and_asks_nothing_more(self) -> None:
        cases = {
            "cannot be reached: ECONNREFUSED": PaseoBridgeFailure(
                "paseo_daemon_unreachable",
                "The Paseo daemon at ws://127.0.0.1:6820/ws cannot be reached: ECONNREFUSED",
            ),
            "not the configured Paseo runtime": PaseoBridgeFailure(
                "paseo_runtime_mismatch",
                "The daemon at ws://127.0.0.1:6820/ws is srv_other, not the configured Paseo "
                "runtime srv_frameTest.",
            ),
            "needs Node.js": PaseoBridgeFailure(
                "paseo_bridge_unavailable",
                "The Paseo bridge needs Node.js on PATH and its packaged script.",
            ),
            "server id": {"serverId": ""},
        }
        for expected, reply in cases.items():
            with self.subTest(expected=expected):
                bridge = FakeBridge(runtime_info=reply)
                answer = self.ask(bridge)
                self.assertEqual(answer["available"], False)
                self.assertEqual(answer["reason"], "unreachable")
                self.assertIn(expected, answer["detail"])
                self.assertEqual(bridge.calls, [("runtime-info", {})])

    def test_no_runtime_and_an_unlisted_origin_never_reach_the_bridge(self) -> None:
        bridge = FakeBridge()
        unconfigured = self.ask(bridge, runtime_config(self.root, None))
        self.assertEqual(unconfigured["reason"], "not-configured")
        self.assertIn("has no paseoRuntime block", unconfigured["detail"])

        app = FastAPI()
        orca_task_routes.register_orca_task_routes(app, self.config)
        with patch.object(paseo_frame, "bridge_call", bridge):
            unlisted = TestClient(app, base_url="http://localhost:9797").get("/api/orca/frame")
        self.assertEqual(unlisted.json()["reason"], "origin-not-listed")
        self.assertEqual(bridge.calls, [])
        # A GET that a page of another site made the browser send carries the dashboard's own
        # Host and no Origin. It gets no frame and causes no call to the runtime.
        with patch.object(paseo_frame, "bridge_call", bridge):
            for marker in ("cross-site", "Cross-Site"):
                forged = TestClient(app, base_url="http://127.0.0.1:9797").get(
                    "/api/orca/frame", headers={"Sec-Fetch-Site": marker}
                )
                self.assertEqual(
                    forged.json(),
                    {
                        "available": False,
                        "reason": "origin-not-listed",
                        "detail": "the request came from a page of another site "
                        "(Sec-Fetch-Site: cross-site)",
                    },
                )
        self.assertEqual(bridge.calls, [])
        # Asked directly without a runtime, the function reports the bridge's own refusal.
        direct = host_frame_facts(runtime_config(self.root, None))
        self.assertEqual(direct.reachable, False)
        self.assertIn("no Paseo runtime configured", direct.detail or "")


@unittest.skipUnless(shutil.which("node"), "the bridge script needs Node.js")
class RuntimeInfoCommandTests(unittest.TestCase):
    """The real bridge script's ``runtime-info`` command against a fake client package."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        settings = runtime_settings(self.root)
        settings.home.mkdir(parents=True)
        (settings.home / "server-id").write_text(SERVER_ID + "\n", encoding="utf-8")
        package = self.root / "prefix" / "node_modules" / "@getpaseo" / "client"
        for name, content in FAKE_CLIENT.items():
            (package / name).parent.mkdir(parents=True, exist_ok=True)
            (package / name).write_text(content, encoding="utf-8")
        self.config = runtime_config(self.root, settings)

    def scenario(self, **scenario: Any) -> Any:
        path = self.root / "scenario.json"
        healthy = {"url": "ws://127.0.0.1:6820/ws", "serverId": SERVER_ID}
        path.write_text(json.dumps({**healthy, **scenario}), encoding="utf-8")
        return patch.dict(os.environ, {"FAKE_RUNTIME_SCENARIO": path.as_posix()})

    def test_it_answers_with_the_server_id_and_nothing_else(self) -> None:
        # The fake client offers no runtime function at all: the command only connects.
        with self.scenario():
            self.assertEqual(bridge_call(self.config, "runtime-info", {}), {"serverId": SERVER_ID})
            # The wired function through the real script: the identity is read, and the workspace
            # call, which this client cannot serve, does not take the frame away.
            facts = host_frame_facts(self.config)
        self.assertEqual(
            (facts.reachable, facts.server_id, facts.projects_workspace_id),
            (True, SERVER_ID, None),
        )
        self.assertTrue(facts.detail)

    def test_a_daemon_that_is_down_or_another_one_is_a_named_failure(self) -> None:
        refusals = {
            "paseo_daemon_unreachable": ({"connectError": "ECONNREFUSED"}, "cannot be reached"),
            "paseo_runtime_mismatch": ({"serverId": "srv_other_home"}, "not the configured"),
        }
        for code, (scenario, said) in refusals.items():
            with self.subTest(code=code), self.scenario(**scenario):
                with self.assertRaises(PaseoBridgeFailure) as raised:
                    bridge_call(self.config, "runtime-info", {})
                self.assertEqual(raised.exception.code, code)
                facts = host_frame_facts(self.config)
                self.assertEqual((facts.reachable, facts.server_id), (False, None))
                self.assertIn(said, facts.detail or "")


if __name__ == "__main__":
    unittest.main()
