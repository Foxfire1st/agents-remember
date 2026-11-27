"""The product's one writer of ``provider-limits.json`` (MIK-R190 rules 6, 7 and 8).

A seat is limited, and until when, by Paseo's usage report for that seat and by
nothing else: no chat text, no error string, no turn failure kind — none exists on
the pinned host. This module judges one read by rule 6 and keeps one fact per
report id in the product's own file beside ``role-agents.json``, which every
reader in rules 9 to 13 reads.

The judgment is deliberately conservative. A fact is set only by a successful
report, and an ``unavailable`` read never clears one. The one exception is Paseo's
``lastSuccessful`` (0.12.0 to 0.16.2): an ``error`` entry can carry the newest available report of
that login, and that report is judged as itself (rule 7).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import ConfigError, McpRuntimeConfig
from agents_remember.observer.host_activity import (
    HOST_ACTIVITY_FILE,
    read_host_activity_publication,
)
from agents_remember.observer.provider_limits import (
    EMPTY_PROVIDER_LIMITS,
    PROVIDER_LIMITS_FILE,
    PROVIDER_LIMITS_SCHEMA,
    ProviderLimitClear,
    ProviderLimitReport,
    ProviderLimits,
    ProviderLimitWindow,
    limited_until,
    read_provider_limits,
)
from agents_remember.serving.paseo.paseo_plugin_files import AR_HOME_DIRECTORY, read_role_agents
from agents_remember.serving.paseo.provider_limit_suppressions import (
    PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA,
    ProviderLimitCommandResult,
    ProviderLimitSuppressions,
    SuppressedLimitWindow,
    SuppressionSelector,
    auto_suppression_candidates,
    build_status_payload,
    merge_suppressions,
    missing_suppression_refusal,
    partition_suppressions_for_unsuppress,
    project_reports_with_suppressions,
    provider_limit_suppressions_path,
    prune_sidecar,
    read_provider_limit_suppressions,
    select_operator_suppressions,
    suppress_success_result,
    unsuppress_success_result,
    update_sidecar_for_usage_reads,
    validate_selector,
)

logger = logging.getLogger(__name__)

# A window is exhausted at this share of its use, or with nothing remaining.
_EXHAUSTED_USED_PCT = 100.0
_USAGE_READ_PERIOD = timedelta(minutes=5)
_LIMIT_RESUME_GRACE = timedelta(seconds=60)


def provider_limits_path_for_home(home: Path) -> Path:
    """Where ``provider-limits.json`` lives inside one Paseo home."""

    return home / AR_HOME_DIRECTORY / PROVIDER_LIMITS_FILE


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
    return provider_limits_path_for_home(runtime.home)


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
    from the answer and leaves what is stored alone. Paseo's ``lastSuccessful`` is
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


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Replace one file atomically while the caller holds the writer lock."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise


def _write_json_atomic(path: Path, body: dict[str, Any]) -> None:
    """Replace one JSON file atomically while the caller holds the writer lock."""

    payload = (json.dumps(body, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _write_bytes_atomic(path, payload)


def _write_limits(path: Path, limits: ProviderLimits) -> None:
    """Replace ``provider-limits.json`` atomically while the caller holds the writer lock."""

    _write_json_atomic(path, limits.model_dump(by_alias=True, mode="json"))


def _write_suppressions(path: Path, sidecar: ProviderLimitSuppressions) -> None:
    """Replace ``provider-limit-suppressions.json`` atomically under ``provider-limits.lock``."""

    _write_json_atomic(path, sidecar.model_dump(by_alias=True, mode="json", exclude_none=True))


def _commit_pair_locked(
    path: Path,
    sidecar_path: Path,
    updated_sidecar: ProviderLimitSuppressions | None,
    limits: ProviderLimits | None,
) -> None:
    """Write sidecar and limits atomically, rolling back sidecar if the limits write fails."""

    if updated_sidecar is None:
        if limits is not None:
            _write_limits(path, limits)
        return
    prior_sidecar = sidecar_path.read_bytes() if sidecar_path.exists() else None
    _write_suppressions(sidecar_path, updated_sidecar)
    if limits is None:
        return
    try:
        _write_limits(path, limits)
    except BaseException:
        with suppress(OSError):
            if prior_sidecar is None:
                sidecar_path.unlink(missing_ok=True)
            else:
                _write_bytes_atomic(sidecar_path, prior_sidecar)
        raise


def claim_due_reports(
    config: McpRuntimeConfig, *, now: datetime | None = None
) -> tuple[ProviderLimits, tuple[str, ...]]:
    """Record each due attempt before the host call, including attempts that may fail."""

    path = provider_limits_path(config)
    if path is None:
        return EMPTY_PROVIDER_LIMITS, ()
    sidecar_path = provider_limit_suppressions_path(path)
    current = read_provider_limits(path)
    sidecar = read_provider_limit_suppressions(sidecar_path)
    proposed, proposed_sidecar, _ = _periodic_update(
        path, current, sidecar, now or datetime.now(UTC)
    )
    if proposed is current and proposed_sidecar is sidecar:
        return current, ()
    with exclusive_file_lock(path.with_suffix(".lock"), "provider limits"):
        current = read_provider_limits(path)
        sidecar = read_provider_limit_suppressions(sidecar_path)
        updated, updated_sidecar, due = _periodic_update(
            path, current, sidecar, now or datetime.now(UTC)
        )
        _commit_pair_locked(
            path,
            sidecar_path,
            updated_sidecar if updated_sidecar is not sidecar else None,
            updated if updated is not current else None,
        )
        return updated, due


def _periodic_update(
    path: Path,
    current: ProviderLimits,
    sidecar: ProviderLimitSuppressions,
    now: datetime,
) -> tuple[ProviderLimits, ProviderLimitSuppressions, tuple[str, ...]]:
    reports = project_reports_with_suppressions(current.reports, sidecar, now)
    pruned_sidecar = prune_sidecar(sidecar, reports, now)
    cutoff = now - _USAGE_READ_PERIOD
    due = tuple(
        report_id
        for report_id, fact in reports.items()
        if max(fact.observedAt, fact.lastReadAttemptAt or fact.observedAt) <= cutoff
    )
    reports_changed = bool(due) or reports != current.reports
    if not reports_changed and not current.clearedSeats:
        return current, pruned_sidecar, ()
    records = read_role_agents(path.parent.parent)
    for report_id in due:
        reports[report_id] = reports[report_id].model_copy(update={"lastReadAttemptAt": now})
    updated = current.model_copy(
        update={
            "reports": reports,
            "agents": _updated_agents(current, {}, records),
            "updatedAt": now,
        }
    )
    markers = _updated_clear_markers(current, updated, records, now)
    if not reports_changed and len(markers) == len(current.clearedSeats):
        return current, pruned_sidecar, ()
    return updated.model_copy(update={"clearedSeats": markers}), pruned_sidecar, due


def record_usage_reads(
    config: McpRuntimeConfig,
    reads: dict[str, object],
    *,
    now: datetime | None = None,
    failed_refreshes: Mapping[str, AbstractSet[str]] | None = None,
    preserved_reports: Mapping[str, AbstractSet[str]] | None = None,
) -> ProviderLimits | None:
    """Store the tick's facts and preserve only current mappings for unresolved reports."""

    path = provider_limits_path(config)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with exclusive_file_lock(path.with_suffix(".lock"), "provider limits"):
        moment = now or datetime.now(UTC)
        current = read_provider_limits(path)
        sidecar_path = provider_limit_suppressions_path(path)
        sidecar = read_provider_limit_suppressions(sidecar_path)
        records = read_role_agents(path.parent.parent)
        raw_reports, judged_ids = _updated_reports(current, reads, failed_refreshes or {}, moment)
        reports = project_reports_with_suppressions(raw_reports, sidecar, moment)
        updated_sidecar = update_sidecar_for_usage_reads(sidecar, reports, judged_ids, moment)
        limits = ProviderLimits(
            schema=PROVIDER_LIMITS_SCHEMA,
            reports=reports,
            agents=_updated_agents(current, reads, records, preserved_reports),
            clearedSeats=current.clearedSeats,
            updatedAt=moment,
        )
        limits = limits.model_copy(
            update={"clearedSeats": _updated_clear_markers(current, limits, records, moment)}
        )
        _commit_pair_locked(
            path,
            sidecar_path,
            updated_sidecar if updated_sidecar is not sidecar else None,
            limits,
        )
        return limits


