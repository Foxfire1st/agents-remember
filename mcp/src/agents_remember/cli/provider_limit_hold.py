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

import argparse
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.kernel.primitives.control_vocabulary import (
    PROVIDER_LIMIT_REASON,
)
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeNotConfigured,
    require_paseo_runtime,
)
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    load_paseo_runtime_settings,
)
from agents_remember.kernel.primitives.user_settings_location import user_settings_path
from agents_remember.observer.provider_limits import (
    ProviderLimits,
    limited_until,
    read_provider_limits,
)
from agents_remember.serving.paseo.paseo_plugin_files import read_role_agents
from agents_remember.serving.paseo.provider_limit_suppressions import (
    ProviderLimitCommandResult,
    SuppressionSelector,
)
from agents_remember.serving.paseo.provider_limits import (
    provider_limits_path,
    provider_limits_path_for_home,
    provider_limits_status,
    record_usage_reads,
    suppress_provider_limit_windows,
    unsuppress_provider_limit_windows,
)
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


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``provider-limits status|suppress|unsuppress`` CLI arguments."""

    config_help = (
        "Absolute path of the active MCP settings file that holds the paseoRuntime host block."
    )
    parser.add_argument("--config", dest="parent_config", default=None, help=config_help)
    sub = parser.add_subparsers(dest="provider_limits_command", required=True)

    status_cmd = sub.add_parser(
        "status",
        help="List every live provider-limit report (with recordedAt, windows, mapped seats) and active suppression.",
    )
    status_cmd.add_argument("--config", dest="sub_config", default=None, help=config_help)

    suppress_cmd = sub.add_parser(
        "suppress",
        help="Suppress a live exhausted window until its resetsAt by --report [--window] or --agent.",
    )
    suppress_cmd.add_argument("--config", dest="sub_config", default=None, help=config_help)
    suppress_cmd.add_argument("--report", default=None, help="Live report id to suppress.")
    suppress_cmd.add_argument(
        "--window",
        default=None,
        help="Live window id on --report (required when a report has multiple live windows).",
    )
    suppress_cmd.add_argument(
        "--agent",
        default=None,
        help="Seat agent id whose mapped live exhausted windows should be suppressed.",
    )

    unsuppress_cmd = sub.add_parser(
        "unsuppress",
        help="Remove an active window suppression by --report [--window] or --agent.",
    )
    unsuppress_cmd.add_argument("--config", dest="sub_config", default=None, help=config_help)
    unsuppress_cmd.add_argument("--report", default=None, help="Report id to unsuppress.")
    unsuppress_cmd.add_argument("--window", default=None, help="Optional window id to unsuppress.")
    unsuppress_cmd.add_argument(
        "--agent",
        default=None,
        help="Seat agent id whose mapped or attributed suppressions should be removed.",
    )


def _resolve_cli_limits_path(config_path: str | None) -> Path | ProviderLimitCommandResult:
    if not config_path:
        return ProviderLimitCommandResult(
            exit_code=2,
            payload={
                "ok": False,
                "error": {"code": "config_required", "message": "--config is required."},
            },
        )
    try:
        loaded = load_paseo_runtime_settings(config_path)
        settings = require_paseo_runtime(loaded, source=str(user_settings_path()))
    except PaseoRuntimeNotConfigured as error:
        return ProviderLimitCommandResult(
            exit_code=2,
            payload={"ok": False, "error": {"code": error.code, "message": str(error)}},
        )
    except ConfigError as error:
        return ProviderLimitCommandResult(
            exit_code=2,
            payload={"ok": False, "error": {"code": "settings_invalid", "message": str(error)}},
        )
    except OSError as error:
        return ProviderLimitCommandResult(
            exit_code=2,
            payload={
                "ok": False,
                "error": {
                    "code": "settings_unreadable",
                    "message": f"cannot read {config_path}: {error}",
                },
            },
        )
    except (ValueError, RecursionError) as error:
        problem = f"{type(error).__name__}: {error}"
        return ProviderLimitCommandResult(
            exit_code=2,
            payload={
                "ok": False,
                "error": {
                    "code": "settings_invalid",
                    "message": f"cannot parse {config_path}: {problem}",
                },
            },
        )
    return provider_limits_path_for_home(settings.home)


def run(args: argparse.Namespace) -> int:
    """Execute ``agents-remember provider-limits <status|suppress|unsuppress>`` and print JSON."""

    config_path = getattr(args, "sub_config", None) or getattr(args, "parent_config", None)
    resolved = _resolve_cli_limits_path(config_path)
    if isinstance(resolved, ProviderLimitCommandResult):
        print(json.dumps(resolved.payload, indent=2))
        return resolved.exit_code
    action = str(args.provider_limits_command)
    try:
        if action == "status":
            result = provider_limits_status(resolved)
        else:
            selector = SuppressionSelector(
                report_id=getattr(args, "report", None),
                window_id=getattr(args, "window", None),
                agent_id=getattr(args, "agent", None),
            )
            result = (
                suppress_provider_limit_windows(resolved, selector)
                if action == "suppress"
                else unsuppress_provider_limit_windows(resolved, selector)
            )
    except OSError as error:
        result = ProviderLimitCommandResult(
            exit_code=1,
            payload={"ok": False, "error": {"code": "filesystem_error", "message": str(error)}},
        )
    except Exception as error:
        result = ProviderLimitCommandResult(
            exit_code=1,
            payload={
                "ok": False,
                "error": {
                    "code": "unexpected_error",
                    "message": f"{type(error).__name__}: {error}",
                },
            },
        )
    print(json.dumps(result.payload, indent=2))
    return result.exit_code
