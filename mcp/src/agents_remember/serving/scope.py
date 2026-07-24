"""Shared browse-scope resolution + error mapping for the read-only serving APIs.

Extracted from ``serving/files.py`` so the Change-Set Viewer backend
(``serving/changeset.py``) reuses one resolver + one error map instead of a parallel
copy. A ``{repo, mainline|enclosure}`` resolves to a :class:`FileScope` of roots;
``run_scoped`` runs a domain function over that scope and maps domain errors to the
serving status idiom (404 unknown-repo/unknown-scope/not-found, 400 bad-path).

Security posture (inherited from the other localhost serving routes): every served path is
confined to an allow-listed root (``confine_rel`` realpath check); the repo allow-list
is ``config.allowed_repo_ids`` and worktree roots come from on-disk leaf-enclosure
contracts.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi.responses import JSONResponse, Response

from agents_remember.controllers._guards import require_repo
from agents_remember.errors import AuthorityError
from agents_remember.kernel.coordination_context.models import MissingMemoryError
from agents_remember.kernel.coordination_context_resolver import resolve_coordination_context
from agents_remember.kernel.sidecar_pairing import confine_rel
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.worktrees.task_resolver import iter_leaf_enclosure_contracts
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

_LANG_BY_EXT = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".json": "json",
    ".md": "markdown",
    ".css": "css",
    ".html": "html",
    ".sh": "bash",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sql": "sql",
    ".txt": "text",
    ".cfg": "ini",
    ".ini": "ini",
}


def language_for(path: Path) -> str:
    """The dashboard language id for a path's extension (``text`` fallback)."""
    return _LANG_BY_EXT.get(path.suffix.lower(), "text")


def decode_capped(raw: bytes, cap: int) -> tuple[str, bool]:
    """Decode the first ``cap`` bytes of ``raw`` as UTF-8, cut on a codepoint boundary.

    The read APIs slice ``raw[:cap]`` before ``decode("utf-8")``. When an
    oversize file's multi-byte character straddles the cap the slice ends mid-character, ``decode``
    raises ``UnicodeDecodeError``, and the caller's binary fallback misreports the whole (perfectly
    textual) file as ``language: "binary"`` with empty content. Backward-scan off any partial trailing
    character before decoding: UTF-8 continuation bytes are ``0b10xxxxxx`` and a character is at most
    4 bytes, so walking ``end`` back over up to 3 continuation bytes lands it on a lead byte -> a clean
    boundary. Returns ``(text, truncated)``; genuinely non-UTF-8 content still raises
    ``UnicodeDecodeError`` (the caller keeps classifying that as binary)."""
    if len(raw) <= cap:
        return raw.decode("utf-8"), False
    end = cap
    limit = max(0, cap - 3)
    # raw[end] is the FIRST excluded byte; while it is a continuation byte the cut splits a character,
    # so step back to the character's lead byte (bounded to <=3 steps = UTF-8's max continuation run).
    while end > limit and (raw[end] & 0xC0) == 0x80:
        end -= 1
    return raw[:end].decode("utf-8"), True


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


def _iter_repo_contracts(config: McpRuntimeConfig, repo_id: str) -> Iterator[WorktreeContract]:
    """Active leaf-enclosure contracts for ``repo_id`` (allow-listed, on-disk, not abandoned)."""
    for contract in _iter_active_contracts(config):
        if contract.repo_name == repo_id:
            yield contract


def _iter_active_contracts(config: McpRuntimeConfig) -> Iterator[WorktreeContract]:
    """Every active leaf-enclosure contract in ONE tasks-tree walk + parse pass.

    The files catalog (``serving/files.py list_repos``) needs the active
    population for EVERY allow-listed repo; iterating per repo cost N full ``tasks/`` rglob
    walks + N parses of the same contracts (the measured /api/files/repos bottleneck).
    Callers needing one repo filter the bucketed result; per-repo filtering semantics
    (skip malformed / abandoned / worktree-gone) are unchanged.
    """
    for path in iter_leaf_enclosure_contracts(config.coordination_root / "tasks"):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue  # one malformed contract never aborts the catalog
        if contract.cleanup == "abandoned":
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
        return FileScope(
            scope_id or "mainline",
            kind,
            repo_id,
            code_root_fallback,
            None,
            None,
            branch,
            contract_path,
        )
    return FileScope(
        scope_id or "mainline",
        kind,
        repo_id,
        ctx.code_worktree or ctx.code_repository_root,
        ctx.onboarding_root,
        ctx.memory_root,
        branch,
        contract_path,
    )


def _resolve_within(root: Path, rel: str) -> Path:
    """Confine ``rel`` to ``root`` and return the absolute path (root for ""/".").

    ``rel`` must be repo-relative; an absolute path (leading ``/``) is rejected by
    ``confine_rel``, never silently re-rooted under ``root``.
    """
    if rel in ("", "."):
        return root
    return root / confine_rel(root, rel)


def run_scoped(
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
    except (AuthorityError, ValueError) as err:
        # ValueError: Path.resolve() rejects malformed input (e.g. an embedded null
        # byte) before confinement even runs — same wire answer as a confinement breach.
        return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
    except FileNotFoundError as err:
        return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)
