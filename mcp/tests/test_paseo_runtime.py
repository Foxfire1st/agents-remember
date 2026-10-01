from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.cli.__main__ import main
from agents_remember.cli.paseo_command import CommandResult, run_command
from agents_remember.cli.paseo_daemon import runtime_status, stop_runtime
from agents_remember.cli.paseo_plugin_files import (
    PLUGIN_ID,
    embed_path,
    installed_plugin_path,
    plugin_source_root,
    tree_digest,
)
from agents_remember.cli.paseo_provision import daemon_settings, provision_runtime
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeSettings,
    parse_paseo_runtime_settings,
)

PINNED = "0.11.0-beta.2"
START_ONLY = ("daemon.listen", "features.webUi", "features.dictation", "features.voiceMode")
MUTATING = {
    ("npm", "install"),
    ("daemon", "start"),
    ("daemon", "stop"),
    ("daemon", "restart"),
    ("daemon", "reload"),
    ("config", "set"),
    ("plugin", "install"),
    ("plugin", "reload"),
    ("plugin", "enable"),
    ("plugin", "remove"),
}


class Interrupted(Exception):
    """The provisioning process died before this call."""


def runtime_settings(root: Path, **overrides: Any) -> PaseoRuntimeSettings:
    block: dict[str, Any] = {
        "installPrefix": (root / "prefix").as_posix(),
        "home": (root / "home").as_posix(),
        "listen": "127.0.0.1:6831",
        "version": PINNED,
        "providers": {"hermes": {"extends": "acp", "label": "Hermes", "command": ["hermes"]}},
        "embed": [
            {"dashboardOrigin": "http://127.0.0.1:9797", "frameBaseUrl": "http://127.0.0.1:6831"}
        ],
    }
    block.update(overrides)
    settings = parse_paseo_runtime_settings(block)
    assert settings is not None
    return settings


def write_plugin(root: Path, marker: str) -> Path:
    source = root / "plugin-source"
    (source / "server").mkdir(parents=True, exist_ok=True)
    (source / "paseo-plugin.json").write_text(json.dumps({"id": PLUGIN_ID}), encoding="utf-8")
    (source / "index.server.ts").write_text(f"// {marker}\n", encoding="utf-8")
    (source / "node_modules" / "left-out").mkdir(parents=True, exist_ok=True)
    return source


def ok(payload: Any) -> CommandResult:
    return CommandResult(0, payload if isinstance(payload, str) else json.dumps(payload), "")


def refused(code: str, message: str) -> CommandResult:
    return CommandResult(1, "", json.dumps({"error": {"code": code, "message": message}}))


