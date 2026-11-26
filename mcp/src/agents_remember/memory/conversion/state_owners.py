"""Who wrote a record's current revision, and the stamps and facts the state conversion writes.

The rules, without guessing:

* the owner of a **first revision** is its origin's leaf or wave;
* the owner of a **later revision** is the owner whose history file holds the ``changed`` row that
  names the record at that revision: the row of the change that raised the revision. Rows that
  only examined a revision (``no_impact``, ``moved``, ``extended``, ``rerouted``, ``assigned``) or
  retired it name no writer. Where no such row exists, or rows of two different owners claim the
  same revision, no owner is found and the revision stays proposed;
* the **task** of an owner is the record's origin task when the owner is the origin's, else the
  task its task document belongs to, else the task a crossing ID names; else ``unknown``, also
  where the task found is no value a stamp can hold.

A record whose current revision a history file's ``written`` list already names takes that recorded
stamp. Any other stamp the conversion writes reads ``unknown`` for role, harness, model, effort and
seat, with the reason, and names the conversion as its writer (``by: conversion``, ``operation:
state-conversion``). A review fact it writes (:mod:`agents_remember.models.knowledge_files.\
converted_reviews`) carries a reviewer's and a hand-over stamp of the same kind.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Final, Literal

from pydantic import TypeAdapter, ValidationError

from agents_remember.memory.conversion.task_review_facts import PassedReview, TaskOwners
from agents_remember.models.knowledge_files.history import HistoryFile, ReviewFact
from agents_remember.models.knowledge_files.records import KnowledgeRecord
from agents_remember.models.knowledge_files.shapes import Label
from agents_remember.models.knowledge_files.writer_stamp import UNKNOWN, WriterStamp
from agents_remember.worktrees.role_reports import (
    COLLECTED_REPORT_PARTS,
    ROLE_REPORT_DIRECTORY,
    collected_report_ref,
)

CONVERSION_OPERATION: Final = "state-conversion"
WRITING_DISPOSITION: Final = "changed"
NOT_RECORDED: Final = "not recorded before writers were stamped"
NO_TASK_DOCUMENT: Final = "no task id is recorded for this owner"
UNSTAMPABLE_TASK: Final = "the recorded task id cannot name a known task in a stamp"
REVIEWER_NOT_RECORDED: Final = "no record names the reviewer of a round recorded before stamps"
HANDOVER_NOT_RECORDED: Final = (
    "no record names the agent that handed the memory change to the review before stamps"
)
_AGENT_FIELDS: Final = ("role", "harness", "model", "effort", "seat")
_CROSSING_TASK: Final = re.compile(r"^(?P<task>.+)-crossing-[1-9][0-9]*$")
_STAMP_VALUE: Final = TypeAdapter(Label)
_REPORT_MARKERS: Final = ((ROLE_REPORT_DIRECTORY,), COLLECTED_REPORT_PARTS)
_WINDOWS_DRIVE: Final = re.compile(r"^[A-Za-z]:/")

OwnerKind = Literal["leaf", "wave", "crossing"]


@dataclass(frozen=True, order=True)
class Owner:
    """A leaf, wave or crossing that writes into the memory."""

    kind: OwnerKind
    id: str


@dataclass
class HistoryIndex:
    """The history files of one tree: each owner's attempts, and the writers of each revision."""

    attempts: dict[str, dict[int, HistoryFile]] = field(default_factory=dict)
    writers: dict[tuple[str, int], set[Owner]] = field(default_factory=dict)

    @classmethod
    def of(cls, histories: Iterable[HistoryFile]) -> HistoryIndex:
        index = cls()
        for history in histories:
            owner = Owner(history.owner_kind, history.owner_id)
            index.attempts.setdefault(owner.id, {})[history.attempt_number] = history
            for row in history.rows:
                revision = getattr(row, "revision", None)
                if isinstance(revision, int) and row.disposition == WRITING_DISPOSITION:
                    index.writers.setdefault((row.subject, revision), set()).add(owner)
        return index


@dataclass(frozen=True)
class OwnerFinding:
    """The owner found for a record's current revision, or why none was found."""

    owner: Owner | None
    ambiguous: bool = False


