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

# The only files that may start a Paseo program or import a Paseo package (PNT-R02 item 1).
BRIDGE_FILES = {
    "mcp/src/agents_remember/cli/paseo_bridge.py",
    "mcp/src/agents_remember/cli/paseo_bridge.mjs",
}
RUNTIME_COMMAND_FILES = {
    "mcp/src/agents_remember/cli/paseo_command.py",
    "mcp/src/agents_remember/cli/paseo_daemon.py",
    "mcp/src/agents_remember/cli/paseo_plugin_files.py",
    "mcp/src/agents_remember/cli/paseo_provision.py",
    "mcp/src/agents_remember/cli/paseo_runtime.py",
}
PLUGIN_DIRECTORY = "mcp/src/agents_remember/package_data/paseo_plugin/"
CODE_SUFFIXES = {".py", ".mjs", ".cjs", ".js", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".sh"}
MANIFEST_NAMES = {"package.json", "pyproject.toml", "requirements.txt"}
TEST_PATHS = re.compile(r"(^|/)(tests|e2e|__tests__)/|\.test\.[cm]?[jt]sx?$|(^|/)test_[^/]+\.py$")

PASEO_PACKAGE = re.compile(r"@getpaseo/")
PASEO_PROGRAM = re.compile(
    r"\.bin[/\\\"', ]+paseo\b"
    r"|which\(\s*[\"']paseo[\"']"
    r"|\[\s*f?[\"']paseo[\"']\s*,"
    r"|\b(?:run|Popen|call|check_call|check_output|system|popen)\(\s*f?[\"']paseo\b"
    r"|\b(?:spawn|spawnSync|exec|execSync|execFile|execFileSync|fork)\(\s*[\"'`]paseo\b"
    r"|\b(?:npx|bunx|npm exec|pnpm dlx)\s+(?:-\S+\s+)*(?:@getpaseo/\S+|paseo)\b"
)
BRIDGE_SCRIPT_NAME = re.compile(r"paseo_bridge\.mjs")
COMMAND_LINE_IMPORT = re.compile(
    r"from\s+agents_remember\.cli\.paseo_command\s+import\s+(\([^)]*\)|[^\n]+)"
    r"|(import\s+agents_remember\.cli\.paseo_command\b)"
    r"|(from\s+agents_remember\.cli\s+import\s+[^\n]*\bpaseo_command\b)"
)
COMMAND_LINE_STARTERS = {"PaseoCli", "run_command"}


def second_paths_to_paseo(path: str, text: str) -> list[str]:
    """Why a source file outside the bridge and the runtime commands reaches Paseo itself."""

    found = []
    if PASEO_PACKAGE.search(text):
        found.append("names a Paseo package")
    if Path(path).name in MANIFEST_NAMES:
        return found
    if PASEO_PROGRAM.search(text):
        found.append("starts the Paseo command line")
    if BRIDGE_SCRIPT_NAME.search(text):
        found.append("names the bridge script")
    for match in COMMAND_LINE_IMPORT.finditer(text):
        names = set(re.findall(r"\w+", match.group(1) or ""))
        if match.group(2) or match.group(3) or names & COMMAND_LINE_STARTERS:
            found.append("imports the Paseo command-line runner")
    return found


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
        def reply(returncode: int, stdout: str) -> SimpleNamespace:
            return SimpleNamespace(returncode=returncode, stdout=stdout)

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
      refresh: async () => { refreshed = true; return { acknowledged: true } },
      waitForReady: async () => {
        if (scenario.discovery === 'hang') await never()
        return { entries: refreshed ? scenario.refreshedEntries : scenario.entries }
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
        ]
        reply = self.call(
            "catalog", {}, entries=entries, refreshedEntries=[entry("late")], models=models
        )

        self.assertEqual(reply["runtime"], {"serverId": SERVER_ID, "version": "0.11.0-beta.2"})
        providers = {row["id"]: row for row in reply["providers"]}
        self.assertEqual(list(providers), ["codex", "eve", "pi", "hermes", "claude"])
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
        cases: dict[str, tuple[str, dict[str, Any]]] = {
            "paseo_runtime_mismatch": ("catalog", {**healthy, "serverId": "srv_other_home"}),
            "paseo_daemon_unreachable": ("catalog", {**healthy, "connectError": "ECONNREFUSED"}),
            "paseo_bridge_timeout": ("catalog", {**healthy, "discovery": "hang"}),
            "unsupported_bridge_command": ("workspaces", healthy),
        }
        for code, (command, scenario) in cases.items():
            with self.subTest(code):
                self.assertEqual(self.refusal(command, **scenario), code)
        with self.subTest("connection lost while listing"):
            self.assertEqual(
                self.refusal(**healthy, connectionLost=True), "paseo_daemon_unreachable"
            )
        with self.subTest("client package missing from the prefix"):
            shutil.rmtree(self.root / "prefix" / "node_modules")
            self.assertEqual(self.refusal(**healthy), "paseo_client_unavailable")


