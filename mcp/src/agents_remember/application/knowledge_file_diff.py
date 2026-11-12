"""The Git diff of a memory repository's knowledge files, as the ``knowledge_diff`` tool serves it.

A knowledge file is any file the validator reads under ``knowledge/`` and ``onboarding/``
(:func:`is_knowledge_path`): record, history and census files, onboarding sidecars, and the Markdown
files beside them -- an onboarding card beside its sidecar, a record's prose beside its record file.

The reviewer's tree diff (:func:`knowledge_tree_diff`) lists, patches and groups the changes. It
groups a file by the JSON document it holds, so a Markdown file arrives ungrouped. This module puts
each one beside the JSON file it belongs to: a card under the source path its sidecar declares, a
record's prose under its record. A Markdown file with no such companion stays under ``other``.

It also narrows a diff to one record or one file, and answers which records and paths the two
trees hold, so that a selector naming nothing in either tree can be refused instead of answered
with an empty diff.

**The bound.** One answer has a size limit that its caller states as a test (:func:`bounded_diff`).
The files keep the answer's order and the answer holds the patches of as many leading files as fit;
every file left out is named, so that the caller can ask for it by ``path``. Names come before
patches: when the names of the left-out files do not all fit, the answer holds no patch and as many
names as fit, with the number that could not be named. A single patch too long for the limit is cut
and named as cut. Nothing is dropped without being counted.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agents_remember.application.review_tree_knowledge import knowledge_tree_diff
from agents_remember.kernel.git_command import read_git_blobs_bytes
from agents_remember.memory_quality.converted_cards import card_sidecar_path
from agents_remember.memory_quality.knowledge_validator.trees import is_knowledge_path, tree_blobs
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeFileChange,
    ReviewKnowledgeRecordGroup,
    ReviewKnowledgeSourceGroup,
    ReviewKnowledgeTreeDiff,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    ONBOARDING_ROOT,
    RECORD_DIRECTORIES,
    split_record_filename,
)
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH

__all__ = [
    "BoundedDiff",
    "HeldPaths",
    "LeftOut",
    "bounded_diff",
    "held_paths",
    "knowledge_file_diff",
    "narrowed_diff",
]

_PROSE_SUFFIX: Final = ".md"
_RECORD_KINDS: Final[Mapping[str, str]] = {
    directory: kind for kind, directory in RECORD_DIRECTORIES.items()
}


def knowledge_file_diff(repository: Path, before: str, after: str) -> ReviewKnowledgeTreeDiff:
    """Every changed knowledge file between two memory trees, each beside the file it belongs to."""

    diff = knowledge_tree_diff(repository, before, after, served=is_knowledge_path)
    prose = tuple(change for change in diff.other if change.path.endswith(_PROSE_SUFFIX))
    if not prose:
        return diff
    records = {group.record_id: group for group in diff.records}
    sources = list(diff.sources)
    declared = _declared_sources(repository, before, after, prose, sources)
    placed: set[str] = set()
    for change in prose:
        record = _record_of(change.path)
        if record is not None:
            group = records.get(record[0]) or ReviewKnowledgeRecordGroup(
                record_id=record[0], kind=record[1]
            )
            records[record[0]] = group.model_copy(update={"files": (*group.files, change)})
        elif change.path in declared:
            _join_source(sources, change, declared[change.path])
        else:
            continue
        placed.add(change.path)
    return diff.model_copy(
        update={
            "records": tuple(records[key] for key in sorted(records)),
            "sources": tuple(sorted(sources, key=lambda group: group.source_path)),
            "other": tuple(change for change in diff.other if change.path not in placed),
        }
    )


def _record_of(path: str) -> tuple[str, str] | None:
    """``(record id, kind)`` for a file in a record directory that is named after a record."""

    parts = path.split("/")
    if len(parts) != 3 or parts[0] != KNOWLEDGE_ROOT or parts[1] not in _RECORD_KINDS:
        return None
    try:
        return split_record_filename(parts[2])[0], _RECORD_KINDS[parts[1]]
    except ValueError:
        return None


def _join_source(
    sources: list[ReviewKnowledgeSourceGroup], change: ReviewKnowledgeFileChange, source: str
) -> None:
    """Put a card into its sidecar's group, or into a group of its own when only the card changed."""

    sidecar = card_sidecar_path(change.path)
    for position, group in enumerate(sources):
        if group.source_path == source and any(one.path == sidecar for one in group.files):
            sources[position] = group.model_copy(update={"files": (*group.files, change)})
            return
    sources.append(ReviewKnowledgeSourceGroup(source_path=source, files=(change,)))


