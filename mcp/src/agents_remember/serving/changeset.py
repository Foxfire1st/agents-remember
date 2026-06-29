"""Read-only change-set API: a task's (and the master's accumulated) code + memory diff.

L3 of the operations-integration series. Mirrors the L1 files API pattern
(``serving/files.py``): GET-only, read-only, 127.0.0.1-bound, reusing
``serving/scope.py`` for scope resolution + the 404/400 error map and
``kernel/sidecar_pairing`` for sidecar pairing on the changed code set.

The change-set is per **enclosure**: it loads the leaf contract for the base/verified
commits and diffs the task's full ``base -> current`` range -- ``base_commit -> the
worktree`` for an active enclosure, ``base_commit -> code_commit`` for a completed leaf
whose worktree is gone (the commits live on the source repo after integration).
``file-diff`` emits BEFORE + AFTER content (not unified-diff text) so the L4 pane feeds
CodeMirror MergeView ``a``/``b`` directly. ``master`` is the NET ``base -> series-tip``
diff (one coherent range, inspectable per file), with a per-leaf counter breakdown
alongside. Mainline has no base, so a mainline scope is a 404.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from agents_remember.errors import AuthorityError
from agents_remember.kernel.sidecar_pairing import confine_rel, route_sidecar_status
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.serving.scope import FileScope, language_for, run_scoped
from agents_remember.worktrees.modules.git import (
    changed_files_with_counts,
    commit_text_or_none,
    head_commit,
)
from agents_remember.worktrees.task_resolver import iter_leaf_enclosure_contracts
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


def _require_contract(scope: FileScope) -> WorktreeContract:
    """Load the leaf contract for an enclosure scope; mainline / unreadable -> 404 not-found."""
    if scope.kind != "worktree" or scope.contract_path is None:
        raise FileNotFoundError(f"no change-set for scope {scope.scope_id!r}")
    try:
        return load_contract(scope.contract_path)
    except (ContractError, OSError) as err:
        raise FileNotFoundError(str(scope.contract_path)) from err


def _sum(files: list[dict[str, Any]]) -> dict[str, int]:
    """Counters for one changed set: file count + summed insertions/deletions (binary -> 0)."""
    return {
        "files": len(files),
        "insertions": sum(int(f["insertions"] or 0) for f in files),
        "deletions": sum(int(f["deletions"] or 0) for f in files),
    }


def task_changeset(scope: FileScope) -> dict[str, Any]:
    """The change-set for one active enclosure: base -> worktree code + memory, with counts."""
    contract = _require_contract(scope)
    code = changed_files_with_counts(scope.code_root, contract.code_base_commit, None)
    for entry in code:
        entry["hasSidecar"] = (
            scope.onboarding_root is not None
            and route_sidecar_status(scope.onboarding_root, str(entry["path"])) == "present"
        )
    memory: list[dict[str, Any]] = []
    if contract.memory_worktree is not None and contract.memory_base_commit:
        memory = changed_files_with_counts(
            contract.memory_worktree, contract.memory_base_commit, None
        )
    return {
        "scope": scope.scope_id,
        "code": code,
        "memory": memory,
        "counters": {"code": _sum(code), "memory": _sum(memory)},
    }


def file_diff(scope: FileScope, kind: str, rel: str) -> dict[str, Any]:
    """BEFORE (base commit) + AFTER (current) content + language for one changed file.

    ``kind`` selects the tree: ``"memory"`` diffs the memory worktree, anything else the
    code worktree. ``before`` is ``None`` for an added file, ``after`` is ``None`` for a
    deleted one -- the L4 MergeView renders those as a pure add/delete.
    """
    contract = _require_contract(scope)
    if kind == "memory":
        root, base = contract.memory_worktree, contract.memory_base_commit
    else:
        root, base = scope.code_root, contract.code_base_commit
    if root is None:
        raise FileNotFoundError(rel)
    relp = confine_rel(root, rel)
    before = commit_text_or_none(root, base, relp) if base else None
    after_path = root / relp
    after = after_path.read_text(errors="replace") if after_path.is_file() else None
    return {
        "scope": scope.scope_id,
        "kind": "memory" if kind == "memory" else "code",
        "path": relp,
        "language": language_for(Path(relp)),
        "before": {"content": before} if before is not None else None,
        "after": {"content": after} if after is not None else None,
    }


def _leaf_counts(contract: WorktreeContract, *, memory: bool) -> list[dict[str, Any]]:
    """One leaf's change-set: base -> worktree when live, else base -> the integrated commit."""
    if memory:
        live, repo = contract.memory_worktree, contract.memory_repo_path
        base, head = contract.memory_base_commit, contract.memory_content_commit
    else:
        live, repo = contract.code_worktree, contract.code_repo_path
        base, head = contract.code_base_commit, contract.code_commit
    if not base:
        return []
    if live is not None and live.exists():
        return changed_files_with_counts(live, base, None)
    if repo is not None and head:
        return changed_files_with_counts(repo, base, head)
    return []


def _load_master_contract(
    config: McpRuntimeConfig, repo_id: str, master: str
) -> WorktreeContract | None:
    """The series (root) contract at ``tasks/<repo>/<master>/series-contract.md``, or None.

    ``master`` is confined to a single path segment (no separators / ``..``) so a wire value
    cannot escape the tasks tree.
    """
    if not master or "/" in master or "\\" in master or master.startswith("."):
        return None
    path = config.coordination_root / "tasks" / repo_id / master / "series-contract.md"
    try:
        return load_contract(path)
    except (ContractError, OSError):
        return None


