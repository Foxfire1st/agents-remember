"""Sidecar models and pure projection helpers for window-scoped provider-limit suppressions.

Keeps ``provider-limits.json`` strictly ``ar-provider-limits/v1`` (for mixed-version readers
with ``extra="forbid"``) while storing per-window ``recordedAt`` timestamps and active
``suppressedWindows`` in the sibling ``provider-limit-suppressions.json`` file.

All disk writes remain exclusively in ``serving.paseo.provider_limits`` under
``provider-limits.lock``; this module owns only the sidecar schema, read-only loading, and
pure window filtering/recomputation/selection helpers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from agents_remember.observer.provider_limits import (
    ProviderLimitReport,
    ProviderLimits,
    ProviderLimitWindow,
)

PROVIDER_LIMIT_SUPPRESSIONS_FILE = "provider-limit-suppressions.json"
PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA = "ar-provider-limit-suppressions/v1"

type WindowKey = tuple[str, str, datetime]
type SuppressionSource = Literal["auto-tool-call", "operator"]


class RecordedLimitWindow(BaseModel):
    """When AR first recorded an active exhausted ``(reportId, windowId, resetsAt)`` window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reportId: str = Field(min_length=1)
    windowId: str = Field(min_length=1)
    resetsAt: AwareDatetime
    recordedAt: AwareDatetime


class SuppressedLimitWindow(BaseModel):
    """One active suppression of a specific ``(reportId, windowId, resetsAt)`` window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reportId: str = Field(min_length=1)
    windowId: str = Field(min_length=1)
    resetsAt: AwareDatetime
    suppressedAt: AwareDatetime
    source: SuppressionSource
    agentId: str | None = None
    turnStartedAt: AwareDatetime | None = None
    callAt: AwareDatetime | None = None
    recordedAt: AwareDatetime | None = None


class ProviderLimitSuppressions(BaseModel):
    """Sibling sidecar beside ``provider-limits.json``."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_: str = Field(default=PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA, alias="schema")
    recordedWindows: tuple[RecordedLimitWindow, ...] = ()
    suppressedWindows: tuple[SuppressedLimitWindow, ...] = ()
    updatedAt: AwareDatetime | None = None


EMPTY_PROVIDER_LIMIT_SUPPRESSIONS = ProviderLimitSuppressions()


@dataclass(frozen=True)
class SuppressionSelector:
    """Typed selector crossing the CLI -> serving boundary for operator suppression commands."""

    report_id: str | None = None
    window_id: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True)
class ProviderLimitCommandResult:
    """Outcome of a ``provider-limits`` command: exit code (0/1/2) and JSON payload."""

    exit_code: int
    payload: dict[str, Any]


def provider_limit_suppressions_path(limits_path: Path) -> Path:
    """The sibling sidecar path next to ``provider-limits.json``."""

    return limits_path.with_name(PROVIDER_LIMIT_SUPPRESSIONS_FILE)


def window_key(report_id: str, window_id: str, resets_at: datetime) -> WindowKey:
    """Canonical UTC-normalized key for ``(report_id, window_id, resetsAt)`` matching."""

    return (report_id, window_id, resets_at.astimezone(UTC))


def read_provider_limit_suppressions(path: Path) -> ProviderLimitSuppressions:
    """Read the sidecar; a missing or unreadable file reads as empty."""

    try:
        return ProviderLimitSuppressions.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return EMPTY_PROVIDER_LIMIT_SUPPRESSIONS


def recorded_at_index(
    sidecar: ProviderLimitSuppressions, now: datetime
) -> dict[WindowKey, datetime]:
    """Map live ``(reportId, windowId, resetsAt)`` keys to their stored ``recordedAt``."""

    return {
        window_key(entry.reportId, entry.windowId, entry.resetsAt): entry.recordedAt
        for entry in sidecar.recordedWindows
        if entry.resetsAt > now
    }


def live_suppressed_keys(sidecar: ProviderLimitSuppressions, now: datetime) -> frozenset[WindowKey]:
    """Keys of active (unexpired) suppressed windows."""

    return frozenset(
        window_key(entry.reportId, entry.windowId, entry.resetsAt)
        for entry in sidecar.suppressedWindows
        if entry.resetsAt > now
    )


