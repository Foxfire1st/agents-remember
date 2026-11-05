"""Evidence-bearing grader results; these are observations, never acceptance."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.errors import AuthorityError

from ..adapters.models import NativeCapture, NativeEvent
from ..grade_sources import current_grade_sources, inspect_grade_sources, require_grade_sources

__all__ = [
    "Grade",
    "GradeStatus",
    "current_grade_sources",
    "inspect_grade_sources",
    "native_reference",
    "referenced_events",
]

GradeStatus = Literal["pass", "fail", "unknown", "needs_rubric"]


@dataclass(frozen=True, slots=True)
class Grade:
    grader_id: str
    status: GradeStatus
    evidence: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    producer: dict[str, Any] | None = None

    @classmethod
    def produced(
        cls,
        source: str | Path,
        grader_id: str,
        status: GradeStatus,
        evidence: tuple[str, ...] = (),
        questions: tuple[str, ...] = (),
    ) -> Grade:
        return cls(grader_id, status, evidence, questions, current_grade_sources(source))

    def to_dict(self) -> dict[str, object]:
        if self.producer is None:
            raise AuthorityError(
                "New grade records require an explicit producer; use Grade.produced"
            )
        require_grade_sources(self.producer)
        return asdict(self)


def native_reference(event: NativeEvent) -> str:
    return f"{event.source}#event-{event.sequence}@sha256:{event.source_sha256}"


def referenced_events(
    capture: NativeCapture, sequences: Sequence[int]
) -> tuple[tuple[NativeEvent, ...], tuple[str, ...]]:
    """Resolve exact event identities against capture provenance, not actor prose."""
    sources = {(source.path, source.sha256) for source in capture.source_files}
    indexed = {event.sequence: event for event in capture.events}
    issues: list[str] = []
    found: list[NativeEvent] = []
    if len(indexed) != len(capture.events):
        issues.append("native capture has duplicate event sequence identities")
    for sequence in sequences:
        event = indexed.get(sequence)
        if event is None:
            issues.append(f"native event {sequence} is absent")
        elif event.session_id != capture.session_id:
            issues.append(f"native event {sequence} belongs to another session")
        elif (event.source, event.source_sha256) not in sources:
            issues.append(f"native event {sequence} has no matching captured source/hash")
        else:
            found.append(event)
    return tuple(found), tuple(issues)
