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
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict

import agents_remember
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.version import SERVER_VERSION
from agents_remember.observer.events import now_iso

# Boot-time probes: the stamp is best-effort and must never delay app creation, so a
# git that does not answer in two seconds is treated as "unstampable" like any other
# failure. Kept tight deliberately, against the runner's general-purpose local bound.
_PROBE_TIMEOUT_SECONDS = 2


class ServingBuildPayload(BaseModel):
    """The declared camelCase wire form of the stamp, as it rides ``servingBuild``.

    A model rather than a hand-built ``dict[str, Any]`` because this object is a field of
    the served state contract (``serving.served_state.ServedWorkspaceProjection``), and a
    contract whose members are untyped dicts only pretends to be one.

    The honest-unknown rule of this module is expressed as ``None`` on every best-effort
    field: callers serialize with ``exclude_none=True``, so an unresolvable commit, an
    unbuilt dashboard bundle and an unprovable tree are all OMITTED rather than emitted as
    a null or a fabricated "clean".
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    bootedAt: str
    commit: str | None = None
    dashboardBuild: str | None = None
    # Only ever True or absent -- see ``ServingBuild.payload``.
    dirty: bool | None = None


@dataclass(frozen=True)
class ServingBuild:
    """The immutable boot-time identity of this serving process."""

    version: str
    commit: str | None
    booted_at: str
    dashboard_build: str | None = None
    # Tri-state: True = proven dirty, False = proven clean, None = unprovable (fail-open).
    dirty: bool | None = False

    def payload(self) -> ServingBuildPayload:
        """The declared wire form of this stamp (serialize with ``exclude_none=True``)."""
        return ServingBuildPayload(
            version=self.version,
            bootedAt=self.booted_at,
            commit=self.commit,
            dashboardBuild=self.dashboard_build,
            # Only a PROVEN-dirty tree carries the marker; clean (False) AND unprovable
            # (None) both collapse to None and drop out, so the wire never fabricates a
            # "clean" fact -- absence is not a pristine claim.
            dirty=True if self.dirty else None,
        )


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
