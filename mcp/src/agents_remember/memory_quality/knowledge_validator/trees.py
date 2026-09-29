"""The two inputs the validator reads: a memory tree's knowledge files and a code tree's paths.

A :class:`KnowledgeTree` is the exact bytes of every file under ``knowledge/`` and ``onboarding/``
of one memory tree, keyed by repository-relative POSIX path. The generated route-index cache
(``*.index.json``) is not knowledge and is never read. A tree is *converted* exactly when it holds
the layout marker ``knowledge/layout.json`` (MIK-R21 rule 1).

A :class:`CodeTree` answers one question for anchor path existence (MIK-R22 rule 6): does the paired
code tree hold a file at this path?

Both are read from a directory (a working tree, for the curator's command and the writer) or from a
Git tree (for a commit route, where the candidate is the exact staged tree and the bases are
commits). Git bytes are read without decoding or newline normalization, so the canonical-format
check sees what will be committed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
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
    """The paired code tree, as anchor path existence sees it."""

    @property
    def label(self) -> str: ...

    def has_file(self, path: str) -> bool: ...


@dataclass(frozen=True)
class CodePathSet:
    """A code tree given as the set of its file paths (a Git tree, or a test's literal set)."""

    label: str
    paths: frozenset[str]

    def has_file(self, path: str) -> bool:
        return path in self.paths


@dataclass(frozen=True)
class CodeDirectory:
    """A code tree given as a working-tree directory: a path exists when it is a regular file."""

    label: str
    root: Path

    def has_file(self, path: str) -> bool:
        return (self.root / path).is_file()


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


def _tree_blobs(repository: Path, tree: str) -> dict[str, str]:
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
        path: blob
        for path, blob in _tree_blobs(repository, tree).items()
        if is_knowledge_path(path)
    }
    contents = read_git_blobs_bytes(repository, wanted.values())
    files = {path: contents[blob] for path, blob in sorted(wanted.items())}
    return KnowledgeTree(label=label or treeish, files=files)


def code_tree_from_git(repository: Path, treeish: str, *, label: str | None = None) -> CodePathSet:
    """Return the file paths of a commit or tree in the code ``repository``."""

    tree = _resolve_tree(repository, treeish)
    return CodePathSet(label=label or treeish, paths=frozenset(_tree_blobs(repository, tree)))
