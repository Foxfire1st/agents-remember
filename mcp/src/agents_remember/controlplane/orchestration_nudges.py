"""Rate-limited orchestration nudge records."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ORCHESTRATION_NUDGE_SCHEMA = "ar-orchestration-nudge/v1"
NudgeReason = Literal["inactive", "missing-turn-report", "manual"]
NudgeState = Literal["sent", "rate-limited"]


class OrchestrationNudgeRecord(BaseModel):
    """One durable nudge attempt keyed by target, subject, and reason."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=ORCHESTRATION_NUDGE_SCHEMA, alias="schema")
    id: str
    ts: str
    state: NudgeState
    reason: NudgeReason
    targetAgentId: str | None = None
    targetLifecycleId: str | None = None
    subjectAgentId: str | None = None
    subjectLifecycleId: str | None = None
    artifactPath: str | None = None
    message: str


class OrchestrationNudgeStore:
    """Append-only nudge log with per-target rate limiting."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    def log_path(self) -> Path:
        return self._root / "workspace" / "orchestration-nudges.jsonl"

    def read(self) -> list[OrchestrationNudgeRecord]:
        """Read the nudge log, skipping any torn/legacy line (F12: dashboard-tolerant reader)."""
        path = self.log_path()
        if not path.exists():
            return []
        records: list[OrchestrationNudgeRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(OrchestrationNudgeRecord.model_validate_json(line))
            except ValidationError:
                continue
        return records

    def append(self, record: OrchestrationNudgeRecord) -> None:
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json(by_alias=True, exclude_none=True) + "\n")

    def last_sent(
        self,
        *,
        target_agent_id: str | None,
        target_lifecycle_id: str | None,
        subject_agent_id: str | None,
        subject_lifecycle_id: str | None,
        reason: NudgeReason,
    ) -> OrchestrationNudgeRecord | None:
        matches = [
            record
            for record in self.read()
            if record.state == "sent"
            and record.reason == reason
            and record.targetAgentId == target_agent_id
            and record.targetLifecycleId == target_lifecycle_id
            and record.subjectAgentId == subject_agent_id
            and record.subjectLifecycleId == subject_lifecycle_id
        ]
        return max(matches, key=lambda record: record.ts, default=None)

    def record(
        self,
        record: OrchestrationNudgeRecord,
        *,
        rate_limit_seconds: int,
    ) -> OrchestrationNudgeRecord:
        previous = self.last_sent(
            target_agent_id=record.targetAgentId,
            target_lifecycle_id=record.targetLifecycleId,
            subject_agent_id=record.subjectAgentId,
            subject_lifecycle_id=record.subjectLifecycleId,
            reason=record.reason,
        )
        if previous is not None:
            elapsed = _elapsed_seconds(previous.ts, record.ts)
            if elapsed is not None and elapsed < rate_limit_seconds:
                record = record.model_copy(update={"state": "rate-limited"})
        self.append(record)
        return record


def nudge_message(reason: NudgeReason, *, subject: str, artifact_path: str | None = None) -> str:
    """Format the stdin nudge text sent to the manager session."""
    if reason == "inactive":
        return f"Nudge: {subject} appears inactive. Check the worker and record the next action."
    if reason == "missing-turn-report":
        suffix = f" Expected artifact: {artifact_path}." if artifact_path else ""
        return f"Nudge: {subject} ended a turn without its worker turn report.{suffix}"
    return f"Nudge: {subject}"


def missing_artifact(path: Path) -> bool:
    """True when the expected turn-report artifact is absent or empty."""
    return not path.exists() or path.stat().st_size == 0


def replace_records(path: Path, records: list[OrchestrationNudgeRecord]) -> None:
    """Rewrite a nudge log after tests or future compaction select records to keep."""
    if not records:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        "\n".join(record.model_dump_json(by_alias=True, exclude_none=True) for record in records) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _elapsed_seconds(previous: str, current: str) -> float | None:
    try:
        return (datetime.fromisoformat(current) - datetime.fromisoformat(previous)).total_seconds()
    except ValueError:
        return None
