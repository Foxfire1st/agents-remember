"""CLI adapter: remove leftover provider runtime folders (MIK-R112 rule 3).

    agents-remember providers-remove [--config <settings>] [--dry-run]

The provider tools left the MCP surface, but provider runtime folders from an
older build linger under the coordination root. The install never deletes them
(it only stops watchers and prunes legacy binaries); only this explicit command
removes data on disk.

With ``--dry-run`` it reports the folders and their sizes and stops nothing.
Otherwise it stops the configured watchers first, refuses when a live process
still holds a folder open (naming the pid and command), and then removes the
provider runtime folders, reporting the bytes freed per folder.

Exit status is 0 when nothing remained or everything was removed, 1 when a stop failed, a folder is busy, a removal failed, or the provider root or a deletion target escapes the owned tree (parent/ancestor aliases are refused with the path before anything stops).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    load_config,
)
from agents_remember.providers.watcher_service import run_configured_watchers

# Provider runtime folders under the coordination root, in removal order
# (volatile state first, caches last). ``memory-repos/`` is user data and is
# never touched.
PROVIDER_DIRNAMES = ("runners", "data", "logs", "_venvs", "_bin")


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
        help="Report the folders and sizes without stopping or removing anything.",
    )


def _tree_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


class ProviderRootUncontainedError(Exception):
    """The provider root or a deletion target escapes the owned provider tree."""


def _contained_providers_root(coordination_root: Path) -> Path:
    """The physical owned provider root, refusing parent/ancestor aliases.

    A symlinked ``providers`` directory (or any symlinked ancestor) makes the
    leaf look like an ordinary directory while its bytes live in an unrelated
    tree. Only an exact resolved match counts as owned; anything else is
    refused with the actionable path before anything stops or is removed.
    """
    root_real = coordination_root.resolve()
    providers = coordination_root / "providers"
    providers_real = providers.resolve()
    if providers_real != root_real / "providers":
        raise ProviderRootUncontainedError(
            "providers-remove: refusing, the provider root escapes the owned tree: "
            f"{providers.as_posix()} resolves to {providers_real.as_posix()}, "
            f"outside {(root_real / 'providers').as_posix()}"
        )
    return providers_real


def _contained_target(root_real: Path, path: Path) -> Path:
    """The resolved deletion target, refused when it escapes the owned root."""
    real = path.resolve()
    if not real.is_relative_to(root_real):
        raise ProviderRootUncontainedError(
            "providers-remove: refusing, the deletion target escapes the owned tree: "
            f"{path.as_posix()} resolves to {real.as_posix()}, "
            f"outside {root_real.as_posix()}"
        )
    return real


def provider_folders(coordination_root: Path) -> list[dict[str, Any]]:
    """The existing provider runtime folders with their sizes in bytes."""
    root_real = _contained_providers_root(coordination_root)
    providers = coordination_root / "providers"
    found: list[dict[str, Any]] = []
    for dirname in PROVIDER_DIRNAMES:
        path = providers / dirname
        if path.is_dir() and not path.is_symlink():
            _contained_target(root_real, path)
            found.append({"path": path.as_posix(), "bytes": _tree_bytes(path)})
    return found


def _open_paths(entry: Path) -> list[Path]:
    """The cwd plus every open fd of one /proc entry (best effort)."""
    paths = [entry / "cwd"]
    try:
        fd_dir = entry / "fd"
        if fd_dir.is_dir():
            paths.extend(fd_dir.iterdir())
    except OSError:
        pass
    return paths


def _holds(entry: Path, resolved: Path) -> dict[str, Any] | None:
    """The pid/command when one process entry points under ``resolved``."""
    try:
        comm = (entry / "comm").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    for candidate in _open_paths(entry):
        try:
            pointed = Path(os.readlink(candidate))
        except OSError:
            continue
        try:
            if pointed.resolve().is_relative_to(resolved):
                return {"pid": int(entry.name), "command": comm}
        except (OSError, RuntimeError):
            continue
    return None


def _process_holders(target: Path) -> list[dict[str, Any]]:
    """Live processes with a cwd or open fd under ``target`` (Linux /proc scan)."""
    holders: list[dict[str, Any]] = []
    try:
        resolved = target.resolve()
    except OSError:
        return holders
    proc = Path("/proc")
    if not proc.is_dir():
        return holders
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        holder = _holds(entry, resolved)
        if holder is not None:
            holders.append(holder)
    return holders


def stop_configured_watchers(config: McpRuntimeConfig, *, dry_run: bool) -> dict[str, Any]:
    """Stop the configured provider watchers; monkeypatch seam for tests."""
    return run_configured_watchers(config, "stop", dry_run=dry_run)


def _busy_refusal(folders: list[dict[str, Any]]) -> str | None:
    """The refusal naming live processes holding folders open, if any."""
    busy: list[dict[str, Any]] = []
    for item in folders:
        busy.extend(
            {**holder, "folder": item["path"]} for holder in _process_holders(Path(item["path"]))
        )
    if not busy:
        return None
    names = ", ".join(
        f"pid {holder['pid']} ({holder['command']}) holds {holder['folder']}" for holder in busy
    )
    return f"providers-remove: refusing, a live process holds a folder open: {names}"


def _remove_folders(folders: list[dict[str, Any]], root_real: Path) -> tuple[int, list[str]]:
    """Remove the folders after revalidating every target; returns freed bytes and failures."""
    freed = 0
    failed: list[str] = []
    for item in folders:
        try:
            _contained_target(root_real, Path(item["path"]))
        except ProviderRootUncontainedError as error:
            failed.append(str(error))
            continue
        try:
            shutil.rmtree(item["path"])
            freed += int(item["bytes"])
        except OSError as error:
            failed.append(f"{item['path']}: {error}")
    return freed, failed


def _resolve_config(args: argparse.Namespace) -> McpRuntimeConfig | None:
    try:
        return load_config(args.config if args.config else discover_config(Path.cwd()))
    except (ConfigError, ConfigDiscoveryError, OSError) as error:
        print(f"providers-remove: cannot resolve configuration: {error}", file=sys.stderr)
        return None


def _apply(folders: list[dict[str, Any]], config: McpRuntimeConfig) -> int:
    root_real = _contained_providers_root(config.coordination_root)
    stopped = stop_configured_watchers(config, dry_run=False)
    if not stopped.get("ok"):
        print(
            f"providers-remove: watcher stop failed, refusing to remove folders: {stopped}",
            file=sys.stderr,
        )
        return 1
    refusal = _busy_refusal(folders)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 1
    freed, failed = _remove_folders(folders, root_real)
    if failed:
        print("providers-remove: removal failed: " + "; ".join(failed), file=sys.stderr)
        return 1
    print(f"providers-remove: removed {len(folders)} folders, freed {freed} bytes.")
    return 0


def run(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    if config is None:
        return 1
    try:
        folders = provider_folders(config.coordination_root)
    except ProviderRootUncontainedError as error:
        print(str(error), file=sys.stderr)
        return 1
    if not folders:
        print("providers-remove: no provider runtime folders remain.")
        return 0
    total = sum(item["bytes"] for item in folders)
    for item in folders:
        print(
            f"providers-remove: {'would remove' if args.dry_run else 'removing'} "
            f"{item['path']} ({item['bytes']} bytes)"
        )
    if args.dry_run:
        print(f"providers-remove: dry run, {total} bytes in {len(folders)} folders left alone.")
        return 0
    return _apply(folders, config)
