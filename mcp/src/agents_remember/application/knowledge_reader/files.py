"""The files behind a reader selection: memory prose and sidecars, code paths and code text.

A selection's memory files are read from Git objects (a commit) or from the scope's working
directory (``published``, a leaf); code is always read from the selection's code tree in the code
repository's object store. Every read is confined: a memory path stays under the memory root, a
code path is a repository-relative path handed to ``ls-tree`` / ``cat-file``, never to the file
system. Nothing is written.

Three answers are kept apart everywhere: the file is **present** (with its text), **absent** (the
tree holds nothing there -- a normal fact, shown as such) and **unavailable** (the tree or object
could not be read, with the reason). An unavailable read is never rendered as absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal

from agents_remember.application.knowledge_reader.selection import (
    READ_FAILURES,
    ReaderReadError,
    ReaderSelection,
)
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    run_git,
)
from agents_remember.kernel.sidecar_pairing import confine_rel
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH

__all__ = [
    "CODE_TEXT_LIMIT",
    "FileRead",
    "ReaderRequestError",
    "census_files",
    "code_kind",
    "code_text",
    "normal_path",
    "read_memory_file",
]

FileState = Literal["present", "absent", "unavailable", "binary", "too-large"]
# The code view serves text only, and at most this many bytes of it: a larger blob, or one that is
# not UTF-8 text, answers a bounded notice naming its size instead of its bytes (review F14).
CODE_TEXT_LIMIT: Final = 2 * 1024 * 1024
_BINARY_PROBE_BYTES: Final = 8192
_CENSUS_ROOT: Final = f"{KNOWLEDGE_ROOT}/census"
_OPTIONS: Final = GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS)


class ReaderRequestError(ValueError):
    """The question names something the reader cannot address: a path outside the repository."""


@dataclass(frozen=True)
class FileRead:
    """One file of the selected tree: its state, its text when present, the reason otherwise."""

    path: str
    state: FileState
    text: str | None = None
    detail: str | None = None

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"path": self.path, "state": self.state}
        if self.text is not None:
            document["text"] = self.text
        if self.detail is not None:
            document["detail"] = self.detail
        return document


def normal_path(path: str) -> str:
    """A repository-relative POSIX path, ``.`` for the root; refuses ``..``, absolute paths and
    control characters (a request error, never a Git call)."""

    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        # A NUL or other control character never names a repository path (review F15).
        raise ReaderRequestError(f"a path holds no control character: {path!r}")
    cleaned = path.strip().strip("/")
    if cleaned in ("", ROOT_ROUTE_PATH):
        return ROOT_ROUTE_PATH
    pure = PurePosixPath(cleaned)
    if pure.is_absolute() or any(part in ("..", ".", "") for part in pure.parts):
        raise ReaderRequestError(f"not a repository-relative path: {path!r}")
    return pure.as_posix()


def read_memory_file(selection: ReaderSelection, path: str) -> FileRead:
    """Read one memory-repository file of the selected tree."""

    try:
        if selection.memory_root is not None:
            target = selection.memory_root / confine_rel(selection.memory_root, path)
            if not target.is_file():
                return FileRead(path, "absent")
            return FileRead(path, "present", target.read_text(encoding="utf-8"))
        result = run_git(
            selection.memory_repository,
            ["cat-file", "-t", f"{selection.revision}:{path}"],
            _OPTIONS,
        )
        if result.returncode != 0 or result.stdout.strip() != "blob":
            return FileRead(path, "absent")
        text = run_git(
            selection.memory_repository,
            ["cat-file", "blob", f"{selection.revision}:{path}"],
            _OPTIONS,
        )
        if text.returncode != 0:
            return FileRead(
                path, "unavailable", detail=text.stderr.strip() or "git cat-file failed"
            )
        return FileRead(path, "present", text.stdout)
    except (*READ_FAILURES, UnicodeDecodeError) as error:
        return FileRead(path, "unavailable", detail=f"{type(error).__name__}: {error}")


def code_kind(selection: ReaderSelection, path: str) -> Literal["file", "dir"] | None:
    """Whether ``path`` is a file or directory of the selection's code tree (``None``: neither)."""

    if selection.code_tree is None:
        return None
    if path == ROOT_ROUTE_PATH:
        return "dir"
    result = run_git(
        selection.code_tree.repository,
        ["cat-file", "-t", f"{selection.code_tree.tree}:{path}"],
        _OPTIONS,
    )
    kind = result.stdout.strip() if result.returncode == 0 else ""
    return "file" if kind == "blob" else "dir" if kind == "tree" else None


