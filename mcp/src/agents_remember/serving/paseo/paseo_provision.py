"""Bring the configured Paseo runtime to its settings and report every change as data.

One repeatable pass: the install prefix holds exactly the pinned version, the daemon
configuration holds the settings this module writes, the AR plugin is installed from its copy in
the home, and the daemon runs. A pass that finds nothing to change makes only read-only calls.

Order is what makes an interrupted pass converge on the next one. A new version is staged beside
the old install and activated only after it reports the pinned version. A running daemon is
stopped before a start-only setting is written and started after it, so a daemon is never left
running on settings the file no longer holds. The plugin stamp is written after the plugin
loaded, so a copy that was replaced but never loaded is loaded by the next pass. The provider
entries are written into the daemon's configuration file, never onto a command line, and the
file they replace is kept until Paseo has accepted the new one.
"""

from __future__ import annotations

import errno
import hashlib
import json
import shutil
import socket
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.paseo_host_contract import HOST_DATA, PASEO_PACKAGE
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.serving.paseo.paseo_command import (
    CommandResult,
    CommandRunner,
    PaseoCli,
    PaseoRuntimeFailure,
    command_failure,
    parse_json_output,
    paseo_error_text,
    run_command,
)
from agents_remember.serving.paseo.paseo_daemon import is_running, plugin_entry
from agents_remember.serving.paseo.paseo_daemon_config import (
    PROVIDER_ENTRIES,
    accept_provider_entries,
    remove_stale_temporaries,
    restore_previous_config,
    write_provider_entries,
)
from agents_remember.serving.paseo.paseo_lock import runtime_lock
from agents_remember.serving.paseo.paseo_node import ensure_node
from agents_remember.serving.paseo.paseo_packages import installed_lock_matches
from agents_remember.serving.paseo.paseo_plugin_files import (
    PLUGIN_ID,
    installed_plugin_path,
    plugin_source_root,
    read_loaded_stamp,
    sync_plugin_copy,
    tree_digest,
    write_embed,
    write_loaded_stamp,
)
from agents_remember.serving.paseo.paseo_process_record import (
    ProcessReader,
    inspect_record,
    read_process,
    remove_stale_record,
)
from agents_remember.serving.paseo.paseo_run import (
    BindProbe,
    ProvisionIntent,
    _check_install_restart,
    _Run,
    preserve_live_install,
)
from agents_remember.serving.paseo.paseo_settings import (
    DaemonSetting,
    pending_settings,
    restart_reasons,
)

INSTALL_TIMEOUT_SECONDS = 900.0
START_TIMEOUT_SECONDS = 120
# How Paseo 0.11 names a configuration file its schema refuses.
_INVALID_CONFIGURATION = "Invalid config"
STAGING_DIRECTORY = ".ar-staging"
PREVIOUS_DIRECTORY = ".ar-previous"
INSTALL_ENTRIES = ("node_modules", "package.json", "package-lock.json")
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})


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


def provision_runtime(
    settings: PaseoRuntimeSettings,
    *,
    runner: CommandRunner = run_command,
    plugin_source: Path | None = None,
    probe: BindProbe = bind_error,
    reader: ProcessReader = read_process,
) -> dict[str, Any]:
    """Move the runtime to the configured state; the report lists every change made."""
    return _provision(
        _Run(
            settings,
            PaseoCli(settings, runner),
            plugin_source or plugin_source_root(),
            probe,
            reader,
        )
    )


def provision_for_install(
    settings: PaseoRuntimeSettings,
    *,
    runner: CommandRunner = run_command,
    plugin_source: Path | None = None,
    probe: BindProbe = bind_error,
    reader: ProcessReader = read_process,
) -> dict[str, Any]:
    """Apply installation changes while keeping an existing host's sessions alive."""
    return _provision(
        _Run(
            settings,
            PaseoCli(settings, runner),
            plugin_source or plugin_source_root(),
            probe,
            reader,
            ProvisionIntent.INSTALL,
        )
    )


