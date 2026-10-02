from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from agents_remember.cli import paseo_bridge
from agents_remember.cli.orca_runtime import (
    HOST_CALL_NOT_AVAILABLE,
    OrcaRuntimeFailure,
    runtime_call,
)
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeSettings,
    parse_paseo_runtime_settings,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SCRIPT = REPO_ROOT / "mcp/src/agents_remember/cli/paseo_bridge.mjs"
SERVER_ID = "srv_configured"


def runtime_settings(root: Path, **overrides: Any) -> PaseoRuntimeSettings:
    block: dict[str, Any] = {
        "installPrefix": (root / "prefix").as_posix(),
        "home": (root / "home").as_posix(),
        "listen": "127.0.0.1:6833",
        "version": "0.11.0-beta.2",
        "providers": {},
        "embed": [],
    }
    block.update(overrides)
    settings = parse_paseo_runtime_settings(block)
    assert settings is not None
    return settings


def runtime_config(root: Path, settings: PaseoRuntimeSettings | None) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings" / "mcp.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
        paseo_runtime=settings,
    )


def started_runtime(root: Path, **overrides: Any) -> McpRuntimeConfig:
    """A configured runtime whose daemon home already carries its identity."""

    settings = runtime_settings(root, **overrides)
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "server-id").write_text(SERVER_ID + "\n", encoding="utf-8")
    return runtime_config(root, settings)


# What Node 22 really prints for an uncaught error and for a thrown value that is not an error
# (captured from runs; only the script path is shortened).
NODE_CRASH_REPORT = """file:///opt/ar/cli/paseo_bridge.mjs:58
setTimeout(() => { throw new TypeError('the daemon answered with a frame the client cannot read') }, 0)
                   ^

TypeError: the daemon answered with a frame the client cannot read
    at Timeout._onTimeout (file:///opt/ar/cli/paseo_bridge.mjs:58:26)
    at listOnTimeout (node:internal/timers:585:17)
    at process.processTimers (node:internal/timers:521:7)

Node.js v22.23.2
"""
NODE_THROWN_VALUE_REPORT = """
node:internal/modules/run_main:123
    triggerUncaughtException(
    ^
the client gave up
(Use `node --trace-uncaught ...` to show where the exception was thrown)

Node.js v22.23.2
"""


