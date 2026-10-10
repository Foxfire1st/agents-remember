"""Read-only coordination-notes API: one folder + read ``tasks/<repo>/<master>/notes/``.

L9 of the agent-orchestration series (friction F-M): the coordination ``notes/`` tree —
design records, the friction ledger, worker turn reports, adversarial verdicts — had NO
dashboard surface; the files API serves only repo roots + worktree enclosures and the
task reader renders only ``task.json`` content, so a task doc's references to notes
files were inert strings. These endpoints give the task reader one notes folder, one
note body, and one-shot reference resolution for one series master.

MIK-R126 removed the endpoint that walked the whole tree: no route here descends below
the requested folder, and no response grows with the number of files under the notes
root. A folder with more entries than ``_FOLDER_PAGE`` answers the first part in a
stable order and says how many remain; a further request names the next ``offset``.

Security posture (inherited from the L1 files API): GET-only, read-only,
127.0.0.1-bound, no auth/CORS. The repo is checked against ``config.allowed_repo_ids``
(``require_repo``); ``master`` is confined to a single path segment (the change-set
``master`` key idiom); every served path is confined to the series' notes root via the
``confine_rel`` realpath idiom, so ``..`` traversal and symlink escapes are rejected.
A missing notes folder is an EMPTY FOLDER, never an error — a series without notes is a
normal young series, not a failure. A folder that disappears between two requests is
likewise empty, with the server's reason. Binary files read as ``language: "binary"``
with empty content (the reader shows a placeholder), mirroring ``serving/files.py``.
"""

from __future__ import annotations

import errno
import os
import re
from collections.abc import Callable
from pathlib import Path
from stat import S_ISDIR, S_ISLNK, S_ISREG
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
    NotesFolder,
    NotesResolutions,
)
from agents_remember.serving.scope import decode_capped, language_for

# Mirrors the serving.files read cap: a pathological file never blocks the event loop;
# the full byte size is still reported (truncated=True).
_MAX_FILE_BYTES = 2 * 1024 * 1024

# One folder answer never grows with the tree: at most this many entries per part, in a
# stable order (directories first, then files, case-insensitive by name). The ceiling is
# fixed here and pinned by the folder tests; a folder with more entries says how many
# remain and a further request names the next offset.
_FOLDER_PAGE = 200

# A reference string is free prose that may NAME a notes file. Path-like tokens are
# contiguous path characters ending in a dotted extension, so surrounding prose never
# bleeds into a candidate. Mirrors the dashboard's former client-side token scan.
_PATH_TOKEN = re.compile(r"[\w./-]+\.[A-Za-z0-9]+")


def _is_single_segment(master: str) -> bool:
    """True when ``master`` is one honest path segment (no separators, no dot-prefix).

    Same confinement as the change-set ``master`` key: a wire value can never escape
    the ``tasks/<repo>/`` tree by smuggling separators or ``..`` into the series name.
    """
    return bool(master) and "/" not in master and "\\" not in master and not master.startswith(".")


def _notes_root(config: McpRuntimeConfig, repo_id: str, master: str) -> Path:
    """The series' notes root under the coordination tree (existence NOT required)."""
    return config.coordination_root / "tasks" / repo_id / master / "notes"


# Open a directory without following a trailing symlink: a folder swapped for a link
# after confinement fails here instead of listing outside entries. Mirrors the removed
# whole-tree walk's dir_fd-relative idiom.
_DIR_OPEN = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

# A confined folder that stopped being a plain directory reads as "gone" (a dangling,
# looping or file-traversing symlink, a removed or swapped directory).
_SWAPPED_AWAY = frozenset({errno.ELOOP, errno.ENOTDIR})


def _open_confined_dir(root: Path, confined: str) -> int:
    """A descriptor for ``root/confined``, no path component following a symlink.

    ``confined`` comes from ``confine_rel`` (already realpath-confined), so any symlink
    met here was swapped in after confinement and the open fails: ELOOP for the swapped
    link, ENOENT/ENOTDIR for a vanished/file path. The caller closes the descriptor.
    """
    fd = os.open(root, _DIR_OPEN)
    try:
        for part in confined.split("/") if confined else ():
            child_fd = os.open(part, _DIR_OPEN, dir_fd=fd)
            os.close(fd)
            fd = child_fd
    except BaseException:
        os.close(fd)
        raise
    return fd