def _declared_sources(
    repository: Path,
    before: str,
    after: str,
    prose: Iterable[ReviewKnowledgeFileChange],
    sources: Iterable[ReviewKnowledgeSourceGroup],
) -> dict[str, str]:
    """Each changed onboarding card's source: the ``path`` the sidecar beside it declares.

    A sidecar that changed too is already a source group, which names the path. Any other sidecar
    is read from the tree that holds the card: the after tree, or the before tree for a deleted card.
    The two trees are listed and the sidecars read in one Git child each, whatever their number.
    """

    cards = [change for change in prose if change.path.startswith(f"{ONBOARDING_ROOT}/")]
    known = {one.path: group.source_path for group in sources for one in group.files}
    found = {
        card.path: known[card_sidecar_path(card.path)]
        for card in cards
        if card_sidecar_path(card.path) in known
    }
    holders = {
        card.path: before if card.status == "deleted" else after
        for card in cards
        if card.path not in found
    }
    listings = {tree: tree_blobs(repository, tree) for tree in set(holders.values())}
    blobs = {
        card_path: listings[tree].get(card_sidecar_path(card_path))
        for card_path, tree in holders.items()
    }
    contents = read_git_blobs_bytes(repository, {blob for blob in blobs.values() if blob})
    for card_path, blob in blobs.items():
        source = None if blob is None else _declared_path(contents[blob])
        if source is not None:
            found[card_path] = source
    return found


def _declared_path(data: bytes) -> str | None:
    """The ``path`` a sidecar's JSON object declares, or ``None`` when it declares none."""

    try:
        document = json.loads(data.decode("utf-8", "surrogateescape"))
    except ValueError:
        return None
    declared = document.get("path") if isinstance(document, dict) else None
    return declared if isinstance(declared, str) and declared else None


@dataclass(frozen=True)
class HeldPaths:
    """Every file path the compared trees hold, for the question "does this selector name anything"."""

    paths: frozenset[str]

    def holds_record(self, record_id: str) -> bool:
        """Whether a record file of one of the trees carries this identifier."""

        found = (_record_of(path) for path in self.paths)
        return any(record is not None and record[0] == record_id for record in found)

    def names(self, path: str) -> bool:
        """Whether ``path`` is a knowledge file of one of the trees, or a source one of them documents.

        A source is documented when its card or its sidecar exists; a route when its overview does.
        """

        stem = ONBOARDING_ROOT if path == ROOT_ROUTE_PATH else f"{ONBOARDING_ROOT}/{path}"
        documented = {f"{stem}.json", f"{stem}.md", f"{stem}/overview.json", f"{stem}/overview.md"}
        return (is_knowledge_path(path) and path in self.paths) or bool(documented & self.paths)


def held_paths(repository: Path, trees: Iterable[str]) -> HeldPaths:
    """The paths of every file in the given memory trees, each tree listed once."""

    return HeldPaths(
        frozenset(path for tree in dict.fromkeys(trees) for path in tree_blobs(repository, tree))
    )


def narrowed_diff(
    diff: ReviewKnowledgeTreeDiff, record_id: str | None, path: str | None
) -> ReviewKnowledgeTreeDiff:
    """The diff cut to the record or the file the caller named; ``changed_files`` follows."""

    if record_id is None and path is None:
        return diff

    def at_path(change: ReviewKnowledgeFileChange) -> bool:
        return path is None or path in (change.path, change.old_path)

    records: list[ReviewKnowledgeRecordGroup] = []
    for group in diff.records:
        if record_id not in (None, group.record_id):
            continue
        files = tuple(change for change in group.files if at_path(change))
        entries = tuple(entry for entry in group.entries if path in (None, entry[1]))
        if files or entries:
            records.append(group.model_copy(update={"files": files, "entries": entries}))
    sources: list[ReviewKnowledgeSourceGroup] = []
    for source in diff.sources:
        if record_id is not None and record_id not in source.records:
            continue
        # A source path selects the source's whole group; a file path selects that one file.
        files = source.files if path in (None, source.source_path) else ()
        files = files or tuple(change for change in source.files if at_path(change))
        if files:
            sources.append(source.model_copy(update={"files": files}))
    unrecorded = record_id is None
    history = tuple(change for change in diff.history if unrecorded and at_path(change))
    other = tuple(change for change in diff.other if unrecorded and at_path(change))
    paths = {
        change.path
        for change in (
            *(c for group in records for c in group.files),
            *(c for group in sources for c in group.files),
            *history,
            *other,
        )
    }
    return diff.model_copy(
        update={
            "records": tuple(records),
            "sources": tuple(sources),
            "history": history,
            "other": other,
            "changed_files": len(paths),
        }
    )


# -- the bound ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LeftOut:
    """What a bounded answer does not hold: files without their patch, and patches cut short.

    ``paths`` names the left-out files in the answer's order; it is shorter than ``files`` only
    when the names themselves did not fit. ``cut_patches`` names each file that is in the answer
    with a patch that is not whole.
    """

    files: int = 0
    paths: tuple[str, ...] = ()
    cut_patches: tuple[str, ...] = ()

    @property
    def unnamed(self) -> int:
        return self.files - len(self.paths)


@dataclass(frozen=True)
class BoundedDiff:
    """One answer's part of a diff, with what it leaves out."""

    diff: ReviewKnowledgeTreeDiff
    left_out: LeftOut

    @property
    def complete(self) -> bool:
        return not self.left_out.files and not self.left_out.cut_patches


