"""The benchmark runner's git step: dry-run printing and failure reporting, nothing else.

Nothing here spawns a subprocess: every command goes through
``kernel.git_command.run_git``, which grew the two things these commands need -- a
``work_dir`` (``git clone`` has to run from the destination's parent) and
``core.longpaths=true``. It also narrows ``safe.directory`` from the ``*`` this module's
own argv builder used to the one repository the command is about.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import (
    GIT_LOCAL_TIMEOUT_SECONDS,
    GIT_METADATA_TIMEOUT_SECONDS,
    run_git,
)


def run_git_command(
    repo_root: Path,
    args: list[str],
    dry_run: bool,
    *,
    work_dir: Path | None = None,
    timeout: float = GIT_LOCAL_TIMEOUT_SECONDS,
) -> None:
    """Run one benchmark git command against ``repo_root``, or print what it would be.

    A non-zero exit raises with the tail of git's own output: preparation is a batch step,
    so a failed ``clone`` has to stop it rather than leave a half-made workspace for the
    next case to be measured in.
    """
    printable = f"git {' '.join(args)}"
    if dry_run:
        print(f"Would run in {work_dir or repo_root}: {printable}")
        return
    result = run_git(repo_root, args, work_dir=work_dir, timeout=timeout)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise RuntimeError(f"command failed ({result.returncode}): {printable}\n{tail}")


def repo_has_commit(repo_root: Path, commit: str) -> bool:
    """Whether ``repo_root`` already holds ``commit`` -- a constant-time metadata read."""
    result = run_git(
        repo_root,
        ["cat-file", "-e", f"{commit}^{{commit}}"],
        timeout=GIT_METADATA_TIMEOUT_SECONDS,
    )
    return result.returncode == 0
