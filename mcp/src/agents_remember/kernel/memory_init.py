"""Memory repository initialization helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agents_remember.mcp.config import McpRuntimeConfig


def initialize_memory(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = True,
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

    created_dirs: list[str] = []
    created_files: list[str] = []
    for path in paths:
        if path.exists():
            continue
        created_dirs.append(path.as_posix())
        if not dry_run:
            path.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        if path.exists():
            continue
        created_files.append(path.as_posix())
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    git: dict[str, Any] = {"requested": initialize_git, "ran": False}
    if initialize_git:
        if dry_run:
            git["planned"] = True
        elif not (memory_root / ".git").exists():
            result = subprocess.run(
                ["git", "init"],
                cwd=memory_root,
                text=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            git.update(
                {
                    "ran": True,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "operation": "memory_init",
                    "repoId": repo_id,
                    "memoryRoot": memory_root.as_posix(),
                    "createdDirs": created_dirs,
                    "createdFiles": created_files,
                    "git": git,
                }

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
