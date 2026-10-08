"""Read-only host status and start-only supervision: no install, reload or configuration write."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_authority import (
    load_shared_paseo_runtime,
    paseo_runtime_path,
)
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeSettings,
    PaseoRuntimeSettingsError,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.paseo.paseo_command import (
    COMMAND_TIMED_OUT,
    CommandResult,
    CommandRunner,
    PaseoCli,
    run_command,
)
from agents_remember.serving.paseo.paseo_daemon import is_running
from agents_remember.serving.paseo.paseo_lock import runtime_lock
from agents_remember.serving.paseo.paseo_node import node_executable_valid, node_status
from agents_remember.serving.paseo.paseo_process_record import (
    ProcessReader,
    inspect_record,
    read_process,
)
from agents_remember.serving.paseo.paseo_provision import (
    START_TIMEOUT_SECONDS,
    _own_daemon_recorded,
    _require_listen_address,
    _start_daemon,
    bind_error,
)
from agents_remember.serving.paseo.paseo_remedy import terminal_provision_remedy
from agents_remember.serving.paseo.paseo_run import (
    BindProbe,
    _Run,
)
from agents_remember.serving.paseo.paseo_settings import pending_settings
from agents_remember.serving.paseo.paseo_start_outcome import joined_outcome, write_outcome

STATUS_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class HostObservation:
    state: str
    line: str
    facts: dict[str, Any]

    @property
    def ready(self) -> bool:
        return self.state in {"running", "not configured"}


def _bounded_runner(runner: CommandRunner, deadline: float) -> CommandRunner:
    def call(argv, timeout):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return CommandResult(COMMAND_TIMED_OUT, "", "host observation deadline passed")
        return runner(argv, min(timeout, remaining))

    return call


def _settings(config: McpRuntimeConfig) -> PaseoRuntimeSettings | HostObservation:
    path = paseo_runtime_path(config.coordination_root)
    try:
        settings = load_shared_paseo_runtime(config.coordination_root)
    except (PaseoRuntimeSettingsError, OSError) as error:
        return HostObservation(
            "not installed", f"host settings cannot be read at {path}: {error}", {}
        )
    if settings is None:
        return HostObservation(
            "not configured", f"host not configured: {path} has no paseoRuntime block", {}
        )
    return replace(settings, command_config_path=config.config_path)


def observe_host(
    config: McpRuntimeConfig,
    *,
    runner: CommandRunner = run_command,
    reader: ProcessReader = read_process,
) -> HostObservation:
    settings = _settings(config)
    if isinstance(settings, HostObservation):
        return settings
    return _observe(
        settings,
        PaseoCli(settings, _bounded_runner(runner, time.monotonic() + STATUS_TIMEOUT_SECONDS)),
        reader,
    )


def _observe(
    settings: PaseoRuntimeSettings, cli: PaseoCli, reader: ProcessReader
) -> HostObservation:
    base: dict[str, Any] = {"home": settings.home.as_posix(), "version": None, "listen": None}
    try:
        record = inspect_record(settings.home, "status", reader)
        base["staleRecord"] = record.as_payload()
        base["supervisorAlive"] = record.kind == "own"
        facts = reader(record.pid) if record.pid is not None and record.kind == "own" else None
        base["sessionVariables"] = list(facts.session_variables) if facts else []
        base["nodeExecutable"] = facts.node_executable if facts else None
        if record.kind == "own":
            status = cli.json("status", "daemon", "status", "--json")
            if not is_running(status) or not status.get("daemonVersion"):
                return _line("not answering", settings, base)
            base.update(
                node_status(base["nodeExecutable"]),
                version=status.get("daemonVersion"),
                listen=status.get("listen"),
            )
            return _line("running", settings, base)
        node = product_node()
        installed = cli.installed_version()
        if not node_executable_valid(node, cli.runner) or installed != settings.version:
            base["version"] = installed
            return _line("not installed", settings, base)
        configuration = settings.home / "config.json"
        if not configuration.is_file() or any(
            item.applies == "start" for item in pending_settings(cli)
        ):
            base["error"] = "host configuration is incomplete; runtime_install must finish setup"
            return _line("not installed", settings, base)
        return _line("not running", settings, base)
    except (PaseoRuntimeFailure, OSError) as error:
        base["error"] = error.as_payload() if isinstance(error, PaseoRuntimeFailure) else str(error)
        return _line(
            "not answering" if base.get("supervisorAlive") else "not installed", settings, base
        )


def _line(state: str, settings: PaseoRuntimeSettings, facts: dict[str, Any]) -> HostObservation:
    line = f"host {state}"
    if settings.command_config_path is not None:
        line += f"; config: {settings.command_config_path}; host settings: {settings.source_path}"
    if state == "running":
        line += f": v{facts['version']}, {facts['listen']}, home {settings.home}"
    if facts.get("version") is not None and facts["version"] != settings.version:
        line += f"; observed version {facts['version']}, build version {settings.version}"
    if state == "running" and facts.get("listen") != settings.listen:
        line += f"; observed listen {facts['listen']}, settings listen {settings.listen}"
    if facts.get("staleRecord"):
        line += f"; stale process record: {facts['staleRecord']}"
    if facts.get("sessionVariables"):
        line += f"; carried session variables: {', '.join(facts['sessionVariables'])}; "
        line += "remove them with paseo stop and a new start when you choose"
    if settings.version_notice:
        line += f"; {settings.version_notice}"
    line += _remedy_line(state, settings, facts)
    if facts.get("error"):
        line += f"; {facts['error']}"
    return HostObservation(state, " ".join(line.splitlines()), facts)


def _remedy_line(state: str, settings: PaseoRuntimeSettings, facts: dict[str, Any]) -> str:
    error = facts.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if code == "node_platform_unsupported":
        return "; this build does not support a host here; no host install or start is available"
    if code == "node_not_installed" and facts.get("supervisorAlive"):
        return "; " + terminal_provision_remedy(settings)
    if facts.get("restartRequired"):
        return (
            f"; Node restart required: observed {facts['nodeExecutable']}, "
            f"build {facts['node']['path']}; {terminal_provision_remedy(settings)}"
        )
    if state == "not installed":
        return "; run runtime_install to install this build's Node and host"
    if state == "running" and (
        facts.get("version") != settings.version or facts.get("listen") != settings.listen
    ):
        return "; " + terminal_provision_remedy(settings)
    return ""


def ensure_host(
    config: McpRuntimeConfig,
    *,
    runner: CommandRunner = run_command,
    reader: ProcessReader = read_process,
    probe: BindProbe = bind_error,
) -> HostObservation:
    settings = _settings(config)
    if isinstance(settings, HostObservation):
        return settings
    entered = time.monotonic()
    deadline = entered + START_TIMEOUT_SECONDS
    cli = PaseoCli(settings, _bounded_runner(runner, deadline))
    observed = _observe(settings, cli, reader)
    if observed.state not in {"not running", "not answering"}:
        return observed
    try:
        with runtime_lock(settings.home, deadline) as waited:
            shared = joined_outcome(settings, entered) if waited else None
            if shared is not None:
                return HostObservation(shared["state"], shared["line"], shared["facts"])
            observed = _observe(settings, cli, reader)
            if observed.state != "not running":
                return observed
            outcome = _start_locked(settings, cli, reader, probe)
            write_outcome(settings, outcome.state, outcome.line, outcome.facts)
            return outcome
    except (PaseoRuntimeFailure, OSError) as error:
        facts = {
            **observed.facts,
            "error": error.as_payload() if isinstance(error, PaseoRuntimeFailure) else str(error),
        }
        return _line("not running", settings, facts)


def _start_locked(
    settings: PaseoRuntimeSettings, cli: PaseoCli, reader: ProcessReader, probe: BindProbe
) -> HostObservation:
    try:
        run = _Run(settings, cli, settings.home, probe, reader)
        _require_listen_address(run, None)
        _own_daemon_recorded(run)
        _start_daemon(run)
        return _observe(settings, cli, reader)
    except (PaseoRuntimeFailure, OSError) as error:
        observed = _observe(settings, cli, reader)
        facts = {
            **observed.facts,
            "error": error.as_payload() if isinstance(error, PaseoRuntimeFailure) else str(error),
        }
        return _line(observed.state, settings, facts)
