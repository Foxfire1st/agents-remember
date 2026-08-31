"""No-following filesystem boundaries for immutable quality-report publication."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from agents_remember.worktrees.modules.quality.published_manifest import (
    require_real_directory_or_missing,
    require_real_file_or_missing,
)


def preflight_report_destination(
    destination: Path,
    generation_root: Path,
    *,
    generations_directory: str,
    exported_files: Collection[str],
    exported_directories: Collection[str],
) -> None:
    """Validate every path publication may traverse before moving the live pointer."""

    require_real_directory_or_missing(destination, purpose="quality report destination")
    require_real_directory_or_missing(
        destination / generations_directory,
        purpose="quality generation directory",
    )
    require_real_directory_or_missing(generation_root, purpose="quality generation")
    for directory in exported_directories:
        require_real_directory_or_missing(
            destination / directory,
            purpose="legacy report directory",
        )
    for name in exported_files:
        require_real_file_or_missing(
            destination / name,
            purpose="legacy report file",
        )


def remove_legacy_report_projection(
    destination: Path,
    *,
    exported_files: Collection[str],
    exported_directories: Collection[str],
) -> None:
    """Remove only the former verified top-level report projection."""

    for legacy_name in exported_files:
        (destination / legacy_name).unlink(missing_ok=True)
    for legacy_directory in sorted(exported_directories, reverse=True):
        try:
            (destination / legacy_directory).rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            # A non-empty directory is not part of the legacy projection and is not ours to erase.
            pass


def report_tree_inventory(root: Path) -> tuple[set[str], set[str], set[str]]:
    """Inventory a report tree without following links or normalizing hidden paths."""

    files: set[str] = set()
    directories: set[str] = set()
    irregular: set[str] = set()
    for entry in root.rglob("*"):
        relative = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            irregular.add(relative)
        elif entry.is_dir():
            directories.add(relative)
        elif entry.is_file():
            files.add(relative)
        else:
            irregular.add(relative)
    return files, directories, irregular
