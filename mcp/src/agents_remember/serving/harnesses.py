"""The harness launch registry: the TUI coding agents the dashboard can spawn (slice 6e-2b).

A small, explicit, *curated* table -- deliberately **not** a mirror of ``scripts/sync-skills.py``'s
skill-install targets. Skill-sync writes skill files into many agent dirs, including GUI editors
(Cursor, VS Code/Copilot, Antigravity) that cannot be hosted inside a PTY. This registry lists only
the harnesses that (a) Agents Remember actively supports and (b) run as a terminal UI the Mode B2
terminal host can spawn. Gemini CLI is not supported in main; the GUI tools are not spawnable TUIs.

The registry is GOOD DEFAULTS, not a wall (developer ruling 2026-07-07): the
``orchestration.harnesses`` settings family (parsed by ``kernel/agentic_settings.py``, documented in
``docs/reference/harnesses.md`` -- THE manual for this surface) merges over this table by id. New
ids ADD a harness (users teach the system a TUI we never enumerated); existing ids OVERRIDE the
defaults (e.g. a pre-customized ``argv`` -- launch the harness exactly the way the user would run it
themselves). A harness id known neither here nor in settings refuses LOUDLY at dispatch, naming the
known set and pointing at the manual -- never a crash.

Detection is :func:`shutil.which`: a harness is *launchable* only when its command resolves on
``PATH``. The argv is fixed here, server-side -- the browser sends a harness **id**, never a command
(``GET /api/harnesses`` reports the set + per-harness detection; ``POST /api/terminal/{id}`` with
``{"kind": "harness", "harness": "<id>"}`` resolves the id to its argv) -- so there is no
command-injection surface, the same posture as the slice-6d fixed-argv host.

The ``which`` lookup is injectable (and falls back to :func:`shutil.which` at call time, so tests can
monkeypatch the module attribute too) -- detection unit-tests deterministically without depending on
what happens to be installed on the test machine.

Built-in model/effort launch mapping belongs to the normalized native adapters, whose dynamic L1
catalog is the authority. The optional mapping fields below remain only for settings-defined custom
harnesses; they are not a fallback catalog for Claude, Codex, or Pi.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

Which = Callable[[str], str | None]
"""A :func:`shutil.which`-shaped lookup: a command name -> its resolved path, or ``None`` if absent."""
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

_BY_ID: dict[str, Harness] = {harness.id: harness for harness in HARNESSES}


@dataclass(frozen=True)
class DetectedHarness:
    """A harness paired with whether it is installed -- the shape ``GET /api/harnesses`` returns."""

    id: str
    name: str
    detected: bool


def find_harness(harness_id: str, *, registry: Sequence[Harness] | None = None) -> Harness | None:
    """The :class:`Harness` for ``harness_id``, or ``None`` if the id is unknown.

    ``registry`` is the EFFECTIVE harness set to search (the builtin table merged with the
    ``orchestration.harnesses`` settings family -- ``AgenticSettings.harnesses``); ``None`` means
    the builtin defaults only.
    """
    if registry is None:
        return _BY_ID.get(harness_id)
    return next((harness for harness in registry if harness.id == harness_id), None)


def unknown_harness_detail(harness_id: str, *, registry: Sequence[Harness] | None = None) -> str:
    """The loud dispatch refusal for an id known neither in the registry nor in settings.

    Names the known set and points at the manual (never a crash): users can TEACH the system a new
    TUI via an ``orchestration.harnesses`` settings entry (developer ruling 2026-07-07).
    """
    known = ", ".join(harness.id for harness in (registry if registry is not None else HARNESSES))
    return (
        f"unknown harness: {harness_id!r}; known harnesses: [{known}]. Define a new one under "
        f"orchestration.harnesses in the agentic settings (see docs/reference/harnesses.md)."
    )


def is_detected(harness: Harness, *, which: Which | None = None) -> bool:
    """Whether ``harness`` is launchable here -- its ``command`` resolves on ``PATH``.

    ``which`` defaults to :func:`shutil.which`, resolved at call time so a test can either inject a
    fake or monkeypatch the module attribute.
    """
    resolver = which if which is not None else shutil.which
    return resolver(harness.command) is not None


def detect_harnesses(
    *, which: Which | None = None, registry: Sequence[Harness] | None = None
) -> list[DetectedHarness]:
    """The full supported set in registry order, each marked detected/undetected for this machine.

    ``registry`` is the effective set to report (builtin merged with settings-defined entries);
    ``None`` means the builtin defaults.
    """
    return [
        DetectedHarness(
            id=harness.id, name=harness.name, detected=is_detected(harness, which=which)
        )
        for harness in (registry if registry is not None else HARNESSES)
    ]


def effort_vocabulary(harness: Harness) -> tuple[str, ...]:
    """``harness``'s known effort values: authoritative enum or non-empty-policy examples.

    Empty means this legacy custom-harness mapping has no known vocabulary.
    """
    return harness.effort_flag_values + harness.effort_session_values


def invalid_effort_detail(harness: Harness, effort: str) -> str | None:
    """The dispatch-time refusal text for ``effort``, or ``None`` when the value is fine.

    A non-empty-policy custom harness accepts a stripped non-empty string. For enumerated harnesses,
    a value inside the vocabulary (flag OR session set) passes. A mapping-less settings-defined
    harness refuses the effort knob outright with guidance: declare the mapping or
    use the free-form escape -- explicit over guessing a flag that might mean something else
    (developer ruling 2026-07-07). Everything else is refused LOUDLY, naming the harness and BOTH
    value sets -- the alternative is the claude CLI's warn-then-silently-degrade, which quietly
    downgrades the most reasoning-hungry seats (probed 2026-07-07).
    """
    if harness.effort_validation == "non-empty":
        if effort.strip():
            return None
        return (
            f"unknown effort {effort!r} for harness {harness.id!r}: this harness requires a "
            "non-empty model-advertised effort after trimming whitespace."
        )
    vocabulary = effort_vocabulary(harness)
    if not vocabulary:
        if harness.defined_in == "settings":
            return (
                f"harness {harness.id!r} declares no effort vocabulary; declare "
                f"effortFlag/effortFlagValues (and/or effortSessionValues + effortSessionCommand) "
                f"for it under orchestration.harnesses, or pass the value through the free-form "
                f"launchArgs/sessionCommands knobs instead (see docs/reference/harnesses.md)."
            )
        return None
    if effort in vocabulary:
        return None
    flag_values = ", ".join(harness.effort_flag_values) or "(none)"
    session_values = ", ".join(harness.effort_session_values) or "(none)"
    return (
        f"unknown effort {effort!r} for harness {harness.id!r}: launch-flag values are "
        f"[{flag_values}]; session values are [{session_values}]. Refused at dispatch because the "
        f"CLI would warn and silently degrade; use the free-form launchArgs/sessionCommands knobs "
        f"for out-of-vocabulary values (see docs/reference/harnesses.md)."
    )


def invalid_model_detail(harness: Harness, model: str) -> str | None:
    """The dispatch-time refusal text for ``model``, or ``None`` when the knob can be applied.

    Model names are never enum-validated here; the only refusal is a settings-defined harness with
    no declared ``modelFlag``. Native adapter models are validated dynamically elsewhere.
    """
    if model and harness.model_flag is None and harness.defined_in == "settings":
        return (
            f"harness {harness.id!r} declares no modelFlag; declare one for it under "
            f"orchestration.harnesses, or pass the value through the free-form launchArgs knob "
            f"instead (see docs/reference/harnesses.md)."
        )
    return None


def knob_argv(
    harness: Harness, *, model: str | None = None, effort: str | None = None
) -> list[str]:
    """The extra argv an explicitly mapped custom harness receives (empty = no static mapping).

    The effort flag is emitted only for values the flag ACCEPTS; session-level values (see
    :func:`effort_session_commands`) ride the other vehicle and must never touch the flag. Values
    are discrete argv elements -- no shell, no interpolation (the fixed-argv posture).
    """
    extra: list[str] = []
    if model and harness.model_flag:
        extra += [harness.model_flag, model]
    mapped_effort = _mapped_effort(harness, effort)
    if mapped_effort is not None and harness.effort_flag:
        value = (
            harness.effort_flag_value_template.format(value=mapped_effort)
            if harness.effort_flag_value_template
            else mapped_effort
        )
        extra += [harness.effort_flag, value]
    return extra


def _mapped_effort(harness: Harness, effort: str | None) -> str | None:
    """The validated value for the launch vehicle, or ``None`` when it must stay off argv."""

    if not effort or harness.effort_flag is None:
        return None
    if harness.effort_validation == "non-empty":
        return effort.strip() or None
    return effort if effort in harness.effort_flag_values else None


def effort_session_commands(harness: Harness, effort: str | None = None) -> list[str]:
    """The post-launch session command(s) delivering a session-level effort value (usually empty).

    Native adapters never use this path to emulate model/effort setting. It remains only for an
    explicitly mapped settings-defined non-native harness.
    """
    if effort and harness.effort_session_command and effort in harness.effort_session_values:
        return [harness.effort_session_command.format(value=effort)]
    return []