def _folder_entries(root: Path, confined: str) -> tuple[list[dict[str, Any]], bool, str | None]:
    """One folder's entries, or ([], False, reason) when the folder is gone.

    ``confined`` is the caller-confined notes-relative folder (``""`` at the root).
    Only the requested folder is read — never its subfolders, so the work does not
    grow with the number of files under the notes root. The folder is opened through
    held descriptors without following symlinks, so a directory swapped for an
    escaping link after confinement reads as gone instead of listing outside entries.
    Symlink escapes are skipped; an unresolvable/vanished entry is skipped, never listed.
    """
    try:
        dir_fd = _open_confined_dir(root, confined)
    except FileNotFoundError:
        return [], False, "that folder is not there"
    except NotADirectoryError:
        return [], False, "that path is a file, not a folder"
    except OSError as err:
        if err.errno in _SWAPPED_AWAY or err.errno == errno.EBADF:
            return [], False, "that folder cannot be read"
        raise
    try:
        names = os.listdir(dir_fd)
        resolved_root = root.resolve()
        rows: list[dict[str, Any]] = []
        for name in names:
            row = _folder_row(dir_fd, name, confined, resolved_root)
            if row is not None:
                rows.append(row)
    finally:
        os.close(dir_fd)
    # A deterministic total order: directories first, then a case-insensitive name
    # order with the exact name breaking case-fold ties, so independently fetched
    # offset parts never depend on readdir order.
    rows.sort(key=lambda row: (row["kind"] != "dir", row["name"].lower(), row["name"]))
    prefix = f"{confined}/" if confined else ""
    for row in rows:
        row["path"] = f"{prefix}{row['name']}"
    return rows, True, None


def _folder_row(
    dir_fd: int, name: str, confined: str, resolved_root: Path
) -> dict[str, Any] | None:
    """One folder row for a confined entry, or ``None`` when it escapes or vanished.

    The entry is examined without following symlinks; only a symlink can escape, and an
    in-root alias still lists under its own name. ``confined`` locates the entry's path
    for the symlink check.
    """
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return None  # an unresolvable/vanished entry is skipped, never listed
    if S_ISLNK(st.st_mode):
        lexical = resolved_root / confined / name if confined else resolved_root / name
        try:
            if not path_is_relative_to(lexical.resolve(), resolved_root):
                return None  # symlink escape: never listed, never served
            st = os.stat(name, dir_fd=dir_fd)
        except OSError:
            return None
    elif not S_ISDIR(st.st_mode) and not S_ISREG(st.st_mode):
        return None
    if S_ISDIR(st.st_mode):
        row = {"name": name, "path": "", "kind": "dir", "size": None}
    elif S_ISREG(st.st_mode):
        row = {
            "name": name,
            "path": "",
            "kind": "file",
            "size": st.st_size,
            "language": language_for(Path(name)),
        }
    else:  # a followed in-root alias that is neither dir nor file
        return None
    return row


def list_folder(
    config: McpRuntimeConfig, repo_id: str, master: str, rel: str, offset: int
) -> dict[str, Any]:
    """One folder's entries in a stable order, in parts; missing folder -> empty + reason."""
    if offset < 0:
        raise ValueError("notes folder offset must not be negative")
    root = _notes_root(config, repo_id, master)
    confined = confine_rel(root, rel) if rel not in ("", ".") else ""
    if not root.is_dir():
        return {
            "repo": repo_id,
            "master": master,
            "path": "",
            "entries": [],
            "total": 0,
            "offset": 0,
            "limit": _FOLDER_PAGE,
            "remaining": 0,
            "exists": False,
            "reason": "this master has no notes folder",
        }
    rows, exists, reason = _folder_entries(root, confined)
    total = len(rows)
    part = rows[offset : offset + _FOLDER_PAGE]
    return {
        "repo": repo_id,
        "master": master,
        "path": confined,
        "entries": part,
        "total": total,
        "offset": offset,
        "limit": _FOLDER_PAGE,
        "remaining": max(0, total - offset - len(part)),
        "exists": exists,
        "reason": reason,
    }


