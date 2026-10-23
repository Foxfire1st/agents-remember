"""Event-driven intake of the five native attention facts (MIK-R200 rules 2-6 and 9).

Every read here is a file the product already writes, on an invalidation the watcher already
carries, or on the observer heartbeat -- and a file-backed kind is read only when its owning
domain was invalidated or when this reader has never read it (L190-R1-F11). Nothing polls the
host, nothing enumerates every agent for each task, and nothing touches a store it was not named
in: the held-message authority names the held rows worth a visit, the activation directory holds
one small record per series contract, and the notice store is one bounded file.

Every kind keeps its last *complete* observation. An unreadable host publication, an unreadable
hold store or a torn notice file resolves nothing: the last known occurrence stays visible and is
reconciled on the next complete read, so neither a false clearing nor a stuck item is admitted.

Two facts are joined rather than read: the permission waits the host publication names, and the
stall clock. Both are derived from the retained snapshot on every pass, which costs no I/O. The
stall clock is one continuous interval (L190-R1-F04): a task that stops being eligible -- bound
work, an explicit pause, an unknown observation, a new native boot -- loses its clock, and a
task-event stamp never dates an interval this reader did not watch, and the lineage that decides
whether a pause broke the interval is the *canonical* parent relation the task list itself serves
(L190-R1-F04) -- one relation, read by the clock and by the row builder, never a second one built
from declared parent text.
"""

from __future__ import annotations

import heapq
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from agents_remember.controlplane.attention_notices import (
    AttentionNoticeStore,
    read_notice_store_state,
    settle_terminal_notices,
)
from agents_remember.controlplane.held_store_discovery import ENUMERATION_BUDGET, DirectoryWalk
from agents_remember.controlplane.held_store_layout import HeldAuthorityReader
from agents_remember.models.structural.atomic_series_activation import (
    AtomicSeriesActivationRecord,
)
from agents_remember.observer.host_activity import HostActivitySnapshot
from agents_remember.observer.native_attention import (
    MAX_HELD_ITEMS,
    MAX_PAUSE_RECORDS,
    MAX_PERMISSION_ITEMS,
    MAX_STALL_CLOCKS,
    PARSED_FILE_BUDGET,
    HeldMessage,
    NativeAttentionFacts,
    PermissionWait,
    task_lineage,
)
from agents_remember.observer.task_entries import TaskEntries, agent_works
from agents_remember.worktrees.terminal_notice_recovery import verify_pending_notice
from pydantic import ValidationError

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

logger = logging.getLogger(__name__)

_STAGES_STARTED = "inProgress"
"""The authored status a stall is eligible at (rule 4): a started task and nothing else."""

#: Invalidation tokens for the file-backed kinds. A pass names the domains that actually
#: changed; a kind whose token is absent keeps the facts it already retained.
HELD_DOMAIN = "attention-held"
CONTROLPLANE_DOMAIN = "attention-controlplane"

#: Directory entries one refresh may examine. L190-R1-F11: a capped heap still visits and stats
#: every entry of the directory to find its newest members, so one refresh cost time proportional
#: to the whole population. A crowded directory is now walked across refreshes -- the walk parks
#: where the budget stopped and resumes there -- and a partial walk publishes nothing.


class _Listing(NamedTuple):
    """What one whole directory walk saw: the kept records and how many existed in all."""

    entries: tuple[Path, ...]
    total: int