def _project_report(
    report_id: str,
    report: ProviderLimitReport,
    suppressed: frozenset[WindowKey],
    now: datetime,
) -> ProviderLimitReport | None:
    if report.until <= now:
        return None
    if not report.windows:
        return report
    remaining = tuple(
        window
        for window in report.windows
        if window.resetsAt > now
        and window_key(report_id, window.id, window.resetsAt) not in suppressed
    )
    if not remaining:
        return None
    recomputed_until = max(window.resetsAt for window in remaining)
    if remaining == report.windows and recomputed_until == report.until:
        return report
    return report.model_copy(update={"windows": remaining, "until": recomputed_until})


def project_reports_with_suppressions(
    reports: Mapping[str, ProviderLimitReport],
    sidecar: ProviderLimitSuppressions,
    now: datetime,
) -> dict[str, ProviderLimitReport]:
    """Filter live exhausted windows against active suppressions and recompute ``until``."""

    suppressed = live_suppressed_keys(sidecar, now)
    projected: dict[str, ProviderLimitReport] = {}
    for report_id, report in reports.items():
        kept = _project_report(report_id, report, suppressed, now)
        if kept is not None:
            projected[report_id] = kept
    return projected


def _live_report_windows(
    reports: Mapping[str, ProviderLimitReport], now: datetime
) -> list[tuple[str, ProviderLimitWindow]]:
    return [
        (report_id, window)
        for report_id, report in reports.items()
        if report.until > now
        for window in report.windows
        if window.resetsAt > now
    ]


def prune_sidecar(
    sidecar: ProviderLimitSuppressions,
    live_reports: Mapping[str, ProviderLimitReport],
    now: datetime,
) -> ProviderLimitSuppressions:
    """Drop expired sidecar entries and stale ``recordedWindows``; return same object if unchanged."""

    active_keys = frozenset(
        window_key(report_id, window.id, window.resetsAt)
        for report_id, window in _live_report_windows(live_reports, now)
    )
    kept_recorded = tuple(
        entry
        for entry in sidecar.recordedWindows
        if entry.resetsAt > now
        and window_key(entry.reportId, entry.windowId, entry.resetsAt) in active_keys
    )
    kept_suppressed = tuple(entry for entry in sidecar.suppressedWindows if entry.resetsAt > now)
    if kept_recorded == sidecar.recordedWindows and kept_suppressed == sidecar.suppressedWindows:
        return sidecar
    return ProviderLimitSuppressions(
        schema=PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA,
        recordedWindows=kept_recorded,
        suppressedWindows=kept_suppressed,
        updatedAt=now,
    )


def update_sidecar_for_usage_reads(
    sidecar: ProviderLimitSuppressions,
    projected_reports: Mapping[str, ProviderLimitReport],
    judged_report_ids: AbstractSet[str],
    now: datetime,
) -> ProviderLimitSuppressions:
    """Preserve existing ``recordedAt`` across re-reads, stamp newly judged windows, prune expired."""

    existing = recorded_at_index(sidecar, now)
    kept_suppressed = tuple(entry for entry in sidecar.suppressedWindows if entry.resetsAt > now)
    recorded_by_key: dict[WindowKey, RecordedLimitWindow] = {}
    for report_id, window in _live_report_windows(projected_reports, now):
        key = window_key(report_id, window.id, window.resetsAt)
        if key in recorded_by_key:
            continue
        recorded_at = existing.get(key) or (now if report_id in judged_report_ids else None)
        if recorded_at is not None:
            recorded_by_key[key] = RecordedLimitWindow(
                reportId=report_id,
                windowId=window.id,
                resetsAt=window.resetsAt,
                recordedAt=recorded_at,
            )
    next_recorded = tuple(recorded_by_key.values())
    if next_recorded == sidecar.recordedWindows and kept_suppressed == sidecar.suppressedWindows:
        return sidecar
    return ProviderLimitSuppressions(
        schema=PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA,
        recordedWindows=next_recorded,
        suppressedWindows=kept_suppressed,
        updatedAt=now,
    )


