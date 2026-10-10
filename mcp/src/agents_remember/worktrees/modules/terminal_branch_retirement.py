"""Terminal branch retirement and its refusal guards (one owner).

Moved whole out of ``cleanup.py`` under the Owner extraction case: local branch
deletion primitives, remote branch deletion, single-branch retirement with its
read-only guard phase, the contract-derived branch authority they check, and the
repository-identity keys that authority is compared by. ``cleanup.py`` keeps the
terminal authority *builder*, worktree/directory/provider reclamation and the
stage orchestration; ``abandon.py`` consumes this module for its branch stage.
A refused retirement changes nothing: every guard runs before the first deletion.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import (
    GIT_REMOTE_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.worktrees.modules.git import (
    branch_commit,
    is_ancestor,
    repository_identity,
)
from agents_remember.worktrees.modules.terminal_inventory import local_branch_presence

_TERMINAL_MUTATION_CAPABILITY = object()


@dataclass(frozen=True)
class _TerminalMutationAuthority:
    """Exact contract-derived terminal targets accepted by the destructive primitives."""

    operation: Literal["worktree_cleanup", "worktree_abandon"]
    worktrees: frozenset[tuple[Path, Path]]
    memory_worktree: Path | None
    branches: frozenset[tuple[Path, str, str]]
    remote_branches: frozenset[tuple[Path, str]]
    _capability: object


def _normalized_branch(branch: str) -> str:
    return branch.strip().removeprefix("refs/heads/")


def _require_terminal_capability(authority: _TerminalMutationAuthority | None) -> None:
    if authority is None or authority._capability is not _TERMINAL_MUTATION_CAPABILITY:
        raise RuntimeError("terminal Git mutation requires contract-derived authority")


def _repository_key(repo: Path) -> Path:
    return _required_repository_identity(repo, "terminal")


def _required_repository_identity(repo: Path, side: str) -> Path:
    identity = repository_identity(repo)
    if identity is None:
        raise RuntimeError(f"cannot resolve {side} terminal repository identity: {repo}")
    return identity


def _require_local_branch_target(
    authority: _TerminalMutationAuthority | None,
    repo: Path,
    branch: str,
    *,
    source_branch: str | None = None,
) -> None:
    _require_terminal_capability(authority)
    assert authority is not None
    repository = _repository_key(repo)
    normalized = _normalized_branch(branch)
    matches = {
        item for item in authority.branches if item[0] == repository and item[1] == normalized
    }
    if not matches:
        raise RuntimeError("local branch deletion target is outside contract authority")
    if source_branch is not None and all(
        item[2] != _normalized_branch(source_branch) for item in matches
    ):
        raise RuntimeError("branch deletion source is outside contract authority")


def _require_remote_branch_target(
    authority: _TerminalMutationAuthority | None,
    repo: Path,
    branch: str,
) -> None:
    _require_terminal_capability(authority)
    assert authority is not None
    target = (_repository_key(repo), _normalized_branch(branch))
    if target not in authority.remote_branches:
        raise RuntimeError("remote branch deletion target is outside cleanup authority")


def delete_branch_if_merged(
    repo: Path,
    branch: str,
    dry_run: bool,
    *,
    authority: _TerminalMutationAuthority | None = None,
) -> dict[str, object]:
    _require_local_branch_target(authority, repo, branch)
    presence = local_branch_presence(repo, branch)
    if presence.state == "error":
        return {"branch": branch, "deleted": False, "reason": presence.reason}
    if presence.state == "absent":
        return {"branch": branch, "deleted": False, "reason": "already-absent"}
    if dry_run:
        return {"branch": branch, "deleted": False, "would_delete": True}
    result = run_git(repo, ["branch", "-d", branch])
    if result.returncode != 0:
        return {
            "branch": branch,
            "deleted": False,
            "reason": result.stderr.strip() or "git branch -d refused the branch",
        }
    return {"branch": branch, "deleted": True}


def _kept_line_branch_proof(
    repo: Path, branch: str, source_branch: str, default_branch: str | None
) -> tuple[str, str] | None:
    """The kept line holding ``branch``: the landing/source line first, then the default.

    The branch check of the cleanup (MIK-R82 rule 5, ASSIGNMENT-SCOPE-BOUNDARIES
    ruling) takes the same proof as the carryover question: a work branch whose
    tip is reachable from one of these two lines holds landed work, also where
    its master's line does not hold the tip. Returns the line and the commit it
    holds the tip in. A probe, never a refusal: unresolvable lines simply do
    not match. The caller fails closed on ``None``.
    """
    candidates = [source_branch] if source_branch else []
    if default_branch and default_branch not in candidates:
        candidates.append(default_branch)
    for line in candidates:
        if is_ancestor(repo, branch, line):
            try:
                return (line, branch_commit(repo, line))
            except RuntimeError:
                continue
    return None


def delete_branch_if_merged_into(  # noqa: PLR0913, PLR0911 -- kept-line proof adds one branch-shape per refusal/delete path
    repo: Path,
    branch: str,
    target_ref: str,
    dry_run: bool,
    *,
    default_branch: str | None = None,
    authority: _TerminalMutationAuthority | None = None,
) -> dict[str, object]:
    _require_local_branch_target(authority, repo, branch, source_branch=target_ref)
    presence = local_branch_presence(repo, branch)
    if presence.state == "error":
        return {"branch": branch, "deleted": False, "reason": presence.reason}
    if presence.state == "absent":
        return {"branch": branch, "deleted": False, "reason": "already-absent"}
    if not is_ancestor(repo, branch, target_ref):
        proof = _kept_line_branch_proof(repo, branch, target_ref, default_branch)
        if proof is None:
            return {
                "branch": branch,
                "deleted": False,
                "reason": "not-merged-into-source",
                "target": target_ref,
            }
        if dry_run:
            return {
                "branch": branch,
                "deleted": False,
                "would_delete": True,
                "target": target_ref,
                "keptLine": proof[0],
                "foundIn": proof[1],
            }
        result = run_git(repo, ["branch", "-D", branch])
        if result.returncode != 0:
            return {
                "branch": branch,
                "deleted": False,
                "reason": result.stderr.strip() or "git branch -D failed",
                "target": target_ref,
                "keptLine": proof[0],
                "foundIn": proof[1],
            }
        return {
            "branch": branch,
            "deleted": True,
            "target": target_ref,
            "keptLine": proof[0],
            "foundIn": proof[1],
        }
    if dry_run:
        return {
            "branch": branch,
            "deleted": False,
            "would_delete": True,
            "target": target_ref,
        }
    result = run_git(repo, ["branch", "-D", branch])
    if result.returncode != 0:
        return {
            "branch": branch,
            "deleted": False,
            "reason": result.stderr.strip() or "git branch -D failed",
            "target": target_ref,
        }
    return {"branch": branch, "deleted": True, "target": target_ref}


def delete_branch_force(
    repo: Path,
    branch: str,
    dry_run: bool,
    *,
    authority: _TerminalMutationAuthority | None = None,
) -> dict[str, object]:
    """Discard a branch even if unmerged (`git branch -D`). Used by abandon force."""
    _require_local_branch_target(authority, repo, branch)
    assert authority is not None
    if authority.operation != "worktree_abandon":
        raise RuntimeError("forced branch deletion requires abandon authority")
    presence = local_branch_presence(repo, branch)
    if presence.state == "error":
        return {"branch": branch, "deleted": False, "reason": presence.reason}
    if presence.state == "absent":
        return {"branch": branch, "deleted": False, "reason": "already-absent"}
    if dry_run:
        return {"branch": branch, "deleted": False, "would_delete": True, "force": True}
    result = run_git(repo, ["branch", "-D", branch])
    if result.returncode != 0:
        return {
            "branch": branch,
            "deleted": False,
            "reason": result.stderr.strip() or "git branch -D failed",
        }
    return {"branch": branch, "deleted": True, "force": True}


def _remote_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a remote-talking git command under the remote bound; ``None`` when it stalled.

    Both callers below run inside an MCP tool call, which the client cannot cancel. They
    used to go through a runner that set no timeout at all, so an unreachable or wedged
    remote held the tool call open forever. A stall now reads exactly like the
    already-handled unreachable-remote case rather than escaping as an exception.
    """
    try:
        return run_git(
            repo,
            args,
            GitRunnerOptions(timeout=GIT_REMOTE_TIMEOUT_SECONDS),
        )
    except subprocess.TimeoutExpired:
        return None


