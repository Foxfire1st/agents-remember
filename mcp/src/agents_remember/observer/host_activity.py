"""The host publication and retained agent facts consumed by the task projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

HOST_ACTIVITY_FILE = "host-activity.json"


class PublishedAgentActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["in-turn", "permission", "idle", "ended"]
    eventAt: datetime | None = None

    @field_validator("eventAt")
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


@dataclass(frozen=True)
class HostActivitySnapshot:
    known: bool = False
    agents: tuple[TaskAgentActivity, ...] = ()
    ignored_agents: int = 0