def merge_suppressions(
    sidecar: ProviderLimitSuppressions,
    additions: Iterable[SuppressedLimitWindow],
    now: datetime,
) -> ProviderLimitSuppressions:
    """Merge new active suppressions into the sidecar and drop expired entries."""

    by_key: dict[WindowKey, SuppressedLimitWindow] = {
        window_key(entry.reportId, entry.windowId, entry.resetsAt): entry
        for entry in sidecar.suppressedWindows
        if entry.resetsAt > now
    }
    for entry in additions:
        if entry.resetsAt > now:
            by_key[window_key(entry.reportId, entry.windowId, entry.resetsAt)] = entry
    kept_recorded = tuple(
        entry
        for entry in sidecar.recordedWindows
        if entry.resetsAt > now
        and window_key(entry.reportId, entry.windowId, entry.resetsAt) not in by_key
    )
    return ProviderLimitSuppressions(
        schema=PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA,
        recordedWindows=kept_recorded,
        suppressedWindows=tuple(by_key.values()),
        updatedAt=now,
    )


def refusal_result(code: str, message: str) -> ProviderLimitCommandResult:
    """Build an exit-2 operator-legible refusal payload."""

    return ProviderLimitCommandResult(
        exit_code=2,
        payload={"ok": False, "error": {"code": code, "message": message}},
    )


def validate_selector(selector: SuppressionSelector) -> ProviderLimitCommandResult | None:
    """Validate that exactly one of ``--report`` or ``--agent`` is supplied."""

    has_report = bool(selector.report_id)
    has_agent = bool(selector.agent_id)
    if not has_report and not has_agent:
        return refusal_result(
            "selector_required",
            "Specify --report <id> or --agent <id>. Run 'agents-remember provider-limits status --config <path>' to inspect live limits.",
        )
    if has_report and has_agent:
        return refusal_result(
            "selector_conflict",
            "Specify either --report <id> or --agent <id>, not both.",
        )
    return None


def auto_suppression_candidates(
    limits: ProviderLimits,
    sidecar: ProviderLimitSuppressions,
    agent_id: str,
    turn_started_at: datetime,
    now: datetime,
) -> tuple[SuppressedLimitWindow, ...]:
    """Select live mapped windows whose explicit ``recordedAt`` strictly precedes ``turn_started_at``."""

    mapped_reports = {
        report_id: limits.reports[report_id]
        for report_id in limits.agents.get(agent_id, ())
        if report_id in limits.reports
    }
    if not mapped_reports:
        return ()
    recorded_map = recorded_at_index(sidecar, now)
    suppressed_keys = live_suppressed_keys(sidecar, now)
    candidates: list[SuppressedLimitWindow] = []
    for report_id, window in _live_report_windows(mapped_reports, now):
        key = window_key(report_id, window.id, window.resetsAt)
        recorded_at = recorded_map.get(key)
        if key in suppressed_keys or recorded_at is None or turn_started_at <= recorded_at:
            continue
        candidates.append(
            SuppressedLimitWindow(
                reportId=report_id,
                windowId=window.id,
                resetsAt=window.resetsAt,
                suppressedAt=now,
                source="auto-tool-call",
                agentId=agent_id,
                turnStartedAt=turn_started_at,
                callAt=now,
                recordedAt=recorded_at,
            )
        )
    return tuple(candidates)


def _format_report_status(
    report_id: str,
    report: ProviderLimitReport,
    recorded_map: Mapping[WindowKey, datetime],
    mapped_seats: list[str],
    now: datetime,
) -> dict[str, Any]:
    live_windows = [window for window in report.windows if window.resetsAt > now]
    window_rows: list[dict[str, Any]] = []
    recorded_times: list[datetime] = []
    for window in live_windows:
        recorded_at = recorded_map.get(window_key(report_id, window.id, window.resetsAt))
        if recorded_at is not None:
            recorded_times.append(recorded_at)
        window_rows.append(
            {
                "id": window.id,
                "label": window.label,
                "resetsAt": window.resetsAt.isoformat(),
                "recordedAt": recorded_at.isoformat() if recorded_at is not None else None,
            }
        )
    report_recorded = (
        min(recorded_times).isoformat()
        if live_windows and len(recorded_times) == len(live_windows)
        else None
    )
    return {
        "reportId": report_id,
        "until": report.until.isoformat(),
        "sourceLabel": report.sourceLabel,
        "accountLabel": report.accountLabel,
        "observedAt": report.observedAt.isoformat(),
        "lastReadAttemptAt": (
            report.lastReadAttemptAt.isoformat() if report.lastReadAttemptAt is not None else None
        ),
        "recordedAt": report_recorded,
        "mappedSeats": sorted(mapped_seats),
        "windows": window_rows,
    }


