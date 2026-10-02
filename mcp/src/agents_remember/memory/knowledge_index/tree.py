"""A memory tree as the index reads it: its key and the bytes of its knowledge files (MIK-R23 rules 1, 2).

A memory tree reaches the index in one of two forms, and both end as the same value, a
:class:`MemoryTreeSnapshot`:

* **A Git tree** read through Git objects (``ls-tree`` and one ``cat-file --batch``), with nothing
  checked out. Its key is the tree id.
* **A working-tree directory.** Its key is the tree id of its *captured state*: ``git add -A`` into
  a temporary index, then ``git write-tree``. The temporary index and every object the capture
  writes live in a disposable directory; the repository's own object database is read as an
  alternate and never written, so computing a key changes nothing in the repository. The capture
  honours ``.gitignore`` exactly as a commit would, so identical content gets an identical key
  and ignored caches (``overview.index.json``) never reach it.

The file bytes of a directory are read from disk and then checked against the blob ids the captured
tree records for the same paths; a file that changed between the two reads fails that check and the
capture is taken again. The snapshot's bytes are therefore always the bytes its key names.

Only the files the index reads are carried: JSON documents under ``knowledge/`` (census files
excepted, MIK-R20 owns them) and under ``onboarding/``, with the validator's exclusions: generated
``*.index.json`` caches and hidden directories. Everything else in the tree -- prose, legacy
databases, notes -- contributes to the key only.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import (
    IsolatedGitState,
    copy_git_index,
    read_git_blobs_bytes,
    read_git_tree_bytes,
    run_git,
    run_git_with_isolated_index_and_objects,
)
from agents_remember.memory_quality.knowledge_validator.trees import is_excluded_from_knowledge
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, ONBOARDING_ROOT

SourceKind = Literal["directory", "git"]

_CENSUS_PREFIX = f"{KNOWLEDGE_ROOT}/census/"
_CAPTURE_ATTEMPTS = 3


class MemoryTreeError(ValueError):
    """The named memory tree cannot be read: not a Git tree, not a Git working tree, or unstable."""


@dataclass(frozen=True)
class MemoryTreeSnapshot:
    """One memory tree: its key (a Git tree id), where it came from, and its knowledge files."""

    key: str
    source: SourceKind
    location: str
    files: Mapping[str, bytes]


def is_indexed_path(path: str) -> bool:
    """Answer whether the index reads the file at this memory-repository-relative path.

    The index reads the JSON knowledge files the validator reads (MIK-R22's shared
    :func:`is_excluded_from_knowledge`: no route-index cache, nothing in a hidden directory), less
    the census files, whose schemas MIK-R20 owns and the index does not answer for.
    """

    if not path.endswith(".json") or is_excluded_from_knowledge(path):
        return False
    if path.startswith(f"{KNOWLEDGE_ROOT}/"):
        return not path.startswith(_CENSUS_PREFIX)
    return path.startswith(f"{ONBOARDING_ROOT}/")


def git_tree_snapshot(repository: Path, revision: str) -> MemoryTreeSnapshot:
    """Read the memory tree ``revision`` names (a commit, a tree or a ref) through Git objects."""

    tree = _git_output(repository, ["rev-parse", "--verify", "--quiet", f"{revision}^{{tree}}"])
    if tree is None:
        raise MemoryTreeError(f"{revision!r} names no Git tree in {repository}")
    entries = _indexed_entries(read_git_tree_bytes(repository, tree))
    blobs = read_git_blobs_bytes(repository, entries.values())
    files = {path: blobs[blob_id] for path, blob_id in entries.items()}
    return MemoryTreeSnapshot(key=tree, source="git", location=str(repository), files=files)


def directory_snapshot(directory: Path) -> MemoryTreeSnapshot:
    """Capture a working-tree directory: its captured tree id and its knowledge files' bytes.

    ``directory`` is a Git working tree or a directory inside one; the key is the id of the tree
    rooted at ``directory``. A directory Git does not track is refused: the key is a Git tree id,
    and a tree id needs a repository to compute it in.
    """

    root = directory.resolve()
    if _git_output(root, ["rev-parse", "--is-inside-work-tree"]) != "true":
        raise MemoryTreeError(f"{root} is not inside a Git working tree")
    for _ in range(_CAPTURE_ATTEMPTS):
        key, entries = _capture(root)
        files = {path: (root / path).read_bytes() for path in entries}
        if all(_blob_id(data, key) == entries[path] for path, data in files.items()):
            return MemoryTreeSnapshot(key=key, source="directory", location=str(root), files=files)
    raise MemoryTreeError(
        f"the files under {root} kept changing while their state was captured; no key was taken"
    )


def directory_key(directory: Path) -> str:
    """Return the captured tree id of a working-tree directory without reading its files."""

    root = directory.resolve()
    if _git_output(root, ["rev-parse", "--is-inside-work-tree"]) != "true":
        raise MemoryTreeError(f"{root} is not inside a Git working tree")
    return _capture(root)[0]


def _capture(root: Path) -> tuple[str, dict[str, str]]:
    """Return (tree id, indexed path -> blob id) of ``root``'s captured state."""

    prefix = _git_output(root, ["rev-parse", "--show-prefix"]) or ""
    objects = _absolute_git_path(root, "objects")
    real_index = _absolute_git_path(root, "index")
    with tempfile.TemporaryDirectory(prefix="ar-knowledge-index-capture-") as temporary:
        scratch = Path(temporary)
        state = IsolatedGitState(scratch / "index", scratch / "objects", objects)
        if real_index.is_file():
            # The repository's own index is copied, never used: its stat data lets the capture
            # rehash only the files that changed, and the copy is what the capture then updates.
            # The copy keeps the index file's time, or a file rewritten in the second the index
            # was written would keep its old blob in the key (:func:`copy_git_index`).
            copy_git_index(real_index, state.index_path)
            _clear_index_flags(root, state)
        _isolated(root, ["add", "--all", "--", "."], state)
        tree_arguments = ["write-tree"] + ([f"--prefix={prefix}"] if prefix else [])
        tree = _isolated(root, tree_arguments, state).strip()
        listing = run_git_with_isolated_index_and_objects(
            root, ["ls-tree", "-r", "-z", "--full-tree", tree], state=state
        )
        if listing.returncode != 0:
            raise MemoryTreeError(f"the captured tree of {root} could not be listed")
        return tree, _indexed_entries(listing.stdout.encode("utf-8", "surrogateescape"))


