"""Hunks, ranges and content identities over the code trees B and C (MIK-R08 definitions 2 and 3).

* **Hunks** are the zero-context hunks between two blobs of one path. They are read from Git with
  every setting that could make two machines disagree pinned on the command line: the Myers
  algorithm, no indent heuristic, no external or text-conversion driver. A binary pair has no hunks
  and is reported as such (``None``), never as "no change".
* **A range** is where an anchor's locator lands in a blob:

  - ``symbol`` -- the one extent the shipped extractor binds for the name
    (:meth:`CodeObjects.symbol_span`, the rule the writer and the conversion bind with); a name bound
    twice or nowhere does not resolve;
  - ``line_range`` -- the recorded lines in the anchor's own blob; in any other blob of the same path,
    the image of those lines through the zero-context diff from the anchor's blob to that blob
    (:func:`map_range`); a range every line of which was deleted, with nothing written in its place,
    has no image;
  - ``file`` -- every line.

* **Content identity** is ``sha256:`` of the range's bytes, through the one definition in
  :mod:`agents_remember.models.knowledge_files.anchor_content`.

A changed line **hits** a range on one side when it lies inside the range on that side. A hunk that
changes nothing on one side (a pure insertion seen from B, a pure deletion seen from C) hits the range
on that side when it sits strictly inside it -- "a changed line inside a line range also counts as
touched" -- and never when it sits at the range's edge.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory.conversion.code_objects import CodeObjectError, CodeObjects
from agents_remember.memory.knowledge.tree_observation import TreeChange
from agents_remember.memory_quality.style.citations import extents, grammars
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    content_identity,
    line_count,
    range_bytes,
)

__all__ = [
    "BLOB_DIFF_ARGS",
    "CodeReadError",
    "CodeTrees",
    "Hunk",
    "LineRange",
    "Resolved",
    "change_hunks",
    "hits_new",
    "hits_old",
    "map_range",
    "parse_hunks",
]

# The zero-context diff of two blobs, with every configurable choice pinned so the hunks are a
# function of the two blobs alone (MIK-R08 rule 6).
BLOB_DIFF_ARGS: Final = (
    "diff",
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "--diff-algorithm=myers",
    "--no-indent-heuristic",
    "--unified=0",
)
_HUNK_HEADER: Final = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_BINARY_MARKER: Final = "Binary files "

LineRange = tuple[int, int]


class CodeReadError(ValueError):
    """A code object the worklist needs cannot be read (the run is ``incomplete``)."""


@dataclass(frozen=True, order=True)
class Hunk:
    """One zero-context hunk: ``count`` lines from ``start`` on each side.

    A side with ``count == 0`` changes nothing there; its ``start`` is then the line *after which*
    the other side's lines were inserted or removed, exactly as Git prints it.
    """

    old_start: int
    old_count: int
    new_start: int
    new_count: int

    def to_document(self) -> dict[str, list[int]]:
        return {
            "base": [self.old_start, self.old_count],
            "candidate": [self.new_start, self.new_count],
        }


def _hits(start: int, count: int, span: LineRange) -> bool:
    low, high = span
    if count > 0:
        return start <= high and start + count - 1 >= low
    return low <= start < high


def hits_old(hunk: Hunk, span: LineRange) -> bool:
    """Whether the hunk changes a line of ``span`` on the B side (or inserts strictly inside it)."""

    return _hits(hunk.old_start, hunk.old_count, span)


def hits_new(hunk: Hunk, span: LineRange) -> bool:
    """Whether the hunk changes a line of ``span`` on the C side (or deletes strictly inside it)."""

    return _hits(hunk.new_start, hunk.new_count, span)


def parse_hunks(output: str) -> tuple[Hunk, ...] | None:
    """The hunks of one ``git diff --unified=0`` of two blobs, or ``None`` for a binary pair."""

    hunks: list[Hunk] = []
    for line in output.splitlines():
        if line.startswith(_BINARY_MARKER):
            return None
        match = _HUNK_HEADER.match(line)
        if match is None:
            continue
        old_start, old_count, new_start, new_count = match.groups()
        hunks.append(
            Hunk(
                int(old_start),
                1 if old_count is None else int(old_count),
                int(new_start),
                1 if new_count is None else int(new_count),
            )
        )
    return tuple(sorted(hunks))


def _map_line(hunks: Sequence[Hunk], line: int) -> int | None:
    """Where an unchanged line of the old blob sits in the new one, or ``None`` if it was removed."""

    delta = 0
    for hunk in hunks:
        if hunk.old_count > 0:
            if line < hunk.old_start:
                break
            if line <= hunk.old_start + hunk.old_count - 1:
                return None
        elif line <= hunk.old_start:
            break
        delta += hunk.new_count - hunk.old_count
    return line + delta


def map_range(hunks: Sequence[Hunk], span: LineRange) -> LineRange | None:
    """The image of the old lines ``span`` in the new blob, or ``None`` when it has none.

    The image spans every surviving line of the range and every line a hunk inside the range wrote
    in its place. A range whose lines were all deleted, with nothing written inside it, has no
    image: that is "its line range has no mapping" (MIK-R08 definition 4).
    """

    low, high = span
    lines = [
        mapped for line in range(low, high + 1) if (mapped := _map_line(hunks, line)) is not None
    ]
    for hunk in hunks:
        if hunk.new_count > 0 and hits_old(hunk, span):
            lines.extend((hunk.new_start, hunk.new_start + hunk.new_count - 1))
    return (min(lines), max(lines)) if lines else None


@dataclass(frozen=True)
class Resolved:
    """An anchor's range in one blob, and the content identity of its bytes."""

    blob: str
    span: LineRange
    content: str

    def to_document(self) -> dict[str, Any]:
        return {"blob": self.blob, "lines": list(self.span), "content": self.content}


