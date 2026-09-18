#!/usr/bin/env python3
"""Place a freshly built dashboard bundle into MCP package data (release build step).

The cockpit's Vite output, ``dashboard/dist/``, is copied into
``mcp/src/agents_remember/package_data/dashboard/`` so the wheel and sdist ship the
cockpit without a Node build at install time. That destination is a build product and is
NOT under version control (master decision OQ6, 2026-07-31): the release job runs
``npm --prefix dashboard run build`` and then this script, and packaging reads what this
script placed.

Because nothing in the repository can drift from a bundle the repository does not
contain, the WRITE path has no drift comparison to make: the drift comparison, the
fingerprint re-verification, and the "no dashboard/dist yet" no-op that made the old check
exit 0 on every fresh clone are all gone with their subject.

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

``--check`` answers the ONE question the write path cannot: whether what is placed right
now still matches the source, which is what a pre-commit or CI gate needs. It reports the
drift and writes NOTHING -- no staging copy, no rename, no sidecar rewrite -- so running it
can never be the thing that made the tree dirty. It is deliberately NOT the old check: it
reports "cannot verify: nothing has been built or placed" as drift rather than as a pass,
because an unverifiable tree and a current one are different facts and the old mode
conflated them.
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
    """Build a complete staged copy, then swap it in with two separate renames.

    This is the copy-then-swap ``sync-runtime`` and ``sync-skills`` use without their
    extended-length path handling. Staging first is what closes the window a delete-then-copy
    leaves open: a copy that fails before the first rename leaves the live target exactly as it
    was, and a re-run prunes leftovers from an earlier crash and rebuilds the staging copy from
    source.

    The window between the two renames remains. The live target is renamed to
    ``<target>.ar-sync-old`` before ``<target>.ar-sync-new`` is renamed onto the live path, and
    those renames are separate operations: a failure after the first leaves the live path absent,
    with the previous copy and the complete replacement both still on disk. A re-run deletes both
    leftovers before it copies again, so a second failure can consume the last copy of the old
    bytes. Nothing here is atomic and nothing rolls back; only a later successful run reconstructs
    the target from source.
    """
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


def tree_digests(root: Path) -> dict[str, str]:
    """Digest every file under ``root``, keyed by its POSIX path relative to ``root``."""
    return {
        path.relative_to(root).as_posix(): _digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and not ignored(path.relative_to(root))
    }


def drift_reasons() -> list[str]:
    """Every reason the placed bundle does not match the source, in a fixed order.

    Ordered and complete rather than short-circuiting: a gate that reports the first reason
    sends the reader back for a second run to find the second one.
    """
    reasons: list[str] = []
    if not SOURCE.is_dir():
        reasons.append(f"dashboard/dist is absent: nothing has been built (run: {REBUILD_HINT})")
        return reasons
    if not TARGET.is_dir():
        reasons.append(
            f"the placed bundle {TARGET} is absent: it has never been placed (run: {REBUILD_HINT})"
        )
        return reasons
    fingerprint = source_fingerprint()
    if not bundle_is_current(fingerprint):
        reasons.append(
            "dashboard/dist is stale: it does not carry the current build-input "
            f"fingerprint, so it was not built from this source (run: {REBUILD_HINT})"
        )
    placed = tree_digests(TARGET)
    expected = tree_digests(SOURCE)
    for name in sorted(set(expected) | set(placed)):
        if name not in placed:
            reasons.append(f"{name} is in dashboard/dist but not in the placed bundle")
        elif name not in expected:
            reasons.append(f"{name} is in the placed bundle but not in dashboard/dist")
        elif placed[name] != expected[name]:
            reasons.append(f"{name} differs between dashboard/dist and the placed bundle")
    stamped = (
        FINGERPRINT_FILE.read_text(encoding="utf-8").strip() if FINGERPRINT_FILE.is_file() else ""
    )
    if stamped != fingerprint:
        reasons.append(
            f"the dashboard.fingerprint sidecar records "
            f"{stamped or '<nothing>'}, not the current build-input fingerprint"
        )
    return reasons


def check() -> int:
    """Report whether the placed bundle still matches the source. Writes nothing."""
    reasons = drift_reasons()
    if reasons:
        print(
            f"[sync-dashboard] --check: the placed bundle has drifted "
            f"({len(reasons)} reason(s)); nothing was written:",
            file=sys.stderr,
        )
        for reason in reasons:
            print(f"[sync-dashboard]   - {reason}", file=sys.stderr)
        return 1
    print("[sync-dashboard] --check: the placed bundle matches dashboard/dist")
    return 0


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
    parser = argparse.ArgumentParser(
        description="Place a freshly built dashboard/dist into MCP package data."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report whether the placed bundle still matches dashboard/dist, and write "
        "nothing. Exits non-zero on any drift, including 'nothing has been built or placed'.",
    )
    args = parser.parse_args()
    return check() if args.check else sync()


if __name__ == "__main__":
    raise SystemExit(main())
