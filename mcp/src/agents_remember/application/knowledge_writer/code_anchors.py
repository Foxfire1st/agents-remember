"""Resolve a locator at the leaf's code candidate tree C into a recorded anchor (MIK-R12 rule 2).

C is the leaf's current code candidate, including uncommitted changes, captured as a tree (MIK-R07
rule 0) through the shipped private-index capture. An anchor records the blob the path holds in C and
the ``content`` identity of the located bytes (:mod:`...models.knowledge_files.anchor_content`):

* ``symbol`` -- the one extent the shipped extractor binds for the name. Lean accepts
  a full name or a unique name as written, with full names taking precedence. A
  symbol resolves only when a supported reader binds a single definition;
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
from typing import Any, Final, Literal

from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    read_git_tree_bytes,
    run_git,
)
from agents_remember.kernel.git_preparation import GitPreparationError, require_git_object_id
from agents_remember.memory_quality.style.citations import extents, grammars
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    content_identity,
    range_bytes,
)
from agents_remember.models.knowledge_files.shapes import Anchor
from agents_remember.worktrees.modules.git import worktree_candidate_tree

_FILE_MODES: Final = frozenset({"100644", "100755", "120000"})
"""Tree entry modes bound as source blobs: regular files and symlinks (MIK-R53 rule 3).

A symlink binds its target blob, so an extant link is never ``absent``; only a deletion is.
The blob is the link target's bytes, never parsed for symbol anchors."""


class AnchorResolutionError(ValueError):
    """A locator does not resolve at C; the message says why."""


