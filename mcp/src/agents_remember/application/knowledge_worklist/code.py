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

**Answers kept across runs (D569).** Two answers here are functions of complete object ids alone,
so a run after a one-blob change recomputes only what that blob changes. Both are kept per process
in bounded tables, answers only, never a failure:

* the hunks of two blobs (:data:`BLOB_HUNKS`): ``BLOB_DIFF_ARGS`` pins every choice, so they are a
  function of the pair. A kept pair is used only once this run's store has read or found both blobs
  (:meth:`CodeObjects.holds`), so a store that lost one still fails where it did. The pairs
  :meth:`CodeTrees.prepare_hunks` reads from one diff of the two trees are kept the same way.
* the paths of C that hold a name's text (:data:`TREE_GREPS`), the candidates of definition 6. For a
  new C the listing of the tree last searched for the same text is carried over for every path whose
  mode and blob did not change (``git diff-tree``), and Git searches only the paths that did -- one
  file's listing is a function of that file alone. Any read that fails, and any changed pathname the
  text transport would rewrite (a carriage return), searches the whole tree exactly as before.

A changed line **hits** a range on one side when it lies inside the range on that side. A hunk that
changes nothing on one side (a pure insertion seen from B, a pure deletion seen from C) hits the range
on that side when it sits strictly inside it -- "a changed line inside a line range also counts as
touched" -- and never when it sits at the range's edge.
"""

from __future__ import annotations

import contextvars
import os
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.git_command import (
    DIFF_PREFIX_OPTIONS,
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
    run_git_bytes,
)
from agents_remember.memory.conversion.code_objects import CodeObjectError, CodeObjects
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo, is_complete_object_id
from agents_remember.memory.knowledge.tree_observation import TreeChange
from agents_remember.memory_quality.style.citations import grammars
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    content_identity,
    range_from_lines,
)

__all__ = [
    "BLOB_DIFF_ARGS",
    "BLOB_HUNKS",
    "LATEST_GREPS",
    "TREE_GREPS",
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
    "--inter-hunk-context=0",
    *DIFF_PREFIX_OPTIONS,
)
#: The same diff over two whole trees, for :meth:`CodeTrees.prepare_hunks`: every path whose blob
#: changed (no rename pairing), each patch naming its two full blob ids.
_TREE_DIFF_ARGS: Final = (*BLOB_DIFF_ARGS, "--no-renames", "--full-index", "--diff-filter=M")
_PATCH_START: Final = b"diff --git "
_PATCH_INDEX: Final = re.compile(
    rb"^index ([0-9a-f]{40}|[0-9a-f]{64})\.\.([0-9a-f]{40}|[0-9a-f]{64})(?: |$)"
)
# Paths per ``git grep`` that searches only changed paths: an argv far below every platform's limit.
_PATHSPEC_CHUNK: Final = 512
_HUNK_HEADER: Final = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_BINARY_MARKER: Final = "Binary files "

LineRange = tuple[int, int]


BLOB_HUNKS: BoundedMemo[tuple[str, str, str], tuple[Hunk, ...] | None] = BoundedMemo(65_536)
"""``(repository, old blob, new blob)`` -> their zero-context hunks (``None`` for a binary pair)."""

TREE_GREPS: BoundedMemo[tuple[str, str, str], tuple[str, ...]] = BoundedMemo(16_384)
"""``(repository, tree, text)`` -> the sorted paths ``git grep -l -F`` lists for the tree."""

LATEST_GREPS: BoundedMemo[tuple[str, str], str] = BoundedMemo(16_384)
"""``(repository, text)`` -> the tree most recently searched for it, the next listing's origin."""

_TREE_CHANGES: BoundedMemo[tuple[str, str, str], tuple[bytes, ...]] = BoundedMemo(
    262_144, weigh=lambda paths: len(paths) + 1
)
"""``(repository, tree, tree)`` -> the exact paths whose entry differs, as ``git diff-tree`` names
them (weighed by their number)."""


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


