"""Read-only files API: browse code + paired onboarding across repos/worktrees.

L1 of the operations-integration series. Bridges the dashboard serving layer to the
kernel :class:`CoordinationContext` so the File Viewer (L2) and Change-Set Viewer
(L3/L4) can enumerate repositories and their ``{mainline + active worktree
enclosures}``, list a scoped directory level (code + paired onboarding), read one
file, and resolve the 1:1 code<->onboarding sidecar pairing in both directions.

Security posture (inherited from the Task-6 localhost routes): GET-only, read-only,
127.0.0.1-bound, no auth/CORS. Every served path is confined to an allow-listed root
(``confine_rel`` realpath check). The repo allow-list is ``config.allowed_repo_ids``;
worktree roots come from on-disk leaf-enclosure contracts.

Missing onboarding is never an error: a repo with no AR memory still browses its code
(onboarding resolves uniformly "missing"), and a code file with no sidecar reports
``status: "missing"`` -- the placeholder the File Viewer renders, not a failure.

Scope resolution (``FileScope`` / ``resolve_scope`` / ``run_scoped``) + the language
map live in the sibling ``serving/scope.py`` so the Change-Set Viewer backend (L3)
reuses them; ``FileScope`` and ``_resolve_within`` are re-exported here for callers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from agents_remember.kernel.onboarding_doc import table_metadata
from agents_remember.kernel.sidecar_pairing import (
    confine_rel,
    is_file_sidecar,
    route_sidecar_status,
    sidecar_body,
    source_path_from_sidecar,
)
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    mirror_onboarding_path,
)
from agents_remember.serving.scope import (
    FileScope,
    _iter_repo_contracts,
    _resolve_within,
    language_for,
    run_scoped,
)

# A read is capped so a pathological file never blocks the event loop; mirrors the
# serving.app image-upload cap. The full byte size is still reported (truncated=True).
_MAX_FILE_BYTES = 2 * 1024 * 1024


# --- catalog ---------------------------------------------------------------


def list_repos(config: McpRuntimeConfig) -> dict[str, Any]:
    """The catalog: every allow-listed repo with its mainline + active enclosures."""
    repos: list[dict[str, Any]] = []
    for repo_id in config.allowed_repo_ids:
        scope = config.repositories[repo_id]
        worktrees = [
            {
                "scope": c.worktree_group.name,
                "label": c.code_work_branch or c.leaf_id or c.worktree_group.name,
                "branch": c.code_work_branch,
                "leafId": c.leaf_id,
                "taskName": c.task_name,
            }
            for c in _iter_repo_contracts(config, repo_id)
        ]
        repos.append(
            {
                "repo": repo_id,
                "mainline": {"scope": "mainline", "label": repo_id, "exists": scope.path.is_dir()},
                "worktrees": worktrees,
            }
        )
    return {"repos": repos}


# --- listing ---------------------------------------------------------------


def _list_children(root: Path, base: Path) -> list[dict[str, Any]]:
    """One directory level under ``base``, dirs first then files, each posix-relative to ``root``."""
    children: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        rel = child.relative_to(root.resolve()).as_posix()
        children.append(
            {
                "name": child.name,
                "path": rel,
                "kind": "file" if child.is_file() else "dir",
            }
        )
    return children


def list_dir(scope: FileScope, rel_dir: str) -> dict[str, Any]:
    """Lazily list one directory level: code children (sidecar-annotated) + onboarding children."""
    code_base = _resolve_within(scope.code_root, rel_dir)
    if not code_base.is_dir():
        raise FileNotFoundError(rel_dir)
    code = _list_children(scope.code_root, code_base)
    for entry in code:
        if entry["kind"] == "file":
            entry["language"] = language_for(Path(entry["name"]))
            entry["hasSidecar"] = (
                scope.onboarding_root is not None
                and route_sidecar_status(scope.onboarding_root, entry["path"]) == "present"
            )
    onboarding: list[dict[str, Any]] = []
    if scope.onboarding_root is not None:
        ob_base = _resolve_within(scope.onboarding_root, rel_dir)
        if ob_base.is_dir():
            onboarding = _list_children(scope.onboarding_root, ob_base)
    return {
        "scope": scope.scope_id,
        "dir": rel_dir.strip("/"),
        "code": code,
        "onboarding": onboarding,
    }


# --- read + pairing --------------------------------------------------------


def read_file(scope: FileScope, rel: str) -> dict[str, Any]:
    """Serve one code file's content (size-capped, binary-tolerant) + its onboarding metadata."""
    src = _resolve_within(scope.code_root, rel)
    if not src.is_file():
        raise FileNotFoundError(rel)
    raw = src.read_bytes()
    truncated = len(raw) > _MAX_FILE_BYTES
    try:
        content = raw[:_MAX_FILE_BYTES].decode("utf-8")
        language = language_for(src)
    except UnicodeDecodeError:
        content, language = "", "binary"
    rel_posix = src.relative_to(scope.code_root.resolve()).as_posix()
    return {
        "scope": scope.scope_id,
        "path": rel_posix,
        "language": language,
        "size": len(raw),
        "truncated": truncated,
        "content": content,
        "onboarding": _onboarding_meta(scope, rel_posix),
    }


