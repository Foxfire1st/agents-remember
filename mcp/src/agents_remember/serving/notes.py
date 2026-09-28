"""Read-only coordination-notes API: list + read ``tasks/<repo>/<master>/notes/``.

L9 of the agent-orchestration series (friction F-M): the coordination ``notes/`` tree —
design records, the friction ledger, worker turn reports, adversarial verdicts — had NO
dashboard surface; the files API serves only repo roots + worktree enclosures and the
task reader renders only ``task.json`` content, so a task doc's references to notes
files were inert strings. These endpoints give the task reader a notes list + a note
body for one series master.

Security posture (inherited from the L1 files API): GET-only, read-only,
127.0.0.1-bound, no auth/CORS. The repo is checked against ``config.allowed_repo_ids``
(``require_repo``); ``master`` is confined to a single path segment (the change-set
``master`` key idiom); every served path is confined to the series' notes root via the
``confine_rel`` realpath idiom, so ``..`` traversal and symlink escapes are rejected.
A missing notes folder is an EMPTY LIST, never an error — a series without notes is a
normal young series, not a failure.

The listing walks subfolders (``notes/reports/`` is where worker reports live) up to a
small depth cap and reports ``truncated: true`` when the cap pruned anything, so the
list never lies about completeness. Binary files read as ``language: "binary"`` with
empty content (the reader shows a placeholder), mirroring ``serving/files.py``.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    path_is_relative_to,
)
from agents_remember.kernel.sidecar_pairing import confine_rel
from agents_remember.serving.response_contract import (
    SCOPED_READ_RESPONSES,
    NoteContents,
    NotesListing,
)
from agents_remember.serving.scope import decode_capped, language_for

# Mirrors the serving.files read cap: a pathological file never blocks the event loop;
# the full byte size is still reported (truncated=True).
_MAX_FILE_BYTES = 2 * 1024 * 1024

# The notes tree is shallow by doctrine (reports/ is one level down); the cap keeps a
# runaway/looping tree from stalling the walk, and the listing says when it pruned.
_MAX_LIST_DEPTH = 4


def _is_single_segment(master: str) -> bool:
    """True when ``master`` is one honest path segment (no separators, no dot-prefix).

    Same confinement as the change-set ``master`` key: a wire value can never escape
    the ``tasks/<repo>/`` tree by smuggling separators or ``..`` into the series name.
    """
    return bool(master) and "/" not in master and "\\" not in master and not master.startswith(".")


def _notes_root(config: McpRuntimeConfig, repo_id: str, master: str) -> Path:
    """The series' notes root under the coordination tree (existence NOT required)."""
    return config.coordination_root / "tasks" / repo_id / master / "notes"


# Directories are entered through descriptors opened relative to their already-confined parent:
# a directory swapped for a symlink (or a file) after the walk observed it fails the open with
# ELOOP/ENOTDIR and is refused instead of being followed out of the root. Not covered: a
# directory renamed out of the root while it is being listed still has its own entries listed
# through the open descriptor (whoever can move it could equally place that content inside the
# root); and a symlink's stat is taken during the sort, before its path check, so retargeting it
# in between can list one outside file's size (a path-based check-then-stat has the same window
# in the other order). Reads stay confined by ``confine_rel`` regardless.
_DIR_OPEN = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_SWAPPED_AWAY = frozenset({errno.ELOOP, errno.ENOTDIR})
# The errors ``Path.is_dir()`` reads as "not a directory" (a dangling, looping or file-traversing
# symlink); any other stat failure propagates exactly as it did through ``Path.is_dir()``.
_NOT_A_DIR = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})


@dataclass
class _NotesWalk:
    """One listing's accumulator: the real notes root and its descriptor, the rows, the prune flag."""

    root: Path
    root_fd: int = -1
    notes: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


