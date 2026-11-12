"""The built-in agent ids of the role settings.

``orchestration.spawn.harness`` is checked against these ids and the ids a settings file adds
under ``orchestration.harnesses``. A role launch that names no agent takes the first of them the
host offers, in this order. Whether an agent of an id exists is the host's answer, not this
table's.
"""

from __future__ import annotations

HARNESS_IDS: tuple[str, ...] = ("claude", "codex", "pi", "eve")
