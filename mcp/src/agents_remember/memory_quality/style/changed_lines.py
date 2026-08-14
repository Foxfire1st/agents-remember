"""Identify memory lines changed against ``HEAD`` for closeout-scoped style rules.

Staged, unstaged, and untracked lines are in scope. A tree without ``HEAD`` is entirely in
scope so an unversioned tree cannot vacuously disable a rule. This is a diff scope, not a
violation baseline: every touched line must conform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git

HUNK_PATTERN = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")


@dataclass(frozen=True)
class ChangedLines:
    """Lines added or modified against HEAD, per absolute path."""

    versioned: bool
    lines: dict[Path, frozenset[int]]

    def covers(self, path: Path, line: int) -> bool:
        if not self.versioned:
            return True
        return line in self.lines.get(path, frozenset())


def changed_lines(root: Path) -> ChangedLines:
    """The lines under ``root`` that differ from HEAD, staged or not, plus untracked files.

    ``root`` is anchored once, here, and the anchored form is what asks git and what git is
    given as a pathspec. A relative pathspec would be read against the REPOSITORY root that
    ``repository_root`` just answered rather than against the caller's directory, so
    absolutising only the probe would trade one wrong scope for another.
    """
    scope = anchored(root)
    repository = repository_root(scope)
    if repository is None:
        return ChangedLines(versioned=False, lines={})
    lines: dict[Path, set[int]] = {}
    collect_diff_lines(repository, scope, lines)
    collect_untracked_lines(repository, scope, lines)
    return ChangedLines(
        versioned=True,
        lines={path: frozenset(numbers) for path, numbers in lines.items()},
    )


def anchored(root: Path) -> Path:
    """Make ``root`` absolute without collapsing worktree symlinks."""
    return root.absolute()


def repository_root(root: Path) -> Path | None:
    """The work tree ``root`` sits in, or ``None`` when it has no history to diff against.

    Anchors ``root`` itself as well as :func:`changed_lines` doing so: this is reachable on
    its own, and a public probe that answers "no history" for a path that has plenty --
    purely because of how the caller spelled it -- is the failure :func:`anchored` exists to
    stop.
    """
    scope = anchored(root)
    toplevel = run_git(scope, ["rev-parse", "--show-toplevel"])
    if toplevel.returncode != 0 or not toplevel.stdout.strip():
        return None
    if run_git(scope, ["rev-parse", "--verify", "HEAD"]).returncode != 0:
        return None
    return Path(toplevel.stdout.strip())


def collect_diff_lines(repository: Path, root: Path, lines: dict[Path, set[int]]) -> None:
    """Collect added lines from staged and unstaged diffs, with rename detection."""
    result = run_git(
        repository,
        [
            "-c",
            "core.quotepath=false",
            "diff",
            "--unified=0",
            "--find-renames",
            "--no-color",
            "HEAD",
            "--",
            root.as_posix(),
        ],
    )
    if result.returncode != 0:
        return
    current: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("+++ "):
            current = diff_target(repository, line)
            continue
        hunk = HUNK_PATTERN.match(line)
        if hunk is None or current is None:
            continue
        start = int(hunk.group("start"))
        count = 1 if hunk.group("count") is None else int(hunk.group("count"))
        lines.setdefault(current, set()).update(range(start, start + count))


def diff_target(repository: Path, header: str) -> Path | None:
    """The new-side path of a ``+++`` header, or ``None`` for a deletion."""
    target = header[len("+++ ") :].strip()
    if target == "/dev/null":
        return None
    return repository / target.removeprefix("b/")


def collect_untracked_lines(repository: Path, root: Path, lines: dict[Path, set[int]]) -> None:
    """A file git has never seen is new in full, so every line of it is in scope."""
    result = run_git(
        repository,
        [
            "-c",
            "core.quotepath=false",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            root.as_posix(),
        ],
    )
    if result.returncode != 0:
        return
    for relative in result.stdout.splitlines():
        path = repository / relative.strip()
        if not path.is_file():
            continue
        count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        lines.setdefault(path, set()).update(range(1, count + 1))