def bounded_diff(diff: ReviewKnowledgeTreeDiff, fits: Callable[[BoundedDiff], bool]) -> BoundedDiff:
    """The leading part of ``diff`` that ``fits`` one answer, with everything left out named.

    ``fits`` is the caller's size test of a whole answer. It is assumed to stay false once an
    answer grew past it, which holds because a file's patch is longer than its name.
    """

    files = _files_in_order(diff)
    count = len(files)
    kept = _largest(count, lambda number: fits(_part(diff, files, number, count - number)))
    if kept == count:
        return _part(diff, files, count, 0)
    if kept:
        return _part(diff, files, kept, count - kept)
    if not fits(_part(diff, files, 0, count)):
        named = _largest(count, lambda number: fits(_part(diff, files, 0, number)))
        return _part(diff, files, 0, named)
    return _first_patch_cut(diff, files, fits)


def _largest(limit: int, fits: Callable[[int], bool]) -> int:
    """The largest number in ``0..limit`` that fits, found without trying an answer far too large.

    Doubling first keeps every tried answer near the size of the one returned, so the search costs
    the same for a diff of ten files and for one of ten thousand.
    """

    if limit == 0 or not fits(1):
        return 0
    low, high = 1, 2
    while high <= limit and fits(high):
        low, high = high, high * 2
    high = min(high, limit + 1)
    while high - low > 1:
        middle = (low + high) // 2
        if fits(middle):
            low = middle
        else:
            high = middle
    return low


def _files_in_order(diff: ReviewKnowledgeTreeDiff) -> list[ReviewKnowledgeFileChange]:
    """Every file of the diff once, in the order the answer lists its groups."""

    return [
        *(change for group in diff.records for change in group.files),
        *(change for group in diff.sources for change in group.files),
        *diff.history,
        *diff.other,
    ]


def _part(
    diff: ReviewKnowledgeTreeDiff,
    files: list[ReviewKnowledgeFileChange],
    kept: int,
    named: int,
) -> BoundedDiff:
    """The diff with the first ``kept`` files, and the next ``named`` of the others named."""

    cut = tuple(change.path for change in files[:kept] if change.truncated)
    if kept == len(files):
        return BoundedDiff(diff, LeftOut(cut_patches=cut))
    shown = {change.path: change for change in files[:kept]}

    def held(changes: Iterable[ReviewKnowledgeFileChange]) -> tuple[ReviewKnowledgeFileChange, ...]:
        return tuple(shown[change.path] for change in changes if change.path in shown)

    sources = tuple(
        group.model_copy(update={"files": held(group.files)})
        for group in diff.sources
        if held(group.files)
    )
    # An entry is shown with the sidecar that holds it, so nothing in the answer points at a patch
    # the answer left out.
    with_sidecar = {group.source_path for group in sources}
    records = tuple(
        group.model_copy(
            update={
                "files": held(group.files),
                "entries": tuple(entry for entry in group.entries if entry[1] in with_sidecar),
            }
        )
        for group in diff.records
    )
    part = diff.model_copy(
        update={
            "records": tuple(group for group in records if group.files or group.entries),
            "sources": sources,
            "history": held(diff.history),
            "other": held(diff.other),
        }
    )
    left = files[kept:]
    return BoundedDiff(
        part,
        LeftOut(
            files=len(left),
            paths=tuple(change.path for change in left[:named]),
            cut_patches=cut,
        ),
    )


def _first_patch_cut(
    diff: ReviewKnowledgeTreeDiff,
    files: list[ReviewKnowledgeFileChange],
    fits: Callable[[BoundedDiff], bool],
) -> BoundedDiff:
    """The first file with as much of its patch as fits beside the names of the others.

    This is the answer when not even one whole patch fits: without it a request for one long file
    could never show any of it. The cut ends at a line end where the patch has one.
    """

    first, others = files[0], len(files) - 1

    def swapped(
        changes: Iterable[ReviewKnowledgeFileChange], cut: ReviewKnowledgeFileChange
    ) -> tuple[ReviewKnowledgeFileChange, ...]:
        return tuple(cut if change.path == cut.path else change for change in changes)

    def with_patch(length: int) -> BoundedDiff:
        cut = first.model_copy(update={"patch": first.patch[:length], "truncated": True})
        shorter = diff.model_copy(
            update={
                "records": tuple(
                    group.model_copy(update={"files": swapped(group.files, cut)})
                    for group in diff.records
                ),
                "sources": tuple(
                    group.model_copy(update={"files": swapped(group.files, cut)})
                    for group in diff.sources
                ),
                "history": swapped(diff.history, cut),
                "other": swapped(diff.other, cut),
            }
        )
        return _part(shorter, [cut, *files[1:]], 1, others)

    length = _largest(len(first.patch), lambda number: fits(with_patch(number)))
    if not length:
        return _part(diff, files, 0, len(files))
    line_end = first.patch.rfind("\n", 0, length)
    return with_patch(line_end + 1 if line_end > 0 else length)
