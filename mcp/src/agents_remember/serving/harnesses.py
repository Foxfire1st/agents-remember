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

Per-harness knob application (260703-L16): each entry may also carry the mapping from the role knobs
(``AR_SPAWN_MODEL`` / ``AR_SPAWN_EFFORT``) onto that harness's concrete CLI. Claude Code maps
``model``/``effort`` onto ``--model``/``--effort`` and splits its effort vocabulary across TWO
delivery vehicles (empirical, 2026-07-07): ``low|medium|high|xhigh|max`` are LAUNCH-FLAG values,
while ``ultracode`` is a SESSION-LEVEL value the ``--effort`` flag rejects (warn-then-silently-
degrade) but the interactive ``/effort`` command accepts -- it is delivered post-launch as a pasted
session command. A harness without a mapping (Codex, Pi.dev) stays **env-only**: the knobs ride the
spawn env untouched and no effort vocabulary is enforced for it. Knob values are appended as
discrete argv elements (never shell-interpolated), preserving the fixed-argv posture.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

Which = Callable[[str], str | None]
"""A :func:`shutil.which`-shaped lookup: a command name -> its resolved path, or ``None`` if absent."""


@dataclass(frozen=True)
class Harness:
    """One supported TUI harness: a stable ``id``, a display ``name``, the ``command`` to detect on
    ``PATH``, and the fixed ``argv`` used to launch it (at the workspace root, like the plain shell).

    The optional knob-mapping fields (260703-L16) describe how the spawn knobs reach THIS harness's
    CLI. All-default fields mean *no mapping*: the knobs ride the spawn env only
    (``AR_SPAWN_MODEL``/``AR_SPAWN_EFFORT``) and no effort vocabulary is enforced.
    """

    id: str
    name: str
    command: str
    argv: tuple[str, ...]
    # The launch flag the model knob maps onto (``--model <value>``); ``None`` = env-only. Model
    # names are deliberately NOT enum-validated -- they evolve faster than this registry.
    model_flag: str | None = None
    # The launch flag the effort knob maps onto, and the values that flag ACCEPTS. Values outside
    # ``effort_flag_values`` are never put on the flag (the claude CLI warns-then-silently-degrades).
    effort_flag: str | None = None
    effort_flag_values: tuple[str, ...] = ()
    # Effort values the LAUNCH FLAG rejects but the RUNNING SESSION accepts as a command (claude:
    # the interactive ``/effort`` offers ``ultracode``). Delivered post-launch as a pasted session
    # command rendered from ``effort_session_command`` (``{value}`` placeholder), before the brief.
    effort_session_values: tuple[str, ...] = ()
    effort_session_command: str | None = None
    # Where this entry came from. ``registry`` = these curated defaults (a settings OVERRIDE of a
    # builtin keeps it); ``settings`` = a NEW ``orchestration.harnesses`` id. Mapping-less builtins
    # are documented env-only, while a mapping-less settings harness REFUSES the model/effort knobs
    # with guidance -- we never guess a flag that might mean something else (ruling 2026-07-07).
    defined_in: Literal["registry", "settings"] = "registry"


HARNESSES: tuple[Harness, ...] = (
    Harness(
        id="claude",
        name="Claude Code",
        command="claude",
        argv=("claude",),
        model_flag="--model",
        effort_flag="--effort",
        # Empirical vocabulary of the installed claude CLI (probed 2026-07-07): the flag set, plus
        # the session-only mode the interactive /effort command offers ("xhigh + workflows").
        effort_flag_values=("low", "medium", "high", "xhigh", "max"),
        effort_session_values=("ultracode",),
        effort_session_command="/effort {value}",
    ),
    Harness(id="codex", name="Codex", command="codex", argv=("codex",)),
    Harness(id="pi", name="Pi.dev", command="pi", argv=("pi",)),
)
"""The developer-curated max set (2026-06-18): the TUI coding agents AR supports. Display order.

Codex and Pi.dev carry no knob mapping yet: for them the model/effort knobs are env-only (documented
in ``docs/reference/settings-json.md``); growing a mapping is a one-line registry edit here."""

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
    """``harness``'s full known effort vocabulary: launch-flag values + session-level values.

    Empty means the harness has NO known vocabulary (no mapping): effort is env-only and never
    validated for it.
    """
    return harness.effort_flag_values + harness.effort_session_values


def invalid_effort_detail(harness: Harness, effort: str) -> str | None:
    """The dispatch-time refusal text for ``effort``, or ``None`` when the value is fine.

    A value inside the harness's vocabulary (flag OR session set) passes; a MAPPING-LESS BUILTIN
    (codex, pi) passes everything -- its knobs are documented env-only. A mapping-less
    SETTINGS-DEFINED harness refuses the effort knob outright with guidance: declare the mapping or
    use the free-form escape -- explicit over guessing a flag that might mean something else
    (developer ruling 2026-07-07). Everything else is refused LOUDLY, naming the harness and BOTH
    value sets -- the alternative is the claude CLI's warn-then-silently-degrade, which quietly
    downgrades the most reasoning-hungry seats (probed 2026-07-07).
    """
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

    Model names are never enum-validated; the only refusal is a SETTINGS-DEFINED harness with no
    declared ``modelFlag`` -- explicit over guessing (a mapping-less builtin stays env-only).
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
    """The extra argv the model/effort knobs map onto for ``harness`` (empty = env-only).

    The effort flag is emitted only for values the flag ACCEPTS; session-level values (see
    :func:`effort_session_commands`) ride the other vehicle and must never touch the flag. Values
    are discrete argv elements -- no shell, no interpolation (the fixed-argv posture).
    """
    extra: list[str] = []
    if model and harness.model_flag:
        extra += [harness.model_flag, model]
    if effort and harness.effort_flag and effort in harness.effort_flag_values:
        extra += [harness.effort_flag, effort]
    return extra


def effort_session_commands(harness: Harness, effort: str | None = None) -> list[str]:
    """The post-launch session command(s) delivering a session-level effort value (usually empty).

    Claude's ``ultracode`` is the first first-class use: ``/effort ultracode`` is pasted (and
    submitted) into the freshly spawned session BEFORE the dispatch brief.
    """
    if effort and harness.effort_session_command and effort in harness.effort_session_values:
        return [harness.effort_session_command.format(value=effort)]
    return []
