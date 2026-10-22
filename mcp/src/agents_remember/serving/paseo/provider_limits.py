"""The product's one writer of ``provider-limits.json`` (MIK-R190 rules 6, 7 and 8).

A seat is limited, and until when, by Paseo's usage report for that seat and by
nothing else: no chat text, no error string, no turn failure kind — none exists on
the pinned host. This module judges one read by rule 6 and keeps one fact per
report id in the product's own file beside ``role-agents.json``, which every
reader in rules 9 to 13 reads.

The judgment is deliberately conservative. A fact is set only by a successful
report, and an ``unavailable`` read never clears one. The one exception is 0.12.0's
``lastSuccessful``: an ``error`` entry can carry the newest available report of
that login, and that report is judged as itself (rule 7).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import ConfigError, McpRuntimeConfig
from agents_remember.observer.provider_limits import (
    PROVIDER_LIMITS_FILE,
    PROVIDER_LIMITS_SCHEMA,
    ProviderLimitReport,
    ProviderLimits,
    ProviderLimitWindow,
    read_provider_limits,
)
from agents_remember.serving.paseo.paseo_plugin_files import AR_HOME_DIRECTORY, read_role_agents

logger = logging.getLogger(__name__)

# A window is exhausted at this share of its use, or with nothing remaining.
_EXHAUSTED_USED_PCT = 100.0
# Rule 7's staleness mark, for the bridge's own refresh decision.
_USAGE_REFRESH_AGE = timedelta(seconds=60)


def provider_limits_path(config: McpRuntimeConfig) -> Path | None:
    """Where the one fact file lives; ``None`` when the settings name no Paseo runtime.

    Rule 8: where the settings name no Paseo runtime — L60's scratch settings, for
    one — nothing reads or writes the file.
    """

    try:
        runtime = config.paseo_runtime
    except ConfigError:
        return None
    if runtime is None:
        return None
    return runtime.home / AR_HOME_DIRECTORY / PROVIDER_LIMITS_FILE


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _records(value: object) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _extra_usage_enabled(details: list[dict[str, Any]]) -> bool:
    """Claude's `extra_usage` detail: with it on, a full window does not stop work."""

    for detail in details:
        if _text(detail.get("id")) != "extra_usage" and _text(detail.get("label")) != "extra_usage":
            continue
        value = detail.get("value", detail.get("text"))
        if isinstance(value, str) and value.strip().lower() in {"enabled", "on", "true"}:
            return True
    return False


def _credits_remaining(balances: list[dict[str, Any]]) -> bool:
    """Codex's `credits` balance, treated as Claude's extra usage is."""

    for balance in balances:
        if _text(balance.get("id")) != "credits" and _text(balance.get("label")) != "credits":
            continue
        remaining = balance.get("remaining")
        if isinstance(remaining, (int, float)) and remaining > 0:
            return True
    return False


def judge_report(
    report: dict[str, Any], now: datetime
) -> tuple[datetime, tuple[ProviderLimitWindow, ...]] | None:
    """Rule 6: the moment this report stops work, or ``None`` when it does not.

    A window counts only when its source marks it account-wide (``summary: true``),
    because a scoped window such as "Weekly · Fable" must not hold an Opus seat for
    days. ``until`` is the latest reset among the exhausted windows.
    """

    if _text(report.get("status")) != "available":
        return None
    if _extra_usage_enabled(_records(report.get("details"))):
        return None
    if _credits_remaining(_records(report.get("balances"))):
        return None
    exhausted: list[ProviderLimitWindow] = []
    for window in _records(report.get("windows")):
        if window.get("summary") is not True:
            continue
        used = window.get("usedPct")
        remaining = window.get("remainingPct")
        full = (isinstance(used, (int, float)) and used >= _EXHAUSTED_USED_PCT) or (
            isinstance(remaining, (int, float)) and remaining <= 0
        )
        if not full:
            continue
        resets = _time(window.get("resetsAt"))
        if resets is None or resets <= now:
            continue
        label = _text(window.get("label")) or _text(window.get("id"))
        exhausted.append(
            ProviderLimitWindow(
                id=_text(window.get("id")) or label or "window",
                label=label or "window",
                resetsAt=resets,
            )
        )
    if not exhausted:
        return None
    return max(window.resetsAt for window in exhausted), tuple(exhausted)


