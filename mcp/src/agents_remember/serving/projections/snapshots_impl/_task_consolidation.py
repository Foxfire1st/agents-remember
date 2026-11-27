"""The task page's view of a consolidation pass: its list, and each subject's outcome.

A consolidation task names the list of its pass in its document (``consolidationList``). The
subjects are the listed families and the listed rules with the members of the listed families,
each once. Each subject's outcome comes from the row about it in the task's own history file in
its memory worktree: ``stands`` where the row left the text as it was (``stands`` or
``no_impact``), ``unresolved`` for an ``unresolved`` row, ``corrected`` for any other row, and
``open`` while there is no row.

The memory worktree is found through the task's series contract. Where the contract or the
worktree cannot be read -- before the start, or after the closeout removed the worktree -- the
listed ids stand as listed and every subject counts as open; ``memoryRead`` says so.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    RECORD_DIRECTORIES,
    owner_history_attempt,
    parse_document_text,
    parse_history_document,
)
from agents_remember.models.knowledge_files.history import (
    HistoryFile,
    HistoryRow,
    merged_leaf_history,
)
from agents_remember.models.knowledge_files.records import FamilyRecord
from agents_remember.observer.projection_task_documents import (
    ConsolidationOutcome,
    TaskConsolidationNode,
    TaskConsolidationRowNode,
)
from agents_remember.serving.projections.task_body_generation import body_input
from agents_remember.tasks import TaskDocument
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

# Dispositions of a row that judged a subject and left its text as it was.
_STANDING_DISPOSITIONS = frozenset({"stands", "no_impact"})
_UNRESOLVED = "unresolved"
_HISTORY_DIRECTORY = f"{KNOWLEDGE_ROOT}/history"


@dataclass(frozen=True)
class _TaskMemory:
    """The task's memory worktree and the owner id its history file is named by."""

    root: Path
    owner: str


@dataclass(frozen=True)
class _Subject:
    subject: str
    kind: Literal["family", "rule"]
    family: str | None = None


def consolidation_fields(
    doc: TaskDocument, body_revision: str, *, preview: bool
) -> tuple[TaskConsolidationNode | None, str]:
    """The served consolidation view of one document and its body revision.

    The revision takes in every row, so a new outcome refetches an already-open page even though
    the history file lies outside the task tree. ``preview`` drops the rows: the always-on summary
    carries the counts of the whole list, the on-demand body every row as well. A document that
    names no list keeps its revision as it was.
    """

    node = task_consolidation_node(doc)
    if node is None:
        return None, body_revision
    content = json.dumps(node.model_dump(mode="json"), sort_keys=True)
    revision = hashlib.sha256((body_revision + content).encode()).hexdigest()
    return (node.model_copy(update={"rows": None}) if preview else node), revision


def task_consolidation_node(doc: TaskDocument) -> TaskConsolidationNode | None:
    """The consolidation view of one task document with every row; ``None`` without a list."""

    listed = doc.consolidationList
    if listed is None:
        return None
    families = list(listed.families or ())
    rules = list(listed.rules or ())
    memory = _task_memory(doc)
    subjects = _subjects(families, rules, memory)
    history = _history_rows(memory) if memory is not None else None
    rows = [_row(subject, history) for subject in subjects]
    counts = {outcome: 0 for outcome in ("stands", "corrected", "unresolved", "open")}
    for row in rows:
        counts[row.outcome] += 1
    return TaskConsolidationNode(
        families=families,
        rules=rules,
        memoryRead=history is not None,
        total=len(rows),
        stands=counts["stands"],
        corrected=counts["corrected"],
        unresolved=counts["unresolved"],
        open=counts["open"],
        rows=rows,
    )


def _task_memory(doc: TaskDocument) -> _TaskMemory | None:
    if not doc.seriesContractPath:
        return None
    path = Path(doc.seriesContractPath)
    try:
        with body_input(path):
            contract = load_contract(path)
    except (OSError, ContractError):
        return None
    root = contract.memory_worktree
    if root is None or not root.is_dir():
        return None
    return _TaskMemory(root=root, owner=contract.leaf_id or contract.task_name)


def _subjects(
    families: Sequence[str], rules: Sequence[str], memory: _TaskMemory | None
) -> list[_Subject]:
    """Each subject once: the listed families, the listed rules, then the families' members."""

    seen: set[str] = set()
    subjects: list[_Subject] = []

    def add(subject: _Subject) -> None:
        if subject.subject not in seen:
            seen.add(subject.subject)
            subjects.append(subject)

    for family in families:
        add(_Subject(family, "family"))
    for rule in rules:
        add(_Subject(rule, "rule"))
    for family in families:
        for member in _family_members(memory, family):
            add(_Subject(member, "rule", family=family))
    return subjects


def _family_members(memory: _TaskMemory | None, family: str) -> tuple[str, ...]:
    if memory is None:
        return ()
    directory = memory.root / KNOWLEDGE_ROOT / RECORD_DIRECTORIES["family"]
    for path in sorted(directory.glob(f"{family}-*.json")):
        try:
            with body_input(path):
                record = parse_document_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(record, FamilyRecord) and record.id == family:
            return record.members
    return ()


def _history_rows(memory: _TaskMemory) -> Mapping[str, HistoryRow] | None:
    """The rows of the task's history by subject, all its attempts read as one.

    Empty while the task has no history file yet. ``None`` when a file is there but cannot be
    read by the history schema: the outcomes are then unknown, and the view says so.
    """

    files: list[HistoryFile] = []
    for path in _owner_history_files(memory):
        try:
            with body_input(path):
                text = path.read_text(encoding="utf-8")
            files.append(parse_history_document(f"{_HISTORY_DIRECTORY}/{path.name}", text))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
    merged = merged_leaf_history(files)
    return {row.subject: row for row in merged.rows} if merged is not None else {}


def _owner_history_files(memory: _TaskMemory) -> Iterator[Path]:
    directory = memory.root / _HISTORY_DIRECTORY
    with body_input(directory):
        candidates = sorted(directory.glob(f"{memory.owner}*.json"))
    for candidate in candidates:
        relative = f"{_HISTORY_DIRECTORY}/{candidate.name}"
        if owner_history_attempt(relative, memory.owner) is not None:
            yield candidate


def _row(subject: _Subject, history: Mapping[str, HistoryRow] | None) -> TaskConsolidationRowNode:
    row = history.get(subject.subject) if history is not None else None
    return TaskConsolidationRowNode(
        subject=subject.subject,
        kind=subject.kind,
        outcome=_outcome(row),
        family=subject.family,
        disposition=row.disposition if row is not None else None,
        reason=row.reason if row is not None else None,
    )


def _outcome(row: HistoryRow | None) -> ConsolidationOutcome:
    if row is None:
        return "open"
    if row.disposition in _STANDING_DISPOSITIONS:
        return "stands"
    if row.disposition == _UNRESOLVED:
        return "unresolved"
    return "corrected"
