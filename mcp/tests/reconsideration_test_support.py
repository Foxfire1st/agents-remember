"""Existing reconsideration fixture configuration and an authored master."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.tasks import TaskDocument, write_task_doc


def _config(coordination: Path) -> McpRuntimeConfig:
    repo = coordination / "repo"
    repo.mkdir(parents=True)
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.invalid"),
        ("config", "user.name", "t"),
        ("commit", "-q", "--allow-empty", "-m", "base"),
        ("update-ref", "refs/remotes/origin/main", "HEAD"),
        ("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return McpRuntimeConfig(
        config_path=coordination / "settings.json",
        coordination_root=coordination,
        workspace_root=coordination,
        transcript_root=coordination / "logs" / "mcp",
        repositories={"agents-remember": RepositoryScope(repo_id="agents-remember", path=repo)},
    )


def write_master(task_root: Path, leaf: TaskDocument) -> None:
    """Give a positive leaf-publication fixture its actual enclosing master."""
    fields = leaf.model_dump(by_alias=True)
    fields.update(id="260928-MIK", slug="task", kind="master", openQuestions=[], decisions=[])
    write_task_doc(task_root, TaskDocument.model_validate(fields))