def revision_owner(record: KnowledgeRecord, index: HistoryIndex) -> OwnerFinding:
    """The owner that wrote ``record`` at its current revision (module docstring)."""

    if record.revision == 1:
        origin = record.origin
        if origin.leaf is not None:
            return OwnerFinding(Owner("leaf", origin.leaf))
        return OwnerFinding(Owner("wave", origin.wave) if origin.wave is not None else None)
    writers = index.writers.get((record.id, record.revision), set())
    if len(writers) == 1:
        return OwnerFinding(next(iter(writers)))
    return OwnerFinding(None, ambiguous=len(writers) > 1)


def record_stamp(
    record: KnowledgeRecord, finding: OwnerFinding, owners: TaskOwners, *, at: str
) -> WriterStamp:
    """The conversion's stamp of ``record``'s current revision."""

    origin = record.origin
    origin_owner = origin.leaf or origin.wave
    owner = finding.owner
    if owner is None or owner.id == origin_owner:
        return _stamp(owner, origin.task, None, at=at, reason=NOT_RECORDED)
    task, task_reason = owner_task(owner, owners)
    return _stamp(owner, task, task_reason, at=at, reason=NOT_RECORDED)


def owner_task(owner: Owner, owners: TaskOwners) -> tuple[str, str | None]:
    """``(task, None)`` where the task is known and a stamp can hold it, else ``(unknown, reason)``."""

    task = owners.tasks.get(owner.id)
    if task is None:
        crossing = _CROSSING_TASK.match(owner.id) if owner.kind == "crossing" else None
        task = crossing["task"] if crossing is not None else None
    if task is None:
        return UNKNOWN, NO_TASK_DOCUMENT
    try:
        return _STAMP_VALUE.validate_python(task), None
    except ValidationError:
        return UNKNOWN, UNSTAMPABLE_TASK


def review_fact(owner: Owner, passed: PassedReview, owners: TaskOwners, *, at: str) -> ReviewFact:
    """The converted fact of ``owner``'s last passed memory review round."""

    task, task_reason = owner_task(owner, owners)
    document: dict[str, object] = {
        "round": passed.round,
        "reviewer": _stamp(owner, task, task_reason, at=at, reason=REVIEWER_NOT_RECORDED),
        "handedOverBy": _stamp(owner, task, task_reason, at=at, reason=HANDOVER_NOT_RECORDED),
    }
    if passed.verdict_ref is not None:
        document["verdictRef"] = _durable_verdict_ref(passed.verdict_ref)
    if passed.tree is not None:
        document["tree"] = passed.tree
    return ReviewFact.model_validate(document)


def _durable_verdict_ref(reference: str) -> str:
    """Map a worktree or absolute report path to its durable ``notes/reports/<rel>`` copy."""

    cleaned = reference.strip().replace("\\", "/")
    parts = [part for part in cleaned.split("/") if part and part != "."]
    if ".." in parts:
        return cleaned
    is_absolute = cleaned.startswith("/") or bool(_WINDOWS_DRIVE.match(cleaned))
    tail = _report_tail(parts, absolute=is_absolute)
    if not tail:
        return cleaned
    return collected_report_ref(PurePosixPath(*tail))


def _report_tail(parts: list[str], *, absolute: bool) -> list[str] | None:
    """Return path segments under ``role-reports`` or ``notes/reports``, or ``None``."""

    if not absolute:
        for marker in _REPORT_MARKERS:
            if tuple(parts[: len(marker)]) == marker:
                return parts[len(marker) :]
        return None
    best: int | None = None
    for marker in _REPORT_MARKERS:
        width = len(marker)
        for index in range(len(parts) - width + 1):
            if tuple(parts[index : index + width]) == marker:
                end = index + width
                if best is None or end > best:
                    best = end
    return None if best is None else parts[best:]


def _stamp(
    owner: Owner | None, task: str, task_reason: str | None, *, at: str, reason: str
) -> WriterStamp:
    unknown: dict[str, str] = dict.fromkeys(_AGENT_FIELDS, reason)
    if task == UNKNOWN:
        unknown["task"] = task_reason or UNSTAMPABLE_TASK
    document: dict[str, object] = {
        "by": "conversion",
        "operation": CONVERSION_OPERATION,
        **dict.fromkeys(_AGENT_FIELDS, UNKNOWN),
        "task": task,
        "at": at,
        "unknown": unknown,
    }
    if owner is not None:
        document[owner.kind] = owner.id
    return WriterStamp.model_validate(document)