class BridgeProcessTests(unittest.TestCase):
    def test_unconfigured_or_never_started_runtime_refuses_before_any_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = {
                "paseo_runtime_not_configured": runtime_config(root, None),
                "paseo_daemon_unreachable": runtime_config(root, runtime_settings(root)),
            }
            for code, config in cases.items():
                with (
                    self.subTest(code=code),
                    patch.object(paseo_bridge.shutil, "which", return_value="/usr/bin/node"),
                    patch.object(paseo_bridge.subprocess, "run") as run,
                    self.assertRaises(PaseoBridgeFailure) as raised,
                ):
                    bridge_call(config, "catalog", {})
                self.assertEqual(raised.exception.code, code)
                run.assert_not_called()
            self.assertTrue(str(raised.exception).startswith("The Paseo daemon of "))
        with self.assertRaises(PaseoBridgeFailure) as unconfigured:
            bridge_call(runtime_config(Path("/trusted"), None), "catalog", {})
        self.assertTrue(str(unconfigured.exception).startswith("no Paseo runtime configured: "))

    def test_the_script_is_pointed_at_the_configured_runtime_and_no_other(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = started_runtime(root)
            inherited = {
                "PATH": "/usr/bin",
                "PASEO_HOME": "/other/paseo-home",
                "PASEO_LISTEN": "127.0.0.1:6799",
                "AR_PASEO_URL": "ws://127.0.0.1:6799/ws",
                "AR_PASEO_SERVER_ID": "srv_inherited",
            }
            completed = SimpleNamespace(returncode=0, stdout=json.dumps({"providers": []}))
            with (
                patch.dict(os.environ, inherited, clear=True),
                patch.object(paseo_bridge.shutil, "which", return_value="/usr/bin/node"),
                patch.object(paseo_bridge.subprocess, "run", return_value=completed) as run,
            ):
                reply = bridge_call(config, "catalog", {"refresh": True})

        self.assertEqual(reply, {"providers": []})
        self.assertEqual(
            run.call_args.args[0], ["/usr/bin/node", BRIDGE_SCRIPT.as_posix(), "catalog"]
        )
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"refresh": True})
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        self.assertEqual(
            run.call_args.kwargs["env"],
            {
                "PATH": "/usr/bin",
                "AR_PASEO_INSTALL_PREFIX": (root / "prefix").resolve().as_posix(),
                "AR_PASEO_URL": "ws://127.0.0.1:6833/ws",
                "AR_PASEO_SERVER_ID": SERVER_ID,
                "AR_PASEO_VERSION": "0.11.0-beta.2",
                "AR_PASEO_DEADLINE_MS": "55000",
            },
        )

    def test_every_failure_of_a_call_is_named(self) -> None:
        def reply(returncode: int, stdout: str, stderr: str = "") -> SimpleNamespace:
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

        refused = {"ok": False, "error": {"code": "paseo_daemon_unreachable", "message": "down"}}
        outcomes: dict[str, tuple[Any, str]] = {
            "no reply": (reply(0, ""), "paseo_bridge_invalid_reply"),
            "reply is not an object": (reply(0, "[1]"), "paseo_bridge_invalid_reply"),
            "named refusal": (reply(1, json.dumps(refused)), "paseo_daemon_unreachable"),
            "refusal with exit 0": (reply(0, json.dumps(refused)), "paseo_daemon_unreachable"),
            "failed without a reason": (reply(1, "{}"), "paseo_call_failed"),
            "cannot start": (OSError("exec format error"), "paseo_bridge_unavailable"),
            "limit": (subprocess.TimeoutExpired("node", 60), "paseo_bridge_timeout"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            config = started_runtime(Path(temporary))
            for label, (outcome, code) in outcomes.items():
                with (
                    self.subTest(label),
                    patch.object(paseo_bridge.shutil, "which", return_value="/usr/bin/node"),
                    patch.object(paseo_bridge.subprocess, "run", side_effect=[outcome]),
                    self.assertRaises(PaseoBridgeFailure) as raised,
                ):
                    bridge_call(config, "catalog", {})
                self.assertEqual(raised.exception.code, code)
                self.assertLessEqual(len(str(raised.exception)), 800)
            with (
                patch.object(paseo_bridge.shutil, "which", return_value=None),
                patch.object(paseo_bridge.subprocess, "run") as run,
                self.assertRaises(PaseoBridgeFailure) as raised,
            ):
                bridge_call(config, "catalog", {})
            self.assertEqual(raised.exception.code, "paseo_bridge_unavailable")
            run.assert_not_called()

            long_error = "RangeError: " + "x" * 500
            reports = {
                "a crash report carries its error line": (
                    NODE_CRASH_REPORT,
                    "TypeError: the daemon answered with a frame the client cannot read",
                ),
                "the last error line is the cause, not one logged earlier": (
                    "Error: socket closed (a line the client logged)\n" + NODE_CRASH_REPORT,
                    "TypeError: the daemon answered with a frame the client cannot read",
                ),
                "an error line is cut to 200 characters": (long_error + "\n", long_error[:200]),
                "without an error line the end of the text is kept, minus the stack": (
                    NODE_THROWN_VALUE_REPORT,
                    "node:internal/modules/run_main:123\n    triggerUncaughtException(\n    ^\n"
                    "the client gave up\n"
                    "(Use `node --trace-uncaught ...` to show where the exception was thrown)",
                ),
            }
            for label, (stderr, cause) in reports.items():
                with (
                    self.subTest(label),
                    patch.object(paseo_bridge.shutil, "which", return_value="/usr/bin/node"),
                    patch.object(
                        paseo_bridge.subprocess, "run", return_value=reply(1, "", stderr=stderr)
                    ),
                    self.assertRaises(PaseoBridgeFailure) as raised,
                ):
                    bridge_call(config, "catalog", {})
                self.assertEqual(raised.exception.code, "paseo_bridge_invalid_reply")
                message = str(raised.exception)
                self.assertTrue(message.endswith("Its standard error says: " + cause), message)
                self.assertNotIn("Node.js v", message)
                self.assertNotIn("node:internal/timers", message)

    def test_a_call_that_does_not_end_is_stopped_and_reported_as_a_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = started_runtime(root)
            stuck = root / "stuck-node"
            stuck.write_text(f"#!/bin/sh\necho $$ > {root}/pid\nexec sleep 30\n", encoding="utf-8")
            stuck.chmod(0o755)
            started = time.monotonic()
            with (
                patch.object(paseo_bridge.shutil, "which", return_value=stuck.as_posix()),
                patch.object(paseo_bridge, "PASEO_BRIDGE_TIMEOUT_SECONDS", 0.5),
                self.assertRaises(PaseoBridgeFailure) as raised,
            ):
                bridge_call(config, "catalog", {})
            self.assertEqual(raised.exception.code, "paseo_bridge_timeout")
            self.assertLess(time.monotonic() - started, 10)
            pid = int((root / "pid").read_text(encoding="utf-8"))
            self.assertFalse(Path(f"/proc/{pid}").exists(), "the stuck bridge process survived")

    def test_host_calls_without_a_paseo_command_refuse_with_a_named_reason(self) -> None:
        config = runtime_config(Path("/trusted"), None)
        for command in ("workspaces", "launch-replay", "agent-history", "restart-continue"):
            with self.subTest(command), self.assertRaises(OrcaRuntimeFailure) as raised:
                runtime_call(config, command, {})
            self.assertEqual(raised.exception.code, HOST_CALL_NOT_AVAILABLE)
            self.assertIn(repr(command), str(raised.exception))
            self.assertIn("PNT-R03", str(raised.exception))


FAKE_DAEMON_CLIENT = """
import { readFileSync } from 'node:fs'
export const scenario = JSON.parse(readFileSync(process.env.FAKE_PASEO_SCENARIO, 'utf8'))
export class DaemonClient {
  constructor(config) { this.config = config; this.state = { status: 'idle' } }
  async connect() {
    // Like the real client: log lines go to the given logger and, without one, to standard output.
    const logger = this.config.logger ?? { info: (_fields, line) => process.stdout.write(line + '\\n') }
    logger.info({ url: this.config.url }, 'connecting')
    console.log('a line a package prints through the console')
    if (scenario.connect === 'crash') setTimeout(() => { throw new TypeError(scenario.crash) }, 0)
    if (scenario.connect) await new Promise(() => {})
    if (this.config.reconnect?.enabled !== false) throw new Error('the bridge must not reconnect')
    if (scenario.connectError) throw new Error(scenario.connectError)
    if (this.config.url !== scenario.url) throw new Error('unexpected url ' + this.config.url)
    this.state = { status: 'connected' }
  }
  async close() { this.state = { status: 'disposed' } }
  getConnectionState() { return scenario.connectionLost ? { status: 'disconnected' } : this.state }
  getLastServerInfoMessage() { return { serverId: scenario.serverId, version: this.config.appVersion } }
}
"""
FAKE_CLIENT_ROOT = """
import { scenario } from './daemon-client.js'
const never = () => new Promise(() => {})
export function createPaseoApi(daemon) {
  let refreshed = false
  return {
    dispose: async () => {},
    providers: {
      refresh: async () => {
        if (scenario.refresh === 'hang') await never()
        refreshed = true
        return { acknowledged: true }
      },
      snapshot: async () => ({ entries: refreshed ? scenario.refreshedEntries : scenario.entries }),
      // Like the real client: no answer while a provider is still loading.
      waitForReady: async () => {
        const entries = refreshed ? scenario.refreshedEntries : scenario.entries
        if (entries.some((entry) => entry.status === 'loading')) await never()
        return { entries }
      },
      listModels: async (provider) => {
        const listing = scenario.models[provider]
        if (listing === 'hang') await never()
        if (listing === 'throw') throw new Error('listing exploded')
        return listing
      }
    }
  }
}
"""


def entry(provider: str, status: str = "ready", enabled: bool = True) -> dict[str, Any]:
    return {"provider": provider, "label": provider.title(), "status": status, "enabled": enabled}


@unittest.skipUnless(shutil.which("node"), "the bridge script needs Node.js")
class BridgeScriptTests(unittest.TestCase):
    """The real script against a fake client package installed in a scratch prefix."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = started_runtime(self.root)
        package = self.root / "prefix" / "node_modules" / "@getpaseo" / "client"
        (package / "dist").mkdir(parents=True)
        exports = {
            ".": {"default": "./dist/index.js"},
            "./internal/daemon-client": {"default": "./dist/daemon-client.js"},
        }
        (package / "package.json").write_text(
            json.dumps({"name": "@getpaseo/client", "type": "module", "exports": exports}),
            encoding="utf-8",
        )
        (package / "dist" / "daemon-client.js").write_text(FAKE_DAEMON_CLIENT, encoding="utf-8")
        (package / "dist" / "index.js").write_text(FAKE_CLIENT_ROOT, encoding="utf-8")

    def call(self, command: str, payload: dict[str, Any], **scenario: Any) -> dict[str, Any]:
        scenario_path = self.root / "scenario.json"
        base = {"url": "ws://127.0.0.1:6833/ws", "serverId": SERVER_ID, "models": {}}
        scenario_path.write_text(json.dumps({**base, **scenario}), encoding="utf-8")
        with (
            patch.dict(os.environ, {"FAKE_PASEO_SCENARIO": scenario_path.as_posix()}),
            patch.object(paseo_bridge, "_SCRIPT_DEADLINE_MS", 1500),
        ):
            return bridge_call(self.config, command, payload)

    def refusal(self, command: str = "catalog", **scenario: Any) -> str:
        with self.assertRaises(PaseoBridgeFailure) as raised:
            self.call(command, {}, **scenario)
        return raised.exception.code

    def test_catalog_lists_ready_enabled_providers_with_models_and_thinking_options(self) -> None:
        thinking = [{"id": "low", "label": "Low"}, {"id": "high", "isDefault": True}]
        models = {
            "codex": {
                "models": [
                    {
                        "id": "gpt-a",
                        "label": "GPT A",
                        "description": "first",
                        "isDefault": True,
                        "thinkingOptions": thinking,
                        "defaultThinkingOptionId": "high",
                        "metadata": {"internal": 1},
                    },
                    {"id": "gpt-b"},
                    {"id": "gpt-c", "thinkingOptions": [{"id": "low"}, {"id": "high"}]},
                ]
            },
            "eve": {"models": []},
            "pi": {"error": "auth missing"},
            "hermes": "throw",
            "claude": "hang",
            "late": {"models": [{"id": "late-1", "label": "Late 1"}]},
        }
        entries = [
            entry("codex"),
            entry("eve"),
            entry("pi"),
            entry("hermes"),
            entry("claude"),
            entry("omp", "ready", enabled=False),
            entry("copilot", "unavailable"),
            entry("muse", "error"),
            entry("slow", "loading"),
            entry("off", "loading", enabled=False),
        ]
        reply = self.call(
            "catalog", {}, entries=entries, refreshedEntries=[entry("late")], models=models
        )

        self.assertEqual(reply["runtime"], {"serverId": SERVER_ID, "version": "0.11.0-beta.2"})
        providers = {row["id"]: row for row in reply["providers"]}
        self.assertEqual(list(providers), ["codex", "eve", "pi", "hermes", "claude", "slow"])
        self.assertEqual(
            providers["slow"],
            {
                "id": "slow",
                "label": "Slow",
                "models": [],
                "listingError": (
                    "the runtime was still listing the models of this provider when the call ended"
                ),
            },
        )
        self.assertEqual(
            providers["codex"],
            {
                "id": "codex",
                "label": "Codex",
                "models": [
                    {
                        "id": "gpt-a",
                        "label": "GPT A",
                        "description": "first",
                        "isDefault": True,
                        "efforts": [{"id": "low", "label": "Low"}, {"id": "high", "label": "high"}],
                        "defaultEffort": "high",
                    },
                    {"id": "gpt-b", "label": "gpt-b", "efforts": []},
                    {
                        "id": "gpt-c",
                        "label": "gpt-c",
                        "efforts": [{"id": "low", "label": "low"}, {"id": "high", "label": "high"}],
                    },
                ],
            },
        )
        self.assertEqual(providers["eve"], {"id": "eve", "label": "Eve", "models": []})
        self.assertEqual(
            {name: providers[name].get("listingError") for name in ("pi", "hermes", "claude")},
            {
                "pi": "auth missing",
                "hermes": "listing exploded",
                "claude": "the runtime did not list the models in time",
            },
        )
        self.assertTrue(all(providers[name]["models"] == [] for name in ("pi", "hermes", "claude")))

        refreshed = self.call(
            "catalog",
            {"refresh": True},
            entries=entries,
            refreshedEntries=[entry("late")],
            models=models,
        )
        self.assertEqual([row["id"] for row in refreshed["providers"]], ["late"])

    def test_script_failures_are_named_and_another_daemon_is_refused(self) -> None:
        healthy: dict[str, Any] = {"entries": [entry("codex")], "models": {"codex": {"models": []}}}
        nothing_ready = [
            entry("codex", "loading"),
            entry("omp", "ready", enabled=False),
            entry("off", "loading", enabled=False),
        ]
        cases: dict[str, tuple[str, str, dict[str, Any]]] = {
            "another home's daemon": (
                "paseo_runtime_mismatch",
                "catalog",
                {**healthy, "serverId": "srv_other_home"},
            ),
            "a daemon without a server id": (
                "paseo_runtime_mismatch",
                "catalog",
                {**healthy, "serverId": None},
            ),
            "connection refused": (
                "paseo_daemon_unreachable",
                "catalog",
                {**healthy, "connectError": "ECONNREFUSED"},
            ),
            "connecting never answers": (
                "paseo_bridge_timeout",
                "catalog",
                {**healthy, "connect": "hang"},
            ),
            "no provider ready when the discovery time is up": (
                "paseo_bridge_timeout",
                "catalog",
                {**healthy, "entries": nothing_ready},
            ),
            "unknown command": ("unsupported_bridge_command", "workspaces", healthy),
        }
        for label, (code, command, scenario) in cases.items():
            with self.subTest(label):
                started = time.monotonic()
                self.assertEqual(self.refusal(command, **scenario), code)
                # The script's own budget (1.5 s here) ends the call, not the 60-second stop.
                self.assertLess(time.monotonic() - started, 5)
        with self.subTest("a refresh the runtime never answers"):
            started = time.monotonic()
            with self.assertRaises(PaseoBridgeFailure) as raised:
                self.call("catalog", {"refresh": True}, **healthy, refresh="hang")
            self.assertEqual(raised.exception.code, "paseo_bridge_timeout")
            self.assertLess(time.monotonic() - started, 5)
        with self.subTest("a crash of the script carries the error Node reports"):
            crash = "the daemon answered with a frame the client cannot read"
            with self.assertRaises(PaseoBridgeFailure) as raised:
                self.call("catalog", {}, **healthy, connect="crash", crash=crash)
            self.assertEqual(raised.exception.code, "paseo_bridge_invalid_reply")
            self.assertTrue(str(raised.exception).endswith("says: TypeError: " + crash))
        with self.subTest("a payload that never arrives is inside the deadline"):
            settings = self.config.paseo_runtime
            assert settings is not None
            environment = {
                **paseo_bridge._bridge_environment(settings),
                "AR_PASEO_DEADLINE_MS": "300",
            }
            with subprocess.Popen(
                ["node", BRIDGE_SCRIPT.as_posix(), "catalog"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                env=environment,
            ) as script:
                try:
                    status = script.wait(5)
                except subprocess.TimeoutExpired:
                    script.kill()
                    self.fail("the script waited for its payload past its deadline")
                assert script.stdout is not None
                reply = json.loads(script.stdout.read())
            self.assertEqual((status, reply["error"]["code"]), (1, "paseo_bridge_timeout"))
        with self.subTest("connection lost while listing"):
            self.assertEqual(
                self.refusal(**healthy, connectionLost=True), "paseo_daemon_unreachable"
            )
        with self.subTest("client package resolved outside the install prefix"):
            modules = self.root / "prefix" / "node_modules"
            modules.rename(self.root / "another-install")
            modules.symlink_to(self.root / "another-install", target_is_directory=True)
            self.assertEqual(self.refusal(**healthy), "paseo_client_unavailable")
            modules.unlink()
            (self.root / "another-install").rename(modules)
            self.assertEqual(self.call("catalog", {}, **healthy)["providers"][0]["id"], "codex")
        with self.subTest("client package missing from the prefix"):
            shutil.rmtree(self.root / "prefix" / "node_modules")
            self.assertEqual(self.refusal(**healthy), "paseo_client_unavailable")


class BridgeScriptHeaderTests(unittest.TestCase):
    """The header of the script is its contract; the single-path scan is test_paseo_single_path.py."""

    def test_the_script_lists_every_command_and_non_public_entry_point_it_uses(self) -> None:
        script = BRIDGE_SCRIPT.read_text(encoding="utf-8")
        header, code = script.split("\nimport ", 1)
        table = re.search(r"const COMMANDS = \{(.*?)\n\}", code, re.DOTALL)
        assert table is not None
        commands = set(re.findall(r"^\s*'?([\w-]+)'?\s*:", table.group(1), re.MULTILINE))
        documented = set(re.findall(r"^//   ([a-z][\w-]*)\s+\{", header, re.MULTILINE))
        self.assertEqual(commands, documented)
        self.assertIn("catalog", commands)

        code_lines = [line for line in code.splitlines() if not line.lstrip().startswith("//")]
        imported = set(re.findall(r"'(@getpaseo/[^']+)'", "\n".join(code_lines)))
        non_public = set(re.findall(r"^//   (@getpaseo/\S+)", header, re.MULTILINE))
        self.assertEqual(imported - {"@getpaseo/client"}, non_public)
        self.assertTrue(all("/internal/" in specifier for specifier in non_public))


if __name__ == "__main__":
    unittest.main()
