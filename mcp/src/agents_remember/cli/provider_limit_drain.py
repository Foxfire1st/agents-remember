"""MIK-R190 rule 10: what a held-message drain owes a limited recipient.

A message to a recipient whose provider usage report is exhausted waits out the reset
without a routine host read. A recipient the publication shows as ``ended`` gets one
host read per stamp. A confirmed archive or absence ends its waiting messages. A
closed session keeps them waiting.

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
    _end_batch,
    _read_store_file,
    _release_claim,
    _write_store_file,
    store_path_for,
)
from agents_remember.cli.role_held_store import open_held_store
from agents_remember.cli.role_launch_receipts import is_deferred_held_receipt
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

    An ``ended`` publication triggers one host confirmation per stamp. A confirmed
    archive or absence ends the waiting messages as not delivered. A closed session
    keeps them waiting for the provider reset without another read for that stamp.
    """

    # L58-R3/R7: a reserved seat has no host agent or usage report yet and is never resolved absent.
    if is_deferred_held_receipt(batch.receipt):
        return
    limit = provider_limit_hold(batch.config, batch.agent_id)
    if limit is None:
        return
    stamp = ended_stamp(batch.config, batch.agent_id)
    if stamp is not None and claim_ended_stamp(batch, stamp):
        reading = read_agent(batch.config, batch.agent_id)
        if not reading.reachable:
            _release_claim(batch.ref, batch.claim)
            raise _DrainPause(
                {"outcome": "host-unreachable", "detail": str(reading.unreachable_reason)}
            )
        if not reading.found or reading.archived:
            ended = _end_batch(batch, host_state="archived" if reading.archived else "gone")
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
    _release_claim(batch.ref, batch.claim)
    raise _DrainPause(
        {
            "outcome": PROVIDER_LIMIT_REASON,
            "limitedUntil": limit["limitedUntil"],
            "detail": limit["detail"],
        }
    )


def ended_stamp(config: McpRuntimeConfig, agent_id: str) -> str | None:
    """Rule 10's stamp for an ``ended`` recipient: its own `eventAt`, else the boot id.

    Host confirmation of the recipient's state requires this stamp. If the publication
    carries no event time, the boot ID bounds confirmation to one read in that boot.
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
    with open_held_store(batch.ref):
        store = _read_store_file(path, batch.agent_id)
        if store.get(PROVIDER_LIMIT_STAMP_KEY) == stamp:
            return False
        store[PROVIDER_LIMIT_STAMP_KEY] = stamp
        _write_store_file(path, store)
    return True
