"""The serving build stamp: which code is answering, and since when.

The ghost-process lesson: a stale dashboard daemon kept serving an old build and
nothing on the surface said so. The stamp is resolved ONCE at app creation (cheap, no
per-request work) and rides the state payload (``/api/state`` + the SSE ``snapshot``
event) as ``servingBuild``; the cockpit renders it muted in the header so a stale server
is visible at a glance.

``sourceDigest`` is the content address of the importable Python package. It distinguishes
equal-version source checkouts and installed artifacts, while ``commit`` remains useful
checkout provenance. Failures never propagate: an unstampable field is omitted rather
than guessed.

``dirty`` marks a checkout whose working tree has uncommitted code (tracked modifications
or untracked, non-ignored files) at resolve time: a fix-round daemon serves its base
commit's hash while running substantially different code, and the header must say so.
Probed ONCE alongside the rev-parse, only when a commit resolved. The probe fails OPEN:
an unprovable tree (``git status`` raises or exits non-zero) yields ``None`` -- omitted
from the wire, never a fabricated "clean". Since the ABSENCE of the marker must never be
read as a verified-pristine claim, an honest unknown mirrors ``commit``'s ``None``.
"""

from __future__ import annotations

import contextlib
import hashlib
import sys
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path

import agents_remember
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.version import SERVER_VERSION
from agents_remember.models.core import ServingBuildPayload
from agents_remember.observer.events import now_iso

# Boot-time probes: the stamp is best-effort and must never delay app creation, so a
# git that does not answer in two seconds is treated as "unstampable" like any other
# failure. Kept tight deliberately, against the runner's general-purpose local bound.
_PROBE_TIMEOUT_SECONDS = 2


@dataclass(frozen=True)
class ServingBuild:
    """The immutable boot-time identity of this serving process."""

    version: str
    commit: str | None
    booted_at: str
    dashboard_build: str | None = None
    # Tri-state: True = proven dirty, False = proven clean, None = unprovable (fail-open).
    dirty: bool | None = False
    source_digest: str | None = None
    python_executable: str | None = None
    package_root: str | None = None

    def payload(self) -> ServingBuildPayload:
        """The declared wire form of this stamp (serialize with ``exclude_none=True``)."""
        return ServingBuildPayload(
            version=self.version,
            bootedAt=self.booted_at,
            sourceDigest=self.source_digest,
            pythonExecutable=self.python_executable,
            packageRoot=self.package_root,
            commit=self.commit,
            dashboardBuild=self.dashboard_build,
            # Only a PROVEN-dirty tree carries the marker; clean (False) AND unprovable
            # (None) both collapse to None and drop out, so the wire never fabricates a
            # "clean" fact -- absence is not a pristine claim.
            dirty=True if self.dirty else None,
        )


def runtime_source_digest(package_root: Path) -> str | None:
    """Content-address the importable Python source without cache or path noise.

    The package version and checkout commit are insufficient for equal-version wheels and
    dirty source candidates. Hashing sorted relative paths plus file bytes makes those
    artifacts distinguishable while remaining stable when the same package moves.
    """
    with contextlib.suppress(OSError):
        sources = sorted(
            path
            for path in package_root.rglob("*.py")
            if path.is_file() and "__pycache__" not in path.parts
        )
        if not sources:
            return None
        digest = hashlib.sha256(b"agents-remember-python-source-v1\0")
        for path in sources:
            relative = path.relative_to(package_root).as_posix().encode("utf-8")
            body = path.read_bytes()
            digest.update(relative)
            digest.update(b"\0")
            digest.update(str(len(body)).encode("ascii"))
            digest.update(b"\0")
            digest.update(body)
        return f"sha256:{digest.hexdigest()}"
    return None


def _git_short_head(anchor: Path) -> str | None:
    """The short HEAD hash of the checkout containing ``anchor``, or ``None`` off-checkout."""
    with contextlib.suppress(Exception):
        # The one runner: it strips GIT_DIR (so the stamp describes the checkout the
        # server was started from, not an inherited one) and DEVNULLs stdin (so the
        # probe can never touch the MCP stdio protocol pipes).
        result = run_git(anchor, ["rev-parse", "--short", "HEAD"], timeout=_PROBE_TIMEOUT_SECONDS)
        if result.returncode == 0:
            head = result.stdout.strip()
            return head or None
    return None


def _git_worktree_dirty(anchor: Path) -> bool | None:
    """Whether the checkout containing ``anchor`` has uncommitted code, or ``None`` if unprovable.

    Tracked modifications or untracked, non-ignored files make it ``True``; an empty, clean
    tree is ``False``. A probe failure (``git status`` raises or exits non-zero) fails OPEN to
    ``None`` -- mirroring ``_git_short_head``, never fabricating a "clean" fact the probe did
    not verify. The stamp exists to expose stale/uncommitted servers, so an unprovable tree
    must read as "not proven clean", never as pristine; the earlier fail-closed ``False`` gave
    the absent marker a false verified-pristine meaning.
    """
    with contextlib.suppress(Exception):
        result = run_git(anchor, ["status", "--porcelain"], timeout=_PROBE_TIMEOUT_SECONDS)
        if result.returncode == 0:
            return bool(result.stdout.strip())
    return None  # unprovable: fail OPEN to unknown, not a fabricated clean tree


def _dashboard_build_fingerprint() -> str | None:
    """Fingerprint of the shipped browser inputs, or ``None`` when no bundle was built.

    The sidecar is a generated artifact written next to the generated bundle, so both are
    absent together: an installation carrying a cockpit stamps which sources produced it,
    and a source checkout that never ran a frontend build stamps nothing. ``None`` drops
    ``dashboardBuild`` from the wire rather than reporting a build identity for a bundle
    that is not being served -- the same honest-unknown rule ``commit`` and ``dirty`` follow.
    """
    fingerprint = resources.files("agents_remember").joinpath(
        "package_data", "dashboard.fingerprint"
    )
    try:
        value = fingerprint.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return value or None


def resolve_serving_build(*, anchor: Path | None = None) -> ServingBuild:
    """Resolve the exact package/runtime stamp once at process boot."""
    root = anchor if anchor is not None else Path(agents_remember.__file__).resolve().parent
    commit = _git_short_head(root)
    return ServingBuild(
        version=SERVER_VERSION,
        commit=commit,
        booted_at=now_iso(),
        source_digest=runtime_source_digest(root),
        python_executable=Path(sys.executable).resolve().as_posix(),
        package_root=root.resolve().as_posix(),
        dashboard_build=_dashboard_build_fingerprint(),
        # Only a real checkout can be dirty; off-checkout stays the clean version-only path.
        dirty=_git_worktree_dirty(root) if commit is not None else False,
    )


@cache
def process_serving_build() -> ServingBuild:
    """Return the one immutable serving identity for this Python process."""

    return resolve_serving_build()
