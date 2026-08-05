"""The harness vocabulary: what a harness IS, and the curated set of them.

A settings vocabulary, so it is kernel (``layers.toml``). The ``orchestration.harnesses``
family in the agentic settings file is parsed into these objects by
``kernel/agentic_settings.py``, which merges the curated table below with what the user
declared and hands the effective registry to everything downstream. The parser used to
import the type it produces from ``serving`` -- a rank-1 package importing a rank-12 one,
so the settings file could not be read without loading the terminal host that eventually
launches one of these.

What is NOT here, and the split is the point: DETECTION and LAUNCH. Whether a harness is
installed is a ``shutil.which`` question about this machine, and turning a harness plus a
model/effort knob into argv is a launch question -- both belong to the process that spawns
terminals, and both stayed in ``serving/harnesses.py``. This module answers only "what is
a harness, and which ones ship by default", which is the half a settings parser needs and
the half nothing above it can define without duplicating.

The registry is GOOD DEFAULTS, not a wall (developer ruling 2026-07-07): a settings entry
with a new id ADDS a harness, and one with an existing id OVERRIDES these defaults.
``docs/reference/harnesses.md`` is the manual for that surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EffortValidation = Literal["enumerated", "non-empty"]


@dataclass(frozen=True)
class Harness:
    """One supported TUI harness: a stable ``id``, a display ``name``, the ``command`` to detect on
    ``PATH``, and the fixed ``argv`` used to launch it (at the workspace root, like the plain shell).

    The optional knob-mapping fields (260703-L16) describe how settings-defined non-native harnesses
    receive spawn knobs. Native Claude/Codex/Pi adapters ignore this legacy mapping surface and own
    their dynamic model-gated catalogs and launch channels directly.
    """

    id: str
    name: str
    command: str
    argv: tuple[str, ...]
    # The custom-harness launch flag the model knob maps onto (``--model <value>``).
    model_flag: str | None = None
    # The launch flag the effort knob maps onto, and the values that flag ACCEPTS. Values outside
    # ``effort_flag_values`` are never put on the flag (the claude CLI warns-then-silently-degrades).
    effort_flag: str | None = None
    effort_flag_values: tuple[str, ...] = ()
    # Custom harnesses may expose an explicit enum or accept a non-empty value.
    effort_validation: EffortValidation = "enumerated"
    # Custom-harness effort values delivered by its explicitly declared running-session command.
    effort_session_values: tuple[str, ...] = ()
    effort_session_command: str | None = None
    effort_flag_value_template: str | None = None
    # Where this entry came from. ``registry`` = these curated defaults (a settings OVERRIDE of a
    # builtin keeps it); ``settings`` = a NEW ``orchestration.harnesses`` id. A mapping-less
    # settings harness REFUSES model/effort; native builtins use the normalized adapter port.
    defined_in: Literal["registry", "settings"] = "registry"


HARNESSES: tuple[Harness, ...] = (
    Harness(
        id="claude",
        name="Claude Code",
        command="claude",
        argv=("claude",),
    ),
    Harness(
        id="codex",
        name="Codex",
        command="codex",
        argv=("codex",),
    ),
    Harness(id="pi", name="Pi.dev", command="pi", argv=("pi",)),
)
"""The developer-curated max set (2026-06-18): the native harnesses AR supports."""