def _provision(run: _Run) -> dict[str, Any]:
    settings, intent = run.settings, run.intent
    failure: PaseoRuntimeFailure | None = None
    node = None
    try:
        inspect_record(settings.home, "install", run.reader)
        node = {**product_node().payload(), "changed": False}
        if intent is ProvisionIntent.INSTALL:
            _check_install_restart(run)
        if not run.restart_reasons:
            with runtime_lock(settings.home, time.monotonic() + INSTALL_TIMEOUT_SECONDS):
                if intent is ProvisionIntent.INSTALL:
                    _check_install_restart(run)
                if not run.restart_reasons:
                    node = ensure_node(run.cli.runner)
                    if node["changed"]:
                        run.change("node", "installed", version=node["version"], path=node["path"])
                    for leftover in node.get("removed", []):
                        run.change("node", "removed-leftover", path=leftover)
                    _restore_unfinished_provider_write(run)
                    _own_daemon_recorded(run)
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
        "node": node,
        "listen": settings.listen,
        "daemon": {"action": _daemon_action(run), "reasons": run.restart_reasons},
        **({"restartRequired": run.restart_reasons} if intent is ProvisionIntent.INSTALL else {}),
        "changes": run.changes,
        "error": None if failure is None else failure.as_payload(),
    }


def _restore_unfinished_provider_write(run: _Run) -> None:
    """Undo a provider write an earlier pass did not finish, before any Paseo call reads the file.

    The change says what was put back: ``file`` when ``config.json`` was still the file that pass
    wrote, ``providers`` when it had changed since and only the provider entries were put back.
    """
    run.step = "config"
    for leftover in remove_stale_temporaries(run.settings.home):
        run.change("config", "removed-leftover", path=leftover.as_posix())
    restored = restore_previous_config(run.settings.home)
    if restored is not None:
        run.change("config", "restored", path=PROVIDER_ENTRIES, restored=restored)
        run.reload_owed = True
    run.step = "install"


def _own_daemon_recorded(run: _Run) -> bool:
    """Whether the home's process record names this home's supervisor; a stale one is removed.

    Paseo takes any live process the record names for the daemon and its stop signals it, so
    this runs before the first Paseo call of a pass and again before every stop.
    """
    record = inspect_record(run.settings.home, run.step, run.reader)
    if record.kind == "stale":
        remove_stale_record(run.settings.home)
        run.change("daemon", "removed-stale-record", pid=record.pid, reason=record.reason)
    return record.kind == "own"


def _stop_daemon(run: _Run, cli: PaseoCli, step: str) -> None:
    """Stop this home's daemon through Paseo, and only a process proven to be it."""
    if _own_daemon_recorded(run):
        preserve_live_install(run, "stop")
        cli.json(step, "daemon", "stop", "--json")


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
    if installed == run.settings.version and installed_lock_matches(prefix):
        for leftover in (staging, prefix / PREVIOUS_DIRECTORY):
            if leftover.exists():
                shutil.rmtree(leftover)
                run.change("install", "removed-leftover", path=leftover.as_posix())
        return
    replacing = (prefix / "node_modules").exists()
    preserve_live_install(run, "replace")
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
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(HOST_DATA / name, staging / name)
    node = product_node()
    argv = [node.node.as_posix(), node.npm.as_posix(), "ci", "--prefix", staging.as_posix()]
    result = run.cli.runner([*argv, "--no-audit", "--no-fund"], INSTALL_TIMEOUT_SECONDS)
    staged = replace(run.cli, root=staging)
    if result.returncode != 0 or staged.installed_version() != run.settings.version:
        shutil.rmtree(staging, ignore_errors=True)
        raise PaseoRuntimeFailure(
            "install_failed",
            "install",
            f"npm ci of locked {spec} failed; the previous install is untouched",
            paseo_error_text(result),
        )
    return staged


