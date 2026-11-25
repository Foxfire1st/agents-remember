"""What the task documents of one repository say about each owner, for the state conversion.

The one-time state conversion (:mod:`.state_conversion`) reads two facts per owner
from the task documents of ``tasks/<repository>/``, active and archived:

* the **passed memory review round**: the last ``memoryReviewState.recordedRounds`` entry of kind
  ``memory``, when it passed -- by its stored verdict ``pass`` or ``pass-with-notes``, or, for a
  round recorded before rounds stored their verdict, by leaving no finding open (the closeout's own
  test, :func:`~agents_remember.tasks.route_review.passed_memory_round`). A stored ``block`` is no
  pass, also when it names no finding, and a later round that does not pass takes an earlier pass
  back. The round comes with its verdict reference, where it holds a usable one, and the
  memory tree it passed: the round's own ``memoryTree``, or for a round recorded before rounds
  stored it the state's ``candidateTree`` while that round is the last recorded one; otherwise the
  tree is unknown and stays absent;
* the **task** an owner belongs to: the ``id`` of its task folder's ``task.json``;
* whether the owner was **reopened**: ``task_reopen`` appends a decision of fixed wording to the
  leaf's document. The rounds carry no attempt and no time, so after a reopening the rounds of all
  attempts stand in one sequence and none of them can be tied to one attempt.

A task document is a ``.json`` file directly in a task folder whose ``schema`` is the task
document's; it is read with the task document model. A document that does not fit the model is
still read for its ``id``: its owner gets no passed round, and the reason is kept for the
conversion's report; where only the memory review state does not fit (a round of a wrong type or
value), the rest of the document still counts. A ``.json`` file that does not parse as JSON stops
the conversion, named. A task document without a text ``id`` is reported by path. A round's
verdict reference is read through :func:`~agents_remember.tasks.route_review.usable_verdict_ref`.
Reads only.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from agents_remember.errors import StateConversionError
from agents_remember.models.knowledge_files.canonical import parse_json
from agents_remember.tasks.document import TASK_DOCUMENT_SCHEMA, TaskDocument
from agents_remember.tasks.reopen_decision import reopened_leaf_decision
from agents_remember.tasks.route_review import (
    ReviewRound,
    ReviewState,
    passed_memory_round,
    usable_verdict_ref,
)
from agents_remember.tasks.task_paths import ARCHIVE_DIR, TASK_DOCUMENT_FILENAME, iter_task_folders

REVIEW_STATE: Final = "memoryReviewState"


@dataclass(frozen=True)
class PassedReview:
    """The last passed memory review round one task document records."""

    round: int
    verdict_ref: str | None
    tree: str | None
    document: Path


@dataclass
class TaskOwners:
    """Per owner ID: its task, and its last passed memory review round."""

    tasks: dict[str, str] = field(default_factory=dict)
    passed: dict[str, PassedReview] = field(default_factory=dict)
    ambiguous: set[str] = field(default_factory=set)
    """Owner IDs that more than one task document records a passed memory round for; no fact is
    taken for them."""
    reopenings: dict[str, int] = field(default_factory=dict)
    """Owner ID -> how many reopenings its task document records (absent: none)."""
    unreadable_reviews: dict[str, str] = field(default_factory=dict)
    """Owner ID -> why its stored memory review state could not be read."""
    documents_without_owner: dict[str, str] = field(default_factory=dict)
    """Document path -> why its owner could not be identified."""
    document_paths: set[Path] = field(default_factory=set)


def read_task_owners(task_root: Path) -> TaskOwners:
    """Read every active and archived task document under ``task_root`` (``tasks/<repository>``)."""

    if not task_root.is_dir():
        raise StateConversionError(
            f"the task root {task_root} is no directory; pass --task-root tasks/<repository>"
        )
    owners = TaskOwners()
    folders = [*iter_task_folders(task_root), *iter_task_folders(task_root / ARCHIVE_DIR)]
    for folder in folders:
        _read_folder(folder, owners)
    for owner in owners.ambiguous:
        owners.passed.pop(owner, None)
    return owners


def _read_folder(folder: Path, owners: TaskOwners) -> None:
    documents = list(_task_documents(folder))
    root = next((one for one in documents if one.path.name == TASK_DOCUMENT_FILENAME), None)
    for one in documents:
        owners.document_paths.add(one.path)
        if one.id is None:
            owners.documents_without_owner[one.path.as_posix()] = (
                "the task document names no text id"
            )
            continue
        if one.unreadable is not None:
            owners.unreadable_reviews[one.id] = one.unreadable
        if root is None:
            owners.tasks.setdefault(one.id, one.id)
        elif root.id is not None:
            owners.tasks.setdefault(one.id, root.id)
        if one.document is not None:
            _record_review(one.document, one.path, owners)


def _record_review(document: TaskDocument, path: Path, owners: TaskOwners) -> None:
    reopened = reopened_leaf_decision(document.id)
    reopenings = sum(1 for entry in document.decisions if entry.decision == reopened)
    if reopenings:
        owners.reopenings[document.id] = owners.reopenings.get(document.id, 0) + reopenings
    passed = last_passed_memory_round(document.memoryReviewState, path)
    if isinstance(passed, str):
        owners.unreadable_reviews[document.id] = passed
        return
    if passed is None:
        return
    if document.id in owners.passed:
        owners.ambiguous.add(document.id)
    owners.passed[document.id] = passed


@dataclass(frozen=True)
class _ReadDocument:
    path: Path
    id: str | None
    document: TaskDocument | None
    """The document, read without its memory review state where only that does not fit the model;
    ``None`` where another part does not fit."""
    unreadable: str | None


def _task_documents(folder: Path) -> Iterator[_ReadDocument]:
    """Each task document, with why its owner's memory review cannot be read where it cannot.

    A ``.json`` file that does not parse as JSON stops the run, named: it may be a task document.
    A task document without a text ``id`` is reported by path.
    """

    for path in sorted(folder.glob("*.json")):
        if not path.is_file():
            continue  # a directory or a link to nothing holds no document
        try:
            data = parse_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as error:  # decoding, JSON, canonical form
            raise StateConversionError(
                f"{path} does not parse as JSON ({type(error).__name__}: {error}); the conversion "
                "reads every task document so that no passed review is missed"
            ) from error
        if not isinstance(data, dict) or data.get("schema") != TASK_DOCUMENT_SCHEMA:
            continue
        owner = data.get("id")
        if not isinstance(owner, str):
            yield _ReadDocument(path, None, None, None)
            continue
        try:
            yield _ReadDocument(path, owner, TaskDocument.model_validate(data), None)
            continue
        except ValidationError as error:
            unreadable = _unreadable_review_state(error)
            failure = error
        if unreadable is not None:
            try:
                without = TaskDocument.model_validate({**data, REVIEW_STATE: None})
                yield _ReadDocument(path, owner, without, unreadable)
                continue
            except ValidationError as error:
                failure = error
        yield _ReadDocument(path, owner, None, _unreadable_document(failure))


def _unreadable_review_state(error: ValidationError) -> str | None:
    """Why the memory review state does not parse, or ``None`` when another part fails too."""

    errors = error.errors()
    if not errors or any(not one["loc"] or one["loc"][0] != REVIEW_STATE for one in errors):
        return None
    first = errors[0]
    place = ".".join(str(part) for part in first["loc"])
    return (
        f"its stored memory review state does not parse ({len(errors)} error(s), first at "
        f"{place}: {first['msg']}), so no passed round can be proven from it"
    )


def last_passed_memory_round(
    state: ReviewState | None, document: Path
) -> PassedReview | str | None:
    """The last pass for a lane without open findings, or a reason if its record conflicts."""

    if state is None or state.remainingFindingIds:
        return None
    rounds = state.recordedRounds
    memory = [one for one in rounds if one.kind == "memory"]
    if not memory:
        return None
    latest = max(one.round for one in memory)
    legacy_tree = state.candidateTree if latest == max(one.round for one in rounds) else None
    facts = {_round_fact(row, document, legacy_tree) for row in memory if row.round == latest}
    if len(facts) == 1:
        return facts.pop()
    return f"its recorded memory review round {latest} gives conflicting facts about the pass"


def _round_fact(row: ReviewRound, document: Path, legacy_tree: str | None) -> PassedReview | None:
    if not passed_memory_round(row):
        return None
    return PassedReview(
        round=row.round,
        verdict_ref=usable_verdict_ref(row.verdictRef),
        tree=row.memoryTree or legacy_tree,
        document=document,
    )


def _unreadable_document(error: ValidationError) -> str:
    first = error.errors()[0]
    place = ".".join(str(part) for part in first["loc"]) or "the document"
    return (
        f"its task document does not parse as {TASK_DOCUMENT_SCHEMA} ({error.error_count()} "
        f"error(s), first at {place}: {first['msg']}), so no passed round can be proven from it"
    )
