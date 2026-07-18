"""The serving build stamp: which code is answering, and since when (260703-L15).

The July-4 ghost-process lesson: a stale dashboard daemon kept serving an old build and
nothing on the surface said so. The stamp is resolved ONCE at app creation (cheap, no
per-request work) and rides the state payload (``/api/state`` + the SSE ``snapshot``
event) as ``servingBuild``; the cockpit renders it muted in the header so a stale server
is visible at a glance.

``commit`` is best-effort: running from a source checkout it is the repo's short HEAD
hash; from an installed wheel (no git metadata) it is ``None`` and the package version
carries the identity alone. Failures never propagate -- an unstampable build serves as
``version``-only, never a crash.
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

    def payload(self) -> dict[str, Any]:
        """The camelCase wire form carried on the state payload (``None`` commit omitted)."""
        body: dict[str, Any] = {"version": self.version, "bootedAt": self.booted_at}
        if self.commit is not None:
            body["commit"] = self.commit
        if self.dashboard_build is not None:
            body["dashboardBuild"] = self.dashboard_build
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


def _dashboard_build_fingerprint() -> str | None:
    """Fingerprint of the shipped browser inputs, or ``None`` for a legacy bundle."""
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
    return ServingBuild(
        version=SERVER_VERSION,
        commit=_git_short_head(root),
        booted_at=now_iso(),
        dashboard_build=_dashboard_build_fingerprint(),
    )
