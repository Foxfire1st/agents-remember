"""Resolve a locator at the leaf's code candidate tree C into a recorded anchor (MIK-R12 rule 2).

C is the leaf's current code candidate, including uncommitted changes, captured as a tree (MIK-R07
rule 0) through the shipped private-index capture. An anchor records the blob the path holds in C and
the ``content`` identity of the located bytes (:mod:`...models.knowledge_files.anchor_content`):

* ``symbol`` -- the one extent the shipped extractor binds for the name. A name bound more than once,
  bound nowhere, or in a language the extractor has no grammar for does not resolve: the writer never
  guesses between definitions or accepts a mention;
* ``line_range`` -- the recorded lines, which must be lines the blob holds;
* ``file`` -- every byte.

Nothing is read from ``HEAD`` or any other tree: a path C does not hold does not resolve.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.git_command import read_git_blobs_bytes, read_git_tree_bytes
from agents_remember.memory_quality.style.citations import extents, grammars
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    content_identity,
    range_bytes,
)
from agents_remember.models.knowledge_files.shapes import Anchor
from agents_remember.worktrees.modules.git import worktree_candidate_tree

_FILE_MODES: Final = frozenset({"100644", "100755"})


class AnchorResolutionError(ValueError):
    """A locator does not resolve at C; the message says why."""


@dataclass
class CodeSnapshot:
    """The captured code candidate tree C: every regular file's blob, read on demand."""

    root: Path
    tree: str
    blobs: Mapping[str, str]
    _bytes: dict[str, bytes] = field(default_factory=dict)

    @classmethod
    def capture(cls, root: Path) -> CodeSnapshot:
        """Capture ``root``'s working tree (committed and uncommitted) as the tree C."""

        with TemporaryDirectory(prefix="ar-knowledge-writer-") as scratch:
            tree = worktree_candidate_tree(root, Path(scratch) / "index")
        blobs: dict[str, str] = {}
        for row in read_git_tree_bytes(root, tree).split(b"\0"):
            if not row:
                continue
            metadata, path = row.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            if kind == "blob" and mode in _FILE_MODES:
                blobs[os.fsdecode(path)] = object_id
        return cls(root=root, tree=tree, blobs=blobs)

    def has_file(self, path: str) -> bool:
        return path in self.blobs

    def blob_bytes(self, path: str) -> bytes:
        blob = self.blobs.get(path)
        if blob is None:
            raise AnchorResolutionError(f"the code candidate tree C holds no file at {path!r}")
        if blob not in self._bytes:
            self._bytes[blob] = read_git_blobs_bytes(self.root, [blob])[blob]
        return self._bytes[blob]

    def resolve(self, path: str, locator: Mapping[str, Any]) -> Anchor:
        """Return the anchor ``locator`` names in ``path`` at C, without ``path`` (the caller adds it)."""

        data = self.blob_bytes(path)
        start, end = _range(path, locator, data)
        located = data if locator.get("kind") == "file" else _range_bytes(data, start, end)
        return Anchor.model_validate(
            {
                "locator": dict(locator),
                "blob": self.blobs[path],
                "content": content_identity(located),
            }
        )


def _range_bytes(data: bytes, start: int, end: int) -> bytes:
    try:
        return range_bytes(data, start, end)
    except RangeOutsideBlobError as error:
        raise AnchorResolutionError(str(error)) from error


def _range(path: str, locator: Mapping[str, Any], data: bytes) -> tuple[int, int]:
    kind = locator.get("kind")
    if kind == "file":
        return 1, 1
    if kind == "line_range":
        return int(locator["start"]), int(locator["end"])
    if kind == "symbol":
        return _symbol_range(path, str(locator["name"]), data)
    raise AnchorResolutionError(f"unknown locator kind {kind!r}")


def _symbol_range(path: str, name: str, data: bytes) -> tuple[int, int]:
    """The one extent the shipped extractor binds for ``name`` in these bytes."""

    if not grammars.parsed(path):
        raise AnchorResolutionError(
            f"{path!r} is in a language the shipped extractor has no grammar for, so a definition "
            f"of {name!r} cannot be told from a mention; cite a line_range or the file instead"
        )
    lines = data.decode("utf-8", errors="surrogateescape").split("\n")
    try:
        spans = _bound_spans(name, path, lines)
    except GrammarUnavailableError as error:
        raise AnchorResolutionError(f"the grammar for {path!r} is unavailable: {error}") from error
    if not spans:
        raise AnchorResolutionError(f"{path!r} at C does not define {name!r}")
    if len(spans) != 1:
        where = ", ".join(f"{start}-{end}" for start, end in spans)
        raise AnchorResolutionError(
            f"{name!r} is bound more than once in {path!r} at C (lines {where}); a symbol anchor "
            "names one construct, so qualify the name or cite a line_range"
        )
    return spans[0]


def _bound_spans(name: str, path: str, lines: list[str]) -> tuple[tuple[int, int], ...]:
    """The distinct extents binding ``name`` (one rule: :func:`extents.qualified_spans`)."""

    return extents.qualified_spans(name, extents.definitions(path, lines))
