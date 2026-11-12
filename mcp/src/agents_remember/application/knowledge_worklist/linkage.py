"""MIK-R08 definition 8: changed files and hunks linked by their recorded ranges."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from agents_remember.application.knowledge_worklist.classification_context import (
    HunkClassification,
    PathClassification,
    share_path,
)
from agents_remember.application.knowledge_worklist.code import (
    CodeTrees,
    Hunk,
    Resolved,
    change_hunks,
    hits_new,
    hits_old,
)
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.memory.knowledge.tree_observation import TreeChange


@dataclass
class _Linkage:
    code: CodeTrees
    base: KnowledgeSide
    candidate: KnowledgeSide

    def _linkage(
        self, changes: tuple[TreeChange, ...], renamed: Mapping[str, str]
    ) -> list[dict[str, Any]]:
        return [self._change(change, renamed.get(change.path)) for change in changes]

    def _change(self, change: TreeChange, renamed_to: str | None) -> dict[str, Any]:
        code = self.code
        path = change.path
        base_blob, candidate_blob = code.base().get(path), code.candidate().get(path)
        document: dict[str, Any] = {
            "path": path,
            "status": change.status,
            "content": change.content,
            "modeChange": change.mode_change,
        }
        if renamed_to is not None:
            document["renamedTo"] = renamed_to
        hunks = self._path_hunks(change, base_blob, candidate_blob)
        if hunks is None or change.mode_change:
            # Non-text changes, and the mode fact of a mode change (definition 8), are linked at
            # file level; a text change's hunks are linked below all the same.
            document["fileLevel"] = {"linked": self._file_covered(path)}
        if hunks is None:
            share_path(PathClassification(change, hunks))
            return document
        before = self._placements(self.base, path, base_blob, tree_side="base")
        after = self._placements(self.candidate, path, candidate_blob)
        classified = tuple(_classify_hunk(hunk, before, after) for hunk in hunks)
        share_path(PathClassification(change, classified, before, after))
        document["hunks"] = [
            {**one.hunk.to_document(), "linked": bool(one.before or one.after)}
            for one in classified
        ]
        return document

    def _path_hunks(
        self, change: TreeChange, base_blob: str | None, candidate_blob: str | None
    ) -> tuple[Hunk, ...] | None:
        return change_hunks(self.code, change, base_blob, candidate_blob)

    def _placements(
        self, side: KnowledgeSide, path: str, blob: str | None, *, tree_side: str = "candidate"
    ) -> dict[str, Resolved | None]:
        if blob is None:
            return {}
        placements: dict[str, Resolved | None] = {}
        for entry_id in side.entries_by_path.get(path, ()):
            anchor = side.entries[entry_id].entry.anchor
            placements[entry_id] = self.code.resolve(
                path, anchor.locator.to_document(), anchor.blob, blob, side=tree_side
            )
        return placements

    def _file_covered(self, path: str) -> bool:
        return non_text_linked(
            side.entries[entry_id].entry.anchor.locator.kind
            for side in (self.base, self.candidate)
            for entry_id in side.entries_by_path.get(path, ())
        )


def non_text_linked(locator_kinds: Iterable[str]) -> bool:
    """Whether a non-text change is linked (definition 8): a ``file``-locator entry covers its path.

    ``locator_kinds`` are the locator kinds of every entry either knowledge side records at the path.
    """

    return any(kind == "file" for kind in locator_kinds)


def _classify_hunk(
    hunk: Hunk,
    before: Mapping[str, Resolved | None],
    after: Mapping[str, Resolved | None],
) -> HunkClassification:
    """Definition 8: the hunk's *changed lines* intersect an entry's range on their own side.

    A side with no changed line links nothing there, symmetrically: a delete-only hunk changes no
    line at C, so only a K_B range at B links it (MIK-R10 rule 4); an insertion-only hunk changes
    no line at B, so only a K_C range at C links it (MIK-R09, carried from the L10 review, N3).
    """

    return HunkClassification(
        hunk,
        frozenset(
            entry_id
            for entry_id, resolved in before.items()
            if hunk.old_count > 0 and resolved is not None and hits_old(hunk, resolved.span)
        ),
        frozenset(
            entry_id
            for entry_id, resolved in after.items()
            if hunk.new_count > 0 and resolved is not None and hits_new(hunk, resolved.span)
        ),
    )


def change_linkage(
    code: CodeTrees,
    base: KnowledgeSide,
    candidate: KnowledgeSide,
    changes: tuple[TreeChange, ...],
    renamed: Mapping[str, str],
) -> list[dict[str, Any]]:
    return _Linkage(code, base, candidate)._linkage(changes, renamed)
