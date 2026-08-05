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

"Singular" was true of twenty-one modules and false of one until L6. The benchmark
runner kept its own argv builder and its own spawn as a sanctioned exception, which
meant the guard on this rule had to carry a matching blind spot -- an argv composed by
a helper is invisible at the spawn -- and a sanctioned exception is still a second
owner. The builder is gone; ``work_dir`` and ``core.longpaths=true`` below are the two
things this runner had to grow to take its commands. What keeps the count at one now is
``code_quality/single_owner.py``, which reports the *construction* of a git argv
anywhere in the package rather than only a spawn it can see the argv of.
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
#
# A fourth band for bulk network work that is NOT inside a tool call. The benchmark runner
# clones and fetches whole third-party repositories as a foreground batch step, and the
# remote band's argument does not hold for it: 120s is sized for a wedged connection an MCP
# client cannot cancel, whereas this is a CLI step a developer can interrupt and a large
# repository legitimately takes longer than two minutes to clone. Those spawns had **no**
# timeout at all before they were routed here, so this is the first bound they have ever
# carried rather than a relaxation of an existing one.
GIT_LOCAL_TIMEOUT_SECONDS = 300
GIT_REMOTE_TIMEOUT_SECONDS = 120
GIT_METADATA_TIMEOUT_SECONDS = 30
GIT_BULK_REMOTE_TIMEOUT_SECONDS = 1800


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
    work_dir: Path | None = None,
    input_text: str | None = None,
    timeout: float = GIT_LOCAL_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run ``git args`` against ``repo_root``, never against an inherited selector.

    ``input_text`` feeds git's stdin (``git patch-id`` is the only caller that needs
    it). Without it stdin is ``DEVNULL``: under the stdio MCP transport the parent's
    stdin IS the JSON-RPC request pipe, and a child holding or reading it wedges the
    tool call (GitHub #49).

    ``work_dir`` separates *where git runs* from *which repository the command is about*,
    which are the same directory for every caller but one. ``git clone <url> <dest>``
    cannot run inside ``<dest>``, because ``<dest>`` is what it is about to create, and
    ``cwd=`` a directory that does not exist raises before git is ever reached. The
    benchmark runner therefore hands the destination's parent here while ``repo_root``
    stays the repository being cloned, so ``safe.directory`` still names the right tree.
    It is passed as git's own ``-C`` as well as the child's ``cwd``: ``-C`` is the form
    the benchmark runner's argv already used on the most destructive commands in the
    package (``reset --hard``, ``clean -fdx``), and reproducing them as a bare ``cwd=``
    would have silently swapped the mechanism that aims them.

    BOTH PATHS MUST BE ABSOLUTE, and every caller passes absolute paths today: ``-C`` is
    resolved against the child's cwd, which this already sets to ``work``, so a relative
    ``work`` would select ``work/work``. Not fixed with ``Path.resolve`` because that also
    collapses symlinks, which would leave ``-C`` and ``safe.directory`` naming different
    trees on a symlinked worktree.

    ``core.longpaths=true`` was likewise carried by that argv and is now carried for
    every caller. It is what lets git check out a path past Windows' MAX_PATH; git
    ignores it everywhere else, so the cost off Windows is two argv words.

    ``safe.directory`` names ``repo_root`` and not the ``*`` the benchmark runner used.
    That is narrower, not weaker: it is the exact tree every one of these commands
    operates on, and a wildcard additionally disarms the ownership check for any *other*
    repository the command happens to reach.
    """

    work = repo_root if work_dir is None else work_dir
    stdin_kwargs: dict[str, object] = (
        {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
    )
    return subprocess.run(
        [
            "git",
            "-C",
            work.as_posix(),
            "-c",
            "core.longpaths=true",
            "-c",
            f"safe.directory={repo_root.as_posix()}",
            *args,
        ],
        cwd=work,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        capture_output=True,
        env=git_environment(),
        timeout=timeout,
        check=False,
        **stdin_kwargs,  # type: ignore[arg-type]
    )
