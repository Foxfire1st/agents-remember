#!/usr/bin/env python3
"""Sync canonical root runtime assets into MCP package data."""

from __future__ import annotations

import argparse
import hashlib
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
    label: str
    source: Path
    path: Path


@dataclass(frozen=True)
class RuntimeDiff:
    missing: tuple[Path, ...]
    extra: tuple[Path, ...]
    changed: tuple[Path, ...]

    @property
    def in_sync(self) -> bool:
        return not self.missing and not self.extra and not self.changed


TARGETS = (
    RuntimeTarget(
        "agents-md-files",
        REPO_ROOT / "agents-md-files",
        PACKAGE_DATA / "runtime/agents-md-files",
    ),
    RuntimeTarget("benchmarks", REPO_ROOT / "benchmarks", PACKAGE_DATA / "benchmarks"),
    RuntimeTarget("providers", REPO_ROOT / "providers", PACKAGE_DATA / "runtime/providers"),
    RuntimeTarget("system", REPO_ROOT / "system", PACKAGE_DATA / "runtime/system"),
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


def ignored(rel_path: Path) -> bool:
    return any(part in IGNORED_NAMES or part.endswith(".pyc") for part in rel_path.parts)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()


def file_digests(root: Path) -> dict[Path, str]:
    if not root.exists():
        return {}

    files: dict[Path, str] = {}
    for path in root.rglob("*"):
        rel_path = path.relative_to(root)
        if ignored(rel_path) or not path.is_file():
            continue
        files[rel_path] = digest(path)
    return files


def diff_target(target: RuntimeTarget) -> RuntimeDiff:
    source = file_digests(target.source)
    current = file_digests(target.path)

    source_paths = set(source)
    current_paths = set(current)
    shared_paths = source_paths & current_paths

    return RuntimeDiff(
        missing=tuple(sorted(source_paths - current_paths)),
        extra=tuple(sorted(current_paths - source_paths)),
        changed=tuple(sorted(path for path in shared_paths if source[path] != current[path])),
    )


def copy_ignore(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if name in IGNORED_NAMES or name.endswith(".pyc")]


def sync_target(target: RuntimeTarget) -> None:
    if target.path.resolve() == target.source.resolve():
        raise RuntimeError(f"refusing to sync {repo_relative(target.source)} onto itself")

    if target.path.exists():
        shutil.rmtree(target.path)
    target.path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(target.source, target.path, ignore=copy_ignore)


def print_diff(target: RuntimeTarget, diff: RuntimeDiff) -> None:
    print(f"[sync-runtime] out of sync: {target.label} ({repo_relative(target.path)})")
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