def _stop_for_replacement(run: _Run, cli: PaseoCli) -> None:
    """Stop a daemon that runs the install about to be replaced; it is started again later."""
    status = cli.json("install", "daemon", "status", "--json")
    if not is_running(status):
        return
    _require_listen_address(run, status)
    _stop_daemon(run, cli, "install")
    run.restart_reasons.append("version")
    run.change("daemon", "stopped", reasons=["version"])


def _activate(run: _Run, prefix: Path, staging: Path) -> None:
    preserve_live_install(run, "activate an install over")
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
    if not running:
        preserve_live_install(run, "start another daemon for")
    pending = pending_settings(run.cli)
    _require_listen_address(run, status if running else None)
    first_change = len(run.changes)
    reasons = restart_reasons(run.settings, status, pending) if running else []
    previous_node: Path | None = None
    if running:
        record = inspect_record(run.settings.home, "daemon", run.reader)
        facts = run.reader(record.pid) if record.pid is not None else None
        if (
            facts is not None
            and facts.node_executable is not None
            and facts.node_executable != product_node().node.as_posix()
        ):
            reasons.append("node")
            previous_node = Path(facts.node_executable).parent.parent
    if running and reasons and run.intent is ProvisionIntent.INSTALL:
        run.restart_reasons += reasons
        return
    reasons += _apply_settings(run, [item for item in pending if item.applies == "live"], running)
    if running and reasons and run.intent is ProvisionIntent.EXPLICIT:
        _stop_daemon(run, run.cli, "daemon")
        run.change("daemon", "stopped", reasons=reasons)
    run.restart_reasons += reasons
    kept_running = running and (not reasons or run.intent is ProvisionIntent.INSTALL)
    run.live_from = first_change if kept_running else None
    if kept_running and run.reload_owed:
        run.cli.json("config", "daemon", "reload", "--json")
        run.change("config", "reloaded", path=PROVIDER_ENTRIES)
    _apply_settings(run, [item for item in pending if item.applies == "start"], running=False)
    _sync_home_files(run)
    if not kept_running:
        _start_daemon(run)
        _report_unused_node(run, previous_node)
    _ensure_plugin(run, kept_running)


def _report_unused_node(run: _Run, previous: Path | None) -> None:
    """Name the earlier product Node folder after a successful host start; keep its files."""
    if previous is not None and previous.parent == product_node().root.parent:
        run.change("node", "no-longer-used", path=previous.as_posix())


def _require_listen_address(run: _Run, running: dict[str, Any] | None) -> None:
    """Refuse before anything is started or stopped when another process holds the address.

    The probe is skipped only when the holder can be this home's own running daemon, which keeps
    the address or is restarted onto it. If another process takes the address between that stop
    and the start, Paseo's own refusal reports it.
    """
    settings = run.settings
    if running is not None and _held_by_own_daemon(running.get("listen"), settings):
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


def _held_by_own_daemon(listen: object, settings: PaseoRuntimeSettings) -> bool:
    """Whether a daemon listening on ``listen`` occupies the configured address itself.

    Same port, and either host is a wildcard address or both hosts resolve to a common address.
    Another host on the same port is another address: a foreign process can hold it.
    """
    host, _, port = listen.rpartition(":") if isinstance(listen, str) else ("", "", "")
    if not port.isdecimal() or int(port) != settings.listen_port:
        return False
    hosts = {host.strip("[]"), settings.listen_host}
    if hosts & _WILDCARD_HOSTS:
        return True
    return bool(set.intersection(*(_addresses(name) for name in hosts)))


def _addresses(host: str) -> set[str]:
    try:
        return {str(info[4][0]) for info in socket.getaddrinfo(host, None)}
    except OSError:
        return set()


def _port_in_use(settings: PaseoRuntimeSettings, detail: str) -> PaseoRuntimeFailure:
    return PaseoRuntimeFailure(
        "listen_port_in_use",
        "daemon",
        f"listen port {settings.listen_port} ({settings.listen}) is held by another process; "
        "no other port is chosen and no daemon is left running",
        detail,
    )


