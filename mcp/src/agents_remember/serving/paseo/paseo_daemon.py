"""Read the state of the configured Paseo home's daemon, and stop it.

Both commands address the daemon only through ``paseo <command> --home <home>``, so Paseo
identifies the process by that home's own process record (``paseo.pid``). Nothing here looks a
process up by port or signals one itself: a daemon of another home is never touched, even when
it listens on the configured address. Because Paseo signals whatever live process the record
names, the record is first proven to name this home's supervisor (``paseo_process_record``); a
stale record is reported, and stop removes it, without anything being signalled.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.serving.paseo.paseo_command import (
    CommandRunner,
    PaseoCli,
    PaseoRuntimeFailure,
    command_failure,
    parse_json_output,
    run_command,
)
from agents_remember.serving.paseo.paseo_daemon_config import agent_tools_setting
from agents_remember.serving.paseo.paseo_lock import runtime_lock
from agents_remember.serving.paseo.paseo_node import node_status
from agents_remember.serving.paseo.paseo_plugin_files import PLUGIN_ID, read_embed
from agents_remember.serving.paseo.paseo_process_record import (
    PROCESS_RECORD,
    ProcessReader,
    RecordState,
    inspect_record,
    read_process,
    remove_stale_record,
)
from agents_remember.serving.paseo.paseo_remedy import terminal_provision_remedy


def is_running(status: dict[str, Any]) -> bool:
    return status.get("localDaemon") == "running"


def plugin_entry(cli: PaseoCli, step: str) -> dict[str, Any] | None:
    """Paseo's record of the AR plugin on the running daemon; ``None`` when not installed."""
    for entry in cli.json(step, "plugin", "ls", "--json", expect=list):
        if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID:
            return entry
    return None


def runtime_status(
    settings: PaseoRuntimeSettings,
    *,
    runner: CommandRunner = run_command,
    reader: ProcessReader = read_process,
) -> dict[str, Any]:
    """Whether the configured home's daemon runs and, when it does, what it runs with.

    ``agentTools`` is the setting for Paseo's own agent tools as the home's configuration file
    holds it; ``differs`` is true when that is not the value provision writes.
    """
    cli = PaseoCli(settings, runner)
    record = inspect_record(settings.home, "status", reader)
    status = None
    if record.kind != "stale":
        status = _home_record(cli, "status", ("daemon", "status", "--json"), reader)
    if status is None or not is_running(status):
        return _not_running(settings, record)
    facts = reader(record.pid) if record.pid is not None and record.kind == "own" else None
    node = node_status(facts.node_executable if facts else None)
    return {
        "ok": True,
        "home": settings.home.as_posix(),
        "running": True,
        **node,
        "restartRemedy": terminal_provision_remedy(settings) if node["restartRequired"] else None,
        "sessionVariables": list(facts.session_variables) if facts else [],
        "sessionRemedy": "paseo stop, then a new start at a time you choose"
        if facts and facts.session_variables
        else None,
        "version": status.get("daemonVersion"),
        "serverId": status.get("serverId"),
        "listen": status.get("listen"),
        "plugin": _plugin_state(cli),
        "embed": read_embed(settings.home) or [],
        "agentTools": agent_tools_setting(settings.home),
        "providers": [
            {key: provider.get(key) for key in ("provider", "available", "error")}
            for provider in status.get("providers") or []
            if isinstance(provider, dict)
        ],
        "staleRecord": None,
    }


def _not_running(settings: PaseoRuntimeSettings, record: RecordState) -> dict[str, Any]:
    """The status of a home without a daemon; a stale process record is named, not removed."""
    return {
        "ok": True,
        "home": settings.home.as_posix(),
        "running": False,
        "sessionVariables": [],
        "sessionRemedy": None,
        "version": None,
        "serverId": None,
        "listen": None,
        "plugin": None,
        "embed": None,
        "agentTools": None,
        "providers": None,
        "staleRecord": record.as_payload(),
    }


def _plugin_state(cli: PaseoCli) -> dict[str, Any]:
    """Paseo's state for the AR plugin: ``running``, ``failed`` (with Paseo's error) and so on."""
    try:
        plugin = plugin_entry(cli, "status")
    except PaseoRuntimeFailure as failure:
        return {"id": PLUGIN_ID, "state": "unknown", "error": failure.detail or str(failure)}
    if plugin is None:
        return {"id": PLUGIN_ID, "state": "not-installed", "error": None}
    return {"id": PLUGIN_ID, "state": plugin.get("status"), "error": plugin.get("error")}


def stop_runtime(
    settings: PaseoRuntimeSettings,
    *,
    runner: CommandRunner = run_command,
    reader: ProcessReader = read_process,
) -> dict[str, Any]:
    """Stop the daemon of the configured home; "not running" when it has none.

    A process record that does not name this home's supervisor is removed and nothing is
    signalled: the process it names belongs to someone else.
    """
    inspect_record(settings.home, "stop", reader)
    cli = PaseoCli(settings, runner)
    if not cli.executable.is_file() and not settings.home.exists():
        return _stop_locked(settings, runner, reader)
    with runtime_lock(settings.home, time.monotonic() + 60):
        return _stop_locked(settings, runner, reader)


def _stop_locked(
    settings: PaseoRuntimeSettings, runner: CommandRunner, reader: ProcessReader
) -> dict[str, Any]:
    cli = PaseoCli(settings, runner)
    stale = inspect_record(settings.home, "stop", reader)
    record = None
    if stale.kind == "stale":
        remove_stale_record(settings.home)
    else:
        record = _home_record(cli, "stop", ("daemon", "stop", "--json"), reader)
    action = "not_running" if record is None else record.get("action")
    if action not in {"stopped", "not_running"}:
        raise PaseoRuntimeFailure(
            "paseo_invalid_response", "stop", f"paseo daemon stop reported {action!r}"
        )
    stopped = action == "stopped"
    return {
        "ok": True,
        "home": settings.home.as_posix(),
        "action": "stopped" if stopped else "not running",
        "pid": record.get("pid") if record is not None and stopped else None,
        "staleRecord": stale.as_payload(),
    }


def _home_record(
    cli: PaseoCli, step: str, args: Sequence[str], reader: ProcessReader
) -> dict[str, Any] | None:
    """Run one home-addressed command; ``None`` when nothing was ever installed or started."""
    if not cli.node.is_file() or not cli.executable.is_file():
        if (cli.settings.home / PROCESS_RECORD).exists():
            remedy = (
                terminal_provision_remedy(cli.settings)
                if inspect_record(cli.settings.home, step, reader).kind == "own"
                else "Run runtime_install, then the one dashboard start."
            )
            raise PaseoRuntimeFailure(
                "node_not_installed" if not cli.node.is_file() else "paseo_cli_unavailable",
                step,
                f"The configured host needs the product Node and Paseo CLI. {remedy}",
            )
        return None
    result = cli.call(*args)
    if result.missing:
        if (cli.settings.home / PROCESS_RECORD).exists():
            raise PaseoRuntimeFailure(
                "paseo_cli_unavailable",
                step,
                "the daemon home has a process record but the install prefix has no Paseo CLI "
                "to address it; run runtime_install",
            )
        return None
    if result.returncode != 0:
        raise command_failure(step, args, result)
    return parse_json_output(step, args, result)