def _fact_or_none(
    report: dict[str, Any],
    entry: dict[str, Any],
    now: datetime,
    observed: datetime | None,
) -> ProviderLimitReport | None:
    """The stored fact one judged report makes; ``None`` when it is not exhausted.

    ``None`` is a verdict, not silence: an ``available`` report that is not
    exhausted clears a stored fact (rule 8).
    """

    judged = judge_report(report, now)
    if judged is None:
        return None
    until, windows = judged
    account = entry.get("account")
    return ProviderLimitReport(
        until=until,
        sourceLabel=_text(entry.get("sourceLabel")),
        accountLabel=_text(account.get("label")) if isinstance(account, dict) else "",
        windows=windows,
        observedAt=observed or now,
    )


def judge_read(entries: object, now: datetime) -> dict[str, ProviderLimitReport | None]:
    """One agent's read: each report id it named, with the fact to keep or clear.

    A value of ``None`` means the report answered that it is not exhausted, so a
    stored fact for it is dropped (rule 8). An entry that says nothing about a
    report — ``unavailable``, or an ``error`` without ``lastSuccessful`` — is absent
    from the answer and leaves what is stored alone. 0.12.0's ``lastSuccessful`` is
    the nested ``{fetchedAt, report}`` object the pinned protocol declares: its
    ``report`` is judged as itself, and its own ``fetchedAt`` is the observation.
    """

    judged: dict[str, ProviderLimitReport | None] = {}
    for entry in _records(entries):
        report_id = _text(entry.get("id"))
        if not report_id:
            continue
        report = entry.get("report")
        report = report if isinstance(report, dict) else {}
        status = _text(report.get("status"))
        if status == "available":
            judged[report_id] = _fact_or_none(report, entry, now, _time(entry.get("fetchedAt")))
            continue
        if status != "error":
            continue
        last = entry.get("lastSuccessful")
        if not isinstance(last, dict):
            continue
        carried = last.get("report")
        if not isinstance(carried, dict) or _text(carried.get("status")) != "available":
            continue
        judged[report_id] = _fact_or_none(carried, entry, now, _time(last.get("fetchedAt")))
    return judged


def _write_limits(path: Path, limits: ProviderLimits) -> None:
    """Write the whole file atomically, under the product's own lock."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    body = limits.model_dump(by_alias=True, mode="json")
    with exclusive_file_lock(path.with_suffix(".lock"), "provider limits"):
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(body, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, path)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary)
            raise


def record_usage_reads(
    config: McpRuntimeConfig,
    reads: dict[str, object],
    *,
    now: datetime | None = None,
) -> ProviderLimits | None:
    """Store what one tick's reads said, and drop what no longer holds (rule 8)."""

    path = provider_limits_path(config)
    if path is None:
        return None
    moment = now or datetime.now(UTC)
    current = read_provider_limits(path)
    reports = dict(current.reports)
    for entries in reads.values():
        for report_id, fact in judge_read(entries, moment).items():
            if fact is None:
                reports.pop(report_id, None)
            else:
                reports[report_id] = fact
    records = read_role_agents(path.parent.parent)
    agents: dict[str, tuple[str, ...]] = {}
    for agent_id in records:
        if agent_id in reads:
            # Rule 8: a successful read replaces this seat's row with exactly the report
            # ids it named -- exhausted or not. The account identity is what a later
            # exhaustion by another seat on the same account reaches, and a read that
            # named nothing (an empty answer) leaves the seat no row at all.
            named = _named_reports(reads, agent_id)
            if named:
                agents[agent_id] = named
            continue
        # A seat this tick did not read (a failed read, or none at all) keeps the ids its
        # last successful read named; only a seat that left the role record loses its row.
        kept = current.agents.get(agent_id, ())
        if kept:
            agents[agent_id] = kept
    live = {report_id: report for report_id, report in reports.items() if report.until > moment}
    limits = ProviderLimits(
        schema=PROVIDER_LIMITS_SCHEMA,
        reports=live,
        agents=agents,
        updatedAt=moment,
    )
    _write_limits(path, limits)
    return limits


def _named_reports(reads: dict[str, object], agent_id: str) -> tuple[str, ...]:
    """The report ids this agent's read named, as rule 8's agent row keeps them."""

    named: list[str] = []
    for entry in _records(reads.get(agent_id)):
        report_id = _text(entry.get("id"))
        if report_id and report_id not in named:
            named.append(report_id)
    return tuple(named)
