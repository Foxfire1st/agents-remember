"""Application-owned startup operations for the MCP process."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Any

from agents_remember.application.agent_binding import read_agent_binding
from agents_remember.application.tool_caller import snapshot_tool_caller
from agents_remember.controlplane.durable_store import declare_process_role
from agents_remember.models.core import ServingBuildPayload
from agents_remember.observer import EventStore, observer_root
from agents_remember.observer.tool_events import ToolEventWriter, install_tool_writer
from agents_remember.serving.build_info import process_serving_build
from agents_remember.serving.daemon import maybe_autostart_dashboard

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )

logger = logging.getLogger(__name__)


def initialize_mcp_application(config: McpRuntimeConfig) -> None:
    """Install the process-wide application collaborators used by registered operations."""
    install_tool_writer(build_tool_event_writer(config))


def build_tool_event_writer(config: McpRuntimeConfig) -> ToolEventWriter:
    """Snapshot this server's caller once and build its workspace-river writer.

    The binding the launch started this server with is read here, with the task
    document it names, so an event row is whole without another read. A binding
    that cannot be read changes nothing about how the server starts (the packet's
    failure rule): the writer then names a chat without a role, and the server
    starts as it does today.
    """
    try:
        binding = read_agent_binding()
    except ValueError:
        binding = None
    caller = snapshot_tool_caller(
        binding,
        coordination_root=config.coordination_root,
        config_path=config.config_path,
    )
    return ToolEventWriter(EventStore(observer_root(config)), caller)


def mcp_serving_build_payload() -> ServingBuildPayload:
    """Return the application-owned boot identity exposed by the MCP adapter."""

    return process_serving_build().payload()


def measuring_build_stamp() -> dict[str, object]:
    """Which build produced a measurement, as wire JSON (D-33).

    A memory-quality or citation count is produced by ONE build, and the build that answers an MCP
    tool call need not be the candidate being measured: ``server_info`` reported
    ``servingBuild.commit f0313143`` while the leaf under curation carried newer code, so a reader
    of a checklist could not tell a candidate-ruler count from a serving-build-ruler count. This
    stamp is what makes the ruler nameable, and it costs nothing per call -- ``process_serving_build``
    resolves the identity once per process, so this is a dict copy rather than a probe. ``dirty``
    rides along so an uncommitted serving tree reads as such instead of being taken for the commit
    it names.
    """

    return {
        "servingBuild": process_serving_build().payload().model_dump(mode="json", exclude_none=True)
    }


def declare_mcp_process() -> None:
    """Declare trusted MCP execution before authority settings are loaded."""
    declare_process_role("mcp")


def prepare_mcp_process() -> None:
    """Declare MCP store ownership; nothing optional starts here (MIK-R204 v1 rule 3).

    Dashboard supervision and the outside-scratch sweep are not discovery work: both start once,
    right after the first ``tools/list`` answer or on the first tool call as a fallback
    (:func:`start_after_discovery`), never at process entry.
    """
    declare_mcp_process()


_SWEEP_IN_CHILD = """
import json

from agents_remember.application.runtime.startup import _sweep_outside_scratch_in_child

print(json.dumps(_sweep_outside_scratch_in_child(), default=str))
"""
"""The one-shot sweep child: the heavy import happens in this process, never in the server."""


def _sweep_outside_scratch_in_child() -> Any:
    """The sweep body that runs in the child: the only place the worktrees owner is imported."""
    from agents_remember.worktrees.integration.lifecycle.outside_scratch_reconcile import (  # noqa: PLC0415
        reconcile_outside_scratch_roots as sweep,
    )

    return sweep()


def reconcile_outside_scratch_roots() -> Any:
    """Reconcile the product's outside scratch roots (MIK-R207, MIK-R204 rule 6).

    The sweep's owner lives under ``agents_remember.worktrees``, which must stay unloaded in the
    serving process even after discovery (rule 6). The daemon thread therefore runs the sweep in a
    short-lived child process: the child imports the owner, sweeps and returns the report as JSON,
    while the server process still never imports that package. This module-level name is also the
    seam L198's ``test_the_tool_server_start_sweeps_the_roots`` monkeypatches.
    """
    completed = subprocess.run(
        [sys.executable, "-P", "-c", _SWEEP_IN_CHILD],
        # The child gets no stdin: over the stdio transport the server's stdin carries the MCP
        # protocol, and the sweep must never consume a client's bytes.
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "outside scratch sweep failed: " + (completed.stderr.strip() or "no stderr")[-2000:]
        )
    return json.loads(completed.stdout)


def start_outside_scratch_reconcile() -> None:
    """Reconcile the product's outside scratch roots at every tool-server start (MIK-R207 rule 5).

    A root whose generation is proven ended is deleted; every other root is reported. The scan
    runs in its own daemon thread so it cannot delay or break the stdio handshake, and a failure
    is logged: a scratch sweep is never a reason for a server not to start.
    """

    try:
        threading.Thread(
            target=_reconcile_outside_scratch_roots,
            name="outside-scratch-reconcile",
            daemon=True,
        ).start()
    except Exception:  # a sweep that cannot even start is logged, never raised
        logger.exception("outside scratch reconcile could not start at tool-server discovery")


def _reconcile_outside_scratch_roots() -> None:
    try:
        report = reconcile_outside_scratch_roots()
    except Exception:
        logger.exception("outside scratch reconcile failed at tool-server start")
        return
    if report["removed"] or report["reported"]:
        logger.info(
            "outside scratch reconcile at tool-server start: %s",
            json.dumps(report, sort_keys=True, default=str),
        )


class _PostDiscoveryStartLatch:
    """The once-per-process latch shared by discovery and the tool-call fallback.

    One post-discovery start runs both optional pieces: the outside-scratch sweep always (MIK-R207)
    and dashboard supervision only when ``dashboard.autoStart`` is true. A holder, not a
    module-level ``global``: the latch is process state this owner mutates through its own
    instance, and it is deliberately not reset by test modules -- the product's server is one
    process with one start.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = False

    def start(self, config: McpRuntimeConfig | None) -> None:
        """Run the one post-discovery start; never raise and never block.

        Called from the event loop right after the first tool-list answer, and from the first
        tool call when a client never lists. The sweep starts even with no config (a bare test
        server); the dashboard start is skipped without one. Both launches are daemon threads,
        so this neither delays nor changes the answer that triggered it.
        """
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
        try:
            start_outside_scratch_reconcile()
        except Exception:  # a sweep failure must never fail or delay a call
            logger.exception("outside scratch reconcile could not start after discovery")
        if config is None:
            return
        try:
            maybe_autostart_dashboard(config)
        except Exception:  # a dashboard failure must never fail or delay a call
            return


_POST_DISCOVERY_START = _PostDiscoveryStartLatch()


def start_after_discovery(config: McpRuntimeConfig | None) -> None:
    """MIK-R204 v1 rule 3 as amended (80f71dff): one start right after discovery.

    The server calls this from the event loop after its first ``tools/list`` answer, and from its
    first tool call as the fallback for a client that never lists; the latch makes whichever
    arrives first the only start. It always starts the outside-scratch sweep (MIK-R207) and starts
    dashboard supervision only when the settings set ``dashboard.autoStart``.
    """
    _POST_DISCOVERY_START.start(config)
