"""Read-only branch freshness facts: is a local branch current with its upstream?

Companion to ``git_facts``: the only mutation is an optional ``git fetch`` of
remote-tracking refs (the working tree is never touched), bounded by a timeout.
Any git, network, or filesystem problem degrades to a ``BranchFreshness`` with
``state="unknown"``/``"unavailable"`` and an ``error`` string rather than an
exception escaping to the caller.
"""

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

DEFAULT_FETCH_TIMEOUT = 30

# The freshness vocabulary, declared once, here. Four members report a comparison that
# succeeded and four report why one could not be made; `_read_branch_freshness` is the only
# writer of any of them, and `models.context_packet.BranchFreshness` imports this alias for the
# wire boundary rather than keeping a second copy the degrade paths could outgrow.
FreshnessState = Literal[
    "current",
    "behind",
    "ahead",
    "diverged",
    "no-upstream",
    "no-branch",
    "unknown",
    "unavailable",
]

# The runtime half of the alias, derived from it rather than retyped beside it.
VALID_FRESHNESS_STATES: frozenset[FreshnessState] = frozenset(get_args(FreshnessState))


@dataclass(frozen=True)
class BranchFreshness:
    branch: str
    upstream: str | None
    fetched: bool
    ahead: int | None
    behind: int | None
    state: FreshnessState
    error: str = ""


def upstream_ref(repo_root: Path, branch: str) -> str | None:
    """The remote-tracking ref of branch (e.g. ``origin/main``), or None when unset."""
    # A local ref lookup, so the metadata bound rather than the runner's local default:
    # every command in this module is classed by what it does, not by which file it is in.
    result = run_git(
        repo_root,
        ["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"],
        timeout=GIT_METADATA_TIMEOUT_SECONDS,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def fetch_remote(repo_root: Path, remote: str, timeout: int = DEFAULT_FETCH_TIMEOUT) -> str | None:
    """Fetch a remote's tracking refs. None on success, error text on failure."""
    try:
        # Its own bound, not the runner's: a fetch is the one network call here and
        # 30s is the point past which "still fetching" means "not coming back".
        result = run_git(repo_root, ["fetch", remote], timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return f"git fetch {remote} failed: {error}"
    if result.returncode != 0:
        return (result.stderr or result.stdout or f"git fetch {remote} failed").strip()
    return None


def ahead_behind(repo_root: Path, local: str, other: str) -> tuple[int, int] | None:
    """(ahead, behind) commit counts of local relative to other, or None when unresolvable."""
    # Not constant time: this walks history, and how much depends on how far the two refs
    # have drifted apart, so it keeps the local bound -- named here so the choice reads as
    # a decision rather than as the default nobody looked at.
    result = run_git(
        repo_root,
        ["rev-list", "--left-right", "--count", f"{local}...{other}"],
        timeout=GIT_LOCAL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    if len(parts) != 2:
        return None
    return int(parts[0]), int(parts[1])


def read_branch_freshness(
    repo_root: Path,
    branch: str | None = None,
    *,
    fetch: bool = True,
    fetch_timeout: int = DEFAULT_FETCH_TIMEOUT,
) -> BranchFreshness:
    """Freshness of branch (default: the checked-out branch) against its upstream."""
    root = repo_root.resolve()
    try:
        return _read_branch_freshness(root, branch, fetch=fetch, fetch_timeout=fetch_timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return BranchFreshness(
            branch or "", None, False, None, None, "unavailable", f"git unavailable: {error}"
        )


def _read_branch_freshness(
    root: Path, branch: str | None, *, fetch: bool, fetch_timeout: int
) -> BranchFreshness:
    if branch is None:
        current = run_git(root, ["branch", "--show-current"], timeout=GIT_METADATA_TIMEOUT_SECONDS)
        if current.returncode != 0:
            return BranchFreshness(
                "",
                None,
                False,
                None,
                None,
                "unavailable",
                (current.stderr or "git branch --show-current failed").strip(),
            )
        branch = current.stdout.strip()
    if not branch:
        return BranchFreshness("", None, False, None, None, "no-branch", "HEAD is detached")

    upstream = upstream_ref(root, branch)
    if upstream is None:
        return BranchFreshness(branch, None, False, None, None, "no-upstream")

    fetch_error = (
        fetch_remote(root, upstream.partition("/")[0], timeout=fetch_timeout) if fetch else None
    )
    counts = ahead_behind(root, branch, upstream)
    ahead, behind = counts if counts is not None else (None, None)
    if fetch_error or counts is None:
        error = fetch_error or f"could not count {branch}...{upstream}"
        return BranchFreshness(branch, upstream, False, ahead, behind, "unknown", error)
    state: FreshnessState = (
        "current"
        if ahead == 0 and behind == 0
        else "behind"
        if ahead == 0
        else "ahead"
        if behind == 0
        else "diverged"
    )
    return BranchFreshness(branch, upstream, fetch, ahead, behind, state)


def freshness_to_packet(freshness: BranchFreshness) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "branch": freshness.branch,
        "upstream": freshness.upstream,
        "fetched": freshness.fetched,
        "ahead": freshness.ahead,
        "behind": freshness.behind,
        "state": freshness.state,
    }
    if freshness.error:
        packet["error"] = freshness.error
    return packet
