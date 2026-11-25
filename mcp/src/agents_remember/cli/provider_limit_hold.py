"""MIK-R190 rule 9: the one check every send path to a seat reaches.

A message to a recipient that Paseo's usage report says is limited is held, with
the reason and the recorded reset, and delivered when the hold clears.
The fact comes from the product's one limit file and never
from chat text, an error message or a turn's failure kind.

The check sits here, in its own module, so that `role_message`, the developer's
tray send and a report to an architect all reach one place: a send path that skips
it is a defect, not a variant.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
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
from agents_remember.serving.projections.host_activity import UsageReadBatch

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
            f"The recipient's provider usage limit is recorded until {until.isoformat()}. "
            "The message is held until the limit clears and delivered in the order it was taken."
        ),
    }


# The leaf seats, which are the roles whose stale entries the bridge refreshes.
_LEAF_ROLES = frozenset({"worker", "reviewer", "curator"})


def read_usage_for_agents(config: McpRuntimeConfig, batch: UsageReadBatch) -> ProviderLimits | None:
    """Read stamped ends and due limits in one bridge process before delivery."""

    path = provider_limits_path(config)
    if path is None:
        return None
    periodic = {report: ids for report, ids in batch.periodic_candidates.items() if ids}
    failed = [
        f"{report}: no mapped seat in the role record eligible for a read"
        for report, ids in batch.periodic_candidates.items()
        if not ids
    ]
    if not batch.stamped_agents and not periodic:
        _log_usage_failures(failed)
        return None
    records = read_role_agents(path.parent.parent) if batch.stamped_agents else {}
    leaf = [
        agent_id
        for agent_id in batch.stamped_agents
        if records.get(agent_id, {}).get("role", "") in _LEAF_ROLES
    ]
    try:
        reply = bridge_call(
            config,
            "usage-reports",
            {
                "agentIds": batch.stamped_agents,
                "leafAgentIds": leaf,
                "periodicCandidates": periodic,
            },
        )
    except PaseoBridgeFailure as error:
        _log_usage_failures([*failed, str(error)])
        return None
    outcomes = _periodic_outcomes(reply.get("periodic") or [])
    reads, failed_refreshes = _usage_report_rows(reply.get("reports") or [], failed)
    failed.extend(f"{report}: {error}" for report, error in outcomes.items() if error is not None)
    failed.extend(f"{report}: no usage reply" for report in periodic.keys() - outcomes.keys())
    failed.extend(
        f"{report}: forced refresh failed"
        for report in {report for ids in failed_refreshes.values() for report in ids}
        if not outcomes.get(report)
    )
    _log_usage_failures(failed)
    if not reads:
        return None
    return record_usage_reads(
        config,
        reads,
        failed_refreshes=failed_refreshes,
        preserved_reports=_preserved_periodic_mappings(batch, reads, outcomes),
    )


def _periodic_outcomes(rows: list[object]) -> dict[str, str | None]:
    return {
        report: str(row["error"]) if "error" in row else None
        for row in rows
        if isinstance(row, dict) and isinstance(report := row.get("reportId"), str)
    }


def _preserved_periodic_mappings(
    batch: UsageReadBatch,
    reads: Mapping[str, object],
    outcomes: Mapping[str, str | None],
) -> dict[str, frozenset[str]]:
    """Keep prior associations for due targets the batch could not read."""

    unresolved = frozenset(
        report
        for report, candidates in batch.periodic_candidates.items()
        if candidates and (report not in outcomes or outcomes[report] is not None)
    )
    if not unresolved:
        return {}
    # A usable sibling response can omit a failed report. An explicitly empty stamped
    # list is authoritative for the seat even when its periodic read was inconclusive.
    return {agent_id: unresolved for agent_id, entries in reads.items() if entries}


def _usage_report_rows(
    rows: list[object], failed: list[str]
) -> tuple[dict[str, object], dict[str, frozenset[str]]]:
    reads: dict[str, object] = {}
    failed_refreshes: dict[str, frozenset[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(agent_id := row.get("agentId"), str):
            continue
        if "error" in row:
            failed.append(f"{agent_id}: {row['error']}")
            continue
        if not isinstance(entries := row.get("entries"), list):
            failed.append(f"{agent_id}: no usage report data")
            continue
        reads[agent_id] = entries
        if isinstance(ids := row.get("failedRefreshReportIds"), list):
            failed_refreshes[agent_id] = frozenset(
                report for report in ids if isinstance(report, str)
            )
    return reads, failed_refreshes


def _log_usage_failures(failed: list[str]) -> None:
    if failed:
        logger.warning(
            "provider usage read failed: %s", "; ".join(failed)[:_FAILED_READ_LINE_LIMIT]
        )
