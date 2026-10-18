"""Event-driven intake of the AR plugin's single host-activity publication.

Only startup and filesystem invalidation call this reader. A publication from before
dashboard startup is unusable until the existing bridge reconciles that plugin boot.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.runtime_config import ConfigError, McpRuntimeConfig
from agents_remember.observer.host_activity import (
    HOST_ACTIVITY_FILE,
    HostActivitySnapshot,
    TaskAgentActivity,
)
from agents_remember.observer.host_activity import (
    read_host_activity_publication as _read_publication,
)
from agents_remember.serving.paseo.paseo_plugin_files import AR_HOME_DIRECTORY, read_role_agents

logger = logging.getLogger(__name__)
type HostActivityReconcile = Callable[[McpRuntimeConfig], dict[str, Any]]


def activity_directory(config: McpRuntimeConfig) -> Path | None:
    try:
        runtime = config.paseo_runtime
    except ConfigError:
        return None
    return runtime.home / AR_HOME_DIRECTORY if runtime is not None else None


# The published states that mean no turn runs: the host writes one when a turn ends.
_NO_TURN_STATES = frozenset({"idle", "ended"})
# How many agent stamps one caller keeps between looks; the memory of a reader, bounded.
_TURN_END_SEEN_LIMIT = 4096


def agent_in_turn(config: McpRuntimeConfig, agent_id: str) -> bool:
    """Whether the host's own activity record says this agent is in a turn now.

    A message is never sent into a running turn (MIK-R171 rule 2), and the agent snapshot
    a bridge reads can lag a turn that the host itself has already published. This is the
    second view the send decision weighs: a complete publication that names the agent as
    ``in-turn`` holds the message, and the publication is the same record rule 1's turn
    end is read from, so the two decisions cannot disagree about one turn.
    """

    directory = activity_directory(config)
    if directory is None:
        return False
    publication = _read_publication(directory / HOST_ACTIVITY_FILE)
    if publication is None or not publication.complete:
        return False
    activity = publication.agents.get(agent_id)
    return activity is not None and activity.state == "in-turn"


def turn_ended_agents(config: McpRuntimeConfig, seen: dict[str, str]) -> list[str]:
    """The agents whose turn the host's activity record reports ended since the last look.

    This is the driver MIK-R171 rule 1 names: a state of ``idle`` or ``ended`` means no
    turn runs, and its ``eventAt`` is when the turn ended. ``seen`` carries each agent's
    stamp between looks, so one turn end is handed over once. The record is read as it
    is, without the boot reconciliation the projection does: a delivery needs the latest
    word only, and nothing here is sent or resumed.
    """

    directory = activity_directory(config)
    if directory is None:
        return []
    publication = _read_publication(directory / HOST_ACTIVITY_FILE)
    if publication is None or not publication.complete:
        return []
    ended: list[str] = []
    for agent_id, activity in publication.agents.items():
        if activity.state not in _NO_TURN_STATES:
            continue
        stamp = activity.eventAt.isoformat() if activity.eventAt is not None else ""
        if stamp and seen.get(agent_id) == stamp:
            continue
        if stamp:
            if len(seen) >= _TURN_END_SEEN_LIMIT:
                seen.clear()
            seen[agent_id] = stamp
        ended.append(agent_id)
    return ended


class HostActivityInput:
    """One retained boot identity; neither a timer nor a second host-state store."""

    def __init__(
        self,
        reconcile: HostActivityReconcile | None = None,
        supervisor_armed: Callable[[], bool] | None = None,
    ) -> None:
        self._reconcile = reconcile
        self._supervisor_armed = supervisor_armed
        self._started = False
        self._attempted_generation: tuple[int, int, int, int] | None = None
        self._reconciled_boot: str | None = None

    def read(self, config: McpRuntimeConfig) -> HostActivitySnapshot:
        directory = activity_directory(config)
        if directory is None or self._supervisor_armed is None or not self._supervisor_armed():
            return HostActivitySnapshot()
        path = directory / HOST_ACTIVITY_FILE
        publication = _read_publication(path)
        try:
            stat = path.stat()
            generation = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns)
        except OSError:
            generation = None
        boot = publication.bootId if publication is not None else None
        if not self._started or (
            boot != self._reconciled_boot and generation != self._attempted_generation
        ):
            self._started = True
            self._attempted_generation = generation
            self._reconciled_boot = None
            try:
                if self._reconcile is None:
                    raise RuntimeError("host activity reconciliation was not supplied")
                reply = self._reconcile(config)
                publication = _read_publication(directory / HOST_ACTIVITY_FILE)
                acknowledged = reply.get("publication")
                if (
                    publication is not None
                    and isinstance(acknowledged, dict)
                    and acknowledged.get("bootId") == publication.bootId
                    and publication.complete
                ):
                    self._reconciled_boot = publication.bootId
                    self._attempted_generation = generation
            except RuntimeError as error:
                logger.warning("Host activity is unknown: %s", error)
        if (
            publication is None
            or not publication.complete
            or publication.bootId != self._reconciled_boot
        ):
            return HostActivitySnapshot()
        records = read_role_agents(directory.parent)
        agents = tuple(
            TaskAgentActivity(records[agent_id]["task"], activity)
            for agent_id, activity in publication.agents.items()
            if agent_id in records
        )
        return HostActivitySnapshot(
            known=True,
            agents=agents,
            ignored_agents=publication.ignoredEvents
            + len(publication.agents.keys() - records.keys()),
        )
