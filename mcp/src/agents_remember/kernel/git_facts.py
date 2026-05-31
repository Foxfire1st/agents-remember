"""Read-only Git facts for context packet assembly."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel.git_command import run_git


@dataclass(frozen=True)
class GitFacts:
    repo_id: str
    root: Path
    branch: str
    head: str
    dirty: bool
    state: str
    error: str = ""


def read_git_facts(repo_id: str, repo_root: Path) -> GitFacts:
    root = repo_root.resolve()
    try:
        return _read_git_facts(repo_id, root)
    except (OSError, subprocess.SubprocessError) as error:
        return GitFacts(repo_id, root, "", "", False, "unavailable", f"git unavailable: {error}")


def _read_git_facts(repo_id: str, root: Path) -> GitFacts:
    if not root.exists():
        return GitFacts(
            repo_id,
            root,
            "",
            "",
            False,
            "unavailable",
            f"repo path does not exist: {root}",
        )
    if not root.is_dir():
        return GitFacts(
            repo_id,
            root,
            "",
            "",
            False,
            "unavailable",
            f"repo path is not a directory: {root}",
        )

    inside = run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return GitFacts(
            repo_id,
            root,
            "",
            "",
            False,
            "unavailable",
            _git_error(inside, "not a git work tree"),
        )

    head = _git_stdout(root, ["rev-parse", "HEAD"])
    if not head:
        return GitFacts(repo_id, root, "", "", False, "unavailable", "git HEAD is unavailable")

    branch = _git_stdout(root, ["branch", "--show-current"])
    dirty = bool(_git_stdout(root, ["status", "--porcelain"]))
    state = "available" if branch else "detached"
    return GitFacts(repo_id, root, branch, head, dirty, state)


def git_facts_to_packet(facts: GitFacts) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "id": facts.repo_id,
        "root": facts.root.as_posix(),
        "branch": facts.branch,
        "head": facts.head,
        "dirty": facts.dirty,
        "state": facts.state,
    }
    if facts.error:
        packet["error"] = facts.error
    return packet


def _git_stdout(repo_root: Path, args: list[str]) -> str:
    result = run_git(repo_root, args)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_error(result: subprocess.CompletedProcess[str], default: str) -> str:
    return (result.stderr or result.stdout or default).strip()