def build_status_payload(
    limits: ProviderLimits,
    sidecar: ProviderLimitSuppressions,
    limits_path: Path,
    sidecar_path: Path,
    now: datetime,
) -> dict[str, Any]:
    """Format the live reports and active suppressions for ``provider-limits status``."""

    recorded_map = recorded_at_index(sidecar, now)
    seats_by_report: dict[str, list[str]] = {}
    for agent_id, report_ids in limits.agents.items():
        for report_id in report_ids:
            seats_by_report.setdefault(report_id, []).append(agent_id)
    reports_out = [
        _format_report_status(
            report_id,
            limits.reports[report_id],
            recorded_map,
            seats_by_report.get(report_id, []),
            now,
        )
        for report_id in sorted(limits.reports)
        if limits.reports[report_id].until > now
    ]
    suppressed_out = [
        entry.model_dump(mode="json", exclude_none=True)
        for entry in sidecar.suppressedWindows
        if entry.resetsAt > now
    ]
    return {
        "ok": True,
        "action": "status",
        "checkedAt": now.isoformat(),
        "limitsPath": limits_path.as_posix(),
        "suppressionsPath": sidecar_path.as_posix(),
        "reports": reports_out,
        "suppressedWindows": suppressed_out,
    }


def _build_operator_entries(
    pairs: Iterable[tuple[str, ProviderLimitWindow]],
    sidecar: ProviderLimitSuppressions,
    now: datetime,
    *,
    agent_id: str | None = None,
) -> tuple[SuppressedLimitWindow, ...]:
    recorded_map = recorded_at_index(sidecar, now)
    return tuple(
        SuppressedLimitWindow(
            reportId=report_id,
            windowId=window.id,
            resetsAt=window.resetsAt,
            suppressedAt=now,
            source="operator",
            agentId=agent_id,
            recordedAt=recorded_map.get(window_key(report_id, window.id, window.resetsAt)),
        )
        for report_id, window in pairs
    )


def select_operator_suppressions(
    limits: ProviderLimits,
    sidecar: ProviderLimitSuppressions,
    selector: SuppressionSelector,
    now: datetime,
) -> tuple[SuppressedLimitWindow, ...] | ProviderLimitCommandResult:
    """Resolve an operator ``suppress`` target to concrete windows, or return a named refusal."""

    if selector.report_id is not None:
        return _select_report_suppressions(
            limits, sidecar, selector.report_id, selector.window_id, now
        )
    assert selector.agent_id is not None
    return _select_agent_suppressions(limits, sidecar, selector.agent_id, selector.window_id, now)


def _select_report_suppressions(
    limits: ProviderLimits,
    sidecar: ProviderLimitSuppressions,
    report_id: str,
    window_id: str | None,
    now: datetime,
) -> tuple[SuppressedLimitWindow, ...] | ProviderLimitCommandResult:
    report = limits.reports.get(report_id)
    if report is None or report.until <= now:
        return refusal_result(
            "report_not_live",
            f"Report {report_id!r} has no live provider limit. Run 'agents-remember provider-limits status --config <path>' to list live reports.",
        )
    live_windows = [window for window in report.windows if window.resetsAt > now]
    if not live_windows:
        return refusal_result(
            "report_has_no_live_windows",
            f"Report {report_id!r} has no live exhausted window entries to suppress.",
        )
    chosen = _choose_report_windows(report_id, live_windows, window_id)
    if isinstance(chosen, ProviderLimitCommandResult):
        return chosen
    return _build_operator_entries(((report_id, window) for window in chosen), sidecar, now)


def _choose_report_windows(
    report_id: str,
    live_windows: list[ProviderLimitWindow],
    window_id: str | None,
) -> list[ProviderLimitWindow] | ProviderLimitCommandResult:
    available = ", ".join(window.id for window in live_windows)
    if window_id is not None:
        matched = [window for window in live_windows if window.id == window_id]
        if not matched:
            return refusal_result(
                "window_not_live",
                f"Window {window_id!r} is not live on report {report_id!r} (live windows: {available}).",
            )
        return matched
    if len(live_windows) > 1:
        return refusal_result(
            "window_required",
            f"Report {report_id!r} has multiple live exhausted windows ({available}); pass --window <id> to choose one.",
        )
    return live_windows


