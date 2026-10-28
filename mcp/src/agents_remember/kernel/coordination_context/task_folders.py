"""The walk over a coordination root's task folders, and the archive segment it skips.

A task folder is a direct child directory of ``tasks/<repository>/``; the archive segment is not
one. This is coordination layout, not a task feature, so it lives at the kernel rank: the task
package derives its own paths from it (``tasks.task_paths`` re-exports ``ARCHIVE_DIR`` and
``task_folder_steps``), and the control plane's receipt discovery walks the same folders without
reaching up into ``tasks`` (``layers.toml`` ranks ``controlplane`` below ``tasks``).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

ARCHIVE_DIR = "0_archive"


def task_folder_steps(
    root: Path,
    *,
    follow_symlinks: bool = True,
    on_directory: Callable[[Path], None] | None = None,
    on_error: Callable[[OSError], None] | None = None,
) -> Iterator[Path | None]:
    """One traversal step per child, with the task folder archive policy."""
    if on_directory is not None:
        on_directory(root)
    if not root.is_dir():
        return
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    directory = entry.is_dir(follow_symlinks=follow_symlinks)
                except OSError as error:
                    if on_error is not None:
                        on_error(error)
                    yield None
                    continue
                yield Path(entry.path) if directory and entry.name != ARCHIVE_DIR else None
    except OSError as error:
        if on_error is None:
            raise
        on_error(error)


def canonical_task_folder_steps(
    tasks_root: Path,
    *,
    on_directory: Callable[[Path, int], None] | None = None,
    on_error: Callable[[OSError], None] | None = None,
) -> Iterator[Path | None]:
    """Canonical repo/task folders; preserve no-follow receipt discovery and its budget."""

    def observe(path: Path, depth: int) -> None:
        if on_directory is not None:
            on_directory(path, depth)

    for repository in task_folder_steps(
        tasks_root,
        follow_symlinks=False,
        on_directory=lambda path: observe(path, 0),
        on_error=on_error,
    ):
        yield None
        if repository is not None:
            for folder in task_folder_steps(
                repository,
                follow_symlinks=False,
                on_directory=lambda path: observe(path, 1),
                on_error=on_error,
            ):
                if folder is not None:
                    observe(folder, 2)
                yield folder
