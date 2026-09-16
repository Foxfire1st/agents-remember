"""Memory repository initialization helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)

DEFAULT_BRANCH_CONFIG_KEY = "agents-remember.defaultBranch"


def _normalize_initial_branch(value: str) -> str:
    """One explicit local branch name, or a refusal naming the value.

    The value becomes a Git ref and a branch-name argument, so it is narrowed here rather
    than handed to ``git``: a leading ``refs/heads/`` is accepted because that is how the
    recorded authority is spelled elsewhere, and anything that is not a plain local branch
    name is refused.
    """

    normalized = value.strip().removeprefix("refs/heads/")
    if not normalized or normalized == "HEAD" or normalized.startswith("refs/"):
        raise ValueError(f"initial_branch must name one local branch; got {value!r}")
    if normalized.startswith("-") or any(character.isspace() for character in normalized):
        raise ValueError(f"initial_branch must name one local branch; got {value!r}")
    return normalized


def _code_repository_branch(code_repository_root: Path) -> str | None:
    """The code repository's current checked-out branch, or ``None`` for "no branch".

    ``None`` covers three real situations the caller must not guess past: a detached
    checkout, a configured code-repository path that does not exist yet, and a path that
    is not a Git checkout at all. In every one of them the honest answer is that there is
    no branch to inherit, and the caller then requires an explicit answer.
    """

    if not (code_repository_root / ".git").exists():
        return None
    symbolic = run_git(code_repository_root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    branch = symbolic.stdout.strip()
    return branch if symbolic.returncode == 0 and branch else None


def _resolved_initial_branch(
    code_repository_root: Path, requested: str | None
) -> tuple[str | None, str]:
    """The branch the memory repository is founded on, with where the answer came from."""

    if requested is not None:
        return _normalize_initial_branch(requested), "explicit"
    inherited = _code_repository_branch(code_repository_root)
    if inherited is None:
        return None, "unresolved"
    return inherited, "code-repository-current-branch"


def _create_missing_dirs(paths: list[Path], *, dry_run: bool) -> list[str]:
    created: list[str] = []
    for path in paths:
        if path.exists():
            continue
        created.append(path.as_posix())
        if not dry_run:
            path.mkdir(parents=True, exist_ok=True)
    return created


def _create_missing_files(files: dict[Path, str], *, dry_run: bool) -> list[str]:
    created: list[str] = []
    for path, content in files.items():
        if path.exists():
            continue
        created.append(path.as_posix())
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return created


def _record_default_branch(memory_root: Path, branch: str) -> Any:
    return run_git(memory_root, ["config", "--local", DEFAULT_BRANCH_CONFIG_KEY, branch])


def _existing_unborn_refusal(git: dict[str, Any], branch: str) -> dict[str, Any]:
    git.update(
        {
            "repairAttempted": True,
            "returncode": 1,
            "stderr": (
                "existing unborn memory repository does not own the expected memory_init "
                f"branch refs/heads/{branch}"
            ),
        }
    )
    return git


def _repair_unborn_memory_repository(
    memory_root: Path, symbolic: Any, local_heads: Any, branch: str
) -> dict[str, Any]:
    """Make an existing empty repository own ``branch`` exactly, or report why not.

    Two things have to hold before this is the repository ``memory_init`` minted: HEAD is
    symbolic on exactly the expected branch, and no local branch exists yet. A repository
    that already carries refs is refused rather than repaired — it is not in the unborn
    state this path is for.
    """

    git: dict[str, Any] = {"requested": True, "ran": False}
    if local_heads.returncode != 0 or local_heads.stdout.strip():
        return _existing_unborn_refusal(git, branch)
    ref = f"refs/heads/{branch}"
    if symbolic.returncode != 0:
        return _existing_unborn_refusal(git, branch)
    if symbolic.stdout.strip() != ref:
        # The repository exists but is empty on another branch. Moving HEAD is mechanical
        # and loses no content here (there is none), and it is the difference between a
        # fresh `git init` and a half-created one. Only the unborn state is entered.
        moved = run_git(memory_root, ["symbolic-ref", "HEAD", ref])
        if moved.returncode != 0:
            return _existing_unborn_refusal(git, branch)
        git["headRepointed"] = f"{symbolic.stdout.strip()} -> {ref}"
    authority = _record_default_branch(memory_root, branch)
    git.update(
        {
            "repairAttempted": True,
            "returncode": authority.returncode,
            "stdout": authority.stdout,
            "stderr": authority.stderr,
        }
    )
    return git


def _git_init_result(
    memory_root: Path,
    *,
    initial_branch: str | None,
    dry_run: bool,
    initialize_git: bool,
) -> dict[str, Any]:
    git: dict[str, Any] = {"requested": initialize_git, "ran": False}
    if initial_branch is None:
        git.update(
            {
                "returncode": 1,
                "stderr": (
                    "memory_init needs the initial branch of the memory repository: pass "
                    "initial_branch, or call it from a code repository whose checked-out "
                    "branch can supply the default"
                ),
            }
        )
        return git
    git["initialBranch"] = initial_branch
    if not initialize_git:
        return git
    if dry_run:
        git["planned"] = True
        return git
    if (memory_root / ".git").exists():
        head = run_git(memory_root, ["rev-parse", "--verify", "HEAD"])
        if head.returncode == 0:
            return git
        symbolic = run_git(memory_root, ["symbolic-ref", "--quiet", "HEAD"])
        local_heads = run_git(
            memory_root,
            ["for-each-ref", "--format=%(refname)", "refs/heads"],
        )
        return _repair_unborn_memory_repository(memory_root, symbolic, local_heads, initial_branch)
    # Through the one runner so GIT_DIR is stripped: `git init` honours GIT_DIR over
    # its cwd, so an inherited one would initialise the repository somewhere else
    # entirely and then report success for a memory root that never became a repo.
    result = run_git(memory_root, ["init", "-b", initial_branch])
    if result.returncode == 0:
        authority = _record_default_branch(memory_root, initial_branch)
        if authority.returncode != 0:
            result = authority
    git.update(
        {
            "ran": True,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    )
    return git


def initialize_memory(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = False,
    initialize_git: bool = True,
    initial_branch: str | None = None,
) -> dict[str, Any]:
    """Scaffold a repository's memory root, founded on one named branch.

    ``initial_branch`` names the code branch the memory is the foundation *of* — the
    branch memory content will be verified against. Omitted, it defaults to the code
    repository's currently checked-out branch; when neither is available the call refuses
    instead of inventing ``main``, because the branch it records is the authority baseline
    adoption and every later worktree entry point compare against.
    """

    repo = config.repositories.get(repo_id)
    if repo is None:
        allowed = ", ".join(config.allowed_repo_ids) or "<none>"
        raise ValueError(f"repo_id {repo_id!r} is not allowed by MCP settings; allowed: {allowed}")
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have an external memory root")

    # The coordinator scaffold is the thing a memory root lives inside. Git would happily
    # create the whole path -- `git init` makes missing parents -- so an absent coordination
    # root would silently produce a memory repository somewhere the developer never meant,
    # under a path nothing else in the product reads. Refuse it here and name the skill that
    # owns creating it. An existing coordination root that is a file, not a directory, is the
    # same condition seen from the other side.
    if not config.coordination_root.is_dir():
        raise ValueError(
            f"coordination root {config.coordination_root.as_posix()} does not exist or is not "
            f"a directory, so repo_id {repo_id!r} has no workspace to initialize memory in; "
            "run runtime_install to create the coordinator scaffold first (the "
            "c-13-install-and-onboard skill owns that sequence)"
        )

    resolved_branch, branch_source = _resolved_initial_branch(repo.path, initial_branch)

    memory_root = repo.memory_root
    paths = [
        memory_root,
        memory_root / "system",
        memory_root / "onboarding",
        memory_root / "docs",
    ]
    files = {
        memory_root / "system" / "settings.md": f"# {repo_id} Memory Settings\n",
        memory_root / "system" / "tools.md": f"# {repo_id} Memory Tools\n",
        memory_root / "system" / "sources.md": f"# {repo_id} Sources\n",
    }

    created_dirs = _create_missing_dirs([memory_root], dry_run=dry_run)
    git = _git_init_result(
        memory_root,
        initial_branch=resolved_branch,
        dry_run=dry_run,
        initialize_git=initialize_git,
    )
    if git.get("returncode", 0) != 0:
        return {
            "ok": False,
            "operation": "memory_init",
            "repoId": repo_id,
            "memoryRoot": memory_root.as_posix(),
            "initialBranch": resolved_branch,
            "initialBranchSource": branch_source,
            "createdDirs": created_dirs,
            "createdFiles": [],
            "git": git,
        }

    created_dirs.extend(_create_missing_dirs(paths[1:], dry_run=dry_run))
    created_files = _create_missing_files(files, dry_run=dry_run)

    return {
        "ok": True,
        "operation": "memory_init",
        "repoId": repo_id,
        "dryRun": dry_run,
        "memoryRoot": memory_root.as_posix(),
        "initialBranch": resolved_branch,
        "initialBranchSource": branch_source,
        "createdDirs": created_dirs,
        "createdFiles": created_files,
        "git": git,
    }
