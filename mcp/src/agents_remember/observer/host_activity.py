"""The host publication and retained agent facts consumed by the task projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agents_remember.observer.provider_limits import ProviderLimits

HOST_ACTIVITY_FILE = "host-activity.json"


class DeclaredWait(BaseModel):
    """A seat's open declared wait, as the plugin publishes it beside the host's own state.

    A seat that ends its turn with ``WAITING FOR SUBAGENTS:`` or ``WAITING FOR BACKGROUND:``
    waits for its helpers or for a process it started. The host still reports it idle, so
    the plugin publishes what it waits for, since when the wait has been open, the plugin's
    next check while one is armed, and the background declaration's own words. The wait never
    moves ``state`` or ``eventAt``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    waitsFor: tuple[Literal["subagent", "background"], ...] = Field(min_length=1)
    since: datetime
    checkDueAt: datetime | None = None
    detail: str | None = Field(default=None, max_length=300)

    @field_validator("since", "checkDueAt")
    @classmethod
    def _aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("declared wait time must carry its timezone")
        return value


class WaitingSeat(BaseModel):
    """One seat of a task shown as waiting: its declared wait, with the seat it belongs to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agentId: str
    role: str | None = None
    waitsFor: tuple[Literal["subagent", "background"], ...]
    since: datetime
    checkDueAt: datetime | None = None
    detail: str | None = None


class PublishedAgentActivity(BaseModel):
    """One agent as the plugin publishes it.

    ``state`` and ``eventAt`` keep their meaning — the agent's turn. MIK-R190 rule 2
    adds two optional subagent fields and still forbids every other: the count is
    absent at 0, and ``subagentsSince`` is the oldest running subagent's start, so
    an agent that works only by its subagents has an age of its own. A subagent
    field never moves ``eventAt``.

    MIK-R200 rule 2 adds the one field a permission wait needs to be an occurrence: the
    plugin publishes the ids of the native prompts that are still unanswered, absent when none
    are, so the attention queue can keep one row per outstanding prompt and end exactly the
    occurrence that was answered. The prompt's arguments are never published.

    ``declaredWait`` is the seat's open declared wait (``DeclaredWait``), absent when none is
    open; it lets AR's surfaces show a waiting seat as waiting while the host says idle.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["in-turn", "permission", "idle", "ended"]
    eventAt: datetime | None = None
    runningSubagents: int | None = Field(default=None, ge=0)
    subagentsSince: datetime | None = None
    permissionPrompts: tuple[str, ...] = ()
    declaredWait: DeclaredWait | None = None

    @field_validator("eventAt", "subagentsSince")
    @classmethod
    def _aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("host event time must carry its timezone")
        return value


class HostActivityPublication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bootId: str = Field(min_length=1)
    complete: bool
    agents: dict[str, PublishedAgentActivity]
    ignoredEvents: int = Field(ge=0)


def read_host_activity_publication(path: Path) -> HostActivityPublication | None:
    """Read the plugin's publication with the same validation for every consumer."""
    try:
        return HostActivityPublication.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


@dataclass(frozen=True)
class TaskAgentActivity:
    task_key: str
    activity: PublishedAgentActivity
    # MIK-R190 rule 13: a limited task names each of its seats, so the reader keeps the
    # identity the role record already holds beside the published activity.
    agent_id: str = ""
    role: str | None = None


@dataclass(frozen=True)
class HostActivitySnapshot:
    known: bool = False
    agents: tuple[TaskAgentActivity, ...] = ()
    ignored_agents: int = 0
    # MIK-R190 rule 13: the one limited-fact file, read where the serves already reads.
    limits: ProviderLimits | None = None
    # MIK-R200 rule 4 (L190-R1-F04): the native boot the listing belongs to. A clock that
    # spans two boots spans an interval nobody observed, so a change here breaks it.
    boot_id: str | None = None
