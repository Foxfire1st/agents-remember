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
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from agents_remember.controllers._guards import require_repo
from agents_remember.errors import AuthorityError
from agents_remember.kernel.coordination_context.models import MissingMemoryError
from agents_remember.kernel.coordination_context_resolver import resolve_coordination_context
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
from agents_remember.worktrees.task_resolver import iter_leaf_enclosure_contracts
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

# A read is capped so a pathological file never blocks the event loop; mirrors the
# serving.app image-upload cap. The full byte size is still reported (truncated=True).
_MAX_FILE_BYTES = 2 * 1024 * 1024

_LANG_BY_EXT = {
    ".py": "python", ".pyi": "python", ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript", ".cjs": "javascript",
    ".json": "json", ".md": "markdown", ".css": "css", ".html": "html",
    ".sh": "bash", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
    ".sql": "sql", ".txt": "text", ".cfg": "ini", ".ini": "ini",
}


class _UnknownScope(Exception):
    """The requested ``scope`` is not the mainline or a known active enclosure."""


@dataclass(frozen=True)
class FileScope:
    """A resolved browse scope: one ``{repo, mainline|enclosure}`` mapped to roots."""

    scope_id: str
    kind: Literal["mainline", "worktree"]
    repo_id: str
    code_root: Path
    onboarding_root: Path | None
    memory_root: Path | None
    branch: str | None
    contract_path: Path | None


# --- scope + catalog -------------------------------------------------------


def _iter_repo_contracts(config: McpRuntimeConfig, repo_id: str) -> Iterator[WorktreeContract]:
    """Active leaf-enclosure contracts for ``repo_id`` (allow-listed, on-disk, not abandoned)."""
    for path in iter_leaf_enclosure_contracts(config.coordination_root / "tasks"):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue  # one malformed contract never aborts the catalog
        if contract.repo_name != repo_id or contract.cleanup == "abandoned":
            continue
        if not contract.code_worktree.exists():
            continue  # cleaned-up enclosure: the contract lingers, the worktree is gone
        yield contract


def _find_enclosure_contract(
    config: McpRuntimeConfig, repo_id: str, scope_id: str
) -> WorktreeContract | None:
    for contract in _iter_repo_contracts(config, repo_id):
        if contract.worktree_group.name == scope_id:
            return contract
    return None


def resolve_scope(config: McpRuntimeConfig, repo_id: str, scope_id: str) -> FileScope:
    """Resolve ``{repo, mainline|enclosure}`` to a :class:`FileScope`.

    Raises :class:`AuthorityError` for an unknown repo and :class:`_UnknownScope` for
    an unknown enclosure. A repo with no AR memory degrades to a code-only scope
    (``onboarding_root=None``) rather than failing -- code stays browsable.
    """
    repo = require_repo(config, repo_id)
    kind: Literal["mainline", "worktree"] = "mainline"
    branch: str | None = None
    contract_path = repo.contract_path
    code_root_fallback = repo.path
    if scope_id not in ("", "mainline"):
        contract = _find_enclosure_contract(config, repo_id, scope_id)
        if contract is None:
            raise _UnknownScope(scope_id)
        kind, branch = "worktree", contract.code_work_branch
        contract_path, code_root_fallback = contract.contract_path, contract.code_worktree
    try:
        ctx = resolve_coordination_context(
            code_repository_name=repo_id,
            workspace_root=config.workspace_root,
            coordination_root=config.coordination_root,
            code_repository_root=repo.path,
            contract_path=contract_path,
        )
    except MissingMemoryError:
        return FileScope(scope_id or "mainline", kind, repo_id, code_root_fallback,
                         None, None, branch, contract_path)
    return FileScope(scope_id or "mainline", kind, repo_id,
                     ctx.code_worktree or ctx.code_repository_root,
                     ctx.onboarding_root, ctx.memory_root, branch, contract_path)


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
        repos.append({
            "repo": repo_id,
            "mainline": {"scope": "mainline", "label": repo_id, "exists": scope.path.is_dir()},
            "worktrees": worktrees,
        })
    return {"repos": repos}