def _onboarding_meta(scope: FileScope, rel: str) -> dict[str, Any]:
    """The drift-bearing onboarding status for a code path; ``missing`` is normal, never an error."""
    if scope.onboarding_root is None:
        return {"status": "missing"}
    sidecar_path = mirror_onboarding_path(scope.onboarding_root, rel)
    if route_sidecar_status(scope.onboarding_root, rel) != "present" or not sidecar_path.is_file():
        return {"status": "missing"}
    meta = table_metadata(sidecar_path)
    return {
        "status": "found",
        "path": sidecar_path.relative_to(scope.onboarding_root.resolve()).as_posix(),
        "lastVerifiedCommitHash": meta.get("lastVerifiedCommitHash"),
        "lastVerifiedCommitDate": meta.get("lastVerifiedCommitDate"),
    }


def resolve_onboarding(scope: FileScope, code_rel: str) -> dict[str, Any]:
    """Forward pairing: a code path -> its 1:1 sidecar status + body (``missing`` when none)."""
    rel = confine_rel(scope.code_root, code_rel)
    meta = _onboarding_meta(scope, rel)
    body = None
    if meta["status"] == "found" and scope.onboarding_root is not None:
        body = sidecar_body(scope.onboarding_root, rel)
    return {"scope": scope.scope_id, "codePath": rel, "body": body, **meta}


def resolve_partner(scope: FileScope, onboarding_rel: str) -> dict[str, Any]:
    """Reverse pairing: a sidecar -> its partner code path, or an overview-without-code node."""
    if scope.onboarding_root is None:
        return {"scope": scope.scope_id, "onboardingPath": onboarding_rel, "kind": "none"}
    rel = confine_rel(scope.onboarding_root, onboarding_rel)
    if not is_file_sidecar(rel):
        route = rel.rsplit("/", 1)[0] if "/" in rel else ""
        return {"scope": scope.scope_id, "onboardingPath": rel, "kind": "overview", "route": route}
    code_path = source_path_from_sidecar(rel)
    return {
        "scope": scope.scope_id,
        "onboardingPath": rel,
        "kind": "sidecar",
        "codePath": code_path,
        "exists": (scope.code_root / code_path).is_file(),
    }


# --- route registration ----------------------------------------------------


def register_files_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    """Register the read-only files routes. Must be called BEFORE the greedy static mount."""

    @app.get("/api/files/repos")
    def api_files_repos() -> Response:
        return JSONResponse(list_repos(config), status_code=200)

    @app.get("/api/files/list")
    def api_files_list(repo: str, scope: str = "mainline", path: str = "") -> Response:
        return run_scoped(lambda fs: list_dir(fs, path), config, repo, scope)

    @app.get("/api/files/read")
    def api_files_read(repo: str, scope: str = "mainline", path: str = "") -> Response:
        return run_scoped(lambda fs: read_file(fs, path), config, repo, scope)

    @app.get("/api/files/onboarding")
    def api_files_onboarding(
        repo: str, scope: str = "mainline", path: str = "", direction: str = "forward"
    ) -> Response:
        if direction == "reverse":
            return run_scoped(lambda fs: resolve_partner(fs, path), config, repo, scope)
        return run_scoped(lambda fs: resolve_onboarding(fs, path), config, repo, scope)