class _PauseWalk:
    """One activation directory's walk, bounded per refresh and carried across refreshes.

    The accumulator is bounded twice: the walk keeps the newest :data:`MAX_PAUSE_RECORDS` records
    by stamp, and one refresh examines at most :data:`ENUMERATION_BUDGET` entries, so a directory
    that grows without bound costs work proportional to the budget rather than to its population.
    ``total`` counts the records the walk has met, which is what lets a pass say honestly that it
    did not see everything. Nothing is published until the walk completes: a partial walk is an
    incomplete observation, never a truncated listing presented as whole.
    """

    __slots__ = ("_kept", "_walk", "stamp", "total")

    def __init__(self, stamp: _Stamp) -> None:
        self.stamp = stamp
        self.total = 0
        self._walk: DirectoryWalk | None = None
        self._kept: list[tuple[int, str, str]] = []

    @property
    def done(self) -> bool:
        return self._walk is not None and self._walk.done

    @property
    def cursor(self) -> str | None:
        """The name of the last record examined: where a resumed walk stands."""

        return None if self._walk is None else self._walk.cursor

    def advance(self, directory: Path, limit: int) -> bool:
        """Examine at most :data:`ENUMERATION_BUDGET` entries; whether the walk completed."""

        if self._walk is None:
            self._walk = DirectoryWalk(directory)
        return self._walk.advance(ENUMERATION_BUDGET, lambda entry: self._keep(entry, limit))

    def _keep(self, entry: os.DirEntry[str], limit: int) -> None:
        if not entry.name.endswith(".json"):
            return
        self.total += 1
        try:
            entry_stat = entry.stat()
        except OSError:
            # A record that vanished mid-walk was not read; the count still saw it, so the
            # observation stays incomplete for this pass rather than pretending to be whole.
            return
        item = (entry_stat.st_mtime_ns, entry.name, entry.path)
        if len(self._kept) < limit:
            heapq.heappush(self._kept, item)
        elif item > self._kept[0]:
            heapq.heapreplace(self._kept, item)

    def listing(self) -> _Listing:
        """The records a whole walk would have kept, in name order for the caller's parse loop."""

        return _Listing(
            tuple(Path(path) for _, _, path in sorted(self._kept, key=lambda item: item[1])),
            self.total,
        )

    def close(self) -> None:
        if self._walk is not None:
            self._walk.close()


def _turn_ends(host: HostActivitySnapshot) -> dict[str, tuple[str, ...]]:
    """The published turn-end stamps of the agents bound to each task (MIK-R200@v2 rule 7).

    ``eventAt`` is the agent's own turn transition, so the latest stamp a task's bound agents
    published is when that task's idle interval began. It is the one stall identity a restarted
    reader derives again from the same publication, and it moves when a turn ends, which is what
    makes the next interval a different occurrence. A seat the publication gives no transition
    for contributes nothing rather than a guessed stamp.
    """

    stamps: dict[str, set[str]] = {}
    for seat in host.agents:
        stamp = seat.activity.eventAt
        if stamp is None or not seat.task_key:
            continue
        stamps.setdefault(seat.task_key, set()).add(stamp.isoformat())
    return {key: tuple(sorted(values)) for key, values in stamps.items()}


class _Stamp(NamedTuple):
    size: int
    mtime_ns: int
    inode: int


def _stamp(path: Path) -> _Stamp | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return _Stamp(info.st_size, info.st_mtime_ns, info.st_ino)


def _tokens(refreshed: frozenset[Any] | None) -> set[str] | None:
    """Normalise the pass's changed domains, whatever the caller's enum spells them as."""

    if refreshed is None:
        return None
    return {str(getattr(item, "value", item)).strip().lower() for item in refreshed}


