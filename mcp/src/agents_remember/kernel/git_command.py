"""The one git command runner for this package.

Every ``git`` subprocess goes through :func:`run_git`. It used to go through six
near-identical copies of this function, and the copies had drifted: only this one
stripped the repository-selector variables below, so with ``GIT_DIR`` exported the
kernel's ``git commit`` landed in the real repository while the worktree module's
``git commit`` -- the same function with ``env=`` omitted -- landed in whatever
repository ``GIT_DIR`` named. The runner is singular now so that guard cannot be
absent from one path and present in another.

``mcp/tests/test_git_command.py`` sets the selectors against a decoy repository and
proves the real one is untouched; ``mcp/tests/conftest.py`` also strips them at
import so fixtures are safe, but that guard is deliberately *not* what makes the
production paths safe -- the decoy test re-sets the variables inside its own scope
precisely so it cannot pass on the conftest's account.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_REPOSITORY_SELECTOR_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)

# Timeout classes. One number cannot bound every git command, and picking one
# anyway is how a correctness fix becomes an outage: this runner was hard-coded to
# five seconds, which is a fine bound for `rev-parse` and an absurd one for
# `rebase`, `merge`, `worktree add` or `push --delete`, so consolidating onto it
# unchanged would have killed every integrate at five seconds. Both numbers name
# the slowest *legitimate* case in their band, so tripping one is always a stall
# and never healthy work.
#
# Local work gets the larger bound: a rebase or a status over a large tree can
# legitimately churn for minutes. Remote work gets the smaller one: bytes either
# move or the connection is wedged, and a wedged connection inside an MCP tool call
# has no cancellation path, so it must not be allowed to sit there for minutes.
# A third band for constant-time metadata reads -- `rev-parse`, `branch --show-current`,
# `ls-files` -- that sit on interactive paths. Measured on this repository they return in
# under a millisecond, so 30s is roughly thirty thousand times the observed cost and can
# only be reached when git is blocked on an index lock another process holds. Inheriting
# the local bound would let one wedged `rev-parse` hold an MCP tool call for five minutes,
# which is the wrong failure for a query that is either instant or stuck.
GIT_LOCAL_TIMEOUT_SECONDS = 300
GIT_REMOTE_TIMEOUT_SECONDS = 120
GIT_METADATA_TIMEOUT_SECONDS = 30


def git_environment() -> dict[str, str]:
    """Return the ambient environment without Git repository selectors."""

    environment = os.environ.copy()
    for name in GIT_REPOSITORY_SELECTOR_ENV:
        environment.pop(name, None)
    return environment


def run_git(
    repo_root: Path,
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: float = GIT_LOCAL_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run ``git args`` against ``repo_root``, never against an inherited selector.

    ``input_text`` feeds git's stdin (``git patch-id`` is the only caller that needs
    it). Without it stdin is ``DEVNULL``: under the stdio MCP transport the parent's
    stdin IS the JSON-RPC request pipe, and a child holding or reading it wedges the
    tool call (GitHub #49).
    """

    stdin_kwargs: dict[str, object] = (
        {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
    )
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root.as_posix()}", *args],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        capture_output=True,
        env=git_environment(),
        timeout=timeout,
        check=False,
        **stdin_kwargs,  # type: ignore[arg-type]
    )
