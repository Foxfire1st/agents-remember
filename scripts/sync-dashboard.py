#!/usr/bin/env python3
"""Place a freshly built dashboard bundle into MCP package data (release build step).

The cockpit's Vite output, ``dashboard/dist/``, is copied into
``mcp/src/agents_remember/package_data/dashboard/`` so the wheel and sdist ship the
cockpit without a Node build at install time. That destination is a build product and is
NOT under version control (master decision OQ6, 2026-07-31): the release job runs
``npm --prefix dashboard run build`` and then this script, and packaging reads what this
script placed.

Because nothing in the repository can drift from a bundle the repository does not
contain, there is no ``--check`` mode: the drift comparison, the fingerprint
re-verification, and the "no dashboard/dist yet" no-op that made the old check exit 0 on
every fresh clone are all gone with their subject.

What survives is a freshness proof, and it runs on the write path where it cannot be
skipped. ``vite.config.ts`` compiles the fingerprint of the real build inputs into the
bundle as ``__AR_DASHBOARD_BUILD__``; this script recomputes that same fingerprint from
the source as it stands now and refuses to place a ``dist`` that does not carry it. So
the value written to the ``dashboard.fingerprint`` sidecar is not a claim about the
bundle -- it is a value read back out of the bundle's own JavaScript. That is the
identity ``serving/build_info.py`` advertises as ``servingBuild.dashboardBuild`` and that
a live tab compares against ``CLIENT_DASHBOARD_BUILD`` to notice it is running stale JS;
stamping it over an unbuilt tree, as the old ``sync()`` did, corrupted exactly the signal
it exists to carry.
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

# ``SOURCE_TREE`` is the canonical build input (its parent holds the production config
# files); ``FINGERPRINT_FILE`` is a sibling of the placed bundle -- kept *outside* TARGET
# so the served tree stays a byte-pure copy of ``dist``.
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

REBUILD_HINT = "npm --prefix dashboard run build && python3 scripts/sync-dashboard.py"


def ignored(rel_path: Path) -> bool:
    return any(part in IGNORED_NAMES for part in rel_path.parts)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_bundled_source(rel_path: Path) -> bool:
    return not any(marker in rel_path.name for marker in NON_BUNDLED_MARKERS)


def source_inputs() -> dict[str, str]:
    """Digest every build input that affects the shipped bundle, keyed by a stable POSIX path."""
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
    """The build-input identity of the dashboard source as it stands right now.

    Byte-for-byte the algorithm ``vite.config.ts::dashboardSourceFingerprint`` compiles
    into the bundle; the two must agree or :func:`bundle_is_current` rejects every build.
    """
    inputs = source_inputs()
    hasher = hashlib.sha256()
    for key in sorted(inputs):
        hasher.update(key.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(inputs[key].encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def bundle_is_current(fingerprint: str) -> bool:
    """Whether ``dashboard/dist`` was built from the source that produced ``fingerprint``.

    Vite's ``define`` substitutes ``__AR_DASHBOARD_BUILD__`` with a string literal, so a
    bundle built from this source contains this fingerprint verbatim and a bundle built
    from any other source cannot. Timestamps would prove nothing in the place this runs:
    a fresh clone or a CI checkout writes every file at once, so "dist is newer than its
    inputs" is satisfiable by an artifact that was never built from them.
    """
    needle = fingerprint.encode("ascii")
    return any(path.is_file() and needle in path.read_bytes() for path in SOURCE.rglob("*"))


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


def sync() -> int:
    """Place a current ``dashboard/dist`` into package data, or refuse and explain."""
    if not SOURCE.is_dir():
        print(
            f"[sync-dashboard] dashboard/dist is absent: nothing has been built. Run: {REBUILD_HINT}",
            file=sys.stderr,
        )
        return 1
    fingerprint = source_fingerprint()
    if not bundle_is_current(fingerprint):
        print(
            "[sync-dashboard] dashboard/dist is stale: it does not carry the current "
            f"build-input fingerprint, so it was not built from this source. Run: {REBUILD_HINT}",
            file=sys.stderr,
        )
        return 1
    replace_tree(SOURCE, TARGET)
    # After the tree, never before: a crash between the two must not leave a sidecar
    # advertising a build identity for a bundle that is not there.
    FINGERPRINT_FILE.write_text(f"{fingerprint}\n", encoding="utf-8")
    print(f"[sync-dashboard] placed {TARGET} (dashboard build {fingerprint[:12]})")
    return 0


def main() -> int:
    argparse.ArgumentParser(
        description="Place a freshly built dashboard/dist into MCP package data."
    ).parse_args()
    return sync()


if __name__ == "__main__":
    raise SystemExit(main())
