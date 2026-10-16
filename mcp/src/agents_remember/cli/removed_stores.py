"""CLI adapter: remove the stores of removed parts (MIK-R113 rule 5).

    agents-remember removed-stores [--config <settings>] [--dry-run]

The parts MIK-R113 removed wrote stores under the coordination root (the list is
``install.removed_stores.REMOVED_PART_STORE_PATHS``), and the gate logs hold gates that no
part of the product decides any more. The install names them once and never deletes
them; only this explicit command removes data on disk, on the user's word.

With ``--dry-run`` it reports the files and their sizes and removes nothing.
Otherwise it removes the listed files and the folders they leave empty, refusing a
target that escapes the owned coordination tree (parent/ancestor aliases are
refused with the path before anything is removed). A link inside a store folder is
removed as the link; what it points to is never touched.

A place that cannot be read, a file that cannot be removed and a folder that cannot
be removed are each named with the path and the reason, and the other stores are
removed all the same.

Exit status is 0 when nothing remained or everything was removed, 1 when a place
could not be read, a removal failed or a deletion target escapes the owned tree.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.install.removed_stores import REMOVED_PART_STORE_PATHS, removed_part_stores
from agents_remember.kernel.primitives.gate_vocab import STORED_GATE_WAY_OUT
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    load_config,
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=None,
        help="Path to trusted MCP settings JSON. Omit to discover it from the working "
        "directory, as the other subcommands do.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the stores and sizes without removing anything.",
    )


def _contained_target(path: Path, root_real: Path) -> Path:
    """The deletion target, refusing anything outside the owned tree.

    A link is removed as the link itself: the folder that holds it is resolved, never
    what the link points to.
    """

    target = path.parent.resolve() / path.name if path.is_symlink() else path.resolve()
    if target != root_real and root_real not in target.parents:
        raise ValueError(f"refusing to remove {path}: outside the coordination root")
    return target


def run(args: argparse.Namespace) -> int:
    try:
        config: McpRuntimeConfig = load_config(
            args.config if args.config else discover_config(Path.cwd())
        )
    except (ConfigError, ConfigDiscoveryError, OSError) as error:
        print(f"removed-stores: cannot resolve configuration: {error}", file=sys.stderr)
        return 1
    unreadable: dict[str, str] = {}
    stores = removed_part_stores(config.coordination_root, unreadable, include_terminal_locks=True)
    unread = [f"{path}: {reason}" for path, reason in unreadable.items()]
    if unread:
        print("removed-stores: cannot read: " + "; ".join(unread), file=sys.stderr)
    if not stores:
        if not unread:
            print("removed-stores: no stores of removed parts remain.")
        return 1 if unread else 0
    verb = "would remove" if args.dry_run else "removing"
    files = [Path(name) for item in stores for name in item["files"]]
    for item in stores:
        print(f"removed-stores: {verb} {item['path']} ({item['bytes']} bytes)")
    if any(item["gateLog"] for item in stores):
        print(f"removed-stores: {STORED_GATE_WAY_OUT}.")
    if args.dry_run:
        print(f"removed-stores: dry run, {len(stores)} stores ({len(files)} files) left alone.")
        return 1 if unread else 0
    removed, freed, failed = _remove(config.coordination_root, stores, unreadable)
    if failed:
        print("removed-stores: removal failed: " + "; ".join(failed), file=sys.stderr)
    if failed or unread:
        print(
            f"removed-stores: incomplete, removed {removed} of {len(files)} files, "
            f"freed {freed} bytes."
        )
        return 1
    print(f"removed-stores: removed {len(stores)} stores, freed {freed} bytes.")
    return 0


def _remove(
    coordination_root: Path, stores: list[dict[str, Any]], unreadable: dict[str, str]
) -> tuple[int, int, list[str]]:
    """Remove the stores' files, then the store folders they leave empty.

    Answers how many files went, the bytes freed, and every file and folder that could
    not be removed with the reason. One failure holds up nothing else. A store folder that
    could not be read whole is named already and is left as it is.
    """

    root_real = coordination_root.resolve()
    files = [Path(name) for item in stores for name in item["files"]]
    removed, freed, failed = _remove_files(files, root_real)
    folders = [coordination_root / relative for relative in REMOVED_PART_STORE_PATHS]
    folders.extend(Path(str(item["path"])) for item in stores)
    lifecycle_root = coordination_root / "logs/observer/lifecycles"
    folders.extend(path.parent for path in files if path.parent.parent == lifecycle_root)
    for store in dict.fromkeys(folders):
        if not any(Path(place).is_relative_to(store) for place in unreadable):
            failed.extend(_remove_emptied_folder(store, root_real))
    return removed, freed, failed


def _remove_files(files: list[Path], root_real: Path) -> tuple[int, int, list[str]]:
    """Remove each file by itself: how many went, the bytes freed, and what failed with why."""

    removed = 0
    freed = 0
    failed: list[str] = []
    for path in files:
        try:
            target = _contained_target(path, root_real)
            size = target.lstat().st_size
            target.unlink()
        except (ValueError, OSError) as error:
            failed.append(f"{path}: {error}")
            continue
        removed += 1
        freed += size
    return removed, freed, failed


def _remove_emptied_folder(store: Path, root_real: Path) -> list[str]:
    """Remove a store folder that holds no file any more, deepest folder first.

    Every folder that cannot be removed is returned with the reason. The folders above it
    stay with it and are not tried; the folders beside it and the other stores go all the same.
    """

    if store.is_symlink() or not store.is_dir():
        return []
    if any(path.is_file() or path.is_symlink() for path in store.rglob("*")):
        return []
    try:
        _contained_target(store, root_real)
    except ValueError:
        return []
    inner = sorted((path for path in store.rglob("*") if path.is_dir()), reverse=True)
    kept: list[Path] = []
    failed: list[str] = []
    for folder in (*inner, store):
        if any(below.is_relative_to(folder) for below in kept):
            continue
        try:
            folder.rmdir()
        except OSError as error:
            kept.append(folder)
            failed.append(f"{folder}: {error}")
    return failed
