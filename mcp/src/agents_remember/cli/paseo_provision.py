"""Bring the configured Paseo runtime to its settings and report every change as data.

One repeatable pass: the install prefix holds exactly the pinned version, the daemon
configuration holds the settings this module writes, the AR plugin is installed from its copy in
the home, and the daemon runs. A pass that finds nothing to change makes only read-only calls.

Order is what makes an interrupted pass converge on the next one. A new version is staged beside
the old install and activated only after it reports the pinned version. A running daemon is
stopped before a start-only setting is written and started after it, so a daemon is never left
running on settings the file no longer holds. The plugin stamp is written after the plugin
loaded, so a copy that was replaced but never loaded is loaded by the next pass.
"""

from __future__ import annotations

import errno
import hashlib
import json
import shutil
import socket
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from agents_remember.cli.paseo_command import (
    PASEO_PACKAGE,
    CommandRunner,
    PaseoCli,
    PaseoRuntimeFailure,
    paseo_error_text,
    run_command,
)
from agents_remember.cli.paseo_daemon import is_running, plugin_entry
from agents_remember.cli.paseo_plugin_files import (
    PLUGIN_ID,
    installed_plugin_path,
    plugin_source_root,
    read_loaded_stamp,
    sync_plugin_copy,
    tree_digest,
    write_embed,
    write_loaded_stamp,
)
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings

INSTALL_TIMEOUT_SECONDS = 900.0
START_TIMEOUT_SECONDS = 120
STAGING_DIRECTORY = ".ar-staging"
PREVIOUS_DIRECTORY = ".ar-previous"
INSTALL_ENTRIES = ("node_modules", "package.json", "package-lock.json")

BindProbe = Callable[[str, int], OSError | None]


@dataclass(frozen=True)
class DaemonSetting:
    """One daemon configuration path provision writes, and when Paseo applies a change to it."""

    path: str
    value: Any
    applies: Literal["start", "live"]


def daemon_settings(settings: PaseoRuntimeSettings) -> tuple[DaemonSetting, ...]:
    """The complete list of daemon settings provision writes; every other key is left alone.

    ``start`` settings are read only when the daemon starts; ``live`` ones are applied by Paseo's
    configuration reload. The split follows Paseo's configuration reference and was confirmed by
    run on 0.11.0-beta.2 for every path except relay enablement, which is never switched on here.
    Dictation and voice mode are off before the first start, so no speech model is downloaded.
    """
    return (
        DaemonSetting("daemon.listen", settings.listen, "start"),
        DaemonSetting("daemon.relay.enabled", False, "live"),
        DaemonSetting("features.webUi.enabled", True, "start"),
        DaemonSetting("features.dictation.enabled", False, "start"),
        DaemonSetting("features.voiceMode.enabled", False, "start"),
        DaemonSetting("pluginsEnabled", True, "live"),
        DaemonSetting("agents.providers", settings.providers, "live"),
    )


def bind_error(host: str, port: int) -> OSError | None:
    """Try to bind the listen address the way the daemon will; return what refused it."""
    try:
        family, kind, protocol, _name, address = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )[0]
        with socket.socket(family, kind, protocol) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(address)
    except OSError as error:
        return error
    return None


@dataclass
class _Run:
    settings: PaseoRuntimeSettings
    cli: PaseoCli
    plugin_source: Path
    probe: BindProbe
    step: str = "install"
    changes: list[dict[str, Any]] = field(default_factory=list)
    restart_reasons: list[str] = field(default_factory=list)
    # Index of the first change applied to a daemon that this pass kept running.
    live_from: int | None = None

    def change(self, step: str, action: str, **facts: Any) -> None:
        self.changes.append({"step": step, "action": action, **facts})


def provision_runtime(
    settings: PaseoRuntimeSettings,
    *,
    runner: CommandRunner = run_command,
    plugin_source: Path | None = None,
    probe: BindProbe = bind_error,
) -> dict[str, Any]:
    """Move the runtime to the configured state; the report lists every change made."""
    run = _Run(settings, PaseoCli(settings, runner), plugin_source or plugin_source_root(), probe)
    failure: PaseoRuntimeFailure | None = None
    try:
        _ensure_install(run)
        _converge_daemon(run)
    except PaseoRuntimeFailure as error:
        failure = error
    except OSError as error:
        failure = PaseoRuntimeFailure(
            "filesystem_error", run.step, f"the {run.step} step could not write: {error}"
        )
    return {
        "ok": failure is None,
        "changed": bool(run.changes),
        "home": settings.home.as_posix(),
        "installPrefix": settings.install_prefix.as_posix(),
        "version": settings.version,
        "listen": settings.listen,
        "daemon": {"action": _daemon_action(run), "reasons": run.restart_reasons},
        "changes": run.changes,
        "error": None if failure is None else failure.as_payload(),
    }


def _daemon_action(run: _Run) -> str:
    """What this pass did to the daemon process: one word for the report."""
    actions = [change["action"] for change in run.changes if change["step"] == "daemon"]
    if "started" in actions:
        return "restarted" if run.restart_reasons else "started"
    if "stopped" in actions:
        return "stopped"
    reloaded = run.live_from is not None and len(run.changes) > run.live_from
    return "reloaded" if reloaded else "untouched"