def _clear_index_flags(root: Path, state: IsolatedGitState) -> None:
    """Clear ``assume-unchanged`` and ``skip-worktree`` in the scratch index copy.

    Both flags tell ``git add`` to trust the index over the file, so a copied flag would let an
    edited file keep its old blob and the key -- and every answer cached for it -- stay the previous
    content's (rule 5). The capture is of the directory's state, so no entry is trusted: the flags
    are cleared on the copy, and the repository's own index keeps them.
    """

    listing = _isolated(root, ["ls-files", "-v", "-z"], state)
    flagged = [
        row[2:]
        for row in listing.split("\0")
        if len(row) > 2 and (row[0].islower() or row[0] == "S")
    ]
    # One flag per command: ``update-index`` applies only the last of several such options.
    for option in ("--no-assume-unchanged", "--no-skip-worktree"):
        if not flagged:
            return
        result = run_git_with_isolated_index_and_objects(
            root,
            ["update-index", "-z", option, "--stdin"],
            state=state,
            input_text="".join(f"{path}\0" for path in flagged),
        )
        if result.returncode != 0:
            raise MemoryTreeError(
                f"capturing the state of {root} failed clearing index flags: "
                f"{result.stderr.strip()}"
            )


def _isolated(root: Path, arguments: list[str], state: IsolatedGitState) -> str:
    result = run_git_with_isolated_index_and_objects(root, arguments, state=state)
    if result.returncode != 0:
        raise MemoryTreeError(
            f"capturing the state of {root} failed at git {arguments[0]}: {result.stderr.strip()}"
        )
    return result.stdout


def _indexed_entries(listing: bytes) -> dict[str, str]:
    """Parse ``ls-tree -r -z`` rows into indexed path -> blob id."""

    entries: dict[str, str] = {}
    for row in listing.split(b"\0"):
        if not row:
            continue
        meta, _, raw_path = row.partition(b"\t")
        _mode, kind, object_id = meta.decode("ascii").split(" ")
        path = raw_path.decode("utf-8", "surrogateescape")
        if kind == "blob" and is_indexed_path(path):
            entries[path] = object_id
    return entries


def _blob_id(data: bytes, tree_id: str) -> str:
    """Return the Git blob id of ``data`` in the object format ``tree_id`` is written in."""

    header = f"blob {len(data)}\0".encode("ascii")
    digest = hashlib.sha256 if len(tree_id) == 64 else hashlib.sha1
    return digest(header + data).hexdigest()


def _absolute_git_path(root: Path, name: str) -> Path:
    value = _git_output(root, ["rev-parse", "--path-format=absolute", "--git-path", name])
    if value is None:
        raise MemoryTreeError(f"the Git {name} of {root} could not be located")
    return Path(value)


def _git_output(root: Path, arguments: list[str]) -> str | None:
    result = run_git(root, arguments)
    if result.returncode != 0:
        return None
    return result.stdout.strip()
