"""Fail-closed preflight and result validation for terminal worktree operations."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import GIT_REMOTE_TIMEOUT_SECONDS, run_git
from agents_remember.worktrees.modules.git import local_branch_ref, repository_identity
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

TerminalMode = Literal["cleanup", "abandon"]


@dataclass(frozen=True)
class BranchTarget:
    key: str
    repo: Path
    branch: str
    source: str
    optional: bool = False
    remote: bool = False


@dataclass(frozen=True)
class TerminalPreflight:
    worktrees: dict[str, dict[str, object]]
    branches: dict[str, dict[str, object]]
    blockers: tuple[dict[str, object], ...]


def require_series_children_retired(series: WorktreeContract) -> None:
    """Refuse atomic-series retirement while any child still owns live resources."""

    if series.kind != "series":
        raise RuntimeError("atomic child terminal census requires a series contract")
    enclosure_root = series.task_root / "enclosures"
    if series.worktree_group.resolve() != enclosure_root.resolve():
        raise RuntimeError("atomic series enclosure root does not match its task authority")
    if not enclosure_root.exists():
        return
    blockers: list[str] = []
    for enclosure in sorted(enclosure_root.iterdir()):
        if enclosure.name == "reports" and not series_reports_is_child_enclosure(series):
            continue
        blocker = _child_terminal_blocker(series, enclosure)
        if blocker is not None:
            blockers.append(blocker)
    if blockers:
        raise RuntimeError(
            "atomic series terminal mutation requires every child leaf to finish its own "
            "cleanup or abandon before series refs can retire: " + "; ".join(blockers)
        )


def series_reports_is_child_enclosure(series: WorktreeContract) -> bool:
    """Distinguish a leaf named ``reports`` from the series-owned reports directory."""

    return (series.task_root / "enclosures" / "reports" / "series-contract.md").is_file()


def _child_terminal_blocker(series: WorktreeContract, enclosure: Path) -> str | None:
    path = enclosure / "series-contract.md"
    if not enclosure.is_dir() or not path.is_file():
        return f"invalid child enclosure {enclosure}"
    try:
        child = load_contract(path)
    except (ContractError, OSError) as exc:
        return f"invalid child contract {path}: {exc}"
    if not _child_contract_matches_series(series, child, path):
        return f"foreign child contract {path}"
    if child.cleanup not in {"completed", "abandoned"}:
        return f"child {child.leaf_id!r} cleanup is {child.cleanup!r}"
    live = _live_child_resources(child)
    if live:
        return f"child {child.leaf_id!r} retains {', '.join(live)}"
    return None


def _child_contract_matches_series(
    series: WorktreeContract,
    child: WorktreeContract,
    path: Path,
) -> bool:
    return (
        child.kind == "leaf"
        and child.coordination_root.resolve() == series.coordination_root.resolve()
        and child.repo_name == series.repo_name
        and child.task_root.resolve() == series.task_root.resolve()
        and child.contract_path.resolve() == path.resolve()
        and child.parent_contract_path is not None
        and child.parent_contract_path.resolve() == series.contract_path.resolve()
        and repository_identity(child.code_repo_path) == repository_identity(series.code_repo_path)
        and child.code_source_branch == series.code_work_branch
        and child.memory_mode == series.memory_mode
        and _child_memory_edge_matches_series(series, child)
    )


def _child_memory_edge_matches_series(
    series: WorktreeContract,
    child: WorktreeContract,
) -> bool:
    if series.memory_mode != "external":
        return True
    return (
        series.memory_repo_path is not None
        and child.memory_repo_path is not None
        and repository_identity(child.memory_repo_path)
        == repository_identity(series.memory_repo_path)
        and child.memory_source_branch == series.memory_work_branch
    )


def _live_child_resources(child: WorktreeContract) -> list[str]:
    resources: list[str] = []
    if child.code_worktree.exists():
        resources.append("code worktree")
    _append_live_branch(resources, child.code_repo_path, child.code_work_branch, "code branch")
    if child.memory_mode == "external":
        if child.memory_repo_path is None or child.memory_worktree is None:
            resources.append("invalid external-memory edge")
            return resources
        if child.memory_worktree.exists():
            resources.append("memory worktree")
        _append_live_branch(
            resources,
            child.memory_repo_path,
            child.memory_work_branch,
            "memory branch",
        )
    return resources


def _append_live_branch(resources: list[str], repo: Path, branch: str, label: str) -> None:
    result = run_git(repo, ["show-ref", "--verify", "--quiet", local_branch_ref(branch)])
    if result.returncode == 0:
        resources.append(label)
        return
    if result.returncode != 1 or result.stderr.strip():
        raise RuntimeError(
            f"atomic child terminal census cannot resolve {label} {branch!r}: "
            f"{result.stderr.strip() or 'git ref query failed'}"
        )


def terminal_preflight(
    contract: WorktreeContract,
    *,
    mode: TerminalMode,
    force: bool,
) -> TerminalPreflight:
    worktrees, worktree_blockers = _worktree_preflight(contract, force=force)
    branches: dict[str, dict[str, object]] = {}
    branch_blockers: list[dict[str, object]] = []
    allowed_worktrees = {
        path.resolve()
        for path in (contract.code_worktree, contract.memory_worktree)
        if path is not None
    }
    for target in _branch_targets(contract, mode=mode):
        result = _branch_preflight(
            target,
            mode=mode,
            force=force,
            allowed_worktrees=allowed_worktrees,
        )
        branches[target.key] = result
        if _blocked(result, pending_key="would_delete"):
            branch_blockers.append(
                {
                    "branch": target.key,
                    "name": target.branch,
                    "reason": result.get("reason"),
                    **(
                        {"unmergedCommits": result["unmergedCommits"]}
                        if result.get("unmergedCommits")
                        else {}
                    ),
                }
            )
    return TerminalPreflight(
        worktrees,
        branches,
        tuple([*worktree_blockers, *branch_blockers]),
    )


def terminal_result_blockers(
    *,
    providers: Mapping[str, object],
    worktrees: dict[str, dict[str, object]],
    branches: dict[str, dict[str, object]],
    directories: dict[str, dict[str, object]],
    drift_snapshots: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    blockers.extend(_provider_blockers(providers))
    blockers.extend(_result_blockers("worktree", worktrees, done_key="removed"))
    blockers.extend(_result_blockers("branch", branches, done_key="deleted", nested="remote"))
    blockers.extend(
        _result_blockers(
            "directory",
            directories,
            done_key="removed",
            benign={
                "repo_worktree_group": frozenset({"not-empty"}),
                "reports": frozenset({"child-enclosure"}),
            },
        )
    )
    if drift_snapshots is not None:
        blockers.extend(_result_blockers("driftSnapshot", drift_snapshots, done_key="removed"))
    return blockers


def _worktree_preflight(
    contract: WorktreeContract,
    *,
    force: bool,
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    if contract.kind == "series":
        return {}, []
    candidates = {"code": contract.code_worktree}
    if contract.memory_mode == "external" and contract.memory_worktree is not None:
        candidates["memory"] = contract.memory_worktree
    previews: dict[str, dict[str, object]] = {}
    blockers: list[dict[str, object]] = []
    for key, worktree in candidates.items():
        if not worktree.exists():
            previews[key] = {
                "path": worktree.as_posix(),
                "removed": False,
                "reason": "already-absent",
            }
            continue
        status = run_git(
            worktree,
            ["status", "--porcelain=v1", "--untracked-files=all"],
        )
        if status.returncode != 0:
            reason = status.stderr.strip() or "git status failed"
            previews[key] = {"path": worktree.as_posix(), "reason": reason}
            blockers.append({"worktree": key, "reason": reason})
            continue
        if status.stdout and not force:
            previews[key] = {"path": worktree.as_posix(), "reason": "dirty"}
            blockers.append({"worktree": key, "reason": "dirty"})
            continue
        previews[key] = {
            "path": worktree.as_posix(),
            "removed": False,
            "would_remove": True,
            **({"force": True} if force else {}),
        }
    return previews, blockers


def _branch_targets(
    contract: WorktreeContract,
    *,
    mode: TerminalMode,
) -> tuple[BranchTarget, ...]:
    targets = [
        BranchTarget(
            "code",
            contract.code_repo_path,
            contract.code_work_branch,
            contract.code_source_branch,
            remote=mode == "cleanup",
        )
    ]
    if contract.memory_mode == "external" and contract.memory_repo_path is not None:
        targets.append(
            BranchTarget(
                "memory",
                contract.memory_repo_path,
                contract.memory_work_branch,
                contract.memory_source_branch,
            )
        )
    return tuple(targets)


def _branch_preflight(
    target: BranchTarget,
    *,
    mode: TerminalMode,
    force: bool,
    allowed_worktrees: set[Path],
) -> dict[str, object]:
    base: dict[str, object] = {"branch": target.branch}
    refusal = _branch_identity_refusal(target, base, allowed_worktrees)
    if refusal is not None:
        if mode == "cleanup" and target.remote and refusal.get("reason") == "already-absent":
            return _local_absent_remote_preflight(target, refusal)
        return refusal
    if mode == "cleanup":
        return _cleanup_branch_preflight(target, base)
    return _abandon_branch_preflight(target, base, force=force)


def _local_absent_remote_preflight(
    target: BranchTarget,
    local: dict[str, object],
) -> dict[str, object]:
    remote = _remote_branch_preflight(target.repo, target.branch)
    result = {**local, "remote": remote}
    if remote.get("would_delete"):
        result["would_delete"] = True
    elif _blocked(remote, pending_key="would_delete"):
        result["reason"] = f"remote: {remote.get('reason')}"
    return result


def _branch_identity_refusal(
    target: BranchTarget,
    base: dict[str, object],
    allowed_worktrees: set[Path],
) -> dict[str, object] | None:
    refs = _branch_refs_refusal(target, base)
    if refs is not None:
        return refs
    return _branch_checkout_refusal(target, base, allowed_worktrees)


def _branch_refs_refusal(
    target: BranchTarget,
    base: dict[str, object],
) -> dict[str, object] | None:
    if not target.branch or not target.source:
        return {**base, "reason": "empty-branch-or-source"}
    source = _branch_presence(target.repo, target.source)
    if source != "present":
        return {
            **base,
            "source": target.source,
            "reason": "source-branch-missing" if source == "absent" else source,
        }
    branch = _branch_presence(target.repo, target.branch)
    if branch == "absent":
        return {**base, "deleted": False, "reason": "already-absent"}
    if branch != "present":
        return {**base, "reason": branch}
    if target.branch == target.source:
        return {**base, "reason": "work-branch-is-source-branch"}
    return None


def _branch_checkout_refusal(
    target: BranchTarget,
    base: dict[str, object],
    allowed_worktrees: set[Path],
) -> dict[str, object] | None:
    checked_out = _checked_out_paths(target.repo, target.branch)
    if isinstance(checked_out, str):
        return {**base, "reason": checked_out}
    foreign = sorted(path.as_posix() for path in checked_out - allowed_worktrees)
    if foreign:
        return {**base, "reason": "branch-checked-out-elsewhere", "worktrees": foreign}
    return None


def _cleanup_branch_preflight(
    target: BranchTarget,
    base: dict[str, object],
) -> dict[str, object]:
    merge = run_git(
        target.repo,
        [
            "merge-base",
            "--is-ancestor",
            f"refs/heads/{target.branch}",
            f"refs/heads/{target.source}",
        ],
    )
    if merge.returncode == 1:
        return {**base, "reason": "not-merged-into-source", "target": target.source}
    if merge.returncode != 0:
        return {
            **base,
            "reason": merge.stderr.strip() or "git ancestry query failed",
            "target": target.source,
        }
    result = {**base, "deleted": False, "would_delete": True, "target": target.source}
    if target.remote:
        result["remote"] = _remote_branch_preflight(target.repo, target.branch)
        remote = result["remote"]
        if isinstance(remote, dict) and _blocked(remote, pending_key="would_delete"):
            result["reason"] = f"remote: {remote.get('reason')}"
            result.pop("would_delete", None)
    return result


def _abandon_branch_preflight(
    target: BranchTarget,
    base: dict[str, object],
    *,
    force: bool,
) -> dict[str, object]:
    if not force:
        log = run_git(
            target.repo,
            [
                "log",
                "--oneline",
                f"refs/heads/{target.source}..refs/heads/{target.branch}",
            ],
        )
        if log.returncode != 0:
            return {**base, "reason": log.stderr.strip() or "git log query failed"}
        unmerged = [line for line in log.stdout.splitlines() if line.strip()]
        if unmerged:
            return {
                **base,
                "reason": "unmerged",
                "unmergedCommits": unmerged,
                "hint": "re-run abandon with force=true to discard these commits",
            }
    return {
        **base,
        "deleted": False,
        "would_delete": True,
        **({"force": True} if force else {}),
    }


def _branch_presence(repo: Path, branch: str) -> str:
    result = run_git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
    if result.returncode == 0:
        return "present"
    if result.returncode == 1 and not result.stderr.strip():
        return "absent"
    return result.stderr.strip() or "git ref query failed"


def _checked_out_paths(repo: Path, branch: str) -> set[Path] | str:
    result = run_git(repo, ["worktree", "list", "--porcelain", "-z"])
    if result.returncode != 0:
        return result.stderr.strip() or "git worktree query failed"
    current_path: Path | None = None
    found: set[Path] = set()
    expected = f"refs/heads/{branch}"
    for field in result.stdout.split("\0"):
        if field.startswith("worktree "):
            current_path = Path(field.removeprefix("worktree ")).resolve()
        elif field == f"branch {expected}" and current_path is not None:
            found.add(current_path)
    return found


def _remote_branch_preflight(repo: Path, branch: str) -> dict[str, object]:
    remotes = run_git(repo, ["remote"])
    if remotes.returncode != 0:
        return {
            "remote_deleted": False,
            "reason": remotes.stderr.strip() or "git remote query failed",
        }
    if "origin" not in remotes.stdout.splitlines():
        return {"remote_deleted": False, "reason": "already-absent"}
    try:
        result = run_git(
            repo,
            ["ls-remote", "--heads", "origin", branch],
            timeout=GIT_REMOTE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"remote_deleted": False, "reason": "remote-unreachable"}
    if result.returncode != 0:
        return {
            "remote_deleted": False,
            "reason": result.stderr.strip() or "remote-unreachable",
        }
    if not result.stdout.strip():
        return {"remote_deleted": False, "reason": "already-absent"}
    return {"remote_deleted": False, "would_delete": True}


def _provider_blockers(providers: Mapping[str, object]) -> list[dict[str, object]]:
    if providers.get("state") == "skipped":
        return []
    blockers: list[dict[str, object]] = []
    for collection in ("containers", "networks"):
        values = providers.get(collection, [])
        if not isinstance(values, list):
            blockers.append({"provider": collection, "reason": "invalid-result"})
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict) or _blocked(item, pending_key="would_remove"):
                reason = item.get("reason") if isinstance(item, dict) else "invalid-result"
                blockers.append({"provider": f"{collection}[{index}]", "reason": reason})
    runtime = providers.get("providerRuntime")
    if runtime is not None and (
        not isinstance(runtime, dict) or _blocked(runtime, pending_key="would_remove")
    ):
        reason = runtime.get("reason") if isinstance(runtime, dict) else "invalid-result"
        blockers.append({"provider": "providerRuntime", "reason": reason})
    return blockers


def _result_blockers(
    kind: str,
    values: dict[str, dict[str, object]],
    *,
    done_key: str,
    nested: str | None = None,
    benign: Mapping[str, frozenset[str]] | None = None,
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    for key, item in values.items():
        allowed = benign.get(key, frozenset()) if benign is not None else frozenset()
        if _blocked(item, done_key=done_key) and item.get("reason") not in allowed:
            blockers.append({kind: key, "reason": item.get("reason")})
        child = item.get(nested) if nested is not None else None
        if isinstance(child, dict) and _blocked(child, done_key="remote_deleted"):
            blockers.append({kind: f"{key}.{nested}", "reason": child.get("reason")})
    return blockers


def _blocked(
    item: dict[str, object],
    *,
    done_key: str = "deleted",
    pending_key: str = "would_delete",
) -> bool:
    if item.get(done_key) or item.get(pending_key):
        return False
    return item.get("reason") != "already-absent"
