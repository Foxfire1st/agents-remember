"""Read the state of the configured Paseo home's daemon, and stop it.

Both commands address the daemon only through ``paseo <command> --home <home>``, so Paseo
identifies the process by that home's own process record (``paseo.pid``). Nothing here looks a
process up by port or signals one itself: a daemon of another home is never touched, even when
it listens on the configured address. Because Paseo signals whatever live process the record
names, the record is first proven to name this home's supervisor (``paseo_process_record``); a
stale record is reported, and stop removes it, without anything being signalled.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agents_remember.cli.paseo_command import (
    CommandRunner,
    PaseoCli,
    PaseoRuntimeFailure,
    command_failure,
    parse_json_output,
    run_command,
)
from agents_remember.cli.paseo_plugin_files import PLUGIN_ID, read_embed
from agents_remember.cli.paseo_process_record import (
    PROCESS_RECORD,
    ProcessReader,
    RecordState,
    inspect_record,
    read_process,
    remove_stale_record,
)
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings


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
    """Whether the configured home's daemon runs and, when it does, what it runs with."""
    cli = PaseoCli(settings, runner)
    record = inspect_record(settings.home, "status", reader)
    status = None
    if record.kind != "stale":
        status = _home_record(cli, "status", ("daemon", "status", "--json"))
    if status is None or not is_running(status):
        return _not_running(settings, record)
    return {
        "ok": True,
        "home": settings.home.as_posix(),
        "running": True,
        "version": status.get("daemonVersion"),
        "serverId": status.get("serverId"),
        "listen": status.get("listen"),
        "plugin": _plugin_state(cli),
        "embed": read_embed(settings.home) or [],
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
        "version": None,
        "serverId": None,
        "listen": None,
        "plugin": None,
        "embed": None,
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
    cli = PaseoCli(settings, runner)
    stale = inspect_record(settings.home, "stop", reader)
    record = None
    if stale.kind == "stale":
        remove_stale_record(settings.home)
    else:
        record = _home_record(cli, "stop", ("daemon", "stop", "--json"))
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


def _home_record(cli: PaseoCli, step: str, args: Sequence[str]) -> dict[str, Any] | None:
    """Run one home-addressed command; ``None`` when nothing was ever installed or started."""
    result = cli.call(*args)
    if result.missing:
        if (cli.settings.home / PROCESS_RECORD).exists():
            raise PaseoRuntimeFailure(
                "paseo_cli_unavailable",
                step,
                "the daemon home has a process record but the install prefix has no Paseo CLI "
                "to address it; run provision first",
            )
        return None
    if result.returncode != 0:
        raise command_failure(step, args, result)
    return parse_json_output(step, args, result)