def _select_agent_suppressions(
    limits: ProviderLimits,
    sidecar: ProviderLimitSuppressions,
    agent_id: str,
    window_id: str | None,
    now: datetime,
) -> tuple[SuppressedLimitWindow, ...] | ProviderLimitCommandResult:
    mapped = {
        report_id: limits.reports[report_id]
        for report_id in limits.agents.get(agent_id, ())
        if report_id in limits.reports
    }
    live_pairs = _live_report_windows(mapped, now)
    if not live_pairs:
        return refusal_result(
            "agent_not_limited",
            f"Agent {agent_id!r} has no live exhausted provider-limit windows. Run 'agents-remember provider-limits status --config <path>' to inspect live limits.",
        )
    if window_id is not None:
        filtered = [(rid, window) for rid, window in live_pairs if window.id == window_id]
        if not filtered:
            available = ", ".join(sorted({window.id for _, window in live_pairs}))
            return refusal_result(
                "window_not_live",
                f"Window {window_id!r} is not live on any report mapped to agent {agent_id!r} (live windows: {available}).",
            )
        live_pairs = filtered
    return _build_operator_entries(live_pairs, sidecar, now, agent_id=agent_id)


def partition_suppressions_for_unsuppress(
    limits: ProviderLimits,
    sidecar: ProviderLimitSuppressions,
    selector: SuppressionSelector,
    now: datetime,
) -> tuple[tuple[SuppressedLimitWindow, ...], tuple[SuppressedLimitWindow, ...]]:
    """Split active suppressions into ``(removed, kept)`` for ``unsuppress``."""

    mapped_reports = (
        frozenset(limits.agents.get(selector.agent_id, ())) if selector.agent_id else frozenset()
    )
    removed: list[SuppressedLimitWindow] = []
    kept: list[SuppressedLimitWindow] = []
    for entry in sidecar.suppressedWindows:
        if entry.resetsAt <= now:
            continue
        if _matches_unsuppress(entry, selector, mapped_reports):
            removed.append(entry)
        else:
            kept.append(entry)
    return tuple(removed), tuple(kept)


def _matches_unsuppress(
    entry: SuppressedLimitWindow,
    selector: SuppressionSelector,
    mapped_reports: frozenset[str],
) -> bool:
    if selector.window_id is not None and entry.windowId != selector.window_id:
        return False
    if selector.report_id is not None:
        return entry.reportId == selector.report_id
    return entry.reportId in mapped_reports or entry.agentId == selector.agent_id


def missing_suppression_refusal(selector: SuppressionSelector) -> ProviderLimitCommandResult:
    """Named exit-2 refusal when ``unsuppress`` matches no active suppression."""

    window_suffix = f" window {selector.window_id!r}" if selector.window_id else ""
    target = (
        f"report {selector.report_id!r}{window_suffix}"
        if selector.report_id
        else f"agent {selector.agent_id!r}{window_suffix}"
    )
    return refusal_result(
        "suppression_not_found",
        f"No active provider-limit suppression matched {target}. Run 'agents-remember provider-limits status --config <path>' to list active suppressions.",
    )


def suppress_success_result(
    selected: tuple[SuppressedLimitWindow, ...],
    previous: ProviderLimits,
    updated: ProviderLimits,
) -> ProviderLimitCommandResult:
    """Format the exit-0 JSON result of ``provider-limits suppress``."""

    return ProviderLimitCommandResult(
        exit_code=0,
        payload={
            "ok": True,
            "action": "suppress",
            "suppressed": [entry.model_dump(mode="json", exclude_none=True) for entry in selected],
            "clearedSeats": sorted(updated.clearedSeats.keys() - previous.clearedSeats.keys()),
            "remainingReports": sorted(updated.reports.keys()),
        },
    )


def unsuppress_success_result(
    removed: tuple[SuppressedLimitWindow, ...],
    next_sidecar: ProviderLimitSuppressions,
) -> ProviderLimitCommandResult:
    """Format the exit-0 JSON result of ``provider-limits unsuppress``."""

    return ProviderLimitCommandResult(
        exit_code=0,
        payload={
            "ok": True,
            "action": "unsuppress",
            "removed": [entry.model_dump(mode="json", exclude_none=True) for entry in removed],
            "remainingSuppressions": len(next_sidecar.suppressedWindows),
        },
    )