def _patches(output: bytes) -> Iterable[tuple[tuple[str, str], str | None, bytes]]:
    """Each patch of a tree diff: its blob pair (from its ``index`` line), its path and its bytes.

    A patch starts at a ``diff --git`` line; with no context lines every line of a patch body
    starts with ``+``, ``-``, ``@`` or ``\\``, so no body line is taken for a start. The path is
    the one both header names agree on, ``None`` when Git quoted it or the two differ.
    """

    starts = [0] if output.startswith(_PATCH_START) else []
    at = output.find(b"\n" + _PATCH_START)
    while at != -1:
        starts.append(at + 1)
        at = output.find(b"\n" + _PATCH_START, at + 1)
    for begin, end in zip(starts, [*starts[1:], len(output)], strict=True):
        patch = output[begin:end]
        lines = patch.split(b"\n")
        index = next((m for line in lines[1:8] if (m := _PATCH_INDEX.match(line))), None)
        if index is None:
            continue
        pair = (index.group(1).decode("ascii"), index.group(2).decode("ascii"))
        yield pair, _patch_path(lines[0]), patch


def _patch_path(header: bytes) -> str | None:
    names = header.removeprefix(_PATCH_START)
    if names.startswith(b'"'):
        return None  # Git quotes a name it would otherwise print raw: not read back
    half = (len(names) - 1) // 2
    old, new = names[:half], names[half + 1 :]
    if names[half : half + 1] != b" " or not old.startswith(b"a/") or old[2:] != new[2:]:
        return None
    if not new.startswith(b"b/"):
        return None
    return old[2:].decode("utf-8", "surrogateescape")


