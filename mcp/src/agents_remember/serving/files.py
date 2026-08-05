"""Read-only files API: browse code + paired onboarding across repos/worktrees.

Bridges the dashboard serving layer to the
kernel :class:`CoordinationContext` so the File Viewer and Change-Set Viewer
can enumerate repositories and their ``{mainline + active worktree
enclosures}``, list a scoped directory level (code + paired onboarding), read one
file, and resolve the 1:1 code<->onboarding sidecar pairing in both directions.

Security posture (inherited from the other localhost serving routes): GET-only, read-only,
127.0.0.1-bound, no auth/CORS. Every served path is confined to an allow-listed root
(``confine_rel`` realpath check). The repo allow-list is ``config.allowed_repo_ids``;
worktree roots come from on-disk leaf-enclosure contracts.

Missing onboarding is never an error: a repo with no AR memory still browses its code
(onboarding resolves uniformly "missing"), and a code file with no sidecar reports
``status: "missing"`` -- the placeholder the File Viewer renders, not a failure.

Scope resolution (``FileScope`` / ``resolve_scope`` / ``run_scoped``) + the language
map live in the sibling ``serving/scope.py`` so the Change-Set Viewer backend
reuses them; ``FileScope`` and ``_resolve_within`` are re-exported here for callers.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from agents_remember.kernel.coordination_context_resolver import mirror_onboarding_path
from agents_remember.kernel.onboarding_doc import table_metadata
from agents_remember.kernel.sidecar_pairing import (
    confine_rel,
    is_file_sidecar,
    route_sidecar_status,
    sidecar_body,
    source_path_from_sidecar,
)
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.serving.response_contract import (
    SCOPED_READ_RESPONSES,
    DirectoryListing,
    FileContents,
    OnboardingResolution,
    RepoCatalog,
)
from agents_remember.serving.scope import (
    FileScope,
    _iter_active_contracts,
    _resolve_within,
    decode_capped,
    language_for,
    run_scoped,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

# A read is capped so a pathological file never blocks the event loop; mirrors the
# serving.app image-upload cap. The full byte size is still reported (truncated=True).
_MAX_FILE_BYTES = 2 * 1024 * 1024


# --- catalog ---------------------------------------------------------------

# /api/files/repos fired 2x per dashboard boot (React StrictMode) plus on
# every File Viewer refresh, and each call re-walked + re-parsed the whole ``tasks/`` tree
# PER REPO (the measured refresh bottleneck: 8x rglob + 8x contract-parse passes). The
# assembly below walks ONCE; this short-TTL memo (mirroring the ``_RepoSurfaceCacheEntry``
# idiom in observer/projection_store.py) makes repeat/StrictMode calls free. Expiry-based,
# not invalidated: a newly started enclosure appears in the catalog within the TTL, which
# the browse UI tolerates. Served as a shared dict -- callers must not mutate it (the route
# serializes it straight into the JSONResponse).
_REPO_CATALOG_TTL_SECONDS = 15.0


@dataclass(frozen=True)
class _RepoCatalogCacheEntry:
    at: float
    value: dict[str, Any]


_repo_catalog_cache: dict[tuple[str, tuple[str, ...]], _RepoCatalogCacheEntry] = {}


def _repo_catalog_cache_key(config: McpRuntimeConfig) -> tuple[str, tuple[str, ...]]:
    return (config.coordination_root.as_posix(), tuple(sorted(config.allowed_repo_ids)))


def list_repos(
    config: McpRuntimeConfig, *, now: Callable[[], float] = time.monotonic
) -> dict[str, Any]:
    """The catalog: every allow-listed repo with its mainline + active enclosures.

    Serves the TTL memo while fresh (``now`` is the TTL clock, injectable for tests);
    on a miss the catalog is re-assembled with a single tasks-tree walk.
    """
    key = _repo_catalog_cache_key(config)
    entry = _repo_catalog_cache.get(key)
    moment = now()
    if entry is not None and 0 <= moment - entry.at < _REPO_CATALOG_TTL_SECONDS:
        return entry.value
    value = _assemble_repo_catalog(config)
    _repo_catalog_cache[key] = _RepoCatalogCacheEntry(at=moment, value=value)
    return value


def _assemble_repo_catalog(config: McpRuntimeConfig) -> dict[str, Any]:
    """One walk + parse pass over ``tasks/``, contracts bucketed by repo, rows in repo order.

    Within a repo the worktree rows keep ``iter_leaf_enclosure_contracts``' sorted-path
    order (bucketing preserves iteration order), so the response is byte-identical to the
    former per-repo iteration.
    """
    contracts_by_repo: dict[str, list[WorktreeContract]] = {}
    for contract in _iter_active_contracts(config):
        contracts_by_repo.setdefault(contract.repo_name, []).append(contract)
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
            for c in contracts_by_repo.get(repo_id, [])
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
        # Cut at a UTF-8 codepoint boundary so an oversize text file whose multi-byte char straddles
        # the cap returns its first ~2 MiB with truncated=True, never an empty "binary".
        content, truncated = decode_capped(raw, _MAX_FILE_BYTES)
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


def _onboarding_doc_body(scope: FileScope, rel: str) -> str | None:
    """The raw text of a partnerless onboarding doc (overview/entities/index) so the reader can render
    it directly — a route overview has no code partner to pair through. Size-capped + binary-tolerant
    like ``read_file``; a missing/unreadable/binary file yields ``None`` (the reader falls back to a
    placeholder)."""
    if scope.onboarding_root is None:
        return None
    try:
        path = _resolve_within(scope.onboarding_root, rel)
        if not path.is_file():
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        # Same codepoint-boundary cut as read_file: a >2 MiB overview whose
        # multi-byte char straddles the cap renders its first ~2 MiB instead of degrading to a placeholder.
        return decode_capped(raw, _MAX_FILE_BYTES)[0]
    except UnicodeDecodeError:
        return None


def resolve_partner(scope: FileScope, onboarding_rel: str) -> dict[str, Any]:
    """Reverse pairing: a sidecar -> its partner code path, or an overview-without-code node.

    An overview / entities / index doc has no code partner, so it carries its own ``body`` instead —
    the File Viewer renders that markdown directly rather than showing an empty 'no code partner'
    placeholder (a route overview must be readable, not just a tree leaf you cannot open).
    """
    if scope.onboarding_root is None:
        return {"scope": scope.scope_id, "onboardingPath": onboarding_rel, "kind": "none"}
    rel = confine_rel(scope.onboarding_root, onboarding_rel)
    if not is_file_sidecar(rel):
        route = rel.rsplit("/", 1)[0] if "/" in rel else ""
        return {
            "scope": scope.scope_id,
            "onboardingPath": rel,
            "kind": "overview",
            "route": route,
            "body": _onboarding_doc_body(scope, rel),
        }
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

    # ``/api/files/repos`` is the one route in this family with no refusal branch at all: the
    # catalog is assembled from the allow-list itself, so it declares a success shape only.
    @app.get("/api/files/repos", response_model=RepoCatalog)
    def api_files_repos() -> Response:
        return JSONResponse(list_repos(config), status_code=200)

    @app.get("/api/files/list", response_model=DirectoryListing, responses=SCOPED_READ_RESPONSES)
    def api_files_list(repo: str, scope: str = "mainline", path: str = "") -> Response:
        return run_scoped(lambda fs: list_dir(fs, path), config, repo, scope)

    @app.get("/api/files/read", response_model=FileContents, responses=SCOPED_READ_RESPONSES)
    def api_files_read(repo: str, scope: str = "mainline", path: str = "") -> Response:
        return run_scoped(lambda fs: read_file(fs, path), config, repo, scope)

    # Five success shapes, because ``direction`` picks the pairing direction and each direction
    # discriminates further -- the union is the honest declaration, not a convenience.
    @app.get(
        "/api/files/onboarding",
        response_model=OnboardingResolution,
        responses=SCOPED_READ_RESPONSES,
    )
    def api_files_onboarding(
        repo: str, scope: str = "mainline", path: str = "", direction: str = "forward"
    ) -> Response:
        if direction == "reverse":
            return run_scoped(lambda fs: resolve_partner(fs, path), config, repo, scope)
        return run_scoped(lambda fs: resolve_onboarding(fs, path), config, repo, scope)