def _ensure_install(run: _Run) -> None:
    """The prefix holds exactly the pinned version; a failed install leaves the old one intact."""
    prefix = run.settings.install_prefix
    staging = prefix / STAGING_DIRECTORY
    installed = run.cli.installed_version()
    if installed == run.settings.version:
        for leftover in (staging, prefix / PREVIOUS_DIRECTORY):
            if leftover.exists():
                shutil.rmtree(leftover)
                run.change("install", "removed-leftover", path=leftover.as_posix())
        return
    replacing = (prefix / "node_modules").exists()
    staged = _stage_install(run, staging)
    try:
        _stop_for_replacement(run, staged if installed is None else run.cli)
        _activate(run, prefix, staging)
    except (PaseoRuntimeFailure, OSError):
        shutil.rmtree(staging, ignore_errors=True)
        raise
    run.change(
        "install",
        "replaced" if replacing else "installed",
        previousVersion=installed,
        version=run.settings.version,
    )


def _stage_install(run: _Run, staging: Path) -> PaseoCli:
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    spec = f"{PASEO_PACKAGE}@{run.settings.version}"
    argv = ["npm", "install", "--prefix", staging.as_posix(), "--save-exact"]
    result = run.cli.runner([*argv, "--no-audit", "--no-fund", spec], INSTALL_TIMEOUT_SECONDS)
    staged = replace(run.cli, root=staging)
    if result.returncode != 0 or staged.installed_version() != run.settings.version:
        shutil.rmtree(staging, ignore_errors=True)
        raise PaseoRuntimeFailure(
            "install_failed",
            "install",
            f"npm install of {spec} failed; the previous install is untouched",
            paseo_error_text(result),
        )
    return staged


def _stop_for_replacement(run: _Run, cli: PaseoCli) -> None:
    """Stop a daemon that runs the install about to be replaced; it is started again later."""
    status = cli.json("install", "daemon", "status", "--json")
    if not is_running(status):
        return
    _require_listen_address(run, status)
    cli.json("install", "daemon", "stop", "--json")
    run.restart_reasons.append("version")
    run.change("daemon", "stopped", reasons=["version"])


def _activate(run: _Run, prefix: Path, staging: Path) -> None:
    previous = prefix / PREVIOUS_DIRECTORY
    shutil.rmtree(previous, ignore_errors=True)
    previous.mkdir()
    _move_install(prefix, previous)
    try:
        _move_install(staging, prefix)
    except OSError:
        _move_install(prefix, staging)
        _move_install(previous, prefix)
        raise
    shutil.rmtree(staging)
    shutil.rmtree(previous)
    if run.cli.installed_version() != run.settings.version:
        raise PaseoRuntimeFailure(
            "install_failed", "install", "the activated install does not report the pinned version"
        )


def _move_install(source: Path, target: Path) -> None:
    for name in INSTALL_ENTRIES:
        if (source / name).exists():
            (source / name).rename(target / name)


def _converge_daemon(run: _Run) -> None:
    """Configuration, plugin files, the running daemon and the loaded plugin, in that order."""
    run.step = "daemon"
    if tree_digest(run.plugin_source) is None:
        raise PaseoRuntimeFailure(
            "plugin_source_missing", "plugin", f"no AR plugin source at {run.plugin_source}"
        )
    status = run.cli.json("daemon", "daemon", "status", "--json")
    running = is_running(status)
    pending = _pending_settings(run)
    _require_listen_address(run, status if running else None)
    first_change = len(run.changes)
    reasons = _restart_reasons(run.settings, status, pending) if running else []
    reasons += _apply_settings(run, [item for item in pending if item.applies == "live"], running)
    if running and reasons:
        run.cli.json("daemon", "daemon", "stop", "--json")
        run.change("daemon", "stopped", reasons=reasons)
    run.restart_reasons += reasons
    kept_running = running and not reasons
    run.live_from = first_change if kept_running else None
    _apply_settings(run, [item for item in pending if item.applies == "start"], running=False)
    _sync_home_files(run)
    if not kept_running:
        _start_daemon(run)
    _ensure_plugin(run, kept_running)


def _pending_settings(run: _Run) -> list[DaemonSetting]:
    run.step = "config"
    document = run.cli.json("config", "daemon", "config", "get", "--json")
    current = document.get("value") if isinstance(document, dict) else None
    return [
        setting
        for setting in daemon_settings(run.settings)
        if _configured_value(current, setting) != setting.value
    ]


def _configured_value(current: object, setting: DaemonSetting) -> Any:
    value = current
    for key in setting.path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    # A configuration without provider entries and an empty provider object are the same state.
    return {} if value is None and setting.value == {} else value


