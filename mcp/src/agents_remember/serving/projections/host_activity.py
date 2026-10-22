"""Event-driven intake of the AR plugin's single host-activity publication.

Only startup and filesystem invalidation call this reader. A publication from before
dashboard startup is unusable until the existing bridge reconciles that plugin boot.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from agents_remember.kernel.primitives.runtime_config import ConfigError, McpRuntimeConfig
from agents_remember.observer.host_activity import (
    HOST_ACTIVITY_FILE,
    HostActivitySnapshot,
    TaskAgentActivity,
)
from agents_remember.observer.host_activity import (
    read_host_activity_publication as _read_publication,
)
from agents_remember.observer.provider_limits import read_provider_limits_file
from agents_remember.serving.paseo.paseo_plugin_files import (
    AR_HOME_DIRECTORY,
    read_role_agents,
    role_agents_path,
)

logger = logging.getLogger(__name__)
type HostActivityReconcile = Callable[[McpRuntimeConfig], dict[str, Any]]
# MIK-R190 rule 7's one usage read per tick, as the composition root binds it: the read is a
# bridge process, and the bridge's caller ranks above this package (rule 19).
type UsageReadPort = Callable[[McpRuntimeConfig, list[str]], object]


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


class _ParsedPublication(NamedTuple):
    """One publication file as the delivery tick reads it, parsed once per content.

    ``endings`` are the agents the publication reports as not in a turn, with their own
    stamps; ``idle`` are the agents it reports ``idle`` with a stamp, which is what
    MIK-R190 rule 7's new stamped end is. Both come from one parse because both are the
    same file: the 3 s tick reads it for two decisions (MIK-R179 rule 2).
    """

    stamp: tuple[int, int, int, int]
    endings: tuple[tuple[str, str], ...]
    idle: tuple[tuple[str, str], ...]


# The last parsed publication per file, with the stamp it was parsed at. The 3 s
# held-delivery tick used to read and parse the host's publication every time even when the
# host had written nothing; one stat answers that. A few files, never a growing map.
_ENDINGS: dict[str, _ParsedPublication] = {}
_ENDINGS_LIMIT = 4
# The same for the role record beside it: the tick's other input, stat-bound the same way.
_ROLE_RECORDS: dict[str, tuple[tuple[int, int, int, int], dict[str, dict[str, str]]]] = {}
_ROLE_RECORDS_LIMIT = 4


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
    parsed = _parsed_publication(directory / HOST_ACTIVITY_FILE)
    if parsed is None:
        return []
    ended: list[str] = []
    for agent_id, stamp in parsed.endings:
        if stamp and seen.get(agent_id) == stamp:
            continue
        if stamp:
            if len(seen) >= _TURN_END_SEEN_LIMIT:
                seen.clear()
            seen[agent_id] = stamp
        ended.append(agent_id)
    return ended


def new_stamped_ends(config: McpRuntimeConfig, seen: dict[str, str]) -> list[str]:
    """MIK-R190 rule 7's trigger: the agents whose idle end this plugin boot has not read.

    A read of usage follows a **new, stamped** end only: the agent is in the role
    record, its published state is ``idle`` (not ``ended``), and its ``eventAt`` is
    present and differs from the one last read for it. An agent handed over without
    a stamp — an idle agent after a plugin boot — triggers nothing, which keeps the
    tick's cost at zero after every reload. ``seen`` is this reader's own memory;
    the delivery's own stamps live in the caller's other map.

    Both of this reader's inputs are the tick's inputs too, so it shares their stat-bound
    parse (``_parsed_publication``, ``_role_records``) instead of reading them again.
    """

    directory = activity_directory(config)
    if directory is None:
        return []
    parsed = _parsed_publication(directory / HOST_ACTIVITY_FILE)
    if parsed is None:
        return []
    records = _role_records(directory.parent)
    fresh: list[str] = []
    for agent_id, stamp in parsed.idle:
        if agent_id not in records:
            continue
        if seen.get(agent_id) == stamp:
            continue
        if len(seen) >= _TURN_END_SEEN_LIMIT:
            seen.clear()
        seen[agent_id] = stamp
        fresh.append(agent_id)
    return fresh


def _file_stamp(path: Path) -> tuple[int, int, int, int] | None:
    """One file's identity and change stamp; ``None`` when it is not readable."""

    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _parsed_publication(path: Path) -> _ParsedPublication | None:
    """The publication parsed once per content, for every reader of the same tick.

    The tick's own input is the host's file, and while that file has not changed there is
    no new input to read: one stat answers it. ``None`` is a missing, unreadable or
    incomplete publication -- nothing to act on, and nothing kept for it.
    """

    stamp = _file_stamp(path)
    key = path.as_posix()
    if stamp is None:
        _ENDINGS.pop(key, None)
        return None
    kept = _ENDINGS.get(key)
    if kept is not None and kept.stamp == stamp:
        return kept
    publication = _read_publication(path)
    if publication is None or not publication.complete:
        _ENDINGS.pop(key, None)
        return None
    endings = tuple(
        sorted(
            (
                agent_id,
                activity.eventAt.isoformat() if activity.eventAt is not None else "",
            )
            for agent_id, activity in publication.agents.items()
            if activity.state in _NO_TURN_STATES
        )
    )
    idle = tuple(
        sorted(
            (agent_id, activity.eventAt.isoformat())
            for agent_id, activity in publication.agents.items()
            if activity.state == "idle" and activity.eventAt is not None
        )
    )
    parsed = _ParsedPublication(stamp=stamp, endings=endings, idle=idle)
    while len(_ENDINGS) >= _ENDINGS_LIMIT:
        _ENDINGS.pop(next(iter(_ENDINGS)))
    _ENDINGS[key] = parsed
    return parsed


