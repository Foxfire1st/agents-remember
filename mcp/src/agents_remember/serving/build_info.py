"""The serving build stamp: which code is answering, and since when.

The ghost-process lesson: a stale dashboard daemon kept serving an old build and
nothing on the surface said so. The stamp is resolved ONCE at app creation (cheap, no
per-request work) and rides the state payload (``/api/state`` + the SSE ``snapshot``
event) as ``servingBuild``; the cockpit renders it muted in the header so a stale server
is visible at a glance.

``commit`` is best-effort: running from a source checkout it is the repo's short HEAD
hash; from an installed wheel (no git metadata) it is ``None`` and the package version
carries the identity alone. Failures never propagate -- an unstampable build serves as
``version``-only, never a crash.

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
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import agents_remember
from agents_remember.mcp import SERVER_VERSION
from agents_remember.observer.events import now_iso


@dataclass(frozen=True)
class ServingBuild:
    """The immutable boot-time identity of this serving process."""

    version: str
    commit: str | None
    booted_at: str
    dashboard_build: str | None = None
    # Tri-state: True = proven dirty, False = proven clean, None = unprovable (fail-open).
    dirty: bool | None = False

    def payload(self) -> dict[str, Any]:
        """The camelCase wire form carried on the state payload (``None`` commit omitted)."""
        body: dict[str, Any] = {"version": self.version, "bootedAt": self.booted_at}
        if self.commit is not None:
            body["commit"] = self.commit
        if self.dashboard_build is not None:
            body["dashboardBuild"] = self.dashboard_build
        # Only a PROVEN-dirty tree emits the marker; clean AND unprovable (None) both omit
        # it, so the wire never fabricates a "clean" fact -- absence is not a pristine claim.
        if self.dirty:
            body["dirty"] = True
        return body


def _git_short_head(anchor: Path) -> str | None:
    """The short HEAD hash of the checkout containing ``anchor``, or ``None`` off-checkout."""
    with contextlib.suppress(Exception):
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # fixed argv, no wire input
            cwd=anchor,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            stdin=subprocess.DEVNULL,  # never inherit the MCP stdio protocol pipes
        )
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
        result = subprocess.run(
            ["git", "status", "--porcelain"],  # fixed argv, no wire input
            cwd=anchor,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            stdin=subprocess.DEVNULL,  # never inherit the MCP stdio protocol pipes
        )
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
    """Resolve the stamp once at boot: package version + best-effort commit + boot time."""
    root = anchor if anchor is not None else Path(agents_remember.__file__).resolve().parent
    commit = _git_short_head(root)
    return ServingBuild(
        version=SERVER_VERSION,
        commit=commit,
        booted_at=now_iso(),
        dashboard_build=_dashboard_build_fingerprint(),
        # Only a real checkout can be dirty; off-checkout stays the clean version-only path.
        dirty=_git_worktree_dirty(root) if commit is not None else False,
    )
