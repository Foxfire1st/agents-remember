#!/usr/bin/env python3
"""Sync the built dashboard bundle (dashboard/dist) into MCP package data.

The dashboard frontend is a root-level sub-project (slice 05); its Vite build output,
``dashboard/dist/``, is copied into ``mcp/src/agents_remember/package_data/dashboard/`` so
the wheel ships the cockpit without a Node build at install time -- mirroring
``sync-runtime.py`` / ``sync-skills.py``. Until that build exists (slice 04 ships a
hand-authored placeholder), ``dashboard/dist/`` is absent and this script no-ops, leaving the
committed placeholder in place.

Unlike the skill/runtime gates -- whose canonical input *is* the source tree they hash --
``dashboard/dist`` is a *generated* artifact. Comparing it to the shipped copy only proves
"built bundle == shipped bundle"; it cannot tell that either still reflects the current
source, so a ``dashboard/src`` edit committed without a rebuild slips through (both stay
stale together, or ``dist`` is simply absent). To close that blind spot, ``sync`` also
fingerprints the dashboard's real build inputs (the ``src`` tree minus tests, plus the
production config files) into a sibling ``dashboard.fingerprint`` sidecar, and ``--check``
re-verifies it -- flagging a source change that was never rebuilt the same way a changed
skill is flagged.
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

# Source-freshness gate. ``SOURCE_TREE`` is the canonical build input (its parent holds the
# production config files); ``FINGERPRINT_FILE`` is a sibling of the shipped bundle -- kept
# *outside* TARGET so the served tree stays a byte-pure copy of ``dist``.
SOURCE_TREE = REPO_ROOT / "dashboard" / "src"
FINGERPRINT_FILE = TARGET.parent / "dashboard.fingerprint"
BUILD_INPUT_FILES = (
    "index.html",
    "vite.config.ts",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
    "panda.config.ts",
    "postcss.config.cjs",
    "package.json",
    "package-lock.json",
)
# Vite never bundles test/spec/story modules, so changing one must not demand a rebuild.
NON_BUNDLED_MARKERS = (".test.", ".spec.", ".stories.")


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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_bundled_source(rel_path: Path) -> bool:
    return not any(marker in rel_path.name for marker in NON_BUNDLED_MARKERS)


def source_inputs() -> dict[str, str]:
    """Digest every build input that affects the shipped bundle, keyed by a stable POSIX
    path. Returns {} when ``dashboard/src`` is absent (a packaged install with no dashboard
    checkout), which disables the source-freshness gate."""
    if not SOURCE_TREE.is_dir():
        return {}
    dashboard_root = SOURCE_TREE.parent
    digests: dict[str, str] = {}
    for path in SOURCE_TREE.rglob("*"):
        rel_path = path.relative_to(SOURCE_TREE)
        if ignored(rel_path) or not path.is_file() or not _is_bundled_source(rel_path):
            continue
        digests[f"src/{rel_path.as_posix()}"] = _digest(path)
    for name in BUILD_INPUT_FILES:
        candidate = dashboard_root / name
        if candidate.is_file():
            digests[name] = _digest(candidate)
    return digests


def source_fingerprint() -> str:
    inputs = source_inputs()
    hasher = hashlib.sha256()
    for key in sorted(inputs):
        hasher.update(key.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(inputs[key].encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def write_fingerprint() -> None:
    if not SOURCE_TREE.is_dir():
        return
    FINGERPRINT_FILE.write_text(f"{source_fingerprint()}\n", encoding="utf-8")
    print(f"[sync-dashboard] recorded source fingerprint -> {FINGERPRINT_FILE.name}")


def fingerprint_check() -> int:
    """Flag a shipped bundle that no longer reflects the dashboard source. Skipped until a
    fingerprint has been recorded (legacy placeholder) or without a source tree present."""
    if not FINGERPRINT_FILE.is_file() or not SOURCE_TREE.is_dir():
        return 0
    if FINGERPRINT_FILE.read_text(encoding="utf-8").strip() == source_fingerprint():
        return 0
    print(
        "[sync-dashboard] out of sync: dashboard source changed since the shipped bundle was "
        "built; rebuild and re-sync: npm --prefix dashboard run build && "
        "python3 scripts/sync-dashboard.py",
        file=sys.stderr,
    )
    return 1


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
    status = fingerprint_check()
    if not SOURCE.is_dir():
        print("[sync-dashboard] no dashboard/dist yet; shipped placeholder retained")
    elif file_digests(SOURCE) == file_digests(TARGET):
        print("[sync-dashboard] ok: package_data/dashboard matches dashboard/dist")
    else:
        print(
            "[sync-dashboard] out of sync: run python3 scripts/sync-dashboard.py", file=sys.stderr
        )
        status = 1
    return status


def sync() -> int:
    if not SOURCE.is_dir():
        print("[sync-dashboard] no dashboard/dist yet; nothing to sync (placeholder retained)")
        return 0
    replace_tree(SOURCE, TARGET)
    print("[sync-dashboard] synced mcp/src/agents_remember/package_data/dashboard")
    write_fingerprint()
    return check()


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy dashboard/dist into MCP package data.")
    parser.add_argument("--check", action="store_true", help="Verify only; do not write files.")
    args = parser.parse_args()
    return check() if args.check else sync()


if __name__ == "__main__":
    raise SystemExit(main())