def _is_dir(entry: os.DirEntry[str]) -> bool:
    """The directory-first sort key with ``Path.is_dir()``'s error contract.

    ``DirEntry.is_dir()`` swallows only ``FileNotFoundError``; a looping or file-traversing
    symlink would otherwise raise out of ``sorted`` and fail the whole listing.
    """
    try:
        return entry.is_dir()
    except OSError as err:
        if err.errno in _NOT_A_DIR:
            return False
        raise


def _confined_stat(entry: os.DirEntry[str], lexical: Path, root: Path) -> os.stat_result | None:
    """The entry's followed stat, or ``None`` when it escapes ``root`` or cannot be read.

    Only a symlink can escape: the walk starts at the realpath'd root and enters only directories
    opened beneath it, so a non-symlink child is confined by construction and needs no
    ``realpath``. Resolving every entry cost three ``realpath`` calls each (one ``lstat`` per path
    component), which dominated the listing and, being pure-Python per-entry work, stretched it
    many-fold under the GIL on a busy server. ``lexical`` is the entry's path through the walk.
    """
    try:
        if entry.is_symlink() and not path_is_relative_to(lexical.resolve(), root):
            return None  # symlink escape: never listed, never served
        return entry.stat()
    except OSError:
        return None  # an unresolvable/vanished entry is skipped, never listed


def _open_below_root(walk: _NotesWalk, rel: Path) -> int:
    """A descriptor for ``root/rel``, every component opened without following a symlink."""
    fd = os.open(".", _DIR_OPEN, dir_fd=walk.root_fd)
    for part in rel.parts:
        try:
            child_fd = os.open(part, _DIR_OPEN, dir_fd=fd)
        finally:
            os.close(fd)
        fd = child_fd
    return fd


def _open_subdir(
    walk: _NotesWalk, entry: os.DirEntry[str], lexical: Path, dir_fd: int
) -> int | None:
    """A descriptor for a confined subdirectory, or ``None`` when it was swapped since observed.

    A plain directory is opened relative to its parent's descriptor. An in-root symlink to a
    directory (listed under its own path, as before) is re-resolved and opened component by
    component from the root, so a retargeted link cannot lead outside either. Only the
    swap signature is refused; every other open failure propagates as the path-based walk's did.
    """
    try:
        if not entry.is_symlink():
            return os.open(entry.name, _DIR_OPEN, dir_fd=dir_fd)
        target = lexical.resolve()
        if not path_is_relative_to(target, walk.root):
            return None
        return _open_below_root(walk, target.relative_to(walk.root))
    except OSError as err:
        if err.errno in _SWAPPED_AWAY:
            return None
        raise


def _walk_notes(walk: _NotesWalk, dir_fd: int, base: Path, prefix: str, depth: int) -> None:
    """Collect note files in the directory ``dir_fd``, files before subfolders, escapes skipped.

    ``base`` is that directory's path through the walk (symlink confinement is judged on it) and
    ``prefix`` its posix path relative to the root (``""`` at the root), so a row's ``path`` is
    built from entry names. A directory beyond the depth cap is not entered and flips
    ``truncated``; only regular files (after following an in-root symlink) are listed.
    """
    with os.scandir(dir_fd) as scan:
        children = sorted(scan, key=lambda e: (_is_dir(e), e.name.lower()))
    for child in children:
        lexical = base / child.name
        st = _confined_stat(child, lexical, walk.root)
        if st is None:
            continue
        if S_ISDIR(st.st_mode):
            if depth >= _MAX_LIST_DEPTH:
                walk.truncated = True
                continue
            _walk_subdir(walk, child, lexical, dir_fd, depth + 1)
        elif S_ISREG(st.st_mode):
            walk.notes.append(
                {
                    "name": child.name,
                    "path": f"{prefix}{child.name}",
                    "size": st.st_size,
                    "language": language_for(Path(child.name)),
                }
            )


