#!/usr/bin/env python3
"""Sync the built dashboard bundle (dashboard/dist) into MCP package data.

The dashboard frontend is a root-level sub-project (slice 05); its Vite build output,
``dashboard/dist/``, is copied into ``mcp/src/agents_remember/package_data/dashboard/`` so
the wheel ships the cockpit without a Node build at install time -- mirroring
``sync-runtime.py`` / ``sync-skills.py``. Until that build exists (slice 04 ships a
hand-authored placeholder), ``dashboard/dist/`` is absent and this script no-ops, leaving the
committed placeholder in place.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "dashboard" / "dist"
TARGET = REPO_ROOT / "mcp/src/agents_remember/package_data/dashboard"
IGNORED_NAMES = frozenset({".DS_Store", "__pycache__"})


def ignored(rel_path: Path) -> bool:
    return any(part in IGNORED_NAMES for part in rel_path.parts)


def file_digests(root: Path) -> dict[Path, str]:
    if not root.is_dir():
        return {}
    files: dict[Path, str] = {}
    for path in root.rglob("*"):
        rel_path = path.relative_to(root)
        if ignored(rel_path) or not path.is_file():
            continue
        files[rel_path] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def replace_tree(source: Path, target: Path) -> None:
    """Copy-then-swap so no crash window leaves a partial target (per sync-runtime)."""
    staging = target.parent / f"{target.name}.ar-sync-new"
    retired = target.parent / f"{target.name}.ar-sync-old"
    for leftover in (staging, retired):
        if leftover.exists():
            shutil.rmtree(leftover)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source, staging, ignore=lambda _d, names: [n for n in names if n in IGNORED_NAMES]
    )
    if target.exists():
        target.rename(retired)
    staging.rename(target)
    if retired.exists():
        shutil.rmtree(retired)


def check() -> int:
    if not SOURCE.is_dir():
        print("[sync-dashboard] no dashboard/dist yet; shipped placeholder retained")
        return 0
    if file_digests(SOURCE) == file_digests(TARGET):
        print("[sync-dashboard] ok: package_data/dashboard matches dashboard/dist")
        return 0
    print("[sync-dashboard] out of sync: run python3 scripts/sync-dashboard.py", file=sys.stderr)
    return 1


def sync() -> int:
    if not SOURCE.is_dir():
        print("[sync-dashboard] no dashboard/dist yet; nothing to sync (placeholder retained)")
        return 0
    replace_tree(SOURCE, TARGET)
    print("[sync-dashboard] synced mcp/src/agents_remember/package_data/dashboard")
    return check()


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy dashboard/dist into MCP package data.")
    parser.add_argument(
        "--check", action="store_true", help="Verify only; do not write files."
    )
    args = parser.parse_args()
    return check() if args.check else sync()


if __name__ == "__main__":
    raise SystemExit(main())
