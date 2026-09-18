#!/usr/bin/env python3
"""Sync canonical root runtime assets into MCP package data."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DATA = REPO_ROOT / "mcp/src/agents_remember/package_data"
IGNORED_NAMES = frozenset(
    {
        ".DS_Store",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
)


@dataclass(frozen=True)
class RuntimeTarget:
    """One canonical tree and where its generated copy belongs.

    ``ignored_names`` is per target because the machine-local trees differ per
    canonical folder: the eve application carries ``node_modules`` and eve's own
    ``.eve``/``.output``/``.vercel`` state, and none of them is authored source.
    A global ignore would silently drop a same-named directory from any other
    target, so the rule travels with the tree it applies to.
    """

    label: str
    source: Path
    path: Path
    ignored_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RuntimeDiff:
    missing: tuple[Path, ...]
    extra: tuple[Path, ...]
    changed: tuple[Path, ...]
    source_missing: bool = False
    """Whether the canonical source itself is absent.

    Without this flag a target whose source and generated copy are *both* missing compared equal
    and reported ``ok``: an empty comparison is not evidence of a synced tree, and a caller who
    pointed the generator at the wrong root would read five green rows for nothing.
    """

    @property
    def in_sync(self) -> bool:
        return not self.source_missing and not self.missing and not self.extra and not self.changed


TARGETS = (
    RuntimeTarget(
        "agents-md-files",
        REPO_ROOT / "agents-md-files",
        PACKAGE_DATA / "runtime/agents-md-files",
    ),
    RuntimeTarget("benchmarks", REPO_ROOT / "benchmarks", PACKAGE_DATA / "benchmarks"),
    RuntimeTarget("providers", REPO_ROOT / "providers", PACKAGE_DATA / "runtime/providers"),
    RuntimeTarget("system", REPO_ROOT / "system", PACKAGE_DATA / "runtime/system"),
    RuntimeTarget(
        "eve-runtime",
        REPO_ROOT / "eve_runtime",
        PACKAGE_DATA / "runtime/eve-runtime",
        ignored_names=frozenset({"node_modules", ".eve", ".output", ".vercel"}),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy canonical root runtime asset folders into MCP package data."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify that package data matches canonical runtime assets; do not write files.",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="Print sync targets and exit.",
    )
    return parser.parse_args()


def repo_relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def ignored(rel_path: Path, target_ignored: frozenset[str] = frozenset()) -> bool:
    return any(
        part in IGNORED_NAMES or part in target_ignored or part.endswith(".pyc")
        for part in rel_path.parts
    )


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()


def extended_length(path: Path) -> Path:
    """Windows extended-length (\\\\?\\) form so file operations work past the
    legacy 260-char MAX_PATH even when LongPathsEnabled is off; identity on
    other platforms."""
    text = str(path)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return Path(f"\\\\?\\{os.path.abspath(text)}")
    return path


def file_digests(root: Path, target_ignored: frozenset[str] = frozenset()) -> dict[Path, str]:
    walk_root = extended_length(root)
    if not walk_root.exists():
        return {}

    files: dict[Path, str] = {}
    for path in walk_root.rglob("*"):
        rel_path = path.relative_to(walk_root)
        if ignored(rel_path, target_ignored) or not path.is_file():
            continue
        files[rel_path] = digest(path)
    return files


def diff_target(target: RuntimeTarget) -> RuntimeDiff:
    if not target.source.is_dir():
        return RuntimeDiff(missing=(), extra=(), changed=(), source_missing=True)
    source = file_digests(target.source, target.ignored_names)
    current = file_digests(target.path, target.ignored_names)

    source_paths = set(source)
    current_paths = set(current)
    shared_paths = source_paths & current_paths

    return RuntimeDiff(
        missing=tuple(sorted(source_paths - current_paths)),
        extra=tuple(sorted(current_paths - source_paths)),
        changed=tuple(sorted(path for path in shared_paths if source[path] != current[path])),
    )


def copy_ignore(names: list[str], target_ignored: frozenset[str]) -> list[str]:
    return [
        name
        for name in names
        if name in IGNORED_NAMES or name in target_ignored or name.endswith(".pyc")
    ]


def sync_target(target: RuntimeTarget) -> None:
    if target.path.resolve() == target.source.resolve():
        raise RuntimeError(f"refusing to sync {repo_relative(target.source)} onto itself")
    replace_tree(target.source, target.path, target.ignored_names)


def replace_tree(source: Path, target: Path, target_ignored: frozenset[str] = frozenset()) -> None:
    """Build a complete staged copy, then swap it in with two separate renames.

    The previous delete-then-copy left the target gutted when the copy (or the
    delete itself) failed mid-way — a real incident on a Windows host without
    long-path support. Staging first closes that window: a copy that fails before
    the first rename leaves the live target exactly as it was, and a re-run prunes
    leftovers from an earlier crash and rebuilds the staging copy from source.

    The window between the two renames remains. The live target is renamed to
    ``<target>.ar-sync-old`` before ``<target>.ar-sync-new`` is renamed onto the
    live path, and those renames are separate operations: a failure after the
    first leaves the live path absent, with the previous copy and the complete
    replacement both still on disk. A re-run deletes both leftovers before it
    copies again, so a second failure can consume the last copy of the old bytes.
    Nothing here is atomic and nothing rolls back; only a later successful run
    reconstructs the target from source."""
    staging = extended_length(target.parent / f"{target.name}.ar-sync-new")
    retired = extended_length(target.parent / f"{target.name}.ar-sync-old")
    target_ext = extended_length(target)
    for leftover in (staging, retired):
        if leftover.exists():
            shutil.rmtree(leftover)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        extended_length(source),
        staging,
        ignore=lambda _directory, names: copy_ignore(names, target_ignored),
    )
    if target_ext.exists():
        os.rename(target_ext, retired)
    os.rename(staging, target_ext)
    if retired.exists():
        shutil.rmtree(retired)


def print_diff(target: RuntimeTarget, diff: RuntimeDiff) -> None:
    print(f"[sync-runtime] out of sync: {target.label} ({repo_relative(target.path)})")
    if diff.source_missing:
        print(f"  canonical source is missing: {repo_relative(target.source)}")
    for label, paths in (
        ("missing", diff.missing),
        ("extra", diff.extra),
        ("changed", diff.changed),
    ):
        if not paths:
            continue
        print(f"  {label}:")
        for path in paths:
            print(f"    {path.as_posix()}")


def check_targets() -> int:
    failed = False
    for target in TARGETS:
        diff = diff_target(target)
        if diff.in_sync:
            print(f"[sync-runtime] ok: {target.label} ({repo_relative(target.path)})")
            continue
        print_diff(target, diff)
        failed = True

    if failed:
        print("[sync-runtime] run: python3 scripts/sync-runtime.py", file=sys.stderr)
        return 1
    return 0


def sync_targets() -> int:
    missing = [target for target in TARGETS if not target.source.is_dir()]
    if missing:
        for target in missing:
            print(
                f"[sync-runtime] missing canonical {repo_relative(target.source)} directory",
                file=sys.stderr,
            )
        return 1

    for target in TARGETS:
        sync_target(target)
        print(f"[sync-runtime] synced {repo_relative(target.path)}")
    return check_targets()


def list_targets() -> int:
    for target in TARGETS:
        print(f"{target.label}: {repo_relative(target.source)} -> {repo_relative(target.path)}")
    return 0


def main() -> int:
    args = parse_args()
    if args.list_targets:
        return list_targets()
    if args.check:
        return check_targets()
    return sync_targets()


if __name__ == "__main__":
    raise SystemExit(main())
