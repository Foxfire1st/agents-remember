"""Rate-limited orchestration nudge records."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import Field, ValidationError

from agents_remember.controlplane.durable_store import (
    ORCHESTRATION_NUDGE_OWNERSHIP,
    DurableRecord,
    append_line,
    exclusive_access,
    read_log_text,
    require_lock_held,
    rewrite_lines,
)
from agents_remember.models.orchestration import NudgeReason, NudgeState

ORCHESTRATION_NUDGE_SCHEMA = "ar-orchestration-nudge/v1"


class OrchestrationNudgeRecord(DurableRecord):
    """One durable nudge attempt keyed by target, subject, and reason."""

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
        records: list[OrchestrationNudgeRecord] = []
        for line in read_log_text(self.log_path()).splitlines():
            if not line.strip():
                continue
            try:
                records.append(OrchestrationNudgeRecord.model_validate_json(line))
            except ValidationError:
                continue
        return records

    def append(self, record: OrchestrationNudgeRecord) -> None:
        ORCHESTRATION_NUDGE_OWNERSHIP.check_declared_writer()
        path = self.log_path()
        with exclusive_access(path, ORCHESTRATION_NUDGE_OWNERSHIP):
            append_line(path, record.model_dump_json(by_alias=True, exclude_none=True))

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

    def compact(self, *, keep: Callable[[OrchestrationNudgeRecord], bool]) -> int:
        """Reclaim the log to the records ``keep`` selects, returning how many were dropped.

        The read, the filter and the rewrite happen under ONE hold of the log's lock. That is
        the whole point: :func:`replace_records` takes a list the caller computed from its own
        earlier read, so anything appended between that read and the rewrite is discarded --
        the exact defect this leaf exists to remove, reachable through the store's own API. Use
        this; the raw primitive is for a caller that already holds the lock.
        """
        path = self.log_path()
        with exclusive_access(path, ORCHESTRATION_NUDGE_OWNERSHIP):
            records = self.read()
            kept = [record for record in records if keep(record)]
            if len(kept) == len(records):
                return 0
            _rewrite(path, kept)
            return len(records) - len(kept)

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
    """Rewrite a nudge log after tests or future compaction select records to keep.

    ``records`` was chosen by a read this call did not make, so this REFUSES unless the caller
    already holds the log's lock -- if it did not, everything appended since its read is about
    to be discarded. Locking only the write here would have looked safe and lost records, which
    is the whole defect in miniature. Prefer :meth:`OrchestrationNudgeStore.compact`, which
    holds the lock across the read and the rewrite so a caller cannot get this wrong.
    """
    require_lock_held(path, ORCHESTRATION_NUDGE_OWNERSHIP.store)
    _rewrite(path, records)


def _rewrite(path: Path, records: list[OrchestrationNudgeRecord]) -> None:
    """Rewrite a nudge log. Its lock -- held by the caller across the read too -- is what makes
    this safe, and it is the ONLY thing checked: ``rewrite_lines`` verifies the lock is held and
    nothing else. No owner check happens here or anywhere below here;
    ``ORCHESTRATION_NUDGE_OWNERSHIP`` is passed so a refusal can name the store."""
    rewrite_lines(
        path,
        [record.model_dump_json(by_alias=True, exclude_none=True) for record in records],
        ORCHESTRATION_NUDGE_OWNERSHIP,
    )


def _elapsed_seconds(previous: str, current: str) -> float | None:
    try:
        return (datetime.fromisoformat(current) - datetime.fromisoformat(previous)).total_seconds()
    except ValueError:
        return None
