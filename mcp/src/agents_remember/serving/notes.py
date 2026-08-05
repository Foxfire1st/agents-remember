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

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.sidecar_pairing import confine_rel
from agents_remember.mcp.config import McpRuntimeConfig, path_is_relative_to
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


def _walk_notes(
    root: Path, base: Path, depth: int, out: list[dict[str, Any]], state: dict[str, bool]
) -> None:
    """Collect note files under ``base``, files before subfolders, symlink escapes skipped.

    Every child is realpath-checked against ``root`` (the confine_rel posture applied to
    a directory walk): a symlink pointing outside the notes tree is silently skipped —
    listed content must be readable content. Depth beyond the cap flips ``truncated``.
    """
    for child in sorted(base.iterdir(), key=lambda p: (p.is_dir(), p.name.lower())):
        try:
            resolved = child.resolve()
            if not path_is_relative_to(resolved, root):
                continue  # symlink escape: never listed, never served
            is_dir = child.is_dir()
            size = child.stat().st_size if child.is_file() else None
        except OSError:
            continue  # an unresolvable/vanished entry never aborts the listing
        if is_dir:
            if depth >= _MAX_LIST_DEPTH:
                state["truncated"] = True
                continue
            _walk_notes(root, child, depth + 1, out, state)
        elif size is not None:
            out.append(
                {
                    "name": child.name,
                    "path": child.relative_to(root).as_posix(),
                    "size": size,
                    "language": language_for(child),
                }
            )


def list_notes(config: McpRuntimeConfig, repo_id: str, master: str) -> dict[str, Any]:
    """Every note file for one series master, notes-root-relative; missing folder -> []."""
    root = _notes_root(config, repo_id, master)
    notes: list[dict[str, Any]] = []
    state = {"truncated": False}
    if root.is_dir():
        real_root = root.resolve()
        _walk_notes(real_root, real_root, 1, notes, state)
    return {"repo": repo_id, "master": master, "notes": notes, "truncated": state["truncated"]}


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
