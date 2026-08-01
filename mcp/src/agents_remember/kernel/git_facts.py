"""Read-only Git facts for context packet assembly."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from agents_remember.kernel.git_command import (
    GIT_LOCAL_TIMEOUT_SECONDS,
    GIT_METADATA_TIMEOUT_SECONDS,
    run_git,
)

# The repo-availability vocabulary, declared once, here. This module is its only writer, and
# `models.context_packet.RepoSummary` imports this alias instead of retyping it, so there is no
# second copy for the two to drift apart in. A hand-written copy at the wire boundary is the
# defect this replaces: the set difference is invisible until a real repo produces the new
# member, and by then it is a pydantic ValidationError raised inside a tool handler with no
# `except` for one -- the failure 165 of the 213 contracts on disk were reproducing.
RepoState = Literal["available", "detached", "unavailable"]

# The runtime half of the alias, derived from it rather than retyped beside it, so a member can
# only ever be added in one place.
VALID_REPO_STATES: frozenset[RepoState] = frozenset(get_args(RepoState))


@dataclass(frozen=True)
class GitFacts:
    repo_id: str
    root: Path
    branch: str
    head: str
    dirty: bool
    state: RepoState
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

    # The timeout class is picked per command, not per module. The three ref reads below
    # are constant time -- ~1.8ms each on this repository -- and sit on `resolve_context`,
    # which runs on essentially every tool call. On the runner's local default all four
    # probes here could hold one MCP call for twenty minutes over a stalled mount or a
    # held index lock, with no cancellation path for the client; the metadata band exists
    # to make that a failure instead of a wait. `kernel/coordination_context/cross_repo.py`
    # already runs two of these three at the metadata bound, and one command must not mean
    # two different things inside `kernel/`.
    inside = run_git(
        root, ["rev-parse", "--is-inside-work-tree"], timeout=GIT_METADATA_TIMEOUT_SECONDS
    )
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

    head = _git_stdout(root, ["rev-parse", "HEAD"], timeout=GIT_METADATA_TIMEOUT_SECONDS)
    if not head:
        return GitFacts(repo_id, root, "", "", False, "unavailable", "git HEAD is unavailable")

    branch = _git_stdout(root, ["branch", "--show-current"], timeout=GIT_METADATA_TIMEOUT_SECONDS)
    # `status --porcelain` is the one probe here that is not constant time -- it stats the
    # whole work tree -- so it keeps the local bound and is named rather than defaulted.
    dirty = bool(_git_stdout(root, ["status", "--porcelain"], timeout=GIT_LOCAL_TIMEOUT_SECONDS))
    state: RepoState = "available" if branch else "detached"
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


def _git_stdout(repo_root: Path, args: list[str], *, timeout: float) -> str:
    """Trimmed stdout, empty on failure. ``timeout`` is required -- see :func:`_read_git_facts`."""

    result = run_git(repo_root, args, timeout=timeout)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_error(result: subprocess.CompletedProcess[str], default: str) -> str:
    return (result.stderr or result.stdout or default).strip()
