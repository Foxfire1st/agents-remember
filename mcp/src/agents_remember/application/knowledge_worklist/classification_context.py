"""One run's computed entry and path classifications, shared with its reviewer facts.

The leaf entry points keep their gate API. The child binds this request-scoped owner while
calling them, then explicitly passes it to the fact reader. It also holds the already-read K_C
for the gate's judgments; exit drops every reference, including on a failed computation.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from agents_remember.application.knowledge_worklist.classify import Classifier
from agents_remember.application.knowledge_worklist.code import Hunk, Resolved
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.memory.knowledge.tree_observation import TreeChange


@dataclass(frozen=True)
class HunkClassification:
    """A computed gate hunk and the entries its changed lines intersect on each side."""

    hunk: Hunk
    before: frozenset[str]
    after: frozenset[str]


@dataclass(frozen=True)
class PathClassification:
    """The gate's computed path, including the placements behind its linkage judgment.

    These are mapped gate placements, not reviewer attribution. A reviewer may use a placement
    only when its entry was recorded at that side's actual blob. Non-text paths have no hunk
    placements; their file-level entries are read by the reviewer as before.
    """

    change: TreeChange
    hunks: tuple[HunkClassification, ...] | None
    before: Mapping[str, Resolved | None] | None = None
    after: Mapping[str, Resolved | None] | None = None


@dataclass
class WorklistClassification:
    """The actual classifier and computed paths of one worklist, never serialized or retained."""

    classifier: Classifier | None = None
    candidate: KnowledgeSide | None = None
    pairing: Mapping[str, Any] = field(default_factory=dict)
    paths: dict[str, PathClassification] = field(default_factory=dict)


_ACTIVE: ContextVar[WorklistClassification | None] = ContextVar(
    "worklist_classification", default=None
)


@contextmanager
def classification_run() -> Iterator[WorklistClassification]:
    owner = WorklistClassification()
    token = _ACTIVE.set(owner)
    try:
        yield owner
    finally:
        _ACTIVE.reset(token)
        owner.classifier = None
        owner.candidate = None
        owner.pairing = {}
        owner.paths.clear()


def share_classifier(
    classifier: Classifier, pairing: Mapping[str, Any], candidate: KnowledgeSide
) -> None:
    owner = _ACTIVE.get()
    if owner is not None:
        owner.classifier = classifier
        owner.candidate = candidate
        owner.pairing = pairing


def share_path(path: PathClassification) -> None:
    owner = _ACTIVE.get()
    if owner is not None:
        owner.paths[path.change.path] = path