def _updated_reports(
    current: ProviderLimits,
    reads: dict[str, object],
    failed_refreshes: Mapping[str, AbstractSet[str]],
    now: datetime,
) -> tuple[dict[str, ProviderLimitReport], frozenset[str]]:
    reports = dict(current.reports)
    judged_ids: set[str] = set()
    for agent_id, entries in reads.items():
        failed = failed_refreshes.get(agent_id, ())
        for report_id, fact in judge_read(entries, now).items():
            if report_id in failed:
                continue
            if fact is None:
                reports.pop(report_id, None)
                judged_ids.discard(report_id)
            else:
                old = reports.get(report_id)
                attempt = old.lastReadAttemptAt if old is not None and old.until > now else None
                reports[report_id] = fact.model_copy(update={"lastReadAttemptAt": attempt})
                judged_ids.add(report_id)
    return reports, frozenset(judged_ids)


def _live_reports(
    reports: Mapping[str, ProviderLimitReport], now: datetime
) -> dict[str, ProviderLimitReport]:
    return {report_id: report for report_id, report in reports.items() if report.until > now}


def _updated_agents(
    current: ProviderLimits,
    reads: dict[str, object],
    records: Mapping[str, object],
    preserved_reports: Mapping[str, AbstractSet[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    agents: dict[str, tuple[str, ...]] = {}
    preserve = preserved_reports or {}
    for agent_id in records:
        previous = current.agents.get(agent_id, ())
        if agent_id in reads:
            # Keep unresolved periodic identities only if the seat still owns that
            # mapping when the writer enters the lock. A successful empty read drops it.
            named = _named_reports(reads, agent_id)
            if named:
                unresolved = preserve.get(agent_id, ())
                kept = (report_id for report_id in previous if report_id in unresolved)
                agents[agent_id] = tuple(dict.fromkeys((*named, *kept)))
            continue
        if previous:
            agents[agent_id] = previous
    return agents


def _kept_clear_markers(
    limits: ProviderLimits, records: Mapping[str, object], now: datetime
) -> dict[str, ProviderLimitClear]:
    return {
        agent_id: marker
        for agent_id, marker in limits.clearedSeats.items()
        if agent_id in records
        and marker.previousUntil + _LIMIT_RESUME_GRACE > now
        and limited_until(limits, agent_id, now) is None
    }


def _updated_clear_markers(
    previous: ProviderLimits,
    updated: ProviderLimits,
    records: Mapping[str, object],
    now: datetime,
) -> dict[str, ProviderLimitClear]:
    kept = _kept_clear_markers(updated, records, now)
    live = _live_reports(previous.reports, now)
    removed = live.keys() - updated.reports.keys()
    if not removed:
        return kept
    for agent_id, report_ids in previous.agents.items():
        if agent_id not in records or limited_until(updated, agent_id, now) is not None:
            continue
        blockers = live.keys() & report_ids
        if blockers and blockers <= removed:
            kept[agent_id] = ProviderLimitClear(
                clearedAt=now, previousUntil=max(live[report_id].until for report_id in blockers)
            )
    return kept


def _named_reports(reads: dict[str, object], agent_id: str) -> tuple[str, ...]:
    """The report ids this agent's read named, as rule 8's agent row keeps them."""

    named: list[str] = []
    for entry in _records(reads.get(agent_id)):
        report_id = _text(entry.get("id"))
        if report_id and report_id not in named:
            named.append(report_id)
    return tuple(named)


def _proven_active_turn_started_at(activity_path: Path, agent_id: str) -> datetime | None:
    """Return ``turnStartedAt`` only when a complete publication proves an active turn."""

    publication = read_host_activity_publication(activity_path)
    if publication is None or not publication.complete:
        return None
    activity = publication.agents.get(agent_id)
    if activity is None or activity.state not in {"in-turn", "permission"}:
        return None
    return activity.eventAt


def _read_auto_suppression_state(
    path: Path, agent_id: str, now: datetime
) -> tuple[ProviderLimits, ProviderLimitSuppressions, tuple[SuppressedLimitWindow, ...]] | None:
    current = read_provider_limits(path)
    if not current.reports or agent_id not in current.agents:
        return None
    turn_started_at = _proven_active_turn_started_at(path.with_name(HOST_ACTIVITY_FILE), agent_id)
    if turn_started_at is None:
        return None
    sidecar = read_provider_limit_suppressions(provider_limit_suppressions_path(path))
    candidates = auto_suppression_candidates(current, sidecar, agent_id, turn_started_at, now)
    if not candidates:
        return None
    return current, sidecar, candidates


def _apply_suppressions_locked(
    path: Path,
    current: ProviderLimits,
    sidecar: ProviderLimitSuppressions,
    additions: tuple[SuppressedLimitWindow, ...],
    *,
    now: datetime,
) -> ProviderLimits:
    """Merge suppressions, project ``provider-limits.json``, and write both atomically."""

    sidecar_path = provider_limit_suppressions_path(path)
    records = read_role_agents(path.parent.parent)
    merged_sidecar = merge_suppressions(sidecar, additions, now)
    projected_reports = project_reports_with_suppressions(current.reports, merged_sidecar, now)
    final_sidecar = prune_sidecar(merged_sidecar, projected_reports, now)
    updated = ProviderLimits(
        schema=PROVIDER_LIMITS_SCHEMA,
        reports=projected_reports,
        agents=current.agents,
        clearedSeats=current.clearedSeats,
        updatedAt=now,
    )
    updated = updated.model_copy(
        update={"clearedSeats": _updated_clear_markers(current, updated, records, now)}
    )
    _commit_pair_locked(path, sidecar_path, final_sidecar, updated)
    return updated


def suppress_provider_limits_for_live_tool_call(
    config: McpRuntimeConfig,
    agent_id: str,
    *,
    now: datetime | None = None,
) -> ProviderLimits | None:
    """Auto-suppress mapped live windows when a bound seat proves a post-limit active turn.

    Pre-checks lock-free so calls with no qualifying live limit take no lock and write nothing.
    Fails closed on missing/incomplete/idle ``host-activity.json``, on ``eventAt <= recordedAt``,
    and on legacy records whose ``recordedAt`` is absent.
    """

    path = provider_limits_path(config)
    if path is None or not path.is_file():
        return None
    if _read_auto_suppression_state(path, agent_id, now or datetime.now(UTC)) is None:
        return None
    with exclusive_file_lock(path.with_suffix(".lock"), "provider limits"):
        moment = now or datetime.now(UTC)
        state = _read_auto_suppression_state(path, agent_id, moment)
        if state is None:
            return None
        current, sidecar, candidates = state
        return _apply_suppressions_locked(path, current, sidecar, candidates, now=moment)


def provider_limits_status(
    limits_path: Path, *, now: datetime | None = None
) -> ProviderLimitCommandResult:
    """Inspect live reports and active suppressions without mutating disk."""

    moment = now or datetime.now(UTC)
    sidecar_path = provider_limit_suppressions_path(limits_path)
    return ProviderLimitCommandResult(
        exit_code=0,
        payload=build_status_payload(
            read_provider_limits(limits_path),
            read_provider_limit_suppressions(sidecar_path),
            limits_path,
            sidecar_path,
            moment,
        ),
    )


def suppress_provider_limit_windows(
    limits_path: Path,
    selector: SuppressionSelector,
    *,
    now: datetime | None = None,
) -> ProviderLimitCommandResult:
    """Operator override: suppress live exhausted window(s) under ``provider-limits.lock``."""

    invalid = validate_selector(selector)
    if invalid is not None:
        return invalid
    limits_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with exclusive_file_lock(limits_path.with_suffix(".lock"), "provider limits"):
        moment = now or datetime.now(UTC)
        current = read_provider_limits(limits_path)
        sidecar = read_provider_limit_suppressions(provider_limit_suppressions_path(limits_path))
        selected = select_operator_suppressions(current, sidecar, selector, moment)
        if isinstance(selected, ProviderLimitCommandResult):
            return selected
        updated = _apply_suppressions_locked(limits_path, current, sidecar, selected, now=moment)
        return suppress_success_result(selected, current, updated)


def unsuppress_provider_limit_windows(
    limits_path: Path,
    selector: SuppressionSelector,
    *,
    now: datetime | None = None,
) -> ProviderLimitCommandResult:
    """Operator override: remove active suppression(s) so later reads judge the window normally."""

    invalid = validate_selector(selector)
    if invalid is not None:
        return invalid
    limits_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with exclusive_file_lock(limits_path.with_suffix(".lock"), "provider limits"):
        moment = now or datetime.now(UTC)
        sidecar_path = provider_limit_suppressions_path(limits_path)
        current = read_provider_limits(limits_path)
        sidecar = read_provider_limit_suppressions(sidecar_path)
        removed, kept = partition_suppressions_for_unsuppress(current, sidecar, selector, moment)
        if not removed:
            return missing_suppression_refusal(selector)
        next_sidecar = prune_sidecar(
            ProviderLimitSuppressions(
                schema=PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA,
                recordedWindows=sidecar.recordedWindows,
                suppressedWindows=kept,
                updatedAt=moment,
            ),
            current.reports,
            moment,
        )
        _write_suppressions(sidecar_path, next_sidecar)
        return unsuppress_success_result(removed, next_sidecar)
