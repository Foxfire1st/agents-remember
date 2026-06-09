#!/usr/bin/env python3
"""Sync the root skill tree into package and harness skill copies."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SKILLS = REPO_ROOT / "skills"
IGNORED_NAMES = frozenset(
    {
        ".DS_Store",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
)


@dataclass(frozen=True)
class SkillTarget:
    label: str
    path: Path


@dataclass(frozen=True)
class SkillDiff:
    missing: tuple[Path, ...]
    extra: tuple[Path, ...]
    changed: tuple[Path, ...]

    @property
    def in_sync(self) -> bool:
        return not self.missing and not self.extra and not self.changed


TARGETS = (
    SkillTarget(
        "mcp package data",
        REPO_ROOT / "mcp/src/agents_remember/package_data/runtime/skills",
    ),
    SkillTarget("Claude Code package", REPO_ROOT / ".claude/skills"),
    SkillTarget("Codex package", REPO_ROOT / ".codex/skills"),
    SkillTarget("Cursor package", REPO_ROOT / ".cursor/skills"),
    SkillTarget("VS Code/Copilot package", REPO_ROOT / ".github-vscode/skills"),
    SkillTarget("Hermes package", REPO_ROOT / ".hermes/skills"),
    SkillTarget("OpenClaw package", REPO_ROOT / ".openclaw/workspace/skills"),
    SkillTarget("Pi package", REPO_ROOT / ".pi/skills"),
    SkillTarget("Antigravity package", REPO_ROOT / ".agents/skills"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy the canonical root skills/ tree into the MCP package data "
            "and every harness starter package."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify that every target matches skills/; do not write files.",
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


def extended_length(path: Path) -> Path:
    """Windows extended-length (\\\\?\\) form so file operations work past the
    legacy 260-char MAX_PATH even when LongPathsEnabled is off; identity on
    other platforms."""
    text = str(path)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return Path(f"\\\\?\\{os.path.abspath(text)}")
    return path


def file_digests(root: Path) -> dict[Path, str]:
    walk_root = extended_length(root)
    if not walk_root.exists():
        return {}

    files: dict[Path, str] = {}
    for path in walk_root.rglob("*"):
        rel_path = path.relative_to(walk_root)
        if ignored(rel_path) or not path.is_file():
            continue
        files[rel_path] = digest(path)
    return files


def diff_target(target: SkillTarget) -> SkillDiff:
    source = file_digests(CANONICAL_SKILLS)
    current = file_digests(target.path)

    source_paths = set(source)
    current_paths = set(current)
    shared_paths = source_paths & current_paths

    return SkillDiff(
        missing=tuple(sorted(source_paths - current_paths)),
        extra=tuple(sorted(current_paths - source_paths)),
        changed=tuple(sorted(path for path in shared_paths if source[path] != current[path])),
    )


def copy_ignore(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if name in IGNORED_NAMES or name.endswith(".pyc")]


def sync_target(target: SkillTarget) -> None:
    if target.path.resolve() == CANONICAL_SKILLS.resolve():
        raise RuntimeError("refusing to sync canonical skills/ onto itself")
    replace_tree(CANONICAL_SKILLS, target.path)


def replace_tree(source: Path, target: Path) -> None:
    """Copy-then-swap so no crash window leaves a missing or partial target.

    The previous delete-then-copy left the target gutted when the copy (or the
    delete itself) failed mid-way — a real incident on a Windows host without
    long-path support. The staged copy is built first; the live target is only
    renamed aside once a complete replacement exists, and a re-run cleans any
    leftovers from an earlier crash."""
    staging = extended_length(target.parent / f"{target.name}.ar-sync-new")
    retired = extended_length(target.parent / f"{target.name}.ar-sync-old")
    target_ext = extended_length(target)
    for leftover in (staging, retired):
        if leftover.exists():
            shutil.rmtree(leftover)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(extended_length(source), staging, ignore=copy_ignore)
    if target_ext.exists():
        os.rename(target_ext, retired)
    os.rename(staging, target_ext)
    if retired.exists():
        shutil.rmtree(retired)


def print_diff(target: SkillTarget, diff: SkillDiff) -> None:
    print(f"[sync-skills] out of sync: {target.label} ({repo_relative(target.path)})")
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
            print(f"[sync-skills] ok: {target.label} ({repo_relative(target.path)})")
            continue
        print_diff(target, diff)
        failed = True

    if failed:
        print("[sync-skills] run: python3 scripts/sync-skills.py", file=sys.stderr)
        return 1
    return 0


def sync_targets() -> int:
    if not CANONICAL_SKILLS.is_dir():
        print("[sync-skills] missing canonical skills/ directory", file=sys.stderr)
        return 1

    for target in TARGETS:
        sync_target(target)
        print(f"[sync-skills] synced {repo_relative(target.path)}")
    return check_targets()


def list_targets() -> int:
    print(f"canonical: {repo_relative(CANONICAL_SKILLS)}")
    for target in TARGETS:
        print(f"{target.label}: {repo_relative(target.path)}")
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
