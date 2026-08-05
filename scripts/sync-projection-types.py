#!/usr/bin/env python3
"""Generate or check the dashboard projection schema and TypeScript contract."""

from __future__ import annotations

import argparse
from pathlib import Path

from agents_remember.code_quality.projection_types import (
    REGENERATE_COMMAND,
    SCHEMA_OUTPUT,
    TYPESCRIPT_OUTPUT,
    stale_generated_files,
    sync_generated_files,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate dashboard projection types from the Python projection schema."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when generated files differ; do not write files.",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="Print the generated targets and exit.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def check(repo_root: Path) -> int:
    stale = stale_generated_files(repo_root)
    if not stale:
        print("[sync-projection-types] projection schema and TypeScript are current")
        return 0
    for path in stale:
        print(f"[sync-projection-types] out of sync: {path.as_posix()}")
    print(f"[sync-projection-types] run: {REGENERATE_COMMAND}")
    return 1


def main() -> int:
    args = parse_args()
    root = args.repo_root.resolve()
    if args.list_targets:
        print(f"{SCHEMA_OUTPUT.as_posix()} <- Python WorkspaceProjection schema")
        print(f"{TYPESCRIPT_OUTPUT.as_posix()} <- served projection schema")
        return 0
    if not args.check:
        sync_generated_files(root)
        print(f"[sync-projection-types] generated {SCHEMA_OUTPUT.as_posix()}")
        print(f"[sync-projection-types] generated {TYPESCRIPT_OUTPUT.as_posix()}")
    return check(root)


if __name__ == "__main__":
    raise SystemExit(main())
