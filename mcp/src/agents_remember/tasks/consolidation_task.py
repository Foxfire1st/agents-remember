"""Whether a leaf is a consolidation task, and which seats such a leaf has.

A consolidation task reads listed knowledge records again against the code. Its task document
names the list of the pass under ``consolidationList`` before any agent starts, so every reader
that asks -- the set start, the hand-over's sibling seats, the Reviewer's settings row -- gets the
same answer whichever seat starts first. Its seats are a Consolidator and a Reviewer; an ordinary
leaf's are a Worker, a Reviewer and a Curator.

The list's schema is :mod:`.consolidation_list`'s; here only its presence matters: a list that
names at least one id. A caller holding only a reference answers from the document file's
declared key, without resolving the task hierarchy.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

CONSOLIDATION_LIST_KEY = "consolidationList"
ORDINARY_LEAF_SEATS: tuple[str, ...] = ("worker", "reviewer", "curator")
CONSOLIDATION_LEAF_SEATS: tuple[str, ...] = ("consolidator", "reviewer")


def names_consolidation_list(document: TaskDocument | Mapping[str, object]) -> bool:
    """Whether a task document, parsed or as its JSON object, names a list with at least one id."""

    if isinstance(document, TaskDocument):
        listed = document.consolidationList
        return listed is not None and bool(listed.families or listed.rules)
    listed_json = document.get(CONSOLIDATION_LIST_KEY)
    if not isinstance(listed_json, Mapping):
        return False
    return any(isinstance(ids, list) and ids for ids in listed_json.values())


def is_consolidation_task(path: Path) -> bool:
    """Whether the task document file at ``path`` is a consolidation task's.

    A document that cannot be read or parsed is not one here: every start that uses this answer
    resolves the same document afterwards and refuses it there with the document's own reason.
    """

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(document, dict) and names_consolidation_list(document)


def leaf_seat_roles(document: TaskDocument) -> tuple[str, ...]:
    """The seat roles of the leaf of ``document``, in start order."""

    return CONSOLIDATION_LEAF_SEATS if names_consolidation_list(document) else ORDINARY_LEAF_SEATS


def leaf_seat_roles_of(coordination_root: Path, ref: TaskDocumentRef) -> tuple[str, ...]:
    """The seat roles of the leaf ``ref`` names under ``coordination_root``, from its file.

    A reference that escapes its repository's task root names no leaf this module can read; it
    answers the ordinary seats, and the selection's own resolution refuses the reference.
    """

    try:
        path = TaskDocumentTopology(coordination_root).path_for_ref(ref)
    except TaskDocumentRefError:
        return ORDINARY_LEAF_SEATS
    return CONSOLIDATION_LEAF_SEATS if is_consolidation_task(path) else ORDINARY_LEAF_SEATS


__all__ = [
    "CONSOLIDATION_LEAF_SEATS",
    "CONSOLIDATION_LIST_KEY",
    "ORDINARY_LEAF_SEATS",
    "is_consolidation_task",
    "leaf_seat_roles",
    "leaf_seat_roles_of",
    "names_consolidation_list",
]
