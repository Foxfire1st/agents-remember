"""Dashboard daemon command adapter and status presentation."""

from __future__ import annotations

import argparse
import json
import urllib.request
from collections.abc import Callable

from agents_remember.kernel.primitives.runtime_config import ConfigError, McpRuntimeConfig
from agents_remember.serving import daemon as serving_daemon
from agents_remember.serving.paseo.paseo_start import STATUS_TIMEOUT_SECONDS, HostObservation
from agents_remember.serving.projector import ProjectionCadence


def _print_background_status(host: str, port: int) -> None:
    """Print the dashboard's own background-work totals (MIK-R81 rule 1).

    Best-effort: the diagnostics route is observability, so its failure prints
    one unavailable line and never changes the status exit code.
    """
    from agents_remember.serving.background_accounting import (  # noqa: PLC0415
        describe_snapshot,
    )

    url = f"http://{host}:{port}/api/diagnostics/background"
    try:
        with urllib.request.urlopen(url, timeout=STATUS_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
        print(describe_snapshot(payload))
    except Exception as error:
        print(f"background work: unavailable ({error})")


def run_daemon_command(
    args: argparse.Namespace,
    config: serving_daemon.McpRuntimeConfig,
    port: int,
    *,
    observe: Callable[[McpRuntimeConfig], HostObservation],
    start_host: Callable[[McpRuntimeConfig], HostObservation],
) -> int:
    """Dispatch --status/--stop/--daemon against the recorded daemon state."""
    if args.sim or args.reload:
        print("error: --daemon/--status/--stop are not supported with --sim or --reload")
        return 1
    directory = serving_daemon.daemon_dir(config)
    if args.status:
        identity_unknown = False
        try:
            state, alive = serving_daemon.probe(
                directory, serving_daemon.DaemonEndpoint(host=args.host, port=port)
            )
        except ConfigError as error:
            state, alive = None, False
            identity_unknown = True
            print(f"dashboard daemon identity is unknown: {error}")
        if alive and state is not None:
            print(
                f"dashboard daemon running: pid {state.pid}, http://{state.host}:{state.port}/ "
                f"(v{state.version}); log: {state.log_path}"
            )
            _print_currentness(state)
            _print_background_status(state.host, state.port)
        elif not identity_unknown:
            print("dashboard daemon not running")
        host = observe(config)
        print(host.line)
        return 1 if not alive else (0 if host.ready else 3)
    if args.stop:
        result = serving_daemon.stop(directory)
        print(f"dashboard daemon: {result}")
        return 0 if result in ("not-running", "stopped", "killed") else 1
    result = serving_daemon.ensure(
        config,
        serving_daemon.DaemonEndpoint(host=args.host, port=port),
        cadence=ProjectionCadence(interval=args.interval, heartbeat=args.heartbeat),
    )
    print(f"dashboard daemon {result.action}: {result.detail}")
    host = start_host(config)
    print(host.line)
    if result.action in ("adopted", "unknown"):
        serving_daemon.record_host_outcome(config, host.line)
    if result.action not in ("adopted", "started", "restarted", "unknown"):
        return 1
    return 0 if host.ready else 3


def _print_currentness(state: serving_daemon.DaemonState) -> None:
    current = state.build_currentness
    if current is None:
        print(f"dashboard build: unknown; loaded digest {state.source_digest}")
        return
    line = (
        f"dashboard build: {current.mode} {current.state}; "
        f"loaded {current.loadedDigest}; installed {current.installedDigest}"
    )
    if current.firstSeenAt is not None:
        line += f"; first seen {current.firstSeenAt}"
    if current.reason is not None:
        line += f"; {current.reason}"
    if current.remedy is not None:
        line += f"; {current.remedy}"
    print(line)
