"""The two inputs the validator reads: a memory tree's knowledge files and a code tree's paths.

A :class:`KnowledgeTree` is the exact bytes of every file under ``knowledge/`` and ``onboarding/``
of one memory tree, keyed by repository-relative POSIX path. The generated route-index cache
(``*.index.json``) is not knowledge and is never read. A tree is *converted* exactly when it holds
the layout marker ``knowledge/layout.json`` (MIK-R21 rule 1).

A :class:`CodeTree` answers two questions: does the paired code tree hold a file at this path (anchor
path existence, MIK-R22 rule 6), and does it hold a directory at this path (a family route, MIK-R04
rule 1)?

Both are read from a directory (a working tree, for the curator's command and the writer) or from a
Git tree (for a commit route, where the candidate is the exact staged tree and the bases are
commits). Git bytes are read without decoding or newline normalization, so the canonical-format
check sees what will be committed.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Final, Protocol

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    read_git_tree_bytes,
    run_git,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    LAYOUT_MARKER_PATH,
    ONBOARDING_ROOT,
)
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH

ROUTE_INDEX_CACHE_SUFFIX: Final = ".index.json"
_KNOWLEDGE_PREFIXES: Final = (f"{KNOWLEDGE_ROOT}/", f"{ONBOARDING_ROOT}/")
_FILE_MODES: Final = frozenset({"100644", "100755"})


class KnowledgeTreeReadError(ValueError):
    """A tree could not be read exactly."""


def is_excluded_from_knowledge(relative: str) -> bool:
    """Answer whether a relative path is outside knowledge: a route-index cache, or in a hidden
    directory (``.ar-index``).

    Only *directories* are hidden this way. A dot-named file is a knowledge file like any other --
    ``onboarding/.git-blame-ignore-revs.md`` is the card of the code repository's
    ``.git-blame-ignore-revs`` -- so no file escapes validation by its name. The validator and
    ``agents-remember knowledge-format`` share this one predicate.
    """

    directories = relative.split("/")[:-1]
    return relative.endswith(ROUTE_INDEX_CACHE_SUFFIX) or any(
        part.startswith(".") for part in directories
    )


def is_knowledge_path(path: str) -> bool:
    """Answer whether ``path`` is a knowledge or onboarding file the validator reads."""

    return path.startswith(_KNOWLEDGE_PREFIXES) and not is_excluded_from_knowledge(path)


@dataclass(frozen=True)
class KnowledgeTree:
    """The bytes of every knowledge and onboarding file of one memory tree, by path."""

    label: str
    files: Mapping[str, bytes]

    @property
    def converted(self) -> bool:
        """A memory tree is converted exactly when it holds the layout marker."""

        return LAYOUT_MARKER_PATH in self.files

    def get(self, path: str) -> bytes | None:
        return self.files.get(path)


class CodeTree(Protocol):
    """The paired code tree, as anchors read it.

    ``has_file`` and ``has_directory`` answer existence (anchor paths, MIK-R22 rule 6; families,
    MIK-R04 rule 1). ``read`` returns the exact bytes of a regular file at the tree, or ``None``
    when the tree holds nothing at the path: the reference-anchor rule (MIK-R198 rule 5) resolves
    an anchor's locator and content identity against those bytes, and the blob identity from
    them, never against a remembered or working-tree copy.
    """

    @property
    def label(self) -> str: ...

    def has_file(self, path: str) -> bool: ...

    def has_directory(self, path: str) -> bool: ...

    def read(self, path: str) -> bytes | None: ...


@dataclass(frozen=True)
class CodePathSet:
    """A code tree given as the set of its file paths (a Git tree, or a test's literal set).

    ``read`` serves the bytes behind those paths, in one of three ways: ``contents`` for an
    in-memory set, ``repository`` plus ``blobs`` for a Git tree (each blob read once, on demand),
    or ``root`` for a working-tree set. A set that carries none of the three answers ``read`` with
    ``None`` for every path; a reference-anchor check against it refuses rather than guess.
    """

    label: str
    paths: frozenset[str]
    blobs: Mapping[str, str] | None = None
    """The tree's blob ID per path, when the set came from a Git tree."""
    repository: Path | None = None
    """The Git repository holding ``blobs``' objects, read on demand."""
    contents: Mapping[str, bytes] | None = None
    """Exact bytes per path, for an in-memory tree."""
    root: Path | None = None
    """The working-tree directory ``paths`` were listed from, read on demand."""
    _read: dict[str, bytes] = field(default_factory=dict, compare=False, repr=False)

    def has_file(self, path: str) -> bool:
        return path in self.paths

    def read(self, path: str) -> bytes | None:
        if path not in self.paths:
            return None
        if path in self._read:
            return self._read[path]
        data = self._content(path)
        if data is not None:
            self._read[path] = data
        return data

    def prefetch(self, paths: Collection[str]) -> None:
        """Read many paths' bytes through one Git call, so a whole-tree check is not one call each.

        The reference-anchor rule asks for every cited path of a memory tree before it resolves an
        anchor (MIK-R198 rule 5); without this a Git-backed tree would start one ``git cat-file`` per
        cited file. Only the requested files are read, never the whole tree.
        """

        wanted = {
            path: self.blobs[path]
            for path in paths
            if path in self.paths and path not in self._read and self.blobs and path in self.blobs
        }
        if wanted and self.repository is not None:
            contents = read_git_blobs_bytes(self.repository, wanted.values())
            self._read.update({path: contents[blob] for path, blob in wanted.items()})
            return
        for path in paths:
            self.read(path)

    def _content(self, path: str) -> bytes | None:
        if self.contents is not None and path in self.contents:
            return self.contents[path]
        blob = None if self.blobs is None else self.blobs.get(path)
        if blob is not None and self.repository is not None:
            return read_git_blobs_bytes(self.repository, [blob])[blob]
        target = None if self.root is None else self.root / path
        return target.read_bytes() if target is not None and target.is_file() else None

    @cached_property
    def directories(self) -> frozenset[str]:
        """Every directory that holds a file of the set, at any depth (the root excluded)."""

        found: set[str] = set()
        for path in self.paths:
            parts = path.split("/")[:-1]
            found.update("/".join(parts[:end]) for end in range(1, len(parts) + 1))
        return frozenset(found)

    def has_directory(self, path: str) -> bool:
        return path == ROOT_ROUTE_PATH or path in self.directories


@dataclass(frozen=True)
class CodeDirectory:
    """A code tree given as a working-tree directory: a path exists when it is a regular file.

    ``read`` serves the file's exact bytes, once per path: the reference-anchor rule resolves every
    anchor of a whole tree against them (MIK-R198 rule 5), so a whole-tree run reads each cited file
    once rather than once per anchor.
    """

    label: str
    root: Path
    _read: dict[str, bytes] = field(default_factory=dict, compare=False, repr=False)

    def has_file(self, path: str) -> bool:
        return (self.root / path).is_file()

    def has_directory(self, path: str) -> bool:
        return (self.root / path).is_dir()

    def read(self, path: str) -> bytes | None:
        if path not in self._read:
            target = self.root / path
            if not target.is_file():
                return None
            self._read[path] = target.read_bytes()
        return self._read[path]


def code_tree_from_working_directory(root: Path) -> CodePathSet:
    """Regular working-tree files Git tracks or would add, excluding ignored files and links."""
    result = run_git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    if result.returncode != 0:
        raise KnowledgeTreeReadError(f"cannot list the files of {root}: {result.stderr}")
    paths = frozenset(
        path
        for path in result.stdout.split("\0")
        if path and not (root / path).is_symlink() and (root / path).is_file()
    )
    return CodePathSet(label=root.as_posix(), paths=paths, root=root)


def knowledge_tree_from_directory(root: Path, *, label: str | None = None) -> KnowledgeTree:
    """Read every knowledge and onboarding file under a memory working tree ``root``."""

    files: dict[str, bytes] = {}
    for top in (KNOWLEDGE_ROOT, ONBOARDING_ROOT):
        base = root / top
        if not base.is_dir():
            continue
        for directory, directories, names in os.walk(base):
            directories[:] = sorted(name for name in directories if not name.startswith("."))
            for name in sorted(names):
                full = Path(directory) / name
                relative = full.relative_to(root).as_posix()
                if is_knowledge_path(relative) and full.is_file():
                    files[relative] = full.read_bytes()
    return KnowledgeTree(label=label or root.as_posix(), files=files)


def _resolve_tree(repository: Path, treeish: str) -> str:
    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", f"{treeish}^{{tree}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    tree = result.stdout.strip()
    if result.returncode != 0 or not tree:
        raise KnowledgeTreeReadError(f"{treeish!r} does not name a tree in {repository}")
    return tree


def tree_blobs(repository: Path, tree: str) -> dict[str, str]:
    """Map each regular file path of ``tree`` to its blob ID."""

    blobs: dict[str, str] = {}
    for row in read_git_tree_bytes(repository, tree).split(b"\0"):
        if not row:
            continue
        metadata, path_bytes = row.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        if kind == "blob" and mode in _FILE_MODES:
            blobs[os.fsdecode(path_bytes)] = object_id
    return blobs


def knowledge_tree_from_git(
    repository: Path, treeish: str, *, label: str | None = None
) -> KnowledgeTree:
    """Read the knowledge and onboarding files of a commit or tree in ``repository``."""

    tree = _resolve_tree(repository, treeish)
    wanted = {
        path: blob for path, blob in tree_blobs(repository, tree).items() if is_knowledge_path(path)
    }
    contents = read_git_blobs_bytes(repository, wanted.values())
    files = {path: contents[blob] for path, blob in sorted(wanted.items())}
    return KnowledgeTree(label=label or treeish, files=files)


def history_tree_from_git(repository: Path, treeish: str) -> KnowledgeTree:
    """Only the history files of a commit or tree: all the freeze rules read of a tree that is not
    a comparison base (the commit a leaf's candidate sits on)."""

    tree = _resolve_tree(repository, treeish)
    wanted = {
        path: blob
        for path, blob in tree_blobs(repository, tree).items()
        if path.startswith("knowledge/history/")
    }
    contents = read_git_blobs_bytes(repository, wanted.values())
    files = {path: contents[blob] for path, blob in sorted(wanted.items())}
    return KnowledgeTree(label=f"history of {treeish}", files=files)


def code_tree_from_git(repository: Path, treeish: str, *, label: str | None = None) -> CodePathSet:
    """Return the file paths, and the blobs behind them, of a commit or tree in ``repository``."""

    tree = _resolve_tree(repository, treeish)
    blobs = tree_blobs(repository, tree)
    return CodePathSet(
        label=label or treeish,
        paths=frozenset(blobs),
        blobs=blobs,
        repository=repository,
    )