def delete_remote_branch_if_present(
    repo: Path,
    branch: str,
    dry_run: bool,
    *,
    authority: _TerminalMutationAuthority | None = None,
) -> dict[str, object]:
    """Delete ``origin/<branch>`` if it still exists -- a PR branch survives a non-deleting merge (05m)."""
    _require_remote_branch_target(authority, repo, branch)
    if not branch:
        return {"remote_deleted": False, "reason": "empty"}
    probe = _remote_git(repo, ["ls-remote", "--heads", "origin", branch])
    if probe is None:
        return {"remote_deleted": False, "reason": "remote-unreachable"}
    if probe.returncode != 0:
        return {
            "remote_deleted": False,
            "reason": probe.stderr.strip() or "remote-unreachable",
        }
    if not probe.stdout.strip():
        return {"remote_deleted": False, "reason": "already-absent"}
    if dry_run:
        return {"remote_deleted": False, "would_delete": True}
    return _push_branch_deletion(repo, branch, authority=authority)


def _origin_refusal(repo: Path) -> dict[str, object] | None:
    remotes = run_git(repo, ["remote"])
    if remotes.returncode != 0:
        return {
            "remote_deleted": False,
            "reason": remotes.stderr.strip() or "git remote query failed",
        }
    if "origin" not in remotes.stdout.splitlines():
        return {"remote_deleted": False, "reason": "already-absent"}
    return None