def _confined_is_file(root: Path, resolved_root: Path, rel: str) -> bool:
    """True when ``rel`` names a regular file inside the notes root.

    A symlink is never followed blindly: it resolves only when its target stays inside
    the root, so an escaping link is not returned as a valid note path while a
    legitimate in-root alias still resolves.
    """
    candidate = root / rel
    try:
        lst = candidate.lstat()
    except OSError:
        return False
    if S_ISLNK(lst.st_mode):
        try:
            if not path_is_relative_to(candidate.resolve(), resolved_root):
                return False
            return S_ISREG(os.stat(candidate).st_mode)
        except (OSError, ValueError):
            return False
    return S_ISREG(lst.st_mode)


def _resolve_one(root: Path, reference: str) -> str | None:
    """The notes-relative path one reference names, or ``None`` (plain text, never a guess).

    A token that names an existing notes file by notes-relative path (with or without
    the ``notes/`` prefix) wins. A bare filename resolves only when it names a file at
    the notes root: deeper names cannot be told apart without a walk of the tree, and
    the page must not fetch one. Anything else resolves to ``None``.
    """
    resolved_root = root.resolve()
    for token in _PATH_TOKEN.findall(reference):
        relative = token[len("notes/") :] if token.startswith("notes/") else token
        if "/" in relative or "\\" in relative:
            try:
                confined = confine_rel(root, relative)
            except (AuthorityError, ValueError):
                continue
            if _confined_is_file(root, resolved_root, confined):
                return confined
        elif _confined_is_file(root, resolved_root, relative):
            return relative
    return None


def resolve_notes_references(
    config: McpRuntimeConfig, repo_id: str, master: str, references: list[str]
) -> dict[str, Any]:
    """Resolve each reference string to a notes path or null, in one request.

    The work grows with the number of references, never with the number of notes.
    """
    root = _notes_root(config, repo_id, master)
    return {
        "repo": repo_id,
        "master": master,
        "resolutions": [
            {"reference": reference, "path": _resolve_one(root, reference)}
            for reference in references
        ],
    }


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
        # It also carries a negative folder offset, which names no path at all.
        return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
    except FileNotFoundError as err:
        return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)


def register_notes_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    """Register the read-only notes routes. Must be called BEFORE the greedy static mount."""

    @app.get("/api/notes/folder", response_model=NotesFolder, responses=SCOPED_READ_RESPONSES)
    def api_notes_folder(repo: str, master: str = "", path: str = "", offset: int = 0) -> Response:
        return _notes_json(
            config, repo, master, lambda: list_folder(config, repo, master, path, offset)
        )

    @app.post(
        "/api/notes/resolve", response_model=NotesResolutions, responses=SCOPED_READ_RESPONSES
    )
    def api_notes_resolve(payload: dict[str, Any]) -> Response:
        references = payload.get("references", [])
        if not isinstance(references, list) or not all(
            isinstance(item, str) for item in references
        ):
            return JSONResponse(
                {"status": "bad-request", "detail": "notes resolve needs a references list"},
                status_code=400,
            )
        repo = payload.get("repo", "")
        master = payload.get("master", "")
        if not isinstance(repo, str) or not isinstance(master, str):
            return JSONResponse(
                {"status": "bad-request", "detail": "notes resolve needs a repo and a master"},
                status_code=400,
            )
        return _notes_json(
            config, repo, master, lambda: resolve_notes_references(config, repo, master, references)
        )

    @app.get("/api/notes/read", response_model=NoteContents, responses=SCOPED_READ_RESPONSES)
    def api_notes_read(repo: str, master: str = "", path: str = "") -> Response:
        return _notes_json(config, repo, master, lambda: read_note(config, repo, master, path))
