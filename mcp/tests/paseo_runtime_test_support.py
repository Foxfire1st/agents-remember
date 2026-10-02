"""A fake npm and Paseo command line for the Paseo runtime tests.

The fake keeps the daemon configuration in the home's real ``config.json`` and models what a
running daemon actually holds: a start-only setting takes effect only at a start, a live setting
at a start or a reload, and a plugin's files only when it is loaded. A test can therefore tell a
converged runtime from files that merely look right.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agents_remember.cli.paseo_command import CommandResult
from agents_remember.cli.paseo_plugin_files import PLUGIN_ID, embed_path, tree_digest
from agents_remember.cli.paseo_process_record import (
    PROCESS_RECORD,
    SUPERVISOR_TITLE,
    ProcessFacts,
    ProcessUnreadable,
)
from agents_remember.cli.paseo_provision import DaemonSetting
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeSettings,
    parse_paseo_runtime_settings,
)

PINNED = "0.11.0-beta.2"
# Stands for a harness key in a provider entry: it must reach the daemon's file and nothing else.
SECRET = "pnt-provider-value-7f3a"
OTHER_SECRET = "pnt-provider-value-91c2"
SUPERVISOR_PID = 4242
START_ONLY = ("daemon.listen", "features.webUi", "features.dictation", "features.voiceMode")
LIVE = ("daemon.relay.enabled", "pluginsEnabled", "agents.providers")
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


def provider_entries(value: str = SECRET) -> dict[str, Any]:
    return {"hermes": {"extends": "acp", "command": ["hermes", "acp"], "env": {"KEY": value}}}


def runtime_settings(root: Path, **overrides: Any) -> PaseoRuntimeSettings:
    block: dict[str, Any] = {
        "installPrefix": (root / "prefix").as_posix(),
        "home": (root / "home").as_posix(),
        "listen": "127.0.0.1:6831",
        "version": PINNED,
        "providers": provider_entries(),
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


def file_states(*roots: Path) -> dict[str, tuple[int, int, str]]:
    """Inode, modification time and content digest of every file below the roots."""
    return {
        path.as_posix(): (
            path.stat().st_ino,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for root in roots
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def free(_host: str, _port: int) -> OSError | None:
    return None


def ok(payload: Any) -> CommandResult:
    return CommandResult(0, payload if isinstance(payload, str) else json.dumps(payload), "")


def refused(code: str, message: str) -> CommandResult:
    return CommandResult(1, "", json.dumps({"error": {"code": code, "message": message}}))


class FakePaseo:
    """npm and the Paseo CLI of one home; see the module docstring for what it models."""

    def __init__(self, settings: PaseoRuntimeSettings) -> None:
        self.settings = settings
        self.calls: list[list[str]] = []
        self.daemon: dict[str, Any] | None = None
        # The process table: what each live process id is. ``None`` stands for a process of
        # another user, whose command line and environment cannot be read.
        self.processes: dict[int, ProcessFacts | None] = {}
        self.signalled: list[int] = []
        self.plugins: dict[str, dict[str, Any]] = {}
        self.events: list[tuple[str, str | None]] = []
        self.answers: dict[tuple[str, str], CommandResult] = {}
        # Something else writes the daemon file while one Paseo call runs (once per entry).
        self.during: dict[tuple[str, str], Callable[[], None]] = {}
        self.plugin_error: str | None = None
        self.plugins_unreachable = False
        self.npm_fails = False
        self.npm_installs: str | None = None
        self.port_held = False
        # A failed start whose supervisor did not exit by itself.
        self.lingering_supervisor = False
        self.speech_downloaded = False
        self.interrupt_at: int | None = None

    # -- the runner -------------------------------------------------------------------------
    def __call__(self, argv: Sequence[str], _timeout_seconds: float) -> CommandResult:
        if self.interrupt_at is not None and len(self.calls) >= self.interrupt_at:
            raise Interrupted
        argv = list(argv)
        self.calls.append(argv)
        # A provider value on a command line is readable in the process list.
        assert not any(secret in word for word in argv for secret in (SECRET, OTHER_SECRET)), argv
        if argv[0] == "npm":
            return self._npm_install(Path(argv[argv.index("--prefix") + 1]), argv[-1])
        version = self.version_at(Path(argv[0]).parents[2])
        if version is None:
            return CommandResult(127, "", "No such file or directory")
        if argv[1:] == ["--version"]:
            return ok(version + "\n")
        assert argv[-2:] == ["--home", self.settings.home.as_posix()], argv
        assert "--host" not in argv, argv
        kind = (argv[1], argv[2])
        self.during.pop(kind, lambda: None)()
        invalid = self.invalid_config()
        if invalid and argv[1] != "plugin" and kind != ("daemon", "stop"):
            return refused("UNKNOWN_ERROR", invalid)
        if kind in self.answers:
            return self.answers[kind]
        handler = getattr(self, "_" + "_".join(kind).replace("-", "_"))
        return handler(argv[3:-2])

    def reader(self, pid: int) -> ProcessFacts | None:
        """What ``/proc`` says about a process id of the fake's process table."""
        if pid not in self.processes:
            return None
        facts = self.processes[pid]
        if facts is None:
            raise ProcessUnreadable("Permission denied")
        return facts

    @property
    def record_file(self) -> Path:
        return self.settings.home / PROCESS_RECORD

    def record(self, pid: int, *alive_as: ProcessFacts | None) -> None:
        """Make the home's process record name ``pid``: a live process when facts are given."""
        self.settings.home.mkdir(parents=True, exist_ok=True)
        listen = self.configured("daemon.listen")
        self.record_file.write_text(json.dumps({"pid": pid, "listen": listen}), encoding="utf-8")
        if alive_as:
            self.processes[pid] = alive_as[0]

    def supervisor(self, home: Path | None = None) -> ProcessFacts:
        return ProcessFacts(SUPERVISOR_TITLE, (home or self.settings.home).as_posix())

    def recorded_pid(self) -> int | None:
        """The live process Paseo would act on: any live id the record names."""
        if not self.record_file.is_file():
            return None
        pid = json.loads(self.record_file.read_text(encoding="utf-8"))["pid"]
        return pid if pid in self.processes else None

    def kill_daemon(self) -> None:
        """The daemon is gone and took its record with it."""
        self.daemon = None
        self.processes.pop(SUPERVISOR_PID, None)
        self.record_file.unlink(missing_ok=True)

    def kinds(self, start: int = 0) -> list[tuple[str, str]]:
        """(group, verb) of every call from ``start``: ``("plugin", "reload")`` and so on."""
        found = []
        for argv in self.calls[start:]:
            words = argv if argv[0] == "npm" else argv[1:]
            found.append(("config", words[2]) if words[:2] == ["daemon", "config"] else words[:2])
        return [(kind[0], kind[1]) for kind in found if len(kind) == 2]

    def mutations(self, start: int = 0) -> list[tuple[str, str]]:
        return [kind for kind in self.kinds(start) if kind in MUTATING]

    # -- the install prefix -----------------------------------------------------------------
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

    # -- the daemon configuration file ------------------------------------------------------
    @property
    def config_file(self) -> Path:
        return self.settings.home / "config.json"

    @property
    def config(self) -> dict[str, Any]:
        if not self.config_file.is_file():
            return {}
        return json.loads(self.config_file.read_text(encoding="utf-8"))

    def save(self, config: dict[str, Any]) -> None:
        self.settings.home.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.config_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(config, indent=2, ensure_ascii=False) + "\n")

    def write(self, path: str, value: Any) -> None:
        config = self.config
        node = config
        for key in path.split(".")[:-1]:
            node = node.setdefault(key, {})
        node[path.rsplit(".", maxsplit=1)[-1]] = value
        self.save(config)

    def write_settings(self, settings: Sequence[DaemonSetting]) -> None:
        for setting in settings:
            self.write(setting.path, setting.value)

    def configured(self, path: str) -> Any:
        value: Any = self.config
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        return value

    def invalid_config(self) -> str | None:
        """Paseo's schema check: here, a provider entry that extends an unknown provider."""
        providers = self.configured("agents.providers") or {}
        for provider_id, entry in providers.items():
            if entry.get("extends") == "nope":
                return f"[Config] Invalid config in {self.config_file}:\n  - agents.providers.{provider_id}.extends"
        return None

    # -- the daemon --------------------------------------------------------------------------
    def start(self) -> None:
        self.settings.home.mkdir(parents=True, exist_ok=True)
        if self.configured("features.dictation.enabled") is not False:
            self.speech_downloaded = True
        if self.configured("features.voiceMode.enabled") is not False:
            self.speech_downloaded = True
        self.daemon = {
            "version": self.version_at(self.settings.install_prefix),
            "started_with": copy.deepcopy({key: self.configured(key) for key in START_ONLY}),
            "live": copy.deepcopy({key: self.configured(key) for key in LIVE}),
        }
        self.record(SUPERVISOR_PID, self.supervisor())
        for entry in self.plugins.values():
            self._load(entry)

    def running(self) -> dict[str, Any]:
        assert self.daemon is not None
        return self.daemon

    def converged(self) -> dict[str, Any]:
        """What the daemon runs with, and whether anything was left behind."""
        entry = self.plugins.get(PLUGIN_ID, {})
        home = self.settings.home
        return {
            "installed": self.version_at(self.settings.install_prefix),
            "daemon": self.running(),
            "plugin": {key: entry.get(key) for key in ("path", "status", "loaded")},
            "leftovers": sorted(path.name for path in self.settings.install_prefix.glob(".ar-*"))
            + sorted(path.name for path in home.rglob("*previous*"))
            + sorted(path.name for path in home.rglob("*.sha256"))
            + sorted(path.name for path in home.rglob(".config*.tmp")),
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

    def _apply_live(self) -> list[str]:
        live = self.running()["live"]
        applied = [key for key in LIVE if live[key] != self.configured(key)]
        live.update(copy.deepcopy({key: self.configured(key) for key in applied}))
        return applied

    def _restart_required(self) -> list[str]:
        started_with = self.running()["started_with"]
        # Paseo also lists the lifecycle-installed plugin entry here on every reload.
        return [key for key in START_ONLY if started_with[key] != self.configured(key)] + [
            "plugins.ar-plugin.path"
        ]

    # -- commands ---------------------------------------------------------------------------
    def _npm_install(self, root: Path, spec: str) -> CommandResult:
        if self.npm_fails:
            (root / "node_modules").mkdir(parents=True, exist_ok=True)
            return CommandResult(1, "", "npm error network request failed")
        self.install(root, self.npm_installs or spec.rsplit("@", 1)[1])
        return ok("added 297 packages")

    def _daemon_status(self, _args: list[str]) -> CommandResult:
        pid = self.recorded_pid()
        if pid is None:
            return ok({"localDaemon": "stopped", "listen": None, "pid": None})
        if self.daemon is None:
            # Paseo takes any live process the record names for the daemon.
            return ok({"localDaemon": "running", "pid": pid, "connectedDaemon": "unreachable"})
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
        self.write(args[1], json.loads(args[2]))
        if self.daemon is None:
            return ok({"action": "saved", "applied": False})
        return ok(
            {
                "action": "saved",
                "appliedPaths": self._apply_live(),
                "restartRequiredPaths": [
                    path
                    for path in [args[1], "plugins.ar-plugin.path"]
                    if path == "plugins.ar-plugin.path" or path.startswith(START_ONLY)
                ],
            }
        )

    def _daemon_reload(self, _args: list[str]) -> CommandResult:
        if self.daemon is None:
            return refused("DAEMON_NOT_RUNNING", "Daemon is not running")
        return ok(
            {"appliedPaths": self._apply_live(), "restartRequiredPaths": self._restart_required()}
        )

    def _daemon_start(self, _args: list[str]) -> CommandResult:
        if self.recorded_pid() is not None:
            return ok({"action": "already_running", "pid": self.recorded_pid()})
        if self.port_held:
            if self.lingering_supervisor:
                self.record(SUPERVISOR_PID, self.supervisor())
            return refused("DAEMON_START_FAILED", "listen EADDRINUSE: address already in use")
        self.start()
        self.events.append(("start", self.version_at(self.settings.install_prefix)))
        return ok({"action": "started"})

    def _daemon_stop(self, _args: list[str]) -> CommandResult:
        # As Paseo does: SIGTERM to whatever live process the record names, then drop the record.
        pid = self.recorded_pid()
        self.record_file.unlink(missing_ok=True)
        if pid is None:
            return ok({"action": "not_running", "pid": None})
        self.signalled.append(pid)
        del self.processes[pid]
        if pid == SUPERVISOR_PID:
            self.daemon = None
            self.events.append(("stop", self.version_at(self.settings.install_prefix)))
        return ok({"action": "stopped", "pid": pid})

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