def _net_changed(repo_path: Path | None, base: str, source_branch: str) -> list[dict[str, Any]]:
    """Net change-set ``base -> the live source-branch tip`` (``[]`` when unresolvable)."""
    if repo_path is None or not base or not source_branch:
        return []
    try:
        tip = head_commit(repo_path, source_branch)
        return changed_files_with_counts(repo_path, base, tip)
    except (RuntimeError, OSError):
        return []


def _master_leaf_summaries(
    config: McpRuntimeConfig, repo_id: str, master: str
) -> list[dict[str, Any]]:
    """Per-leaf counter breakdown (each leaf vs its own base) shown alongside the net diff."""
    leaves: list[dict[str, Any]] = []
    for path in iter_leaf_enclosure_contracts(config.coordination_root / "tasks"):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue
        if contract.repo_name != repo_id or contract.cleanup == "abandoned":
            continue
        if master not in (contract.parent_task_name, contract.task_name):
            continue
        try:
            code = _leaf_counts(contract, memory=False)
            memory = _leaf_counts(contract, memory=True)
        except (RuntimeError, OSError):
            continue  # a leaf whose commits/worktree are unreadable never aborts the breakdown
        leaves.append(
            {"leafId": contract.leaf_id, "counters": {"code": _sum(code), "memory": _sum(memory)}}
        )
    return leaves


def master_changeset(config: McpRuntimeConfig, repo_id: str, master: str) -> dict[str, Any]:
    """The series NET change-set: ``git diff <master-base> <series-tip>`` for code + memory.

    Unlike a sum of the leaf change-sets (which double-counts a file two leaves touched and has
    no single base to diff against), the net range from the master's recorded base to the live
    source-branch tip is one coherent diff -- so every changed file is inspectable via
    :func:`master_file_diff`. ``leaves`` keeps the per-leaf counter breakdown alongside it.
    Reflects the COMMITTED/landed series state; an in-flight leaf not yet integrated is excluded.
    """
    contract = _load_master_contract(config, repo_id, master)
    code: list[dict[str, Any]] = []
    memory: list[dict[str, Any]] = []
    onboarding_root: Path | None = None
    if contract is not None:
        code = _net_changed(
            contract.code_repo_path, contract.code_base_commit, contract.code_source_branch
        )
        memory = _net_changed(
            contract.memory_repo_path, contract.memory_base_commit, contract.memory_source_branch
        )
        if contract.memory_repo_path is not None:
            candidate = contract.memory_repo_path / "onboarding"
            if candidate.is_dir():
                onboarding_root = candidate
    for entry in code:
        entry["hasSidecar"] = (
            onboarding_root is not None
            and route_sidecar_status(onboarding_root, str(entry["path"])) == "present"
        )
    return {
        "master": master,
        "leaves": _master_leaf_summaries(config, repo_id, master),
        "code": code,
        "memory": memory,
        "counters": {"code": _sum(code), "memory": _sum(memory)},
    }


def master_file_diff(
    config: McpRuntimeConfig, repo_id: str, master: str, kind: str, rel: str
) -> dict[str, Any]:
    """BEFORE (master base) + AFTER (series tip) content for one file in the net series diff."""
    contract = _load_master_contract(config, repo_id, master)
    if contract is None:
        raise FileNotFoundError(f"no series contract for {master!r}")
    if kind == "memory":
        repo_path = contract.memory_repo_path
        base, branch = contract.memory_base_commit, contract.memory_source_branch
    else:
        repo_path = contract.code_repo_path
        base, branch = contract.code_base_commit, contract.code_source_branch
    if repo_path is None or not base or not branch:
        raise FileNotFoundError(rel)
    relp = confine_rel(repo_path, rel)
    try:
        tip = head_commit(repo_path, branch)
    except (RuntimeError, OSError) as err:
        raise FileNotFoundError(str(branch)) from err
    before = commit_text_or_none(repo_path, base, relp)
    after = commit_text_or_none(repo_path, tip, relp)
    return {
        "scope": master,
        "kind": "memory" if kind == "memory" else "code",
        "path": relp,
        "language": language_for(Path(relp)),
        "before": {"content": before} if before is not None else None,
        "after": {"content": after} if after is not None else None,
    }


def register_changeset_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    """Register the read-only change-set routes. Must be called BEFORE the greedy static mount."""

    @app.get("/api/changeset/task")
    def api_changeset_task(repo: str, scope: str = "mainline") -> Response:
        return run_scoped(task_changeset, config, repo, scope)

    @app.get("/api/changeset/file-diff")
    def api_changeset_file_diff(
        repo: str, scope: str = "mainline", kind: str = "code", path: str = "", master: str = ""
    ) -> Response:
        if master:
            # Series net diff (master base -> source-branch tip); no enclosure scope, so it does
            # not go through run_scoped -- map its domain errors to the same status idiom.
            try:
                return JSONResponse(
                    master_file_diff(config, repo, master, kind, path), status_code=200
                )
            except AuthorityError as err:
                return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
            except FileNotFoundError as err:
                return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)
        return run_scoped(lambda fs: file_diff(fs, kind, path), config, repo, scope)

    @app.get("/api/changeset/master")
    def api_changeset_master(repo: str, master: str) -> Response:
        return JSONResponse(master_changeset(config, repo, master), status_code=200)