class FakePaseo:
    """npm and the Paseo CLI of one home, with the daemon's started and loaded state modelled.

    A start-only setting takes effect only when the daemon starts and a plugin's files only when
    it is loaded, so a test can tell a converged runtime from a file that merely looks right.
    """

    def __init__(self, settings: PaseoRuntimeSettings) -> None:
        self.settings = settings
        self.calls: list[list[str]] = []
        self.config: dict[str, Any] = {}
        self.daemon: dict[str, Any] | None = None
        self.plugins: dict[str, dict[str, Any]] = {}
        self.events: list[tuple[str, str | None]] = []
        self.plugin_error: str | None = None
        self.plugins_unreachable = False
        self.npm_fails = False
        self.port_held = False
        self.speech_downloaded = False
        self.interrupt_at: int | None = None

    # -- the runner -------------------------------------------------------------------------
    def __call__(self, argv: Sequence[str], _timeout_seconds: float) -> CommandResult:
        if self.interrupt_at is not None and len(self.calls) >= self.interrupt_at:
            raise Interrupted
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "npm":
            return self._npm_install(Path(argv[argv.index("--prefix") + 1]), argv[-1])
        version = self.version_at(Path(argv[0]).parents[2])
        if version is None:
            return CommandResult(127, "", "No such file or directory")
        if argv[1:] == ["--version"]:
            return ok(version + "\n")
        assert argv[-2:] == ["--home", self.settings.home.as_posix()], argv
        assert "--host" not in argv, argv
        handler = getattr(self, "_" + "_".join(argv[1:3]).replace("-", "_"))
        return handler(argv[3:-2])

    def kinds(self, start: int = 0) -> list[tuple[str, str]]:
        """(group, verb) of every call from ``start``: ``("plugin", "reload")`` and so on."""
        found = []
        for argv in self.calls[start:]:
            words = argv if argv[0] == "npm" else argv[1:]
            found.append(("config", words[2]) if words[:2] == ["daemon", "config"] else words[:2])
        return [tuple(kind) for kind in found if len(kind) == 2]

    def mutations(self, start: int = 0) -> list[tuple[str, str]]:
        return [kind for kind in self.kinds(start) if kind in MUTATING]

    # -- state helpers ----------------------------------------------------------------------
    def version_at(self, root: Path) -> str | None:
        manifest = root / "node_modules" / "@getpaseo" / "cli" / "package.json"
        if not manifest.is_file():
            return None
        return json.loads(manifest.read_text(encoding="utf-8"))["version"]

    def install(self, root: Path, version: str) -> None:
        manifest = root / "node_modules" / "@getpaseo" / "cli" / "package.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"version": version}), encoding="utf-8")
        (root / "package.json").write_text(
            json.dumps({"dependencies": {"@getpaseo/cli": version}}), encoding="utf-8"
        )
        (root / "package-lock.json").write_text("{}", encoding="utf-8")

    def configured(self, path: str) -> Any:
        value: Any = self.config
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        return value

    def start(self) -> None:
        self.settings.home.mkdir(parents=True, exist_ok=True)
        if self.configured("features.dictation.enabled") is not False:
            self.speech_downloaded = True
        if self.configured("features.voiceMode.enabled") is not False:
            self.speech_downloaded = True
        self.daemon = {
            "version": self.version_at(self.settings.install_prefix),
            "started_with": copy.deepcopy({key: self.configured(key) for key in START_ONLY}),
        }
        for entry in self.plugins.values():
            self._load(entry)

    def running(self) -> dict[str, Any]:
        assert self.daemon is not None
        return self.daemon

    def converged(self) -> dict[str, Any]:
        """What the daemon runs with: started settings, live settings and the loaded plugin."""
        entry = self.plugins.get(PLUGIN_ID, {})
        return {
            "installed": self.version_at(self.settings.install_prefix),
            "daemon": self.running(),
            "live": {key: self.configured(key) for key in ("pluginsEnabled", "agents.providers")},
            "relay": self.configured("daemon.relay.enabled"),
            "plugin": {key: entry.get(key) for key in ("path", "status", "loaded")},
            "leftovers": sorted(path.name for path in self.settings.install_prefix.glob(".ar-*")),
        }

    def _load(self, entry: dict[str, Any]) -> None:
        embed = embed_path(self.settings.home)
        entry["loaded"] = [
            tree_digest(Path(entry["path"])),
            embed.read_text(encoding="utf-8") if embed.is_file() else None,
        ]
        entry["status"] = "failed" if self.plugin_error else "running"
        entry["error"] = self.plugin_error

    def _listed(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in entry.items() if key != "loaded"}

    # -- commands ---------------------------------------------------------------------------
    def _npm_install(self, root: Path, spec: str) -> CommandResult:
        if self.npm_fails:
            (root / "node_modules").mkdir(parents=True, exist_ok=True)
            return CommandResult(1, "", "npm error network request failed")
        self.install(root, spec.rsplit("@", 1)[1])
        return ok("added 297 packages")

    def _daemon_status(self, _args: list[str]) -> CommandResult:
        if self.daemon is None:
            return ok({"localDaemon": "stopped", "listen": None, "pid": None})
        return ok(
            {
                "localDaemon": "running",
                "listen": self.daemon["started_with"]["daemon.listen"],
                "daemonVersion": self.daemon["version"],
                "serverId": "srv_fake",
                "providers": [
                    {"provider": "claude", "available": True, "error": None, "label": "Claude"},
                    {"provider": "hermes", "available": False, "error": "not installed"},
                ],
            }
        )

    def _daemon_config(self, args: list[str]) -> CommandResult:
        if args[0] == "get":
            return ok({"source": "configured", "path": None, "set": True, "value": self.config})
        path, value = args[1], json.loads(args[2])
        if path == "agents.providers" and not all(isinstance(v, dict) for v in value.values()):
            return CommandResult(1, "", "Error: [Config] Invalid config to save: agents.providers")
        self.settings.home.mkdir(parents=True, exist_ok=True)
        node = self.config
        for key in path.split(".")[:-1]:
            node = node.setdefault(key, {})
        node[path.split(".")[-1]] = value
        if self.daemon is None:
            return ok({"action": "saved", "applied": False})
        start_only = path.startswith(START_ONLY)
        return ok(
            {
                "action": "saved",
                "appliedPaths": [] if start_only else [path],
                # Paseo also lists the lifecycle-installed plugin entry here on every reload.
                "restartRequiredPaths": [path, "plugins.ar-plugin.path"]
                if start_only
                else ["plugins.ar-plugin.path"],
            }
        )

    def _daemon_start(self, _args: list[str]) -> CommandResult:
        if self.port_held:
            return refused("DAEMON_START_FAILED", "listen EADDRINUSE: address already in use")
        self.start()
        self.events.append(("start", self.version_at(self.settings.install_prefix)))
        return ok({"action": "started"})

    def _daemon_stop(self, _args: list[str]) -> CommandResult:
        if self.daemon is None:
            return ok({"action": "not_running", "pid": None})
        self.daemon = None
        self.events.append(("stop", self.version_at(self.settings.install_prefix)))
        return ok({"action": "stopped", "pid": 4242})

    def _plugin_ls(self, _args: list[str]) -> CommandResult:
        if self.daemon is None or self.plugins_unreachable:
            return refused("DAEMON_NOT_RUNNING", "Daemon is not running")
        return ok([self._listed(entry) for entry in self.plugins.values()])

    def _plugin_install(self, args: list[str]) -> CommandResult:
        manifest = json.loads((Path(args[0]) / "paseo-plugin.json").read_text(encoding="utf-8"))
        if self.daemon is None or manifest["id"] in self.plugins:
            return refused("handler_error", "cannot install")
        entry = {"id": manifest["id"], "path": args[0], "enabled": True}
        self.plugins[manifest["id"]] = entry
        return self._loaded(entry)

    def _plugin_reload(self, args: list[str]) -> CommandResult:
        return self._loaded(self.plugins[args[0]])

    def _plugin_enable(self, args: list[str]) -> CommandResult:
        self.plugins[args[0]]["enabled"] = True
        return self._loaded(self.plugins[args[0]])

    def _plugin_remove(self, args: list[str]) -> CommandResult:
        return ok(self._listed(self.plugins.pop(args[0])))

    def _loaded(self, entry: dict[str, Any]) -> CommandResult:
        self._load(entry)
        if self.plugin_error:
            return refused("handler_error", f"Request failed: {self.plugin_error}")
        return ok(self._listed(entry))