def _encodes(name: str) -> bool:
    """Whether ``name`` is UTF-8 text Git can be asked about (a byte name it is not)."""

    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _as_text(output: bytes) -> str:
    """``output`` as the text transport hands a command's output over (see :func:`run_git`)."""

    return output.decode("utf-8", "surrogateescape").replace("\r\n", "\n").replace("\r", "\n")


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
    _candidate_symlinks: frozenset[str] | None = field(default=None, repr=False)
    """Paths of C whose tree entry is a symlink, once read (MIK-R53 rule 3)."""
    _base_symlinks: frozenset[str] | None = field(default=None, repr=False)
    """Paths of B whose tree entry is a symlink, once read (MIK-R53 rule 3)."""

    @classmethod
    def open(
        cls,
        repository: Path,
        base_tree: str,
        candidate_tree: str,
        *,
        object_inputs: Path | None = None,
    ) -> CodeTrees:
        """Open both trees, reading ``object_inputs`` beside the repository's store when given."""

        return cls(CodeObjects(repository, object_inputs=object_inputs), base_tree, candidate_tree)

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

    def warm(self, paths: Iterable[str]) -> None:
        """Read the blobs of ``paths`` on both sides in batches, before they are asked for one by one.

        Each blob a classification or a hunk needs would otherwise cost its own ``git cat-file``.
        This is a read-ahead of exact blobs the run is about to read, and nothing more: a blob
        that cannot be read here is read, and fails, where it is used, as it always did.
        """

        with suppress(CodeReadError, CodeObjectError):
            trees = (self.base(), self.candidate())
            self.objects.prefetch(
                tree[path] for path in set(paths) for tree in trees if path in tree
            )

    def hunks(self, old: str, new: str) -> tuple[Hunk, ...] | None:
        """The zero-context hunks from blob ``old`` to blob ``new`` (``None`` for a binary pair)."""

        if old == new:
            return ()
        key = (old, new)
        kept = (str(self.repository), old, new)
        if key not in self._hunks and self.objects.holds(old) and self.objects.holds(new):
            remembered = BLOB_HUNKS.get(kept)
            if remembered is not None:
                self._hunks[key] = remembered[0]
        if key not in self._hunks:
            result = run_git(
                self.repository,
                [*BLOB_DIFF_ARGS, old, new],
                # Git's environment context option overrides even --unified=0.
                GitRunnerOptions(
                    timeout=GIT_METADATA_TIMEOUT_SECONDS,
                    identity={"GIT_DIFF_OPTS": ""},
                    object_inputs=self.objects.object_inputs,
                ),
            )
            if result.returncode != 0:
                raise CodeReadError(
                    f"the blobs {old} and {new} cannot be compared: "
                    f"{result.stderr.strip() or 'git diff failed'}"
                )
            self._hunks[key] = parse_hunks(result.stdout)
            if is_complete_object_id(old) and is_complete_object_id(new):
                BLOB_HUNKS.put(kept, self._hunks[key])
        return self._hunks[key]

    def prepare_hunks(self, changed: Iterable[tuple[str, str, str]]) -> None:
        """Compute ahead, in one ``git diff`` of B and C, the hunks :meth:`hunks` is about to be asked.

        ``changed`` names ``(path, blob at B, blob at C)``. The tree diff runs the blob diff's
        exact options, so each path's patch is the one ``git diff <old> <new>`` prints for the pair,
        but for two things the tree diff adds: the path's own ``diff`` attribute (the blob diff
        looks attributes up under the two blob ids, which it is given as names) and the header
        lines, which no hunk is read from. A pair is kept only when ``git check-attr`` finds the
        ``diff`` attribute unspecified for its path and for both blob ids, so neither diff has a
        driver and both decide binary by content; its patch is then decoded as the blob diff's
        output is (UTF-8 with escapes, universal newlines) and parsed by :func:`parse_hunks`.
        Anything else -- a pair with an attribute, a read that fails, a reply of another shape --
        keeps nothing, and :meth:`hunks` asks Git for that pair alone, as it always did.
        """

        wanted: dict[tuple[str, str], list[str]] = {}
        for path, old, new in changed:
            if old != new and (old, new) not in self._hunks:
                wanted.setdefault((old, new), []).append(path)
        if not wanted:
            return
        try:
            plain = self._without_diff_attribute(
                {name for (old, new), paths in wanted.items() for name in (old, new, *paths)}
            )
            if plain is None:
                return
            result = run_git_bytes(
                self.repository,
                [*_TREE_DIFF_ARGS, self.base_tree, self.candidate_tree],
                GitRunnerOptions(
                    timeout=GIT_METADATA_TIMEOUT_SECONDS,
                    identity={"GIT_DIFF_OPTS": ""},
                    object_inputs=self.objects.object_inputs,
                ),
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return
        if result.returncode != 0:
            return
        for pair, path, patch in _patches(result.stdout):
            if pair not in wanted or path is None or pair in self._hunks:
                continue
            if path not in wanted[pair] or not {path, *pair} <= plain:
                continue
            self._hunks[pair] = parse_hunks(_as_text(patch))
            BLOB_HUNKS.put((str(self.repository), *pair), self._hunks[pair])

    def _without_diff_attribute(self, names: set[str]) -> set[str] | None:
        """The names whose ``diff`` attribute is unspecified, or ``None`` when Git cannot say."""

        listed = sorted(name for name in names if _encodes(name))
        result = run_git_bytes(
            self.repository,
            ["check-attr", "-z", "--stdin", "diff"],
            GitRunnerOptions(
                timeout=GIT_METADATA_TIMEOUT_SECONDS,
                input_text="".join(f"{name}\0" for name in listed),
                object_inputs=self.objects.object_inputs,
            ),
        )
        fields = result.stdout.split(b"\0")
        if result.returncode != 0 or fields[-1:] != [b""] or (len(fields) - 1) != 3 * len(listed):
            return None
        rows = [fields[at : at + 3] for at in range(0, len(fields) - 1, 3)]
        if any(row[1] != b"diff" for row in rows):
            return None
        return {
            row[0].decode("utf-8", "surrogateescape") for row in rows if row[2] == b"unspecified"
        }

    def has_blob(self, blob: str) -> bool:
        """Whether the store holds ``blob``; a Git failure is a :class:`CodeReadError`, never absent."""

        try:
            return self.objects.has_blob(blob)
        except CodeObjectError as error:
            raise CodeReadError(str(error)) from error

    def candidate_symlinks(self) -> frozenset[str]:
        """Paths of C whose tree entry is a symlink (mode 120000), read once and cached."""

        if self._candidate_symlinks is None:
            self._candidate_symlinks = self._side_symlinks(self.candidate_tree)
        return self._candidate_symlinks

    def base_symlinks(self) -> frozenset[str]:
        """Paths of B whose tree entry is a symlink (mode 120000), read once and cached."""

        if self._base_symlinks is None:
            self._base_symlinks = self._side_symlinks(self.base_tree)
        return self._base_symlinks

    def _side_symlinks(self, tree: str) -> frozenset[str]:
        try:
            modes = self.objects.tree_modes(tree)
        except Exception as error:
            raise CodeReadError(f"the code tree {tree} cannot be read: {error}") from error
        return CodeObjects.link_paths(modes)

    def resolve(
        self,
        path: str,
        locator: Mapping[str, Any],
        recorded_blob: str,
        blob: str,
        *,
        side: str = "candidate",
    ) -> Resolved | None:
        """Where ``locator`` (recorded against ``recorded_blob``) lands in ``blob`` of ``path``.

        ``side`` names the tree ``blob`` comes from: ``"base"`` guards symbol resolution
        with B's modes, anything else with C's. A symbol locator on a symlink of that
        side is refused before the extractor runs (MIK-R53 rule 3).
        """

        kind = locator.get("kind")
        data = self._blob(blob)
        if kind == "file":
            lines = self.objects.blob_lines(blob)
            return Resolved(blob, (1, max(len(lines), 1)), content_identity(data))
        if kind == "symbol":
            symlinks = self.base_symlinks() if side == "base" else self.candidate_symlinks()
            try:
                span = self.objects.symbol_span(
                    path,
                    blob,
                    str(locator.get("name", "")),
                    symlinks=symlinks,
                )
            except CodeObjectError as error:
                raise CodeReadError(str(error)) from error
        else:
            span = self._mapped_lines(locator, recorded_blob, blob)
        if span is None:
            return None
        try:
            # The blob's lines are split once per run (range_bytes would split them per range).
            lines = self.objects.blob_lines(blob)
            return Resolved(blob, span, content_identity(range_from_lines(lines, *span)))
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
        self._blob(blob)
        return len(self.objects.blob_lines(blob))

    def unique_binder(self, name: str) -> str | None:
        """The only path of C whose extractor binds ``name``, when it binds it once (definition 6).

        Every C file that holds the name's last segment as text is a candidate (a file that binds a
        name spells it); each candidate the shipped extractor parses is asked how many constructs
        bind the name. The answer is a path only when exactly one path binds it, exactly once.
        """

        leaf = name.rsplit(".", maxsplit=1)[-1]
        if not leaf:
            return None
        candidates = self._holding(leaf)
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
        self._blob(blob)
        try:
            return len(self.objects.qualified_spans(path, blob, name))
        except GrammarUnavailableError as error:
            raise CodeReadError(f"the grammar for {path!r} is unavailable: {error}") from error

    def _holding(self, leaf: str) -> tuple[str, ...]:
        """The sorted paths ``git grep -l -F`` lists for C and ``leaf`` (:data:`TREE_GREPS`)."""

        repository, tree = str(self.repository), self.candidate_tree
        kept = is_complete_object_id(tree)
        if kept:
            remembered = TREE_GREPS.get((repository, tree, leaf))
            if remembered is not None:
                return remembered[0]
        held = self._held_since(leaf) if kept else None
        if held is None:
            held = self._grep(leaf, ())
        if kept:
            TREE_GREPS.put((repository, tree, leaf), held)
            LATEST_GREPS.put((repository, leaf), tree)
        return held

    def _held_since(self, leaf: str) -> tuple[str, ...] | None:
        """C's listing from the last searched tree's: unchanged paths kept, changed ones searched.

        ``None`` when there is no earlier listing or a read it needs fails: the whole tree is
        searched instead, which also raises where a failing search always raised.
        """

        repository = str(self.repository)
        earlier = LATEST_GREPS.get((repository, leaf))
        if earlier is None or earlier[0] == self.candidate_tree:
            return None
        listed = TREE_GREPS.get((repository, earlier[0], leaf))
        changed = None if listed is None else self._changed_since(earlier[0])
        if listed is None or changed is None:
            return None
        if any(b"\r" in path for path in changed):
            return None  # the text transport rewrites CR, so two names could meet: search it all
        try:
            found = self._grep(leaf, changed) if changed else ()
        except CodeReadError:
            return None
        gone = {_listed_name(path) for path in changed}
        return tuple(sorted({*(path for path in listed[0] if path not in gone), *found}))

    def _changed_since(self, earlier: str) -> tuple[bytes, ...] | None:
        """Every path whose tree entry differs between ``earlier`` and C, or ``None`` if unread."""

        key = (str(self.repository), earlier, self.candidate_tree)
        remembered = _TREE_CHANGES.get(key)
        if remembered is not None:
            return remembered[0]
        try:
            result = run_git_bytes(
                self.repository,
                ["diff-tree", "-r", "-z", "--no-renames", earlier, self.candidate_tree],
                GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        tokens = result.stdout.split(b"\0")
        if tokens and tokens[-1] == b"":
            tokens.pop()
        if len(tokens) % 2 or not all(token.startswith(b":") for token in tokens[::2]):
            return None
        changed = tuple(tokens[1::2])
        _TREE_CHANGES.put(key, changed)
        return changed

    def _grep(self, leaf: str, paths: Sequence[bytes]) -> tuple[str, ...]:
        """``git grep -l -F`` over C: the whole tree, or only ``paths`` (exact, literal paths)."""

        prefix = f"{self.candidate_tree}:"
        chunks = (
            [()]
            if not paths
            else [paths[at : at + _PATHSPEC_CHUNK] for at in range(0, len(paths), _PATHSPEC_CHUNK)]
        )
        found: set[str] = set()
        for chunk in chunks:
            pathspecs = (
                ["--", *(f":(literal){os.fsdecode(path)}" for path in chunk)] if chunk else []
            )
            search = ["grep", "--no-color", "-l", "-z", "-F", "-e", leaf, self.candidate_tree]
            result = run_git(
                self.repository,
                [*search, *pathspecs],
                GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
            )
            if result.returncode not in (0, 1):
                raise CodeReadError(
                    f"the code tree {self.candidate_tree} cannot be searched: {result.stderr.strip()}"
                )
            found.update(
                hit.removeprefix(prefix)
                for hit in result.stdout.split("\0")
                if hit.startswith(prefix)
            )
        return tuple(sorted(found))

    def prepare_binders(self, names: Iterable[str]) -> None:
        """Search C ahead, at once, for the names :meth:`unique_binder` is about to be asked.

        Each name's listing is the one :meth:`unique_binder` reads (one ``git grep`` per text, run
        side by side in threads of this process). A search that fails here keeps nothing:
        :meth:`unique_binder` searches again and fails where it always did.
        """

        leaves = sorted({leaf for name in names if (leaf := name.rsplit(".", maxsplit=1)[-1])})
        if not leaves:
            return
        with (
            suppress(RuntimeError),  # no search thread: unique_binder searches each name itself
            ThreadPoolExecutor(
                max_workers=min(len(leaves), max(1, os.process_cpu_count() or 1)),
                thread_name_prefix="worklist-binder-search",
            ) as searches,
        ):
            for leaf in leaves:
                searches.submit(contextvars.copy_context().run, self._quiet_holding, leaf)

    def _quiet_holding(self, leaf: str) -> tuple[str, ...] | None:
        try:
            return self._holding(leaf)
        except (CodeReadError, OSError, subprocess.SubprocessError):
            return None


def _listed_name(path: bytes) -> str:
    """``path`` as ``git grep``'s listing reads through the text transport (UTF-8, escapes kept)."""

    return path.decode("utf-8", "surrogateescape")


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