# --- path confinement + listing --------------------------------------------


def _resolve_within(root: Path, rel: str) -> Path:
    """Confine ``rel`` to ``root`` and return the absolute path (root for ""/".").

    ``rel`` must be repo-relative; an absolute path (leading ``/``) is rejected by
    ``confine_rel``, never silently re-rooted under ``root``.
    """
    if rel in ("", "."):
        return root
    return root / confine_rel(root, rel)


def _list_children(root: Path, base: Path) -> list[dict[str, Any]]:
    """One directory level under ``base``, dirs first then files, each posix-relative to ``root``."""
    children: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        rel = child.relative_to(root.resolve()).as_posix()
        children.append({
            "name": child.name,
            "path": rel,
            "kind": "file" if child.is_file() else "dir",
        })
    return children


def list_dir(scope: FileScope, rel_dir: str) -> dict[str, Any]:
    """Lazily list one directory level: code children (sidecar-annotated) + onboarding children."""
    code_base = _resolve_within(scope.code_root, rel_dir)
    if not code_base.is_dir():
        raise FileNotFoundError(rel_dir)
    code = _list_children(scope.code_root, code_base)
    for entry in code:
        if entry["kind"] == "file":
            entry["language"] = _language_for(Path(entry["name"]))
            entry["hasSidecar"] = (
                scope.onboarding_root is not None
                and route_sidecar_status(scope.onboarding_root, entry["path"]) == "present"
            )
    onboarding: list[dict[str, Any]] = []
    if scope.onboarding_root is not None:
        ob_base = _resolve_within(scope.onboarding_root, rel_dir)
        if ob_base.is_dir():
            onboarding = _list_children(scope.onboarding_root, ob_base)
    return {"scope": scope.scope_id, "dir": rel_dir.strip("/"), "code": code, "onboarding": onboarding}


def _language_for(path: Path) -> str:
    return _LANG_BY_EXT.get(path.suffix.lower(), "text")


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
        language = _language_for(src)
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


def _run(
    op: Callable[[FileScope], dict[str, Any]],
    config: McpRuntimeConfig,
    repo_id: str,
    scope_id: str,
) -> Response:
    """Resolve the scope, run ``op``, and map domain errors to the serving status idiom."""
    try:
        scope = resolve_scope(config, repo_id, scope_id)
    except AuthorityError:
        return JSONResponse({"status": "unknown-repo", "repo": repo_id}, status_code=404)
    except _UnknownScope:
        return JSONResponse({"status": "unknown-scope", "scope": scope_id}, status_code=404)
    try:
        return JSONResponse(op(scope), status_code=200)
    except AuthorityError as err:
        return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
    except FileNotFoundError as err:
        return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)


def register_files_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    """Register the read-only files routes. Must be called BEFORE the greedy static mount."""

    @app.get("/api/files/repos")
    def api_files_repos() -> Response:
        return JSONResponse(list_repos(config), status_code=200)

    @app.get("/api/files/list")
    def api_files_list(repo: str, scope: str = "mainline", path: str = "") -> Response:
        return _run(lambda fs: list_dir(fs, path), config, repo, scope)

    @app.get("/api/files/read")
    def api_files_read(repo: str, scope: str = "mainline", path: str = "") -> Response:
        return _run(lambda fs: read_file(fs, path), config, repo, scope)

    @app.get("/api/files/onboarding")
    def api_files_onboarding(
        repo: str, scope: str = "mainline", path: str = "", direction: str = "forward"
    ) -> Response:
        if direction == "reverse":
            return _run(lambda fs: resolve_partner(fs, path), config, repo, scope)
        return _run(lambda fs: resolve_onboarding(fs, path), config, repo, scope)
