"""MIK-R190 rule 9: the one check every send path to a seat reaches.

A message to a recipient that Paseo's usage report says is limited is held, with
the reason and the moment work may resume, and delivered after that moment
(rule 10). The fact comes from the product's one limit file (rule 8) and never
from chat text, an error message or a turn's failure kind.

The check sits here, in its own module, so that `role_message`, the developer's
tray send and a report to an architect all reach one place: a send path that skips
it is a defect, not a variant.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.kernel.primitives.control_vocabulary import (
    PROVIDER_LIMIT_REASON,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.observer.provider_limits import (
    ProviderLimits,
    limited_until,
    read_provider_limits,
)
from agents_remember.serving.paseo.paseo_plugin_files import read_role_agents
from agents_remember.serving.paseo.provider_limits import provider_limits_path, record_usage_reads

logger = logging.getLogger(__name__)

# Rule 9's held reason, and the fourth case MIK-R100 rule 4b's three gain.
# Rule 7's one failed-read line per tick names the batch's failures inside this bound.
_FAILED_READ_LINE_LIMIT = 500


def limited_until_for(
    config: McpRuntimeConfig, agent_id: str, now: datetime | None = None
) -> datetime | None:
    """The recipient's own reset time while it is limited, else ``None``."""

    path = provider_limits_path(config)
    if path is None:
        return None
    return limited_until(read_provider_limits(path), agent_id, now or datetime.now(UTC))


def provider_limit_hold(
    config: McpRuntimeConfig, agent_id: str, now: datetime | None = None
) -> dict[str, Any] | None:
    """Rule 9's hold for one recipient, or ``None`` when nothing is limited.

    The answer names the reason and `limitedUntil` as an ISO time with offset, so
    the sender learns when the recipient can take a message instead of sending
    again into a limit.
    """

    until = limited_until_for(config, agent_id, now)
    if until is None:
        return None
    return {
        "heldReason": PROVIDER_LIMIT_REASON,
        "limitedUntil": until.isoformat(),
        "detail": (
            f"The recipient's provider usage limit lasts until {until.isoformat()}; "
            "the message is held and delivered after that moment, in the order it was taken."
        ),
    }


# The leaf seats, which are the roles whose stale entries the bridge refreshes.
_LEAF_ROLES = frozenset({"worker", "reviewer", "curator"})


def read_usage_for_agents(config: McpRuntimeConfig, agent_ids: list[str]) -> ProviderLimits | None:
    """Rule 7: one bridge process per tick that saw a new stamped end.

    The read comes before that tick's held delivery to the same agents, so the
    check each send makes (rule 9) already sees the end's own fact. One agent's
    failure is its own: it changes nothing for that agent and stops no other.
    """

    if not agent_ids:
        return None
    path = provider_limits_path(config)
    if path is None:
        return None
    records = read_role_agents(path.parent.parent)
    leaf = [
        agent_id
        for agent_id in agent_ids
        if _LEAF_ROLES & {records.get(agent_id, {}).get("role", "")}
    ]
    try:
        reply = bridge_call(
            config,
            "usage-reports",
            {"agentIds": agent_ids, "leafAgentIds": leaf},
        )
    except PaseoBridgeFailure as error:
        # A failed or timed-out read writes one log line and changes no fact.
        logger.warning("provider usage read failed: %s", error)
        return None
    reads: dict[str, object] = {}
    failed: list[str] = []
    for row in reply.get("reports") or []:
        if not isinstance(row, dict) or "agentId" not in row:
            continue
        if "error" in row:
            failed.append(f"{row['agentId']}: {row['error']}")
            continue
        reads[str(row["agentId"])] = row.get("entries")
    if failed:
        # Rule 7 bounds failed and timed-out read logging to one line per tick, and one
        # batch is one tick: the failures are named once, together, however many agents
        # they were. Each agent's own failure still changes nothing for it, and the
        # successes below are recorded exactly as they would be alone.
        logger.warning(
            "provider usage read failed for %d of %d agent(s): %s",
            len(failed),
            len(failed) + len(reads),
            "; ".join(failed)[:_FAILED_READ_LINE_LIMIT],
        )
    if not reads:
        return None
    return record_usage_reads(config, reads)