@dataclass
class CodeTrees:
    """The code trees B and C of one run, read through one object store, with caches."""

    objects: CodeObjects
    base_tree: str
    candidate_tree: str
    _hunks: dict[tuple[str, str], tuple[Hunk, ...] | None] = field(default_factory=dict)

    @classmethod
    def open(cls, repository: Path, base_tree: str, candidate_tree: str) -> CodeTrees:
        return cls(CodeObjects(repository), base_tree, candidate_tree)

    @property
    def repository(self) -> Path:
        return self.objects.repository

    def base(self) -> Mapping[str, str]:
        """Each regular file of B, with its blob."""

        return self._tree(self.base_tree)

    def candidate(self) -> Mapping[str, str]:
        """Each regular file of C, with its blob."""

        return self._tree(self.candidate_tree)

    def _tree(self, tree: str) -> Mapping[str, str]:
        try:
            return self.objects.tree(tree)
        except Exception as error:  # a tree the store does not hold: named, never an empty tree
            raise CodeReadError(f"the code tree {tree} cannot be read: {error}") from error

    def hunks(self, old: str, new: str) -> tuple[Hunk, ...] | None:
        """The zero-context hunks from blob ``old`` to blob ``new`` (``None`` for a binary pair)."""

        if old == new:
            return ()
        key = (old, new)
        if key not in self._hunks:
            result = run_git(
                self.repository,
                [*BLOB_DIFF_ARGS, old, new],
                GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
            )
            if result.returncode != 0:
                raise CodeReadError(
                    f"the blobs {old} and {new} cannot be compared: "
                    f"{result.stderr.strip() or 'git diff failed'}"
                )
            self._hunks[key] = parse_hunks(result.stdout)
        return self._hunks[key]

    def has_blob(self, blob: str) -> bool:
        return self.objects.has_blob(blob)

    def resolve(
        self, path: str, locator: Mapping[str, Any], recorded_blob: str, blob: str
    ) -> Resolved | None:
        """Where ``locator`` (recorded against ``recorded_blob``) lands in ``blob`` of ``path``."""

        kind = locator.get("kind")
        data = self._blob(blob)
        if kind == "file":
            return Resolved(blob, (1, max(line_count(data), 1)), content_identity(data))
        if kind == "symbol":
            try:
                span = self.objects.symbol_span(path, blob, str(locator.get("name", "")))
            except CodeObjectError as error:
                raise CodeReadError(str(error)) from error
        else:
            span = self._mapped_lines(locator, recorded_blob, blob)
        if span is None:
            return None
        try:
            return Resolved(blob, span, content_identity(range_bytes(data, *span)))
        except RangeOutsideBlobError:
            return None

    def _mapped_lines(
        self, locator: Mapping[str, Any], recorded_blob: str, blob: str
    ) -> LineRange | None:
        recorded = (int(locator["start"]), int(locator["end"]))
        if blob == recorded_blob:
            return recorded
        if not self.has_blob(recorded_blob):
            return None  # the lines were recorded in a blob this store does not hold: no mapping
        hunks = self.hunks(recorded_blob, blob)
        return None if hunks is None else map_range(hunks, recorded)

    def _blob(self, blob: str) -> bytes:
        try:
            return self.objects.blob(blob)
        except Exception as error:
            raise CodeReadError(f"the code blob {blob} cannot be read: {error}") from error

    def line_count(self, blob: str) -> int:
        return line_count(self._blob(blob))

    def unique_binder(self, name: str) -> str | None:
        """The only path of C whose extractor binds ``name``, when it binds it once (definition 6).

        Every C file that holds the name's last segment as text is a candidate (a file that binds a
        name spells it); each candidate the shipped extractor parses is asked how many constructs
        bind the name. The answer is a path only when exactly one path binds it, exactly once.
        """

        leaf = name.rsplit(".", maxsplit=1)[-1]
        if not leaf:
            return None
        result = run_git(
            self.repository,
            ["grep", "-l", "-z", "-F", "-e", leaf, self.candidate_tree],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        if result.returncode not in (0, 1):
            raise CodeReadError(
                f"the code tree {self.candidate_tree} cannot be searched: {result.stderr.strip()}"
            )
        prefix = f"{self.candidate_tree}:"
        candidates = sorted(
            hit.removeprefix(prefix) for hit in result.stdout.split("\0") if hit.startswith(prefix)
        )
        tree = self.candidate()
        binders = [
            (path, count)
            for path in candidates
            if path in tree and (count := self._binding_count(path, tree[path], name))
        ]
        if len(binders) == 1 and binders[0][1] == 1:
            return binders[0][0]
        return None

    def _binding_count(self, path: str, blob: str, name: str) -> int:
        grammar = grammars.grammar_of(path)
        if grammar is None:
            return 0
        lines = self._blob(blob).decode("utf-8", errors="surrogateescape").split("\n")
        try:
            return len(extents.qualified_spans(name, extents.definitions(path, lines)))
        except GrammarUnavailableError as error:
            raise CodeReadError(f"the grammar for {path!r} is unavailable: {error}") from error


def change_hunks(
    code: CodeTrees, change: TreeChange, base_blob: str | None, candidate_blob: str | None
) -> tuple[Hunk, ...] | None:
    """A changed path's text hunks (definition 2), or ``None`` for a change linked at file level.

    A path whose content is not text, or whose type changed, has no hunks: it is a non-text change
    (definition 8). An added or a deleted text file is one hunk of all its lines; an empty one has
    none and is linked at file level too. The gate linkage and the reviewer's unexplained-changes
    lane both take a path's hunks from here, so the two never disagree about what a hunk is.
    """

    if change.content != "text" or change.status == "type_changed":
        return None
    if base_blob is not None and candidate_blob is not None:
        return code.hunks(base_blob, candidate_blob)
    present = base_blob or candidate_blob
    count = 0 if present is None else code.line_count(present)
    if count == 0:
        return None
    return (Hunk(1, count, 0, 0),) if candidate_blob is None else (Hunk(0, 0, 1, count),)