def _require_listen_address(run: _Run, running: dict[str, Any] | None) -> None:
    """Refuse before anything is started or stopped when another process holds the address."""
    settings = run.settings
    if running is not None and running.get("listen") == settings.listen:
        return
    refused = run.probe(settings.listen_host, settings.listen_port)
    if refused is None:
        return
    if refused.errno == errno.EADDRINUSE:
        raise _port_in_use(settings, str(refused))
    raise PaseoRuntimeFailure(
        "listen_address_unusable",
        "daemon",
        f"the listen address {settings.listen} cannot be bound; nothing was started",
        str(refused),
    )


def _port_in_use(settings: PaseoRuntimeSettings, detail: str) -> PaseoRuntimeFailure:
    return PaseoRuntimeFailure(
        "listen_port_in_use",
        "daemon",
        f"listen port {settings.listen_port} ({settings.listen}) is held by another process; "
        "no other port is chosen and no daemon is left running",
        detail,
    )


def _restart_reasons(
    settings: PaseoRuntimeSettings, status: dict[str, Any], pending: list[DaemonSetting]
) -> list[str]:
    """Why a running daemon must be restarted: its version, or a setting applied only at start."""
    reasons = [f"setting:{item.path}" for item in pending if item.applies == "start"]
    version = status.get("daemonVersion")
    if version and version != settings.version:
        reasons.append("version")
    if status.get("listen") != settings.listen and "setting:daemon.listen" not in reasons:
        reasons.append("listen")
    return reasons


def _apply_settings(run: _Run, pending: list[DaemonSetting], running: bool) -> list[str]:
    """Write each setting; return the ones a running daemon said it cannot apply live."""
    late: list[str] = []
    for setting in pending:
        run.step = "config"
        response = run.cli.json(
            "config", "daemon", "config", "set", setting.path, json.dumps(setting.value)
        )
        run.change("config", "set", path=setting.path, value=setting.value, applies=setting.applies)
        required = response.get("restartRequiredPaths") if isinstance(response, dict) else None
        if running and any(
            path == setting.path or path.startswith(setting.path + ".") for path in required or []
        ):
            late.append(f"setting:{setting.path}")
    return late


def _sync_home_files(run: _Run) -> None:
    run.step = "plugin"
    home = run.settings.home
    if write_embed(home, run.settings.embed_payload()):
        run.change("embed", "written", entries=len(run.settings.embed))
    target = installed_plugin_path(home)
    if sync_plugin_copy(run.plugin_source, target):
        run.change("plugin", "copied", path=target.as_posix(), digest=tree_digest(target))


def _start_daemon(run: _Run) -> None:
    run.step = "daemon"
    result = run.cli.call(
        "daemon",
        "start",
        "--json",
        "--timeout",
        str(START_TIMEOUT_SECONDS),
        timeout=START_TIMEOUT_SECONDS + 30,
    )
    if result.returncode != 0:
        # Paseo's supervisor exits with a worker that never became ready; stop is the guarantee.
        run.cli.call("daemon", "stop", "--json")
        if "EADDRINUSE" in result.stderr:
            raise _port_in_use(run.settings, paseo_error_text(result))
        raise PaseoRuntimeFailure(
            "daemon_start_failed",
            "daemon",
            "paseo daemon start failed; no daemon is left running",
            paseo_error_text(result),
        )
    run.change("daemon", "started", listen=run.settings.listen)


def _ensure_plugin(run: _Run, kept_running: bool) -> None:
    """The AR plugin is installed from the home's copy and loaded with the current files."""
    run.step = "plugin"
    home = run.settings.home
    target = installed_plugin_path(home)
    loaded = {"plugin": tree_digest(target), "embed": _embed_digest(run.settings)}
    stale = kept_running and read_loaded_stamp(home) != loaded
    entry = plugin_entry(run.cli, "plugin")
    command_error: str | None = None
    if entry is not None and entry.get("path") != target.as_posix():
        run.cli.json("plugin", "plugin", "remove", PLUGIN_ID, "--json")
        run.change("plugin", "removed", path=entry.get("path"))
        entry = None
    if entry is None:
        command_error = _plugin_command(run, "installed", "install", target.as_posix())
    elif entry.get("enabled") is False:
        command_error = _plugin_command(run, "enabled", "enable", PLUGIN_ID)
    elif stale or entry.get("status") != "running":
        command_error = _plugin_command(run, "reloaded", "reload", PLUGIN_ID)
    final = plugin_entry(run.cli, "plugin")
    if final is None or final.get("status") != "running":
        raise PaseoRuntimeFailure(
            "plugin_load_failed",
            "plugin",
            "the AR plugin did not load; the daemon stays up",
            (final or {}).get("error") or command_error,
        )
    if read_loaded_stamp(home) != loaded:
        write_loaded_stamp(home, loaded)


def _plugin_command(run: _Run, action: str, *args: str) -> str | None:
    """Run one plugin lifecycle command; a load failure is judged from the plugin's state."""
    result = run.cli.call("plugin", *args, "--json")
    run.change("plugin", action)
    return None if result.returncode == 0 else paseo_error_text(result)


def _embed_digest(settings: PaseoRuntimeSettings) -> str:
    payload = json.dumps(settings.embed_payload(), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
