"""The harness launch registry: the TUI coding agents the dashboard can spawn (slice 6e-2b).

A small, explicit, *curated* table -- deliberately **not** a mirror of ``scripts/sync-skills.py``'s
skill-install targets. Skill-sync writes skill files into many agent dirs, including GUI editors
(Cursor, VS Code/Copilot, Antigravity) that cannot be hosted inside a PTY. This registry lists only
the harnesses that (a) Agents Remember actively supports and (b) run as a terminal UI the Mode B2
terminal host can spawn. Gemini CLI is not supported in main; the GUI tools are not spawnable TUIs.

Detection is :func:`shutil.which`: a harness is *launchable* only when its command resolves on
``PATH``. The argv is fixed here, server-side -- the browser sends a harness **id**, never a command
(``GET /api/harnesses`` reports the set + per-harness detection; ``POST /api/terminal/{id}`` with
``{"kind": "harness", "harness": "<id>"}`` resolves the id to its argv) -- so there is no
command-injection surface, the same posture as the slice-6d fixed-argv host.

The ``which`` lookup is injectable (and falls back to :func:`shutil.which` at call time, so tests can
monkeypatch the module attribute too) -- detection unit-tests deterministically without depending on
what happens to be installed on the test machine.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass

Which = Callable[[str], str | None]
"""A :func:`shutil.which`-shaped lookup: a command name -> its resolved path, or ``None`` if absent."""


@dataclass(frozen=True)
class Harness:
    """One supported TUI harness: a stable ``id``, a display ``name``, the ``command`` to detect on
    ``PATH``, and the fixed ``argv`` used to launch it (at the workspace root, like the plain shell)."""

    id: str
    name: str
    command: str
    argv: tuple[str, ...]


HARNESSES: tuple[Harness, ...] = (
    Harness(id="claude", name="Claude Code", command="claude", argv=("claude",)),
    Harness(id="codex", name="Codex", command="codex", argv=("codex",)),
    Harness(id="pi", name="Pi.dev", command="pi", argv=("pi",)),
)
"""The developer-curated max set (2026-06-18): the TUI coding agents AR supports. Display order."""

_BY_ID: dict[str, Harness] = {harness.id: harness for harness in HARNESSES}


@dataclass(frozen=True)
class DetectedHarness:
    """A harness paired with whether it is installed -- the shape ``GET /api/harnesses`` returns."""

    id: str
    name: str
    detected: bool


def find_harness(harness_id: str) -> Harness | None:
    """The registered :class:`Harness` for ``harness_id``, or ``None`` if the id is unknown."""
    return _BY_ID.get(harness_id)


def is_detected(harness: Harness, *, which: Which | None = None) -> bool:
    """Whether ``harness`` is launchable here -- its ``command`` resolves on ``PATH``.

    ``which`` defaults to :func:`shutil.which`, resolved at call time so a test can either inject a
    fake or monkeypatch the module attribute.
    """
    resolver = which if which is not None else shutil.which
    return resolver(harness.command) is not None


def detect_harnesses(*, which: Which | None = None) -> list[DetectedHarness]:
    """The full supported set in registry order, each marked detected/undetected for this machine."""
    return [
        DetectedHarness(id=harness.id, name=harness.name, detected=is_detected(harness, which=which))
        for harness in HARNESSES
    ]