@dataclass
class CodeSnapshot:
    """The captured code candidate tree C: every regular file's blob, read on demand."""

    root: Path
    tree: str
    blobs: Mapping[str, str]
    _bytes: dict[str, bytes] = field(default_factory=dict)
    symlinks: frozenset[str] = frozenset()
    """Paths whose tree entry is a symlink (mode 120000): never parsed for symbols."""
    commit: str | None = None
    """The commit this tree is, when the snapshot was taken at a committed state.

    ``None`` for a worktree capture: the tree then holds uncommitted changes and no commit names
    it (MIK-R124 rule 1 -- a confirmation never claims a commit the recorded values did not come
    from).
    """

    def __post_init__(self) -> None:
        if self.commit is not None:
            try:
                require_git_object_id(self.commit)
            except GitPreparationError as error:
                raise AnchorResolutionError(
                    "the snapshot commit must be a full Git commit ID"
                ) from error

    @classmethod
    def capture(cls, root: Path) -> CodeSnapshot:
        """Capture ``root``'s working tree (committed and uncommitted) as the tree C."""

        with TemporaryDirectory(prefix="ar-knowledge-writer-") as scratch:
            tree = worktree_candidate_tree(root, Path(scratch) / "index")
        return cls.of_tree(root, tree)

    @classmethod
    def at_commit(cls, repository: Path, commit: str) -> CodeSnapshot:
        """The tree C of a committed code state (a master line's paired code commit)."""

        commit_id = _snapshot_revision(repository, commit, "commit")
        tree = _snapshot_revision(repository, commit_id, "tree")
        return cls.of_tree(repository, tree, commit=commit_id)

    @classmethod
    def of_tree(cls, root: Path, tree: str, *, commit: str | None = None) -> CodeSnapshot:
        """Read the regular files and symlinks of ``tree`` in ``root``'s object store."""

        blobs: dict[str, str] = {}
        symlinks: set[str] = set()
        for row in read_git_tree_bytes(root, tree).split(b"\0"):
            if not row:
                continue
            metadata, path = row.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            if kind == "blob" and mode in _FILE_MODES:
                decoded = os.fsdecode(path)
                blobs[decoded] = object_id
                if mode == "120000":
                    symlinks.add(decoded)
        return cls(root=root, tree=tree, blobs=blobs, symlinks=frozenset(symlinks), commit=commit)

    def has_file(self, path: str) -> bool:
        return path in self.blobs

    def blob_bytes(self, path: str) -> bytes:
        blob = self.blobs.get(path)
        if blob is None:
            raise AnchorResolutionError(f"the code candidate tree C holds no file at {path!r}")
        if blob not in self._bytes:
            self._bytes[blob] = read_git_blobs_bytes(self.root, [blob])[blob]
        return self._bytes[blob]

    def file_subject_mismatch(self, subject: str) -> str | None:
        """Why a ``file:<path>@<object>`` row subject names no change at C, or ``None`` (MIK-R10).

        The object is the path's tree entry at C (a regular file's blob, a symlink's blob, a
        submodule's commit), or ``absent`` when C holds nothing at the path. Other subjects pass.
        """

        if not subject.startswith("file:"):
            return None
        path, _, recorded = subject.removeprefix("file:").rpartition("@")
        result = run_git(
            self.root,
            ["rev-parse", "--verify", "--quiet", f"{self.tree}:{path}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        actual = result.stdout.strip() if result.returncode == 0 else ""
        if recorded == (actual or "absent"):
            return None
        return (
            f"{subject} names no change at C: the code candidate holds "
            f"{actual or 'nothing'} at {path!r} (unknown subject)"
        )

    def resolve(self, path: str, locator: Mapping[str, Any]) -> Anchor:
        """Return the anchor ``locator`` names in ``path`` at C, without ``path`` (the caller adds it).

        A symbol locator on a symlink is refused before the extractor runs: link bytes are
        never parsed for symbol anchors (MIK-R53 rule 3). File and line-range locators read
        the entry bytes as before.
        """

        if locator.get("kind") == "symbol" and path in self.symlinks:
            raise AnchorResolutionError(
                f"{path!r} is a symlink (mode 120000): symbol anchors are never parsed "
                "from link bytes; cite a line_range or the file instead"
            )
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


def _snapshot_revision(repository: Path, revision: str, kind: Literal["commit", "tree"]) -> str:
    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{{kind}}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    object_id = result.stdout.strip()
    if result.returncode != 0 or not object_id:
        raise AnchorResolutionError(
            f"the code revision {revision!r} does not resolve to a Git {kind} in {repository}"
        )
    return object_id


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
    """The one extent the active ``LanguageSet`` extractor binds for ``name`` in these bytes."""

    language_set = grammars.active_language_set()
    grammar = grammars.grammar_of(path)
    provider = language_set.provider_for(grammar) if grammar is not None else None
    if grammar is None or provider is None:
        raise AnchorResolutionError(
            f"the shipped extractor has no grammar or scanner for {path!r}, so a definition "
            f"of {name!r} cannot be told from a mention; cite a line_range or the file instead"
        )
    try:
        lines = data.decode("utf-8").split("\n")
    except UnicodeDecodeError as error:
        raise AnchorResolutionError(f"{path!r} is not valid UTF-8: {error}") from error
    try:
        bound = extents.definitions(path, lines)
        spans = extents.qualified_spans(name, bound, grammar=grammar)
    except GrammarUnavailableError as error:
        raise AnchorResolutionError(f"the grammar for {path!r} is unavailable: {error}") from error
    if not spans:
        if grammar == grammars.LEAN and extents.uncertain_lean_scope(name, bound):
            raise AnchorResolutionError(
                f"{name!r} in {path!r} has an uncertain Lean scope. Cite a line_range instead."
            )
        refusal = grammars.test_case_refusal(path, lines, name)
        raise AnchorResolutionError(refusal or f"{path!r} at C does not define {name!r}")
    if len(spans) != 1:
        where = ", ".join(f"{start}-{end}" for start, end in spans)
        remedy = "qualify the name or cite a line_range"
        if grammar == grammars.LEAN:
            full_names = dict.fromkeys(one.full_name for one in bound[name] if one.full_name)
            if len(full_names) > 1:
                choices = ", ".join(repr(full_name) for full_name in full_names)
                remedy = f"use a full name ({choices}) or cite a line_range"
        raise AnchorResolutionError(
            f"{name!r} is bound more than once in {path!r} at C (lines {where}); a symbol anchor "
            f"names one construct, so {remedy}"
        )
    return spans[0]
