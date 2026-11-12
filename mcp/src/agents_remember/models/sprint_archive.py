"""Bounded, sprint-owned proof for archiving one orchestration sprint as a whole (MIK-R82 rule 1).

The record ``notes/reports/sprint-archive.json`` is written into the sprint folder before the
first folder moves, moves with the sprint folder under ``0_archive/``, and protects the archive
the way a retirement record protects a retirement: a repeated request is compared with it by
reason and commanded-master selection, and a request that differs is refused and changes nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_remember.models.task_document_ref import TaskDocumentRef

RECORD_NAME = "sprint-archive.json"
RECORD_VERSION: Literal["sprint-archive/v1"] = "sprint-archive/v1"


class SprintArchiveMasterEntry(BaseModel):
    """One commanded master as the archive recorded it: moved, or staying and why."""

    model_config = ConfigDict(extra="forbid")

    masterRef: TaskDocumentRef
    folder: str = Field(min_length=1, max_length=512)
    archivePath: str = Field(min_length=1, max_length=4096)
    staying: bool = False
    stayReason: str | None = Field(default=None, max_length=4096)


class SprintArchiveRecord(BaseModel):
    """The one record of a sprint archive, and what a repeated request is compared with."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["sprint-archive/v1"] = RECORD_VERSION
    sprintRef: TaskDocumentRef
    reason: str = Field(min_length=1, max_length=4096)
    archivedAt: str
    sprintFolder: str = Field(min_length=1, max_length=512)
    sprintArchivePath: str = Field(min_length=1, max_length=4096)
    sprintJsonSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sprintMarkdownSha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    masters: list[SprintArchiveMasterEntry] = Field(max_length=2048)
    # Ordered move progress: the sprint folder first, then the moving master folders in the
    # deterministic order the first phase moves them. A repeat resumes whatever prefix is done.
    moveOrder: list[str] = Field(default_factory=list, max_length=2049)
    # What the retire-operation readiness found and left as it is when the archive was admitted.
    readinessFacts: list[str] = Field(default_factory=list, max_length=1024)

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("sprint archive requires a nonblank reason")
        return trimmed

    @field_validator("archivedAt")
    @classmethod
    def _timestamp(cls, value: str) -> str:
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("sprint archive time requires a timezone")
        return value

    def selection(self) -> list[str]:
        """The commanded-master selection this record admits repeats against, in stable order."""

        return sorted(
            f"{entry.masterRef.key}:{'staying' if entry.staying else 'moving'}"
            for entry in self.masters
        )
