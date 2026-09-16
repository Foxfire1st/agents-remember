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

One seam crosses back: a harness whose runtime is not a PATH command declares a *readiness probe*
(``Harness.runtime_probe``), and this module owns the registry of them so detection never has to
hardcode a harness id. The probe itself lives with the runtime it describes
(``kernel/eve_runtime_readiness.py``); the registry keeps only the name.

The registry is GOOD DEFAULTS, not a wall (developer ruling 2026-07-07): a settings entry
with a new id ADDS a harness, and one with an existing id OVERRIDES these defaults.
``docs/reference/harnesses.md`` is the manual for that surface.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

Which = Callable[[str], str | None]
"""A :func:`shutil.which`-shaped command lookup, injectable so detection is testable."""

ProbeResult = tuple[bool, str, tuple[str, ...]]
"""One readiness verdict: can this runtime start here, what is missing when it cannot, and which
paths the probe resolved (application root first, then interpreter) for callers that need the
runtime identity it already proved."""

RuntimeProbe = Callable[..., ProbeResult]
"""A readiness resolver. Called with keyword ``env`` and ``which`` only."""

RUNTIME_PROBES: dict[str, RuntimeProbe] = {}
"""Readiness resolvers by probe name; a harness with no entry uses the PATH probe."""


def register_runtime_probe(name: str) -> Callable[[RuntimeProbe], RuntimeProbe]:
    """Register one readiness resolver under the name a harness row declares."""

    def _register(resolver: RuntimeProbe) -> RuntimeProbe:
        RUNTIME_PROBES[name] = resolver
        return resolver

    return _register


def load_runtime_probes() -> None:
    """Import the modules that register readiness probes, so a declared probe name resolves.

    Called lazily -- never at import time -- from the function that needs the registry entry, which
    is what keeps this module importable on its own and the dependency one-directional: the probe
    module imports this one at module scope, and this one only ever reaches back inside a call.
    """

    from agents_remember.kernel import eve_runtime_readiness  # noqa: F401, PLC0415


def is_harness_available(
    harness: Harness,
    *,
    which: Which | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Whether ``harness`` can be launched here: its readiness probe, else its PATH command."""

    return harness_availability_detail(harness, which=which, env=env) is None


def harness_availability_detail(
    harness: Harness,
    *,
    which: Which | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """``None`` when ``harness`` is launchable, else the operator-readable reason it is not.

    A runtime-probed harness answers from its probe and reports the probe's own reason (the missing
    component, named). A PATH harness answers from ``shutil.which`` and reports the ordinary
    not-on-PATH sentence. An unknown probe name is a registry defect and refuses loudly rather than
    silently falling back to a ``which`` lookup that would report the wrong cause.
    """

    probe_name = harness.runtime_probe
    if probe_name is None:
        resolver = which if which is not None else shutil.which
        if resolver(harness.command) is not None:
            return None
        return f"harness {harness.id!r} is not installed: {harness.command!r} is not on PATH"
    ready, reason, _locations = harness_runtime_verdict(harness, which=which, env=env)
    return None if ready else f"harness {harness.id!r} is unavailable: {reason}"


def harness_runtime_verdict(
    harness: Harness,
    *,
    which: Which | None = None,
    env: Mapping[str, str] | None = None,
) -> ProbeResult:
    """The full probe verdict for a runtime-probed harness, including the paths it resolved.

    A PATH harness (or an unknown probe name) answers ``(False, reason, ())`` with the same sentence
    ``harness_availability_detail`` returns, so a caller that needs both the verdict and the paths
    never resolves the runtime twice.
    """

    probe_name = harness.runtime_probe
    if probe_name is None:
        resolver = which if which is not None else shutil.which
        if resolver(harness.command) is not None:
            return True, "", ()
        return (
            False,
            f"harness {harness.id!r} is not installed: {harness.command!r} is not on PATH",
            (),
        )
    load_runtime_probes()
    probe = RUNTIME_PROBES.get(probe_name)
    if probe is None:
        return (
            False,
            f"harness {harness.id!r} declares unknown readiness probe {probe_name!r}; "
            f"registered probes: {', '.join(sorted(RUNTIME_PROBES)) or 'none'}",
            (),
        )
    ready, reason, locations = probe(env=env, which=which)
    return ready, reason, tuple(locations)


EffortValidation = Literal["enumerated", "non-empty"]

EVE_RUNTIME_PROBE = "eve-runtime-application"
EVE_RUNTIME_COMMAND = "ar-eve-runtime-application"
"""The eve row's non-executable placeholders; see the row's comment below and the probe module."""


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
    # How this harness is proved launchable here. ``None`` = the ordinary PATH probe
    # (:func:`shutil.which` over ``command``). A registered id names a readiness resolver in
    # :data:`RUNTIME_PROBES` for a harness whose runtime is not a PATH command at all -- its
    # detection question is "can this runtime start", not "is this name on PATH", and answering it
    # with ``which`` would report a missing runtime as a generic not-installed harness.
    runtime_probe: str | None = None
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
    Harness(
        id="eve",
        name="eve",
        # eve is not a PATH TUI. Its runtime is the AR-owned Node application the session adapter
        # spawns itself, so the ``command``/``argv`` pair here is the registry's honest placeholder
        # for "there is no command line to exec" and is never spawned: terminal launch refuses an
        # undetected harness, and this one is detected through its readiness probe instead.
        command=EVE_RUNTIME_COMMAND,
        argv=(EVE_RUNTIME_COMMAND,),
        runtime_probe=EVE_RUNTIME_PROBE,
    ),
)
"""The developer-curated max set (2026-06-18): the native harnesses AR supports.

``eve`` joined this table as a SETTINGS VOCABULARY row: it is what makes ``"harness": "eve"``
legal under ``orchestration.roles``/``orchestration.spawn``, what puts the id in the loud
unknown-harness refusal, and what carries its readiness probe into detection. It adds a row; it
changes no other harness, no default, and no role profile -- nothing selects eve unless a
settings file names it. Whether a usable eve runtime exists on this machine is answered by
:func:`~agents_remember.kernel.eve_runtime_readiness.eve_runtime_readiness`, and that answer is
what ``GET /api/harnesses`` reports and what makes ``GET /api/harnesses/eve/capabilities`` either
discover the real pinned configuration or refuse by name.
"""