def free(_host: str, _port: int) -> OSError | None:
    return None


class PaseoRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def provision(self, fake: FakePaseo, **overrides: Any) -> dict[str, Any]:
        settings = overrides.pop("settings", fake.settings)
        if "plugin_source" not in overrides:
            overrides["plugin_source"] = write_plugin(self.root, "one")
        return provision_runtime(settings, runner=fake, probe=free, **overrides)

    def test_fresh_provision_then_a_repeat_that_touches_nothing(self) -> None:
        settings = runtime_settings(self.root)
        fake = FakePaseo(settings)

        report = provision_runtime(settings, runner=fake, probe=free)

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["daemon"], {"action": "started", "reasons": []})
        self.assertEqual(fake.version_at(settings.install_prefix), PINNED)
        self.assertEqual(
            json.loads((settings.install_prefix / "package.json").read_text())["dependencies"],
            {"@getpaseo/cli": PINNED},
        )
        self.assertIn("--save-exact", fake.calls[1])
        self.assertEqual(fake.calls[1][-1], f"@getpaseo/cli@{PINNED}")
        # Every setting provision owns was written before the first start; nothing else was.
        written = {setting.path: setting.value for setting in daemon_settings(settings)}
        self.assertEqual(
            written,
            {
                "daemon.listen": "127.0.0.1:6831",
                "daemon.relay.enabled": False,
                "features.webUi.enabled": True,
                "features.dictation.enabled": False,
                "features.voiceMode.enabled": False,
                "pluginsEnabled": True,
                "agents.providers": settings.providers,
            },
        )
        self.assertEqual({path: fake.configured(path) for path in written}, written)
        kinds = fake.kinds()
        self.assertLess(
            max(i for i, kind in enumerate(kinds) if kind == ("config", "set")),
            kinds.index(("daemon", "start")),
        )
        self.assertFalse(fake.speech_downloaded)
        # The packaged plugin source is what got installed, from its copy inside the home.
        copy_root = installed_plugin_path(settings.home)
        self.assertEqual(tree_digest(copy_root), tree_digest(plugin_source_root()))
        self.assertEqual(json.loads((copy_root / "paseo-plugin.json").read_text())["id"], PLUGIN_ID)
        self.assertTrue((copy_root / "index.server.ts").is_file())
        self.assertTrue((copy_root / "index.client.tsx").is_file())
        self.assertEqual(fake.plugins[PLUGIN_ID]["path"], copy_root.as_posix())
        self.assertEqual(
            [(change["step"], change["action"]) for change in report["changes"]],
            [("install", "installed")]
            + [("config", "set")] * 7
            + [("embed", "written"), ("plugin", "copied"), ("daemon", "started")]
            + [("plugin", "installed")],
        )

        before = len(fake.calls)
        repeat = provision_runtime(settings, runner=fake, probe=free)

        self.assertTrue(repeat["ok"], repeat)
        self.assertFalse(repeat["changed"])
        self.assertEqual(repeat["changes"], [])
        self.assertEqual(repeat["daemon"], {"action": "untouched", "reasons": []})
        self.assertEqual(fake.mutations(before), [])
        self.assertEqual(fake.events, [("start", PINNED)])

    def test_other_version_is_replaced_and_a_failed_install_changes_nothing(self) -> None:
        settings = runtime_settings(self.root)
        fake = FakePaseo(settings)
        fake.install(settings.install_prefix, "0.10.2")
        for setting in daemon_settings(settings):
            fake(
                [
                    (settings.install_prefix / "node_modules/.bin/paseo").as_posix(),
                    *("daemon", "config", "set", setting.path, json.dumps(setting.value)),
                    *("--home", settings.home.as_posix()),
                ],
                1.0,
            )
        fake.start()

        fake.npm_fails = True
        before = len(fake.calls)
        failed = self.provision(fake)

        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"]["code"], "install_failed")
        self.assertEqual(failed["error"]["step"], "install")
        self.assertIn("network request failed", failed["error"]["detail"])
        self.assertEqual(failed["changes"], [])
        self.assertEqual(fake.version_at(settings.install_prefix), "0.10.2")
        self.assertEqual(fake.running()["version"], "0.10.2")
        self.assertEqual(fake.mutations(before), [("npm", "install")])
        self.assertEqual(list(settings.install_prefix.glob(".ar-*")), [])

        fake.npm_fails = False
        report = self.provision(fake)

        self.assertTrue(report["ok"], report)
        self.assertEqual(
            report["changes"][0],
            {"step": "daemon", "action": "stopped", "reasons": ["version"]},
        )
        self.assertEqual(
            report["changes"][1],
            {
                "step": "install",
                "action": "replaced",
                "previousVersion": "0.10.2",
                "version": PINNED,
            },
        )
        self.assertEqual(report["daemon"], {"action": "restarted", "reasons": ["version"]})
        # The old daemon was stopped while its own install was still in place.
        self.assertEqual(fake.events, [("stop", "0.10.2"), ("start", PINNED)])
        self.assertEqual(fake.running()["version"], PINNED)
        self.assertEqual(list(settings.install_prefix.glob(".ar-*")), [])

    def test_restart_reload_or_untouched_follows_what_changed(self) -> None:
        settings = runtime_settings(self.root)
        fake = FakePaseo(settings)
        self.assertTrue(self.provision(fake)["ok"])

        def rerun(**changed: Any) -> tuple[dict[str, Any], list[tuple[str, str]]]:
            nonlocal settings
            source = changed.pop("plugin_source", None) or write_plugin(self.root, "one")
            settings = runtime_settings(self.root, **changed)
            before = len(fake.calls)
            report = self.provision(fake, settings=settings, plugin_source=source)
            self.assertTrue(report["ok"], report)
            return report, fake.mutations(before)

        with self.subTest("a live setting is reloaded"):
            report, mutations = rerun(providers={})
            self.assertEqual(report["daemon"], {"action": "reloaded", "reasons": []})
            self.assertEqual(mutations, [("config", "set")])
            self.assertEqual(fake.configured("agents.providers"), {})

        with self.subTest("the embed list reloads the plugin"):
            embed = [{"dashboardOrigin": "http://localhost:9797", "frameBaseUrl": "http://h:1"}]
            report, mutations = rerun(providers={}, embed=embed)
            self.assertEqual(report["daemon"]["action"], "reloaded")
            self.assertEqual(mutations, [("plugin", "reload")])
            self.assertEqual(json.loads(fake.plugins[PLUGIN_ID]["loaded"][1])["embed"], embed)

        with self.subTest("changed plugin content is reinstalled and reloaded"):
            source = write_plugin(self.root, "two")
            report, mutations = rerun(providers={}, embed=embed, plugin_source=source)
            self.assertEqual(report["daemon"]["action"], "reloaded")
            self.assertEqual(mutations, [("plugin", "reload")])
            self.assertEqual(fake.plugins[PLUGIN_ID]["loaded"][0], tree_digest(source))
            self.assertFalse((installed_plugin_path(settings.home) / "node_modules").exists())

        with self.subTest("nothing changed"):
            report, mutations = rerun(providers={}, embed=embed, plugin_source=source)
            self.assertEqual(report["daemon"], {"action": "untouched", "reasons": []})
            self.assertEqual((report["changed"], mutations), (False, []))

        with self.subTest("a start-only setting restarts"):
            report, mutations = rerun(
                providers={}, embed=embed, plugin_source=source, listen="127.0.0.1:6832"
            )
            self.assertEqual(
                report["daemon"],
                {"action": "restarted", "reasons": ["setting:daemon.listen"]},
            )
            self.assertEqual(
                mutations, [("daemon", "stop"), ("config", "set"), ("daemon", "start")]
            )
            self.assertEqual(fake.running()["started_with"]["daemon.listen"], "127.0.0.1:6832")

    def test_held_listen_port_fails_naming_it_and_leaves_nothing_running(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            port = holder.getsockname()[1]
            settings = runtime_settings(self.root, listen=f"127.0.0.1:{port}")
            fake = FakePaseo(settings)

            report = provision_runtime(
                settings, runner=fake, plugin_source=write_plugin(self.root, "one")
            )

        self.assertFalse(report["ok"])
        self.assertEqual(report["error"]["code"], "listen_port_in_use")
        self.assertIn(f"listen port {port} ", report["error"]["message"])
        self.assertNotIn(("daemon", "start"), fake.kinds())
        self.assertEqual(fake.configured("daemon.listen"), None)
        self.assertEqual(report["daemon"]["action"], "untouched")

        # The address is taken between the check and the start: Paseo's own refusal is reported
        # the same way, and the configured port is kept.
        fake.port_held = True
        raced = provision_runtime(
            settings, runner=fake, plugin_source=write_plugin(self.root, "one"), probe=free
        )

        self.assertEqual(raced["error"]["code"], "listen_port_in_use")
        self.assertIn("EADDRINUSE", raced["error"]["detail"])
        self.assertEqual(fake.kinds()[-2:], [("daemon", "start"), ("daemon", "stop")])
        self.assertIsNone(fake.daemon)
        self.assertEqual(fake.configured("daemon.listen"), f"127.0.0.1:{port}")

    def test_plugin_load_failure_keeps_the_daemon_up_and_status_says_failed(self) -> None:
        settings = runtime_settings(self.root)
        fake = FakePaseo(settings)
        fake.plugin_error = 'Build failed with 1 error: index.server.ts:1:37: ERROR: Expected "{"'

        report = self.provision(fake)

        self.assertFalse(report["ok"])
        self.assertEqual(report["error"]["code"], "plugin_load_failed")
        self.assertEqual(report["error"]["detail"], fake.plugin_error)
        self.assertEqual(report["daemon"]["action"], "started")
        self.assertIsNotNone(fake.daemon)
        status = runtime_status(settings, runner=fake)
        self.assertTrue(status["running"])
        self.assertEqual(
            status["plugin"], {"id": PLUGIN_ID, "state": "failed", "error": fake.plugin_error}
        )

        # Once the cause is gone the next pass loads the plugin again without a restart.
        fake.plugin_error = None
        recovered = self.provision(fake)

        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(recovered["daemon"]["action"], "reloaded")
        self.assertEqual(runtime_status(settings, runner=fake)["plugin"]["state"], "running")

    def test_a_pass_interrupted_at_any_call_converges_on_the_next(self) -> None:
        target = {
            "providers": {},
            "embed": [{"dashboardOrigin": "http://localhost:9797", "frameBaseUrl": "http://h:1"}],
        }
        # What ran before the interrupted pass: nothing, another version, the pinned version on
        # another listen address, or the pinned runtime with only live facts about to change.
        earlier: dict[str, dict[str, Any] | None] = {
            "fresh": None,
            "other version": {"version": "0.10.2", "listen": "127.0.0.1:6830"},
            "start-only setting": {"listen": "127.0.0.1:6830"},
            "live facts only": {},
        }

        def world(name: str, before: dict[str, Any] | None) -> tuple[FakePaseo, Path]:
            root = self.root / name
            fake = FakePaseo(runtime_settings(root, **target))
            if before is not None:
                fake.install(fake.settings.install_prefix, before.get("version", PINNED))
                done = provision_runtime(
                    runtime_settings(root, **before),
                    runner=fake,
                    plugin_source=write_plugin(root, "one"),
                    probe=free,
                )
                assert done["ok"], done
            return fake, write_plugin(root, "two")

        def converged(fake: FakePaseo) -> dict[str, Any]:
            state = fake.converged()
            state["plugin"]["path"] = Path(state["plugin"]["path"]).name
            return state

        for label, before in earlier.items():
            fake, source = world(f"reference {label}", before)
            first = len(fake.calls)
            reference = provision_runtime(
                fake.settings, runner=fake, plugin_source=source, probe=free
            )
            self.assertTrue(reference["ok"], reference)
            expected = converged(fake)
            self.assertEqual(
                expected["daemon"],
                {
                    "version": PINNED,
                    "started_with": {
                        "daemon.listen": "127.0.0.1:6831",
                        "features.webUi": {"enabled": True},
                        "features.dictation": {"enabled": False},
                        "features.voiceMode": {"enabled": False},
                    },
                },
            )
            live = expected["live"]
            self.assertEqual((live["pluginsEnabled"], live["agents.providers"] or {}), (True, {}))
            self.assertEqual(expected["plugin"]["status"], "running")
            self.assertEqual(expected["plugin"]["loaded"][0], tree_digest(source))
            self.assertEqual(
                json.loads(expected["plugin"]["loaded"][1]), {"embed": target["embed"]}
            )
            self.assertEqual((expected["installed"], expected["leftovers"]), (PINNED, []))
            for cut in range(first, len(fake.calls)):
                with self.subTest(earlier=label, cut=cut - first):
                    fake, source = world(f"{label} cut {cut}", before)
                    fake.interrupt_at = cut
                    with self.assertRaises(Interrupted):
                        provision_runtime(
                            fake.settings, runner=fake, plugin_source=source, probe=free
                        )
                    fake.interrupt_at = None

                    resumed = provision_runtime(
                        fake.settings, runner=fake, plugin_source=source, probe=free
                    )

                    self.assertTrue(resumed["ok"], resumed)
                    self.assertEqual(converged(fake), expected)
                    self.assertFalse(fake.speech_downloaded)
                    again = provision_runtime(
                        fake.settings, runner=fake, plugin_source=source, probe=free
                    )
                    self.assertEqual((again["ok"], again["changed"]), (True, False))

    def test_status_and_stop_address_only_the_configured_home(self) -> None:
        settings = runtime_settings(self.root)
        fake = FakePaseo(settings)

        # Nothing installed and no process record: not running, and nothing else is invoked.
        absent = runtime_status(settings, runner=fake)
        self.assertEqual(
            absent,
            {
                "ok": True,
                "home": settings.home.as_posix(),
                "running": False,
                "version": None,
                "serverId": None,
                "listen": None,
                "plugin": None,
                "embed": None,
                "providers": None,
            },
        )
        self.assertEqual(stop_runtime(settings, runner=fake)["action"], "not running")
        self.assertEqual(len(fake.calls), 2)

        self.assertTrue(self.provision(fake)["ok"])
        status = runtime_status(settings, runner=fake)
        self.assertEqual(
            {key: status[key] for key in ("running", "version", "serverId", "listen")},
            {"running": True, "version": PINNED, "serverId": "srv_fake", "listen": settings.listen},
        )
        self.assertEqual(status["plugin"], {"id": PLUGIN_ID, "state": "running", "error": None})
        self.assertEqual(status["embed"], settings.embed_payload())
        self.assertEqual(
            status["providers"],
            [
                {"provider": "claude", "available": True, "error": None},
                {"provider": "hermes", "available": False, "error": "not installed"},
            ],
        )

        # A daemon that does not answer the plugin query is still reported as running.
        fake.plugins_unreachable = True
        self.assertEqual(
            runtime_status(settings, runner=fake)["plugin"],
            {"id": PLUGIN_ID, "state": "unknown", "error": "Daemon is not running"},
        )
        fake.plugins_unreachable = False

        before = len(fake.calls)
        stopped = stop_runtime(settings, runner=fake)
        again = stop_runtime(settings, runner=fake)

        self.assertEqual((stopped["action"], stopped["pid"]), ("stopped", 4242))
        self.assertEqual((again["action"], again["pid"]), ("not running", None))
        # The fake refuses any call that does not name the configured home or that names a host,
        # so a stop can only ever have reached this home's own process record.
        self.assertEqual(fake.kinds(before), [("daemon", "stop"), ("daemon", "stop")])
        self.assertFalse(runtime_status(settings, runner=fake)["running"])

    def test_commands_refuse_without_a_runtime_block_and_drop_paseo_environment(self) -> None:
        settings_path = self.root / "mcp-settings.json"
        settings_path.write_text(json.dumps({"version": 1}), encoding="utf-8")

        def command(name: str) -> tuple[int, dict[str, Any]]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["paseo", name, "--config", settings_path.as_posix()])
            return code, json.loads(output.getvalue())

        with patch("agents_remember.cli.paseo_command.subprocess.run") as run:
            for name in ("provision", "status", "stop"):
                with self.subTest(name):
                    code, document = command(name)
                    self.assertEqual(code, 2)
                    self.assertEqual(document["error"]["code"], "paseo_runtime_not_configured")
                    self.assertIn("no Paseo runtime configured", document["error"]["message"])
            block = {"installPrefix": (self.root / "prefix").as_posix()}
            settings_path.write_text(json.dumps({"paseoRuntime": block}), encoding="utf-8")
            code, document = command("provision")
            self.assertEqual((code, document["error"]["code"]), (2, "settings_invalid"))
            run.assert_not_called()

        # Through the real command boundary: a prefix that holds nothing is "not running".
        block = {
            "installPrefix": (self.root / "prefix").as_posix(),
            "home": (self.root / "home").as_posix(),
            "listen": "127.0.0.1:6831",
            "version": PINNED,
            "providers": {},
            "embed": [],
        }
        settings_path.write_text(json.dumps({"paseoRuntime": block}), encoding="utf-8")
        code, document = command("stop")
        self.assertEqual((code, document["action"]), (0, "not running"))
        code, document = command("status")
        self.assertEqual((code, document["running"], document["version"]), (0, False, None))
        self.assertFalse((self.root / "home").exists())

        names = ("PASEO_HOME", "PASEO_HOST", "PNT_KEPT")
        script = f"import os; print([name in os.environ for name in {names!r}])"
        with patch.dict(os.environ, dict.fromkeys(names, "inherited")):
            result = run_command([sys.executable, "-c", script], 30.0)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "[False, False, True]"))


if __name__ == "__main__":
    unittest.main()
