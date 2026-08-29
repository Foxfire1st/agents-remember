from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from agents_remember.kernel import filesystem
from agents_remember.kernel.git_command import run_git, run_git_with_index
from agents_remember.worktrees.worktree_contract import WorktreeContract

# This module used to define its own `run_git` -- the kernel's function with the
# environment guard, the timeout and the explicit encoding all dropped -- and every
# destructive worktree operation (commit, merge --ff-only, reset --hard, rebase,
# branch -f, branch -D, worktree remove --force, push origin --delete) ran through
# it. With GIT_DIR exported those landed in whatever repository GIT_DIR named. The
# helpers below now call the one guarded runner; nothing else about them changed.


def _transport_safe_git_diagnostic(text: str) -> str:
    """Render surrogateescaped Git bytes without leaking invalid Unicode to MCP."""

    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def require_git(repo: Path, args: list[str]) -> str:
    result = run_git(repo, args)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(_transport_safe_git_diagnostic(detail))
    return result.stdout.strip()


def worktree_candidate_tree(repo: Path, index_path: Path) -> str:
    """Hash the full add-all candidate through one invocation-owned Git index."""
    # The caller selects a scratch namespace; every observation owns its physical index.
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{index_path.name}-", dir=index_path.parent) as temporary:
        isolated_index = Path(temporary) / "index"
        for action, args in (
            ("seed candidate index", ["read-tree", "HEAD"]),
            ("materialize candidate tree", ["add", "-A"]),
        ):
            result = run_git_with_index(repo, args, isolated_index)
            if result.returncode != 0:
                raise RuntimeError(
                    f"could not {action}: "
                    f"{_transport_safe_git_diagnostic(result.stderr.strip() or result.stdout.strip())}"
                )
        result = run_git_with_index(repo, ["write-tree"], isolated_index)
        if result.returncode != 0:
            raise RuntimeError(
                "could not resolve candidate tree: "
                f"{_transport_safe_git_diagnostic(result.stderr.strip() or result.stdout.strip())}"
            )
        return result.stdout.strip()


def current_branch(repo: Path) -> str:
    return require_git(repo, ["branch", "--show-current"])


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return require_git(repo, ["rev-parse", ref])


def local_branch_ref(branch: str) -> str:
    """Return the explicit local-ref spelling for a branch authority cell."""

    normalized = branch.removeprefix("refs/heads/")
    if not normalized or normalized == "HEAD" or normalized.startswith("refs/"):
        raise RuntimeError(f"invalid local branch authority: {branch!r}")
    return f"refs/heads/{normalized}"


def branch_commit(repo: Path, branch: str) -> str:
    """Resolve one exact local branch, never a same-named tag or remote ref."""

    return require_git(repo, ["rev-parse", "--verify", f"{local_branch_ref(branch)}^{{commit}}"])


def branch_exists(repo: Path, branch: str) -> bool:
    return (
        run_git(repo, ["show-ref", "--verify", "--quiet", local_branch_ref(branch)]).returncode == 0
    )


def repository_identity(repo: Path | None) -> Path | None:
    """Resolve Git's shared object-store identity for a checkout or linked worktree."""

    if repo is None or not repo.is_dir():
        return None
    result = run_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    common_dir = result.stdout.strip()
    if result.returncode != 0 or not common_dir:
        return None
    return Path(common_dir).resolve()


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
    contract: WorktreeContract,
    *,
    side: Literal["code", "memory"],
    dry_run: bool,
) -> str:
    """Create one exact contract-owned ordinary worktree after live authority validation."""

    from agents_remember.worktrees.integration.integration_branch_authority import (  # noqa: PLC0415
        require_ordinary_worktree,
    )

    if contract.kind != "leaf":
        raise RuntimeError("worktree creation requires an ordinary leaf contract")
    require_ordinary_worktree(contract, operation="worktree_start")
    if side == "code":
        repo = contract.code_repo_path
        worktree = contract.code_worktree
        branch = contract.code_work_branch
        source_branch = contract.code_source_branch
    elif contract.memory_mode == "external" and contract.memory_repo_path is not None:
        if contract.memory_worktree is None:
            raise RuntimeError("external-memory worktree authority is missing its target path")
        repo = contract.memory_repo_path
        worktree = contract.memory_worktree
        branch = contract.memory_work_branch
        source_branch = contract.memory_source_branch
    else:
        raise RuntimeError("memory worktree creation requires an external-memory contract")
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


