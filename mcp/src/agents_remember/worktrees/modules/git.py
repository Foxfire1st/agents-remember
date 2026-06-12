from __future__ import annotations

import subprocess
from pathlib import Path

from agents_remember.kernel import filesystem
from agents_remember.worktrees.worktree_contract import WorktreeContract


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", *args],
        cwd=repo,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )


def require_git(repo: Path, args: list[str]) -> str:
    result = run_git(repo, args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def current_branch(repo: Path) -> str:
    return require_git(repo, ["branch", "--show-current"])


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return require_git(repo, ["rev-parse", ref])


def branch_exists(repo: Path, branch: str) -> bool:
    return run_git(repo, ["rev-parse", "--verify", "--quiet", branch]).returncode == 0


def has_changes(repo: Path) -> bool:
    return bool(require_git(repo, ["status", "--porcelain"]))


def worktree_dirty(repo: Path | None) -> bool:
    return bool(repo and repo.exists() and run_git(repo, ["status", "--porcelain"]).stdout.strip())


def contract_has_worktree_changes(contract: WorktreeContract) -> bool:
    return worktree_dirty(contract.code_worktree) or worktree_dirty(contract.memory_worktree)


def require_clean(repo: Path, label: str) -> None:
    changes = require_git(repo, ["status", "--porcelain"])
    if changes:
        raise RuntimeError(f"{label} is not clean:\n{changes}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def ensure_git_identity(repo: Path) -> None:
    if not run_git(repo, ["config", "--get", "user.email"]).stdout.strip():
        require_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    if not run_git(repo, ["config", "--get", "user.name"]).stdout.strip():
        require_git(repo, ["config", "user.name", "Agents Remember"])


def ensure_worktree(
    repo: Path, worktree: Path, branch: str, source_branch: str, dry_run: bool
) -> str:
    if worktree.exists():
        return "existing"
    if dry_run:
        return "would-create"
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists(repo, branch):
        require_git(repo, ["worktree", "add", str(worktree), branch])
    else:
        require_git(repo, ["worktree", "add", "-b", branch, str(worktree), source_branch])
    return "created"


def commit_if_dirty(repo: Path, message: str) -> str:
    if not has_changes(repo):
        return head_commit(repo)
    require_git(repo, ["add", "-A"])
    require_git(repo, ["commit", "-m", message])
    return head_commit(repo)


def commit_date(repo: Path, commit: str) -> str:
    return require_git(repo, ["show", "-s", "--format=%cI", commit])


def longest_tracked_path_length(repo: Path, ref: str = "HEAD") -> int:
    """Length of the longest tracked relative path at ref (HEAD fallback)."""
    result = run_git(repo, ["ls-tree", "-r", "--name-only", ref])
    if result.returncode != 0 and ref != "HEAD":
        result = run_git(repo, ["ls-tree", "-r", "--name-only", "HEAD"])
    if result.returncode != 0:
        return 0
    return max((len(line.strip()) for line in result.stdout.splitlines() if line.strip()), default=0)


def commit_text_or_none(repo: Path, ref: str, relative_path: str) -> str | None:
    """Text of relative_path at ref in the repo, or None when absent at that ref."""
    result = run_git(repo, ["show", f"{ref}:{relative_path}"])
    return result.stdout if result.returncode == 0 else None


def head_text_or_none(repo: Path, relative_path: str) -> str | None:
    """Text of relative_path at the repo's HEAD, or None when absent at HEAD."""
    return commit_text_or_none(repo, "HEAD", relative_path)


def changed_worktree_paths(repo: Path) -> list[str]:
    tracked = require_git(repo, ["diff", "--name-only", "HEAD", "--"]).splitlines()
    untracked = require_git(repo, ["ls-files", "--others", "--exclude-standard"]).splitlines()
    paths = {
        path.strip().replace("\\", "/")
        for path in [*tracked, *untracked]
        if path.strip() and filesystem.is_file(repo / path.strip())
    }
    return sorted(paths)


def _diff_paths(repo: Path, from_commit: str) -> set[str]:
    lines = require_git(repo, ["diff", "--name-only", f"{from_commit}..HEAD", "--"]).splitlines()
    return {line.strip().replace("\\", "/") for line in lines if line.strip()}


def committed_changed_paths(repo: Path, base_commit: str, verified_commit: str) -> list[str]:
    """Paths changed by commits on the work branch that closeout has not yet verified.

    Tree-diff against the recorded base, intersected with the tree-diff against
    the last verified commit when one exists: content the synced source branch
    already carries and content a previous closeout already verified both drop
    out of the worklist.
    """
    changed = _diff_paths(repo, base_commit)
    if verified_commit and verified_commit != base_commit:
        changed &= _diff_paths(repo, verified_commit)
    return sorted(path for path in changed if filesystem.is_file(repo / path))
