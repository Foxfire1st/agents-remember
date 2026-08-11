"""Independent relay-death surfacing (N5): the relay never relays its own death.

The agent-notifier sweep cannot report its own failure -- if the loop is dead, nothing in it
can post. This dashboard-side watcher is deliberately NOT part of the notifier loop: it reads
the heartbeat row on its own cadence and, when the tick goes stale past the configured cutoff,
posts a durable ``degradation-alert`` row to the architect mailbox. It is bounded by the
append-only marker file (one post per stale heartbeat identity) and by the inbox store's own
D4 cap, and it is fail-safe on settings-read failure (default cutoff).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxOwner,
    InboxPoster,
    InboxRouting,
    InboxSubject,
    OperatorInboxEntry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.agentic_settings import (
    DEFAULT_AGENT_NOTIFIER_STALE_CUTOFF_SECONDS,
    load_agentic_settings,
)
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving._app_common import _ServingRuntime, logger
from agents_remember.serving.agent_notifier_heartbeat import heartbeat_age_seconds
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    InboxDeliveryLog,
    deliver_inbox_entry,
)

RELAY_DEATH_WATCH_INTERVAL_SECONDS = 30.0
"""How often the dashboard-side watcher re-checks the heartbeat row (independent cadence)."""

RELAY_DEATH_MARKER_FILENAME = "agent-notifier-death-watch.json"


@dataclass(frozen=True)
class RelayDeathMarker:
    """One post-per-stale-heartbeat marker: which tick was reported, and when/where."""

    lastTickAt: str
    entryId: str | None = None
    postedAt: str | None = None


class RelayDeathMarkerStore:
    """Tiny durable marker preventing a stale-heartbeat post storm across watcher restarts."""

    def __init__(self, observer_root: Path) -> None:
        self._path = observer_root / "workspace" / RELAY_DEATH_MARKER_FILENAME

    def read(self) -> RelayDeathMarker | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("lastTickAt"), str):
            return None
        return RelayDeathMarker(
            lastTickAt=data["lastTickAt"],
            entryId=data.get("entryId"),
            postedAt=data.get("postedAt"),
        )

    def write(self, marker: RelayDeathMarker) -> None:
        atomic_write_text(
            self._path,
            json.dumps(
                {
                    "lastTickAt": marker.lastTickAt,
                    "entryId": marker.entryId,
                    "postedAt": marker.postedAt,
                }
            )
            + "\n",
        )


def _stale_cutoff_seconds(coordination_root: Path) -> float:
    """The configured stale cutoff, falling back to the default when settings are unreadable."""
    try:
        return load_agentic_settings(coordination_root).agent_notifier.stale_cutoff_seconds
    except Exception:
        return DEFAULT_AGENT_NOTIFIER_STALE_CUTOFF_SECONDS


def post_relay_death_signal(runtime: _ServingRuntime, *, now: datetime) -> bool:
    """Post one architect-mailbox row when the heartbeat is stale; dedupe per tick identity.

    A heartbeat that never ticked is deliberately silent (the relay is opt-in, so absence is
    not evidence of death -- the same posture as the staleness banner). Returns whether a new
    row was posted this call.
    """
    heartbeat = runtime.heartbeat_store.read()
    if heartbeat is None:
        return False
    age = heartbeat_age_seconds(heartbeat, now=now)
    cutoff = _stale_cutoff_seconds(runtime.config.coordination_root)
    if age is None or age < cutoff:
        return False
    marker_store = RelayDeathMarkerStore(runtime.observer_root)
    marker = marker_store.read()
    if marker is not None and marker.lastTickAt == heartbeat.lastTickAt:
        return False
    entry = create_operator_inbox_entry(
        InboxMessage(
            ask="Agent notifier relay death: heartbeat stale",
            response=(
                f"last tick {heartbeat.lastTickAt} is {age / 60.0:.1f}m old "
                f"(past the {cutoff:.0f}s cutoff); the relay does not relay its own death"
            ),
            message_kind="degradation-alert",
            subject=InboxSubject(),
        ),
        entry_id=new_ulid(),
        now=now.isoformat(),
        routing=InboxRouting(
            address=InboxAddress(
                recipient_role="developer",
            ),
            owner=InboxOwner(role="developer"),
        ),
        poster=InboxPoster(
            created_by="agent-notifier-death-watch",
            created_via="cli",
            sender_role="system",
        ),
    )
    store = OperatorInboxStore(runtime.observer_root)
    store.append(entry)
    marker_store.write(
        RelayDeathMarker(
            lastTickAt=heartbeat.lastTickAt,
            entryId=entry.id,
            postedAt=now.isoformat(),
        )
    )
    _try_deliver(runtime, entry)
    return True


def _try_deliver(runtime: _ServingRuntime, entry: OperatorInboxEntry) -> None:
    """Best-effort push to a live architect seat; the durable row is the surface regardless."""
    try:
        deliver_inbox_entry(
            InboxDeliveryLog(store=OperatorInboxStore(runtime.observer_root), entry=entry),
            sessions=HostedSessionRuntime(catalog=runtime.catalog, host=runtime.host),
            paster=runtime.paster,
        )
    except Exception:
        return


async def relay_death_watch_loop(runtime: _ServingRuntime) -> None:
    """Dashboard-side watcher: independent of the agent-notifier loop, bounded cadence."""
    while True:
        await asyncio.sleep(RELAY_DEATH_WATCH_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(post_relay_death_signal, runtime, now=runtime.liveness_clock())
        except Exception:
            logger.exception("relay-death watch failed; retrying next interval")
