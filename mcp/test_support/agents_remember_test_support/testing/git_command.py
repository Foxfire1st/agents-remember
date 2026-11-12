"""Shared real-Git fixture primitives for runner and object-memo tests."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import run_git


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "git-command@example.invalid"],
        ["config", "user.name", "Git Command Tests"],
    ):
        assert run_git(repo, args).returncode == 0, args


def _commit(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    assert run_git(repo, ["add", "-A"]).returncode == 0
    assert run_git(repo, ["commit", "-m", f"add {name}"]).returncode == 0
    return run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()
