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

L4a adds the doc-reader views: a ``leaf`` change-set resolved by leaf-id from the persisted
enclosure contract (so it works with no live worktree, for a completed leaf) in one of two
modes -- ``committed`` (``base -> code_commit``, the leaf's landed delta) or ``working``
(``worktree-HEAD -> worktree``, the UNCOMMITTED delta only, live worktree required). These ride
the same ``/api/changeset/{task,file-diff}`` routes via a ``leaf`` + ``mode`` selector, returning
the ``task_changeset`` shape. Selection precedence is ``leaf > master > scope``.
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
from agents_remember.worktrees.task_resolver import iter_leaf_enclosure_contracts, slugify
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


def _load_leaf_contract(
    config: McpRuntimeConfig, repo_id: str, master: str, leaf: str
) -> WorktreeContract | None:
    """The leaf enclosure contract for ``leaf`` under ``master``, resolved by leaf-id, or None.

    Matched by ``slugify(leaf) == contract.leaf_id`` over the persisted enclosure contracts, so it
    keeps resolving after the leaf's worktree is cleaned up (the contract outlives the worktree).
    ``master`` scopes the search to one series (matched against the contract's parent/task name) so a
    leaf-id that recurs across series cannot collide. ``leaf`` is confined to a single path segment.
    """
    if not leaf or "/" in leaf or "\\" in leaf or leaf.startswith("."):
        return None
    want = slugify(leaf)
    for path in iter_leaf_enclosure_contracts(config.coordination_root / "tasks"):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue
        if contract.repo_name != repo_id or contract.cleanup == "abandoned":
            continue
        if master not in (contract.parent_task_name, contract.task_name):
            continue
        if contract.leaf_id == want:
            return contract
    return None


def _leaf_range(contract: WorktreeContract, *, memory: bool, mode: str) -> list[dict[str, Any]]:
    """One side's (code or memory) change-set for a leaf view ``mode``.

    ``committed`` = ``base -> code_commit`` (the leaf's LANDED delta); a still-live leaf whose
    ``code_commit`` is not written yet falls back to the worktree's HEAD (committed-so-far, NOT the
    dirty tree). ``working`` = ``worktree-HEAD -> worktree`` (the UNCOMMITTED delta only) and requires
    a live worktree. Two-commit diffs run against the source repo (durable, and it shares the
    worktree's object store) so ``committed`` keeps working after the worktree is cleaned up.
    """
    if memory:
        worktree, repo = contract.memory_worktree, contract.memory_repo_path
        base, committed = contract.memory_base_commit, contract.memory_content_commit
    else:
        worktree, repo = contract.code_worktree, contract.code_repo_path
        base, committed = contract.code_base_commit, contract.code_commit
    live = worktree is not None and worktree.exists()
    if mode == "working":
        # No worktree on this side (e.g. memory disabled) -> nothing to show, like task_changeset's
        # memory degradation. The code-side liveness that makes ``working`` meaningful is enforced once
        # in leaf_changeset, so a missing memory worktree never fails the whole view.
        if not live:
            return []
        return changed_files_with_counts(worktree, head_commit(worktree, "HEAD"), None)
    if not base:
        return []
    head = committed or (head_commit(worktree, "HEAD") if live else "")
    if not head:
        return []
    diff_repo = repo if repo is not None else worktree
    if diff_repo is None:
        return []
    return changed_files_with_counts(diff_repo, base, head)


def _leaf_onboarding_root(contract: WorktreeContract, mode: str) -> Path | None:
    """The onboarding root for sidecar pairing: the live worktree's for ``working``, else the repo's."""
    if mode == "working":
        candidates = [contract.memory_worktree, contract.memory_repo_path]
    else:
        candidates = [contract.memory_repo_path, contract.memory_worktree]
    for cand in candidates:
        if cand is not None and (cand / "onboarding").is_dir():
            return cand / "onboarding"
    return None


def leaf_changeset(
    config: McpRuntimeConfig, repo_id: str, master: str, leaf: str, mode: str
) -> dict[str, Any]:
    """A leaf's change-set in one ``mode`` (``committed`` or ``working``), resolved by leaf-id.

    Returns the same shape as :func:`task_changeset` (so the L4 viewer renders it unchanged): the
    code + memory changed-file lists with counts, code files tagged ``hasSidecar``. ``committed``
    works with no live worktree (a completed leaf); ``working`` requires one (404 otherwise).
    """
    contract = _load_leaf_contract(config, repo_id, master, leaf)
    if contract is None:
        raise FileNotFoundError(f"no leaf contract for {leaf!r}")
    if mode == "working" and not (
        contract.code_worktree is not None and contract.code_worktree.exists()
    ):
        raise FileNotFoundError("no live worktree for the working change-set")
    code = _leaf_range(contract, memory=False, mode=mode)
    memory = _leaf_range(contract, memory=True, mode=mode)
    onboarding_root = _leaf_onboarding_root(contract, mode)
    for entry in code:
        entry["hasSidecar"] = (
            onboarding_root is not None
            and route_sidecar_status(onboarding_root, str(entry["path"])) == "present"
        )
    return {
        "scope": contract.leaf_id,
        "mode": mode,
        "code": code,
        "memory": memory,
        "counters": {"code": _sum(code), "memory": _sum(memory)},
    }


def leaf_file_diff(
    config: McpRuntimeConfig, repo_id: str, master: str, leaf: str, kind: str, rel: str, mode: str
) -> dict[str, Any]:
    """BEFORE + AFTER content for one file in a leaf's ``committed`` or ``working`` change-set.

    ``committed`` = ``base`` vs ``code_commit`` (or the worktree HEAD when live and not yet
    committed). ``working`` = the worktree HEAD vs the (dirty) worktree file. Mirrors
    :func:`file_diff`'s response so the L4 MergeView feeds it directly.
    """
    contract = _load_leaf_contract(config, repo_id, master, leaf)
    if contract is None:
        raise FileNotFoundError(f"no leaf contract for {leaf!r}")
    if kind == "memory":
        worktree, repo = contract.memory_worktree, contract.memory_repo_path
        base, committed = contract.memory_base_commit, contract.memory_content_commit
    else:
        worktree, repo = contract.code_worktree, contract.code_repo_path
        base, committed = contract.code_base_commit, contract.code_commit
    live = worktree is not None and worktree.exists()
    if mode == "working":
        if not live:
            raise FileNotFoundError("no live worktree for the working change-set")
        relp = confine_rel(worktree, rel)
        before = commit_text_or_none(worktree, head_commit(worktree, "HEAD"), relp)
        after_path = worktree / relp
        after = after_path.read_text(errors="replace") if after_path.is_file() else None
    else:
        head = committed or (head_commit(worktree, "HEAD") if live else "")
        diff_repo = repo if repo is not None else worktree
        if diff_repo is None or not base or not head:
            raise FileNotFoundError(rel)
        relp = confine_rel(diff_repo, rel)
        before = commit_text_or_none(diff_repo, base, relp)
        after = commit_text_or_none(diff_repo, head, relp)
    return {
        "scope": contract.leaf_id,
        "kind": "memory" if kind == "memory" else "code",
        "path": relp,
        "language": language_for(Path(relp)),
        "before": {"content": before} if before is not None else None,
        "after": {"content": after} if after is not None else None,
    }


def _leaf_json(produce: Any, master: str, mode: str) -> Response:
    """Validate the leaf selector, then run ``produce`` with the change-set 400/404 status idiom.

    A leaf change-set is qualified by ``master`` (which scopes the contract search) and needs an
    explicit ``mode`` -- so the selector is explicit and validated, not inferred from which optional
    param happens to be present. A leaf without ``master`` or with an unknown ``mode`` is a 400.
    """
    if not master:
        return JSONResponse(
            {"status": "bad-request", "detail": "leaf change-set needs master"}, status_code=400
        )
    if mode not in ("committed", "working"):
        return JSONResponse(
            {"status": "bad-request", "detail": "leaf change-set needs mode=committed|working"},
            status_code=400,
        )
    try:
        return JSONResponse(produce(), status_code=200)
    except AuthorityError as err:
        return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
    except FileNotFoundError as err:
        return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)


def register_changeset_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    """Register the read-only change-set routes. Must be called BEFORE the greedy static mount.

    The change-set is selected by precedence ``leaf > master > scope`` (a ``leaf`` view is the per-leaf
    committed/working delta, ``master`` the series net, ``scope`` one active enclosure). ``leaf`` is
    qualified by ``master`` and requires a ``mode``; both ``leaf`` and ``master`` go through the
    JSONResponse 400/404 idiom rather than ``run_scoped``.
    """

    @app.get("/api/changeset/task")
    def api_changeset_task(
        repo: str, scope: str = "mainline", master: str = "", leaf: str = "", mode: str = ""
    ) -> Response:
        if leaf:
            return _leaf_json(lambda: leaf_changeset(config, repo, master, leaf, mode), master, mode)
        return run_scoped(task_changeset, config, repo, scope)

    @app.get("/api/changeset/file-diff")
    def api_changeset_file_diff(
        repo: str,
        scope: str = "mainline",
        kind: str = "code",
        path: str = "",
        master: str = "",
        leaf: str = "",
        mode: str = "",
    ) -> Response:
        if leaf:
            return _leaf_json(
                lambda: leaf_file_diff(config, repo, master, leaf, kind, path, mode), master, mode
            )
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
