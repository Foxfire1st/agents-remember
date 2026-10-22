"""The product's one record of a provider limit, and how a task shows it.

MIK-R190 rule 8 makes ``<PASEO_HOME>/agents-remember/provider-limits.json`` the
only place a limit is kept. Its one writer is the product (``serving/paseo``);
every reader in rules 9 to 13 reads that file and nothing else stores a limit.
This module is the read side and sits at ``observer`` so that the one status
function can use it without an upward import (rule 19).

The file holds, per report id, the moment work may resume, the report's source
and account labels, the exhausted windows and when the fact was observed; and,
per agent, the report ids its last read named. An agent is limited while now is
before the ``until`` of one of its report ids, so one exhausted account stops
every seat on it — which is row 178's case.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PROVIDER_LIMITS_FILE = "provider-limits.json"
PROVIDER_LIMITS_SCHEMA = "ar-provider-limits/v1"


class _Seat(Protocol):
    """A seat as rule 13 reads it: the id, and the role the record names.

    Both are declared read-only, so the reader's own frozen dataclass and a named tuple
    satisfy it. This module only reads a seat; a writable declaration would exclude
    every immutable one and type the callers out of a contract they already meet.
    """

    @property
    def agent_id(self) -> str: ...

    @property
    def role(self) -> str | None: ...


class ProviderLimitWindow(BaseModel):
    """One exhausted account-wide window of a report, as the host named it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    resetsAt: datetime

    @field_validator("resetsAt")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("a provider limit time must carry its timezone")
        return value


class ProviderLimitReport(BaseModel):
    """One report's fact: the moment the account may work again, and when it was seen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    until: datetime
    sourceLabel: str = ""
    accountLabel: str = ""
    windows: tuple[ProviderLimitWindow, ...] = ()
    observedAt: datetime

    @field_validator("until", "observedAt")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("a provider limit time must carry its timezone")
        return value


class ProviderLimits(BaseModel):
    """The whole file: report facts, and the agents each fact holds."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_: str = Field(default=PROVIDER_LIMITS_SCHEMA, alias="schema")
    reports: dict[str, ProviderLimitReport] = Field(default_factory=dict)
    agents: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    updatedAt: datetime | None = None


class LimitedSeat(BaseModel):
    """One limited seat of a task, as rule 13's hover names it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    agentId: str
    source: str
    account: str
    window: str
    resetsAt: datetime


class ProviderLimit(BaseModel):
    """What a task shows in place of ``paused``: the earliest `until`, and its seats."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    until: datetime
    seats: tuple[LimitedSeat, ...] = ()


EMPTY_PROVIDER_LIMITS = ProviderLimits()


def read_provider_limits(path: Path) -> ProviderLimits:
    """Read the file as it stands; a missing or malformed file is no fact at all.

    Rule 14: where Paseo has no report, nothing changes, so an unreadable file
    never raises into a reader that only asks whether anything is limited.
    """

    try:
        return ProviderLimits.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return EMPTY_PROVIDER_LIMITS


def read_provider_limits_file(directory: Path | None) -> ProviderLimits:
    """The file beside the plugin's own, when the caller knows where that is."""

    return (
        EMPTY_PROVIDER_LIMITS
        if directory is None
        else read_provider_limits(directory / PROVIDER_LIMITS_FILE)
    )


def limited_until(limits: ProviderLimits, agent_id: str, now: datetime) -> datetime | None:
    """The agent's own `until` while it is limited now, else ``None`` (rules 9, 11 and 12)."""

    latest: datetime | None = None
    for report_id in limits.agents.get(agent_id, ()):
        report = limits.reports.get(report_id)
        if report is None or report.until <= now:
            continue
        if latest is None or report.until > latest:
            latest = report.until
    return latest


def is_limited(limits: ProviderLimits, agent_id: str, now: datetime) -> bool:
    return limited_until(limits, agent_id, now) is not None


def _latest_report(
    limits: ProviderLimits, agent_id: str, now: datetime
) -> ProviderLimitReport | None:
    chosen: ProviderLimitReport | None = None
    for report_id in limits.agents.get(agent_id, ()):
        report = limits.reports.get(report_id)
        if report is None or report.until <= now:
            continue
        if chosen is None or report.until > chosen.until:
            chosen = report
    return chosen


def _controlling_window(report: ProviderLimitReport) -> ProviderLimitWindow | None:
    """The exhausted window whose reset is the report's own ``until``.

    The producer stores ``until`` as the latest reset among the exhausted windows, so the window
    that says when the seat works again is that one, never whichever window the runtime happened
    to list first. Two windows resetting at the same moment keep the report's own order, so one
    file always names one window.
    """

    controlling: ProviderLimitWindow | None = None
    for window in report.windows:
        if controlling is None or window.resetsAt > controlling.resetsAt:
            controlling = window
    return controlling


def task_provider_limit(
    limits: ProviderLimits,
    seats: Iterable[_Seat],
    now: datetime,
) -> ProviderLimit | None:
    """Rule 13's task fact, from the seat identities the task's own agents carry.

    ``seats`` carries each agent's id and role; it is read here rather than in the
    projection so that the one status function stays the one place the word is
    decided. ``until`` is the earliest of the seats' own limits — the moment the
    task can move again. Each seat names the window that holds it: the one whose
    reset is the seat's own limit, so a weekly window under a session window is
    named with the days it actually waits.
    """

    limited: list[LimitedSeat] = []
    untils: list[datetime] = []
    for seat in seats:
        agent_id = seat.agent_id
        if not agent_id:
            continue
        until = limited_until(limits, agent_id, now)
        if until is None:
            continue
        report = _latest_report(limits, agent_id, now)
        if report is None:
            continue
        window = _controlling_window(report)
        limited.append(
            LimitedSeat(
                role=seat.role or "unknown",
                agentId=agent_id,
                source=report.sourceLabel,
                account=report.accountLabel,
                window=window.label if window is not None else "",
                resetsAt=window.resetsAt if window is not None else until,
            )
        )
        untils.append(until)
    if not limited:
        return None
    return ProviderLimit(until=min(untils), seats=tuple(limited))


def now_utc() -> datetime:
    return datetime.now(UTC)