def _walk_subdir(
    walk: _NotesWalk, entry: os.DirEntry[str], lexical: Path, dir_fd: int, depth: int
) -> None:
    """Enter one confined subdirectory through its own descriptor; a swapped one is skipped."""
    sub_fd = _open_subdir(walk, entry, lexical, dir_fd)
    if sub_fd is None:
        return
    try:
        _walk_notes(walk, sub_fd, lexical, f"{lexical.relative_to(walk.root).as_posix()}/", depth)
    finally:
        os.close(sub_fd)


def list_notes(config: McpRuntimeConfig, repo_id: str, master: str) -> dict[str, Any]:
    """Every note file for one series master, notes-root-relative; missing folder -> []."""
    root = _notes_root(config, repo_id, master)
    walk = _NotesWalk(root=root)
    if root.is_dir():
        walk.root = root.resolve()
        walk.root_fd = os.open(walk.root, _DIR_OPEN)
        try:
            _walk_notes(walk, walk.root_fd, walk.root, "", 1)
        finally:
            os.close(walk.root_fd)
    return {"repo": repo_id, "master": master, "notes": walk.notes, "truncated": walk.truncated}


def read_note(config: McpRuntimeConfig, repo_id: str, master: str, rel: str) -> dict[str, Any]:
    """One note's content (size-capped, binary-tolerant), confined to the notes root."""
    root = _notes_root(config, repo_id, master)
    relp = confine_rel(root, rel)
    src = root / relp
    if not src.is_file():
        raise FileNotFoundError(rel)
    raw = src.read_bytes()
    truncated = len(raw) > _MAX_FILE_BYTES
    try:
        # Cut at a UTF-8 codepoint boundary: a multi-byte char straddling the cap must NOT make an
        # oversize text/markdown note misdecode into an empty "binary" (260703-L18 finding 5).
        content, truncated = decode_capped(raw, _MAX_FILE_BYTES)
        language = language_for(src)
    except UnicodeDecodeError:
        content, language = "", "binary"
    return {
        "repo": repo_id,
        "master": master,
        "path": relp,
        "language": language,
        "size": len(raw),
        "truncated": truncated,
        "content": content,
    }


def _notes_json(
    config: McpRuntimeConfig, repo_id: str, master: str, produce: Callable[[], dict[str, Any]]
) -> Response:
    """Validate the ``{repo, master}`` selector, run ``produce``, map the status idiom.

    Unknown repo -> 404 ``unknown-repo`` (the allow-list is the boundary), a
    multi-segment ``master`` -> 400 ``bad-request``, a confined-path violation -> 400
    ``bad-path``, a missing note file -> 404 ``not-found`` — the same wire idiom as the
    files/change-set APIs so ``data/files.ts``'s error mapping applies unchanged.
    """
    try:
        require_repo(config, repo_id)
    except AuthorityError:
        return JSONResponse({"status": "unknown-repo", "repo": repo_id}, status_code=404)
    if not _is_single_segment(master):
        return JSONResponse(
            {"status": "bad-request", "detail": "notes need a single-segment master"},
            status_code=400,
        )
    try:
        return JSONResponse(produce(), status_code=200)
    except (AuthorityError, ValueError) as err:
        # ValueError: Path.resolve() rejects malformed input (e.g. an embedded null
        # byte) before confinement even runs — same wire answer as a confinement breach.
        return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
    except FileNotFoundError as err:
        return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)


def register_notes_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    """Register the read-only notes routes. Must be called BEFORE the greedy static mount."""

    @app.get("/api/notes/list", response_model=NotesListing, responses=SCOPED_READ_RESPONSES)
    def api_notes_list(repo: str, master: str = "") -> Response:
        return _notes_json(config, repo, master, lambda: list_notes(config, repo, master))

    @app.get("/api/notes/read", response_model=NoteContents, responses=SCOPED_READ_RESPONSES)
    def api_notes_read(repo: str, master: str = "", path: str = "") -> Response:
        return _notes_json(config, repo, master, lambda: read_note(config, repo, master, path))