def _push_branch_deletion(
    repo: Path,
    branch: str,
    *,
    authority: _TerminalMutationAuthority | None = None,
) -> dict[str, object]:
    _require_remote_branch_target(authority, repo, branch)
    res = _remote_git(repo, ["push", "origin", "--delete", branch])
    if res is None:
        return {"remote_deleted": False, "reason": "remote-unreachable"}
    if res.returncode != 0:
        return {"remote_deleted": False, "reason": res.stderr.strip() or "git push --delete failed"}
    return {"remote_deleted": True}


@dataclass(frozen=True)
class RetiringBranch:
    """One task work branch on its way out.

    The repo it lives in, the branch itself, the source branch it must be proven merged into
    before deletion, and that repo's default branch -- the one to check out when the branch
    being deleted is the one currently checked out. Retirement can never consult any of these
    without the others, and each call site derives the whole set from one contract side.
    """

    repo: Path
    branch: str
    source_branch: str
    default_branch: str


def _retire_work_branch(
    target: RetiringBranch,
    dry_run: bool,
    *,
    remote: bool,
    authority: _TerminalMutationAuthority,
) -> dict[str, object]:
    repo = target.repo
    branch = target.branch
    out: dict[str, object] = {"branch": branch}
    if not branch or branch == target.default_branch:
        out.update({"deleted": False, "reason": "default-or-empty"})
        return out
    if dry_run:
        out.update(
            delete_branch_if_merged_into(
                repo,
                branch,
                target.source_branch,
                True,
                default_branch=target.default_branch,
                authority=authority,
            )
        )
        if remote and (out.get("would_delete") or out.get("reason") == "already-absent"):
            out["remote"] = _retire_remote_branch(
                repo,
                branch,
                True,
                authority=authority,
            )
        return out
    # Guard phase (read-only): prove the local branch may go before anything mutates.
    # The remote push cannot be dry-probed (a dry run does not reproduce an origin-side
    # checkout refusal), so it runs as the first mutation, ahead of the local delete.
    guard = delete_branch_if_merged_into(
        repo,
        branch,
        target.source_branch,
        True,
        default_branch=target.default_branch,
        authority=authority,
    )
    if not guard.get("would_delete") and guard.get("reason") != "already-absent":
        out.update({**guard, "deleted": False})
        return out
    if remote and (guard.get("would_delete") or guard.get("reason") == "already-absent"):
        pushed = _retire_remote_branch(
            repo,
            branch,
            False,
            authority=authority,
        )
        out["remote"] = pushed
        if not pushed.get("remote_deleted") and pushed.get("reason") != "already-absent":
            out.update({"deleted": False, "reason": pushed.get("reason")})
            return out
    if guard.get("reason") == "already-absent":
        out.update({"deleted": False, "reason": "already-absent"})
        return out
    if run_git(repo, ["branch", "--show-current"]).stdout.strip() == branch:
        run_git(repo, ["checkout", target.default_branch])
    out.update(
        delete_branch_if_merged_into(
            repo,
            branch,
            target.source_branch,
            False,
            default_branch=target.default_branch,
            authority=authority,
        )
    )
    return out


def _retire_remote_branch(
    repo: Path,
    branch: str,
    dry_run: bool,
    *,
    authority: _TerminalMutationAuthority,
) -> dict[str, object]:
    origin_refusal = _origin_refusal(repo)
    if origin_refusal is not None:
        return origin_refusal
    return delete_remote_branch_if_present(repo, branch, dry_run, authority=authority)