def run_pre_commit_hook_if_configured(repo: Path) -> bool:
    """Run the checkout's configured pre-commit hook, or report that none exists."""
    hook_path = Path(require_git(repo, ["rev-parse", "--git-path", "hooks/pre-commit"]))
    if not hook_path.is_absolute():
        hook_path = repo / hook_path
    if not hook_path.is_file():
        return False
    require_git(repo, ["hook", "run", "pre-commit"])
    return True


def commit_verified_staged(repo: Path, message: str) -> str:
    """Commit exactly the staged tree a preceding strict gate certified.

    The caller has already staged and verified the index.  In particular, this helper
    must neither restage the working tree nor rerun a hook after the gate's pytest-final
    subprocess.
    """
    if run_git(repo, ["diff", "--cached", "--quiet"]).returncode == 0:
        return head_commit(repo)
    require_git(repo, ["commit", "--no-verify", "-m", message])
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
    return max(
        (len(line.strip()) for line in result.stdout.splitlines() if line.strip()), default=0
    )


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


def _rename_aware_path(field: str) -> str:
    """The post-rename path from a numstat path field (handles ``a => b`` and ``p/{a => b}/q``)."""
    field = field.strip()
    if " => " not in field:
        return field.replace("\\", "/")
    if "{" in field and "}" in field:
        prefix, rest = field.split("{", 1)
        inner, suffix = rest.split("}", 1)
        new = inner.split(" => ", 1)[1]
        return f"{prefix}{new}{suffix}".replace("\\", "/")
    return field.split(" => ", 1)[1].replace("\\", "/")


def changed_files_with_counts(
    repo: Path, base: str, head: str | None = None
) -> list[dict[str, Any]]:
    """Per-file change-set ``base``->``head`` (``head=None`` -> the working tree).

    Each entry is ``{path, insertions, deletions, status}``. Unlike
    :func:`changed_worktree_paths` / :func:`committed_changed_paths` this KEEPS
    deletions (status ``D``) and reports per-file insertion/deletion counts (``None``
    for binary files, whose numstat shows ``-``); in working-tree mode untracked files
    are reported as additions (status ``A``). ``status`` is the git letter
    (``A``/``M``/``D``/``R``/``C``). Paths are posix, sorted.
    """
    rng = [base] if head is None else [base, head]
    status: dict[str, str] = {}
    for line in require_git(repo, ["diff", "--name-status", "--find-renames", *rng]).splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        status[fields[-1].replace("\\", "/")] = fields[0][:1]
    out: list[dict[str, Any]] = []
    for line in require_git(repo, ["diff", "--numstat", "--find-renames", *rng]).splitlines():
        if not line.strip():
            continue
        ins, dels, raw_path = line.split("\t", 2)
        path = _rename_aware_path(raw_path)
        out.append(
            {
                "path": path,
                "insertions": None if ins == "-" else int(ins),
                "deletions": None if dels == "-" else int(dels),
                "status": status.get(path, "M"),
            }
        )
    if head is None:
        for raw in require_git(repo, ["ls-files", "--others", "--exclude-standard"]).splitlines():
            rel = raw.strip().replace("\\", "/")
            if rel and filesystem.is_file(repo / rel):
                line_count = len((repo / rel).read_text(errors="replace").splitlines())
                out.append({"path": rel, "insertions": line_count, "deletions": 0, "status": "A"})
    return sorted(out, key=lambda entry: str(entry["path"]))
