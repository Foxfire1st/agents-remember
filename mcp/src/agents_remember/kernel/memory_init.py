"""Memory repository initialization helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)


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


def _git_init_result(memory_root: Path, *, dry_run: bool, initialize_git: bool) -> dict[str, Any]:
    git: dict[str, Any] = {"requested": initialize_git, "ran": False}
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
        if (
            symbolic.returncode != 0
            or symbolic.stdout.strip() != "refs/heads/main"
            or local_heads.returncode != 0
            or local_heads.stdout.strip()
        ):
            git.update(
                {
                    "repairAttempted": True,
                    "returncode": 1,
                    "stderr": (
                        "existing unborn memory repository does not own the exact "
                        "memory_init branch refs/heads/main"
                    ),
                }
            )
            return git
        authority = run_git(
            memory_root,
            ["config", "--local", "agents-remember.defaultBranch", "main"],
        )
        git.update(
            {
                "repairAttempted": True,
                "returncode": authority.returncode,
                "stdout": authority.stdout,
                "stderr": authority.stderr,
            }
        )
        return git
    # Through the one runner so GIT_DIR is stripped: `git init` honours GIT_DIR over
    # its cwd, so an inherited one would initialise the repository somewhere else
    # entirely and then report success for a memory root that never became a repo.
    result = run_git(memory_root, ["init", "-b", "main"])
    if result.returncode == 0:
        authority = run_git(
            memory_root,
            ["config", "--local", "agents-remember.defaultBranch", "main"],
        )
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
) -> dict[str, Any]:
    repo = config.repositories.get(repo_id)
    if repo is None:
        allowed = ", ".join(config.allowed_repo_ids) or "<none>"
        raise ValueError(f"repo_id {repo_id!r} is not allowed by MCP settings; allowed: {allowed}")
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have an external memory root")

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
    git = _git_init_result(memory_root, dry_run=dry_run, initialize_git=initialize_git)
    if git.get("returncode", 0) != 0:
        return {
            "ok": False,
            "operation": "memory_init",
            "repoId": repo_id,
            "memoryRoot": memory_root.as_posix(),
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
        "createdDirs": created_dirs,
        "createdFiles": created_files,
        "git": git,
    }