class NativeAttentionInput:
    """One retained view per native kind, refreshed from the files each kind is published in."""

    def __init__(self) -> None:
        self._permissions: tuple[PermissionWait, ...] = ()
        self._permissions_complete = False
        # Retained per-agent prompt identity: an incomplete identity observation keeps it.
        self._permission_rows: dict[str, tuple[PermissionWait, ...]] = {}
        self._held: tuple[HeldMessage, ...] = ()
        self._held_complete = False
        self._held_reader = HeldAuthorityReader()
        self._paused: frozenset[str] = frozenset()
        self._pauses_complete = False
        self._notices: tuple[Any, ...] = ()
        self._notices_complete = False
        self._stall_clocks: dict[str, str] = {}
        self._stall_boot: str | None = None
        self._parsed: dict[str, tuple[_Stamp, Any]] = {}
        self._seen: set[str] = set()
        # One walk per activation directory, kept until that directory itself changes: a walked
        # directory is re-listed from it, and a walk the budget stopped resumes from it.
        self._pause_walks: dict[str, _PauseWalk] = {}

    def read(
        self,
        config: McpRuntimeConfig,
        *,
        host: HostActivitySnapshot,
        task_entries: TaskEntries,
        now: datetime,
        refreshed: frozenset[Any] | None = None,
    ) -> NativeAttentionFacts:
        """Refresh the kinds whose inputs changed, then return one bounded facts bundle.

        ``refreshed`` names the domains a pass saw change. ``None`` refreshes everything,
        which is what a first pass and a caller with no invalidation information means.
        """

        changed = _tokens(refreshed)
        self._read_permissions(host)
        if self._due("held", changed, HELD_DOMAIN):
            self._read_held(config, host)
        if self._due("pauses", changed, CONTROLPLANE_DOMAIN):
            self._read_pauses(config)
        if self._due("notices", changed, CONTROLPLANE_DOMAIN):
            self._read_notices(config)
        # L190-R1-F04: the clock reads the canonical task list, so the relation that suppresses a
        # row and the relation that breaks its interval are the same one.
        self._read_stall_clocks(host, task_entries, now)
        return NativeAttentionFacts(
            permissions=self._permissions,
            permissions_complete=self._permissions_complete,
            held=self._held,
            held_complete=self._held_complete,
            paused_task_keys=self._paused,
            pauses_complete=self._pauses_complete,
            notices=self._notices,
            notices_complete=self._notices_complete,
            stall_clocks=dict(self._stall_clocks),
            turn_ends=_turn_ends(host),
        )

    def _due(self, kind: str, changed: set[str] | None, domain: str) -> bool:
        """Whether this pass reads a file-backed kind: first look, or its domain changed."""

        pending = (
            kind == "pauses" and any(not walk.done for walk in self._pause_walks.values())
        ) or (kind == "held" and self._held_reader.pending)
        if pending or kind not in self._seen or changed is None or domain in changed:
            self._seen.add(kind)
            return True
        return False

    # --- permissions (rule 2) ------------------------------------------------

    def _read_permissions(self, host: HostActivitySnapshot) -> None:
        if not host.known:
            # No prompt is resolved by an observation that could not be completed.
            self._permissions_complete = False
            return
        rows: dict[str, tuple[PermissionWait, ...]] = {}
        complete = True
        for seat in sorted(host.agents, key=lambda row: (row.agent_id, row.task_key)):
            activity = seat.activity
            if activity.state != "permission":
                # The agent left the wait, which ends every prompt it was waiting on.
                continue
            ids = tuple(activity.permissionPrompts)
            if not ids:
                # L190-R1-F03: an identity the publication does not name proves neither the
                # prompt's identity nor its end. Inventing one ("unknown", a timestamp) would
                # publish an occurrence that does not exist, so the last known identity for
                # this agent is kept and the observation is not called whole.
                known = self._permission_rows.get(seat.agent_id)
                if known:
                    rows[seat.agent_id] = known
                complete = False
                continue
            rows[seat.agent_id] = tuple(
                PermissionWait(
                    agent_id=seat.agent_id,
                    prompt_id=prompt_id,
                    task_key=seat.task_key,
                    role=seat.role,
                    since=activity.eventAt,
                )
                for prompt_id in ids
            )
        self._permission_rows = rows
        waits = [wait for agent_id in sorted(rows) for wait in rows[agent_id]]
        self._permissions = tuple(waits[:MAX_PERMISSION_ITEMS])
        self._permissions_complete = complete

    # --- held messages (rule 3) ----------------------------------------------

    def _read_held(self, config: McpRuntimeConfig, host: HostActivitySnapshot) -> None:
        """L190-R1-F02: the held stores are the authority, not the delivery queue's entries.

        The authority reader names candidate stores from every receipt the queue still lists
        (expired ones included) and from its own resumable walk of the execution receipts, so a
        store the queue has aged out, evicted or dropped is still read. A row leaves the view
        only when it leaves its own store -- delivery or withdrawal -- and an incomplete view
        keeps each store's own last reading rather than a merged global set.
        """

        view = self._held_reader.read(config.coordination_root)
        roles = {seat.agent_id: (seat.role, seat.task_key) for seat in host.agents}
        rows: list[HeldMessage] = []
        for row in view.rows:
            role, task_key = roles.get(row.recipient_agent_id, (None, None))
            rows.append(
                HeldMessage(
                    recipient_agent_id=row.recipient_agent_id,
                    message_id=row.message_id,
                    sender_agent_id=row.sender_agent_id,
                    taken_at=row.taken_at,
                    reason=row.reason,
                    limited_until=row.limited_until,
                    task_key=task_key,
                    role=role,
                )
            )
        # L190-R1-F02: the view retains each store's own last reading, so this reader publishes
        # exactly what it returned. Merging a previous global set back in on an incomplete pass
        # resurrected rows whose stores had been read empty -- a delivered message reappearing
        # because some *other* store was torn. An incomplete view keeps the rows it could read
        # and the last rows of the stores it could not, per store, and says it is incomplete.
        self._held = tuple(rows[:MAX_HELD_ITEMS])
        self._held_complete = view.complete

    # --- explicit pause (rule 4) ---------------------------------------------

    def _read_pauses(self, config: McpRuntimeConfig) -> None:
        directory = config.coordination_root / "controlplane" / "atomic-series-activation"
        listing = self._activation_entries(directory)
        if listing is None:
            # L190-R1-F11: a walk the enumeration budget stopped is not an observation. The last
            # published set stays visible -- a partial population never replaces a whole one.
            self._pauses_complete = False
            return
        # An enumeration that had to stop short cannot claim the absence of a pause it did not
        # read, so it is an incomplete observation rather than a confident empty set.
        complete = listing.total <= MAX_PAUSE_RECORDS
        paused: set[str] = set()
        for path in listing.entries:
            record = self._activation_record(path)
            if record is None:
                # An unreadable record proves no pause and no absence of one: the last known
                # set is retained rather than read as "no master is paused".
                complete = False
                continue
            if record.state == "vacant" and record.pausedAt is not None:
                paused.add(record.selectedMaster.key)
        self._pauses_complete = complete
        if complete:
            self._paused = frozenset(paused)

    def _activation_entries(self, directory: Path) -> _Listing | None:
        """The directory's listing, walked within one refresh's enumeration budget.

        L190-R1-F11: a crowded directory is enumerated across refreshes. The walk retains its
        place and its bounded heap, keyed on the directory's own stamp, so an unchanged directory
        that has been walked whole is re-listed from that result without being read again. A walk
        the budget stopped returns ``None``: the caller keeps the listing it last published and
        calls the observation incomplete, rather than publishing part of a population it has not
        met. The walk keeps a bounded heap of the newest ``MAX_PAUSE_RECORDS`` and counts the rest,
        so a completed listing is the one a whole walk would have produced.
        """

        try:
            info = directory.stat()
        except FileNotFoundError:
            # No directory means nothing has ever been selected: honestly empty, not unknown.
            self._discard_pause_walk(directory)
            return _Listing((), 0)
        except OSError:
            self._discard_pause_walk(directory)
            return None
        stamp = _Stamp(info.st_size, info.st_mtime_ns, info.st_ino)
        key = directory.as_posix()
        walk = self._pause_walks.get(key)
        if walk is None or walk.stamp != stamp:
            # A directory that changed is a different population: its partial walk is not mixed in.
            self._discard_pause_walk(directory)
            walk = self._pause_walks[key] = _PauseWalk(stamp)
        if walk.done:
            return walk.listing()
        try:
            walk.advance(directory, MAX_PAUSE_RECORDS)
        except OSError:
            self._discard_pause_walk(directory)
            return None
        return walk.listing() if walk.done else None

    def _discard_pause_walk(self, directory: Path) -> None:
        """Forget one directory's walk, releasing the scan it holds."""

        walk = self._pause_walks.pop(directory.as_posix(), None)
        if walk is not None:
            walk.close()

    def _activation_record(self, path: Path) -> AtomicSeriesActivationRecord | None:
        parsed = self._parsed_file(path, AtomicSeriesActivationRecord)
        return parsed if isinstance(parsed, AtomicSeriesActivationRecord) else None

    # --- terminal notices (rules 5 and 6) ------------------------------------

    def _read_notices(self, config: McpRuntimeConfig) -> None:
        def verify(row: Any) -> bool | None:
            return verify_pending_notice(config.coordination_root, row)

        settled = settle_terminal_notices(config.coordination_root, verify)
        if settled is not None:
            store: AttentionNoticeStore | None = settled
        else:
            # The store itself could not be observed: an unreadable store is unknown, never
            # an empty complete one (L190-R1-F07).
            observed = read_notice_store_state(config.coordination_root)
            if observed.state == "unreadable" or observed.store is None:
                self._notices_complete = False
                return
            store = observed.store
        self._notices = tuple(store.published())
        self._notices_complete = True

    # --- stall clocks (rule 4) -----------------------------------------------

    def _read_stall_clocks(
        self,
        host: HostActivitySnapshot,
        task_entries: TaskEntries,
        now: datetime,
    ) -> None:
        """Keep one continuous eligible-silence clock per started task (L190-R1-F04).

        The interval is broken -- and starts again from this observation -- when the station
        that owns it changes: an unknown host observation, a different native boot, bound work
        appearing on the task, or an explicit pause anywhere on its lineage. Only an interval
        this reader itself watched, end to end, can reach the settled deadline.
        """

        if not host.known:
            self._stall_clocks = {}
            self._stall_boot = None
            return
        if host.boot_id != self._stall_boot:
            self._stall_clocks = {}
            self._stall_boot = host.boot_id
        working: dict[str, bool] = {}
        for seat in host.agents:
            working[seat.task_key] = working.get(seat.task_key, False) or agent_works(seat.activity)
        # The canonical relation, not the authored one: a listed child that names no master is
        # bound to the master whose listing holds it, and a pause on that master must break the
        # child's interval exactly as it hides the child's row (L190-R1-F04).
        parent_of = {task.key: task.parent for task in task_entries.tasks}
        started = {task.key for task in task_entries.tasks if task.stage == "started"}
        clocks: dict[str, str] = {}
        for key in sorted(started):
            if working.get(key, False):
                continue
            if task_lineage(key, parent_of) & self._paused:
                continue
            clocks[key] = self._stall_clocks.get(key) or now.isoformat()
            if len(clocks) >= MAX_STALL_CLOCKS:
                break
        self._stall_clocks = clocks

    # --- bounded parse cache --------------------------------------------------

    def _parsed_json(self, path: Path) -> dict[str, Any] | None:
        parsed = self._parsed_file(path, dict)
        return parsed if isinstance(parsed, dict) else None

    def _parsed_file(self, path: Path, kind: type | tuple[type, ...]) -> Any | None:
        stamp = _stamp(path)
        if stamp is None:
            self._parsed.pop(path.as_posix(), None)
            return None
        key = path.as_posix()
        kept = self._parsed.get(key)
        if kept is not None and kept[0] == stamp:
            return kept[1]
        try:
            text = path.read_text(encoding="utf-8")
            value: Any = (
                json.loads(text) if kind is dict else kind.model_validate_json(text)  # type: ignore[union-attr]
            )
        except (OSError, json.JSONDecodeError, ValidationError):
            self._parsed.pop(key, None)
            return None
        while len(self._parsed) >= PARSED_FILE_BUDGET:
            self._parsed.pop(next(iter(self._parsed)))
        self._parsed[key] = (stamp, value)
        return value