class SinglePathTests(unittest.TestCase):
    def test_only_the_bridge_and_the_runtime_commands_reach_paseo(self) -> None:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\0")
        allowed = BRIDGE_FILES | RUNTIME_COMMAND_FILES
        sources = [
            path
            for path in listed
            if path
            and (Path(path).suffix in CODE_SUFFIXES or Path(path).name in MANIFEST_NAMES)
            and not TEST_PATHS.search(path)
            and path not in allowed
            and not path.startswith(PLUGIN_DIRECTORY)
            and (REPO_ROOT / path).is_file()
        ]
        self.assertGreater(
            len(sources), 500, "the source scan found too few files to mean anything"
        )
        self.assertTrue(allowed <= set(listed), "an allowed Paseo boundary file no longer exists")
        offenders = {
            path: reasons
            for path in sources
            if (
                reasons := second_paths_to_paseo(
                    path, (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
                )
            )
        }
        self.assertEqual(offenders, {})

    def test_the_scan_catches_each_kind_of_second_path(self) -> None:
        second_paths = {
            'import { createPaseoClient } from "@getpaseo/client"': "names a Paseo package",
            'await import(prefix + "/node_modules/@getpaseo/client/dist/index.js")': (
                "names a Paseo package"
            ),
            'subprocess.run([prefix / "node_modules/.bin/paseo", "provider", "models"])': (
                "starts the Paseo command line"
            ),
            'executable = prefix / "node_modules" / ".bin" / "paseo"': (
                "starts the Paseo command line"
            ),
            'subprocess.run(["paseo", "provider", "ls", "--json"])': (
                "starts the Paseo command line"
            ),
            'cli = shutil.which("paseo")': "starts the Paseo command line",
            'os.system("paseo daemon status")': "starts the Paseo command line",
            'spawn("paseo", ["run", prompt])': "starts the Paseo command line",
            "npx @getpaseo/cli provider ls": "names a Paseo package",
            'script = Path(__file__).with_name("paseo_bridge.mjs")': "names the bridge script",
            "from agents_remember.cli.paseo_command import PaseoCli": (
                "imports the Paseo command-line runner"
            ),
            "from agents_remember.cli.paseo_command import (\n    PaseoRuntimeFailure,\n"
            "    run_command,\n)": "imports the Paseo command-line runner",
            "from agents_remember.cli import paseo_command": (
                "imports the Paseo command-line runner"
            ),
        }
        for text, reason in second_paths.items():
            with self.subTest(text):
                self.assertIn(reason, second_paths_to_paseo("mcp/src/agents_remember/x.py", text))
        self.assertEqual(
            second_paths_to_paseo("dashboard/package.json", '"@getpaseo/client": "0.11.0-beta.2"'),
            ["names a Paseo package"],
        )
        allowed_uses = (
            "from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call",
            "from agents_remember.cli.paseo_command import PaseoRuntimeFailure",
            "from agents_remember.cli.paseo_provision import provision_runtime",
            'paseo = sub.add_parser(\n    "paseo",\n    help="Paseo runtime")',
            'argv = [sys.executable, "-m", "agents_remember.cli", "paseo", "provision"]',
            'receipt["execution"] = {"kind": "paseo-agent", "agentId": agent_id}',
            'reply = bridge_call(config, "catalog", {})',
        )
        for text in allowed_uses:
            with self.subTest(text):
                self.assertEqual(second_paths_to_paseo("mcp/src/agents_remember/x.py", text), [])

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