def _apply_settings(run: _Run, pending: list[DaemonSetting], running: bool) -> list[str]:
    """Write each setting; return the ones a running daemon said it cannot apply live."""
    late: list[str] = []
    for setting in pending:
        run.step = "config"
        if setting.path == PROVIDER_ENTRIES:
            response = _apply_provider_entries(run, setting, running)
        else:
            response = run.cli.json(
                "config", "daemon", "config", "set", setting.path, json.dumps(setting.value)
            )
            run.change(
                "config", "set", path=setting.path, value=setting.value, applies=setting.applies
            )
        required = response.get("restartRequiredPaths")
        if running and any(
            path == setting.path or path.startswith(setting.path + ".")
            for path in (required if isinstance(required, list) else [])
            if isinstance(path, str)
        ):
            late.append(f"setting:{setting.path}")
    return late


def _apply_provider_entries(run: _Run, setting: DaemonSetting, running: bool) -> dict[str, Any]:
    """Write the provider entries into the daemon's file and have Paseo take them from there.

    Their values never appear on a command line or in the report, which names the ids only. A
    running daemon reloads the file; for a stopped one Paseo reads the file back, which validates
    it. If Paseo does not accept the file, what this pass wrote is undone: the kept file goes back
    whole, or only the provider entries when the file changed in the meantime.
    """
    home = run.settings.home
    write_provider_entries(home, setting.value)
    args = ("daemon", "reload", "--json") if running else ("daemon", "config", "get", "--json")
    result = run.cli.call(*args)
    if result.returncode != 0:
        raise _provider_entries_not_accepted(args, result, restore_previous_config(home))
    accept_provider_entries(home)
    run.reload_owed = run.reload_owed and not running
    run.change(
        "config",
        "set",
        path=setting.path,
        providerIds=sorted(setting.value),
        applies=setting.applies,
    )
    return parse_json_output("config", args, result) if running else {}


def _provider_entries_not_accepted(
    args: tuple[str, ...], result: CommandResult, restored: str | None
) -> PaseoRuntimeFailure:
    """Paseo refused the entries, or the call failed for another reason; both were rolled back."""
    put_back = "the previous file was put back"
    if restored == "providers":
        put_back = (
            "the file had changed meanwhile, so only the previous provider entries were put back"
        )
    detail = paseo_error_text(result)
    if _INVALID_CONFIGURATION in detail:
        message = f"Paseo refused the provider entries; {put_back}"
        return PaseoRuntimeFailure("provider_entries_refused", "config", message, detail)
    failure = command_failure("config", args, result)
    return PaseoRuntimeFailure(failure.code, "config", f"{failure}; {put_back}", detail)


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
    preserve_live_install(run, "start another daemon for")
    result = run.cli.call(
        "daemon",
        "start",
        "--json",
        "--timeout",
        str(START_TIMEOUT_SECONDS),
        timeout=START_TIMEOUT_SECONDS + 30,
    )
    if result.returncode != 0:
        # Explicit provision may clean up its failed start; INSTALL preserves any live host
        # observed after the start-site admission, including an ambiguous failed reply.
        if _own_daemon_recorded(run):
            preserve_live_install(run, "stop after a failed start of")
            run.cli.call("daemon", "stop", "--json")
        still_alive = _own_daemon_recorded(run)
        if "EADDRINUSE" in result.stderr and not still_alive:
            raise _port_in_use(run.settings, paseo_error_text(result))
        raise PaseoRuntimeFailure(
            "daemon_start_failed",
            "daemon",
            "paseo daemon start failed; the supervisor is still alive/not answering and "
            "cleanup is outstanding; no second daemon was started"
            if still_alive
            else "paseo daemon start failed; no daemon is left running",
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
    """Run one plugin lifecycle command; a load failure is judged from the plugin's state.

    The change is recorded only when the command succeeded; a failure returns Paseo's text.
    """
    result = run.cli.call("plugin", *args, "--json")
    if result.returncode != 0:
        return paseo_error_text(result)
    run.change("plugin", action)
    return None


def _embed_digest(settings: PaseoRuntimeSettings) -> str:
    payload = json.dumps(settings.embed_payload(), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
