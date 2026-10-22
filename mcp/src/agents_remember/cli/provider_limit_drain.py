"""MIK-R190 rule 10: what a held-message drain owes a limited recipient.

A message to a recipient whose provider usage report is exhausted waits out the reset,
and waiting out a limit must cost no host read. The one read a drain still owes is for
a recipient the publication shows ``ended``: an archive ends its waiting messages, a
closed session keeps them, and that reading is paid once per ``ended`` stamp.

Both decisions are policy, so both live here rather than in the held-message store's
own module (rule 19): that module owns the store and the drain, this one decides when
they wait. The order is part of the rule -- the limit comes first and costs no host
read; only then, and only for a stamped ``ended`` recipient, is one reading taken.
"""

from __future__ import annotations

from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.provider_limit_hold import (
    PROVIDER_LIMIT_REASON,
    provider_limit_hold,
)
from agents_remember.cli.role_held_messages import (
    _END_NOT_DELIVERED,
    _DrainBatch,
    _DrainPause,
    _read_store_file,
    _release_claim,
    _write_store_file,
    end_waiting_as,
    store_path_for,
)
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.observer.host_activity import (
    HOST_ACTIVITY_FILE,
    read_host_activity_publication,
)
from agents_remember.serving.projections.host_activity import activity_directory

# MIK-R190 rule 10: the `ended` stamp this store already paid one host read for.
PROVIDER_LIMIT_STAMP_KEY = "providerLimitEndedStamp"


def pause_for_provider_limit(batch: _DrainBatch) -> None:
    """MIK-R190 rule 10: a limited recipient pauses the drain before any host read.

    One host read is still owed when the publication shows the recipient ``ended``,
    and then only once per ``ended`` stamp: an archive ends its messages as not
    delivered, as today, while a closed session keeps them waiting with no further
    read on later sweeps. A recipient the publication does not show as ended costs
    no bridge process at all, and a recipient that is not limited returns here.
    """

    limit = provider_limit_hold(batch.config, batch.agent_id)
    if limit is None:
        return
    stamp = ended_stamp(batch.config, batch.agent_id)
    if stamp is not None and claim_ended_stamp(batch, stamp):
        reading = read_agent(batch.config, batch.agent_id)
        if not reading.reachable:
            _release_claim(batch.folder, batch.agent_id, batch.claim)
            raise _DrainPause(
                {"outcome": "host-unreachable", "detail": str(reading.unreachable_reason)}
            )
        if not reading.found or reading.archived:
            ended = end_waiting_as(batch.folder, batch.agent_id)
            raise _DrainPause(
                {
                    "outcome": "recipient-gone",
                    "detail": (
                        "The recipient is archived or gone; its waiting messages end as "
                        "not delivered."
                    ),
                    "ended": ended,
                    "how": _END_NOT_DELIVERED,
                }
            )
    _release_claim(batch.folder, batch.agent_id, batch.claim)
    raise _DrainPause(
        {
            "outcome": PROVIDER_LIMIT_REASON,
            "limitedUntil": limit["limitedUntil"],
            "detail": limit["detail"],
        }
    )


def ended_stamp(config: McpRuntimeConfig, agent_id: str) -> str | None:
    """Rule 10's stamp for an ``ended`` recipient: its own `eventAt`, else the boot id.

    A closed session is published ``ended`` without an archive time, so the boot id
    stands in for it. A recipient the publication does not show as ended has no
    stamp, and its drain makes no host read.
    """

    directory = activity_directory(config)
    if directory is None:
        return None
    publication = read_host_activity_publication(directory / HOST_ACTIVITY_FILE)
    if publication is None:
        return None
    activity = publication.agents.get(agent_id)
    if activity is None or activity.state != "ended":
        return None
    return activity.eventAt.isoformat() if activity.eventAt is not None else publication.bootId


def claim_ended_stamp(batch: _DrainBatch, stamp: str) -> bool:
    """Take this stamp for one host read; ``False`` when a sweep already did."""

    path = store_path_for(batch.folder, batch.agent_id)
    with exclusive_file_lock(path, "held-message store"):
        store = _read_store_file(path, batch.agent_id)
        if store.get(PROVIDER_LIMIT_STAMP_KEY) == stamp:
            return False
        store[PROVIDER_LIMIT_STAMP_KEY] = stamp
        _write_store_file(path, store)
    return True