def code_text(selection: ReaderSelection, path: str) -> tuple[str, str] | FileRead:
    """``(blob, text)`` of ``path`` at the selection's code tree, or why it is not served as text.

    The blob's size is asked before its bytes are read, so a large or binary file is answered with
    a bounded notice and never loaded whole.
    """

    located = _code_blob(selection, path)
    if isinstance(located, FileRead):
        return located
    blob, repository = located
    try:
        size = _blob_size(repository, blob)
        if size > CODE_TEXT_LIMIT:
            return FileRead(path, "too-large", detail=_size_notice(blob, size))
        data = read_git_blobs_bytes(repository, [blob])[blob]
    except READ_FAILURES as error:
        return FileRead(path, "unavailable", detail=f"{type(error).__name__}: {error}")
    return _text(path, blob, data)


def _code_blob(selection: ReaderSelection, path: str) -> tuple[str, Path] | FileRead:
    if selection.code_tree is None:
        return FileRead(path, "unavailable", detail=selection.code_note)
    repository, tree = selection.code_tree.repository, selection.code_tree.tree
    result = run_git(repository, ["rev-parse", "--verify", "--quiet", f"{tree}:{path}"], _OPTIONS)
    blob = result.stdout.strip()
    if result.returncode != 0 or not blob:
        return FileRead(path, "absent", detail=f"the code tree {tree[:12]} holds no {path}")
    kind = run_git(repository, ["cat-file", "-t", blob], _OPTIONS).stdout.strip()
    if kind != "blob":
        # A directory (or a submodule) is a path of the tree, but not a file (review F18).
        return FileRead(path, "absent", detail=f"{path} is a {kind or 'non-file'}, not a file")
    return blob, repository


def _text(path: str, blob: str, data: bytes) -> tuple[str, str] | FileRead:
    binary = FileRead(path, "binary", detail=f"blob {blob[:12]} is not text ({len(data)} bytes)")
    if b"\0" in data[:_BINARY_PROBE_BYTES]:
        return binary
    try:
        return blob, data.decode("utf-8")
    except UnicodeDecodeError:
        return binary


def _blob_size(repository: Path, blob: str) -> int:
    result = run_git(repository, ["cat-file", "-s", blob], _OPTIONS)
    if result.returncode != 0:
        raise ReaderReadError(result.stderr.strip() or f"git cat-file -s {blob} failed")
    return int(result.stdout.strip())


def _size_notice(blob: str, size: int) -> str:
    return f"blob {blob[:12]} is {size} bytes, above the {CODE_TEXT_LIMIT}-byte code view bound"


def census_files(selection: ReaderSelection) -> dict[str, bytes]:
    """Every file under ``knowledge/census/`` of the selected tree, by memory-relative path."""

    if selection.memory_root is not None:
        return _census_directory(selection.memory_root)
    result = run_git(
        selection.memory_repository,
        ["ls-tree", "-r", "-z", str(selection.revision), "--", f"{_CENSUS_ROOT}/"],
        _OPTIONS,
    )
    if result.returncode != 0:
        raise ReaderReadError(result.stderr.strip() or "git ls-tree failed")
    rows = [_row(entry) for entry in result.stdout.split("\0") if entry]
    blobs = {path: obj for kind, obj, path in rows if kind == "blob"}
    data = read_git_blobs_bytes(selection.memory_repository, blobs.values())
    return {path: data[obj] for path, obj in blobs.items()}


def _census_directory(memory_root: Path) -> dict[str, bytes]:
    root = memory_root / _CENSUS_ROOT
    if not root.is_dir():
        return {}
    return {
        path.relative_to(memory_root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _row(entry: str) -> tuple[str, str, str]:
    meta, _, path = entry.partition("\t")
    _mode, kind, obj = meta.split(" ", 2)
    return kind, obj, path