def _role_records(home: Path) -> dict[str, dict[str, str]]:
    """The role record parsed once per content, and stat-bound like the publication."""

    path = role_agents_path(home)
    stamp = _file_stamp(path)
    key = path.as_posix()
    if stamp is None:
        _ROLE_RECORDS.pop(key, None)
        return {}
    kept = _ROLE_RECORDS.get(key)
    if kept is not None and kept[0] == stamp:
        return kept[1]
    records = read_role_agents(home)
    while len(_ROLE_RECORDS) >= _ROLE_RECORDS_LIMIT:
        _ROLE_RECORDS.pop(next(iter(_ROLE_RECORDS)))
    _ROLE_RECORDS[key] = (stamp, records)
    return records


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
        # L184-R1-F08: the reconciliation this input last applied. An answer that began earlier
        # than that one is an older reply arriving late, and it cannot certify a boot.
        self._reconciled_reconcile: int | None = None

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
                answered = acknowledged.get("reconcile") if isinstance(acknowledged, dict) else None
                # F08: the answer names the reconciliation it began. A number older than the one
                # already applied is a reply that lost the race, whatever the file now says, so it
                # does not certify this boot and the next read asks again.
                stale = (
                    isinstance(answered, int)
                    and self._reconciled_reconcile is not None
                    and answered < self._reconciled_reconcile
                )
                if (
                    publication is not None
                    and isinstance(acknowledged, dict)
                    and acknowledged.get("bootId") == publication.bootId
                    and publication.complete
                    and not stale
                ):
                    self._reconciled_boot = publication.bootId
                    if isinstance(answered, int):
                        self._reconciled_reconcile = answered
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
            TaskAgentActivity(
                records[agent_id]["task"],
                activity,
                agent_id=agent_id,
                role=records[agent_id].get("role"),
            )
            for agent_id, activity in publication.agents.items()
            if agent_id in records
        )
        return HostActivitySnapshot(
            known=True,
            agents=agents,
            ignored_agents=publication.ignoredEvents
            + len(publication.agents.keys() - records.keys()),
            # MIK-R190 rule 13: the one limited-fact file, read beside the publication.
            limits=read_provider_limits_file(directory),
        )


def running_subagents(config: McpRuntimeConfig, agent_id: str) -> int:
    """MIK-R190 rule 4: the running provider subagents the publication shows for an agent.

    The other readers of "works" keep the turn meaning, except that a seat whose own
    subagent runs is retired or replaced only with the word that its work may be
    interrupted: archiving an agent cancels its running subagents, so the refusal has
    to name them rather than let them go silently.
    """

    directory = activity_directory(config)
    if directory is None:
        return 0
    publication = _read_publication(directory / HOST_ACTIVITY_FILE)
    if publication is None or not publication.complete:
        return 0
    activity = publication.agents.get(agent_id)
    if activity is None or activity.state == "ended":
        return 0
    return activity.runningSubagents or 0
