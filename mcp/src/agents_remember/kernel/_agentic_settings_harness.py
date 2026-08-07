"""``orchestration.harnesses`` parser: the effective harness registry.

Entries merge over the builtin table by id; the strict merged pass enforces
completeness and delivery-vehicle pairing, while the per-file pass checks
shapes and unknown keys only.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, replace
from typing import Any

from agents_remember.kernel._agentic_settings_core import (
    KNOWN_HARNESS_ENTRY_FIELDS,
    AgenticSettingsError,
    _refuse_unknown,
    _require_object,
    _require_string,
    _require_string_list,
)
from agents_remember.kernel.harnesses import HARNESSES, Harness


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/_agentic_settings_harness.py:25).
def _parse_harnesses(
    raw: object, *, source: str, strict: bool
) -> tuple[Harness, ...]:  # pragma: no cover
    """``orchestration.harnesses`` (260703-L16): the effective registry, builtin merged with settings.

    Entries merge over the builtin table BY ID: a new id ADDS a harness (it must resolve a
    ``command`` and/or ``argv``; ``command`` defaults to ``argv[0]``, ``argv`` to ``(command,)``);
    a builtin id OVERRIDES the curated defaults per field (its ``argv`` array REPLACES ours -- the
    user launches the harness exactly the way they would run it themselves). Vocabulary fields come
    in delivery-vehicle pairs and must resolve together post-merge: ``effortFlag`` with
    ``effortFlagValues``, ``effortSessionValues`` with ``effortSessionCommand``. Detection still
    applies downstream (the command is probed on PATH at dispatch). ``strict=False`` checks shapes
    and unknown keys only (a single layer may be partial); the merged pass enforces completeness.
    """
    if raw is None:
        return HARNESSES
    block = _require_object(raw, "orchestration.harnesses", source)
    effective: dict[str, Harness] = {harness.id: harness for harness in HARNESSES}
    order: list[str] = [harness.id for harness in HARNESSES]
    for harness_id, value in block.items():
        if not isinstance(harness_id, str) or not harness_id:
            raise AgenticSettingsError(
                f"orchestration.harnesses keys must be non-empty harness ids: {source}"
            )
        parsed = _parse_harness_entry(harness_id, value, source)
        if not strict:
            continue
        base = effective.get(harness_id)
        merged = _merged_harness(harness_id, parsed, base, source)
        if base is None:
            order.append(harness_id)
        effective[harness_id] = merged
    if not strict:
        return HARNESSES
    return tuple(effective[harness_id] for harness_id in order)


@dataclass(frozen=True)
class _HarnessEntry:
    """One raw ``orchestration.harnesses.<id>`` entry, shape-validated; ``None`` = not declared."""

    name: str | None
    command: str | None
    argv: tuple[str, ...] | None
    model_flag: str | None
    effort_flag: str | None
    effort_flag_values: tuple[str, ...] | None
    effort_session_values: tuple[str, ...] | None
    effort_session_command: str | None


def _entry_string(entry: dict[str, Any], key: str, owner: str, source: str) -> str | None:
    if key not in entry:
        return None
    return _require_string(entry[key], f"{owner}.{key}", source)


def _entry_string_list(
    entry: dict[str, Any], key: str, owner: str, source: str
) -> tuple[str, ...] | None:
    if key not in entry:
        return None
    return _require_string_list(entry[key], f"{owner}.{key}", source)


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/_agentic_settings_harness.py:88).
def _parse_harness_entry(
    harness_id: str, value: object, source: str
) -> _HarnessEntry:  # pragma: no cover
    """Shape-validate one harness entry (both passes run this; errors name the owner + file)."""
    owner = f"orchestration.harnesses.{harness_id}"
    entry = _require_object(value, owner, source)
    _refuse_unknown(entry, KNOWN_HARNESS_ENTRY_FIELDS, owner, source)
    argv = _entry_string_list(entry, "argv", owner, source)
    if argv is not None and not argv:
        raise AgenticSettingsError(f"{owner}.argv must be a non-empty command array: {source}")
    return _HarnessEntry(
        name=_entry_string(entry, "name", owner, source),
        command=_entry_string(entry, "command", owner, source),
        argv=argv,
        model_flag=_entry_string(entry, "modelFlag", owner, source),
        effort_flag=_entry_string(entry, "effortFlag", owner, source),
        effort_flag_values=_entry_string_list(entry, "effortFlagValues", owner, source),
        effort_session_values=_entry_string_list(entry, "effortSessionValues", owner, source),
        effort_session_command=_entry_string(entry, "effortSessionCommand", owner, source),
    )


def _resolved_launch(
    parsed: _HarnessEntry, base: Harness | None, owner: str, source: str
) -> tuple[str, tuple[str, ...]]:
    """The merged ``(command, argv)``: entry over builtin, each derivable from the other."""
    if base is None and parsed.command is None and parsed.argv is None:
        raise AgenticSettingsError(
            f"{owner} defines a new harness and must declare command and/or argv "
            f"(see docs/reference/harnesses.md): {source}"
        )
    argv = parsed.argv if parsed.argv is not None else (base.argv if base else None)
    command = parsed.command if parsed.command is not None else (base.command if base else None)
    if command is None and argv is not None:
        command = argv[0]
    if argv is None and command is not None:
        argv = (command,)
    assert command is not None and argv is not None
    return command, argv


def _merged_harness(
    harness_id: str, parsed: _HarnessEntry, base: Harness | None, source: str
) -> Harness:
    """One effective harness: the entry's declared fields over the builtin defaults (or fresh)."""
    owner = f"orchestration.harnesses.{harness_id}"
    command, argv = _resolved_launch(parsed, base, owner, source)
    fallback = (
        base
        if base is not None
        else Harness(
            id=harness_id, name=harness_id, command=command, argv=argv, defined_in="settings"
        )
    )
    overrides: dict[str, Any] = {"command": command, "argv": argv}
    declared = {
        "name": parsed.name,
        "model_flag": parsed.model_flag,
        "effort_flag": parsed.effort_flag,
        "effort_flag_values": parsed.effort_flag_values,
        "effort_session_values": parsed.effort_session_values,
        "effort_session_command": parsed.effort_session_command,
    }
    for field_name, declared_value in declared.items():
        if declared_value is not None:
            overrides[field_name] = declared_value
    if parsed.effort_flag_values is not None:
        # A settings-declared vocabulary is authoritative even when it overrides the dynamic
        # Codex builtin. Silently inheriting ``non-empty`` would accept values the user's custom
        # delivery vehicle explicitly did not declare.
        overrides["effort_validation"] = "enumerated"
    if (
        base is not None
        and parsed.effort_flag is not None
        and parsed.effort_flag != base.effort_flag
    ):
        # The Codex builtin's value template belongs to its ``--config`` vehicle. Replacing that
        # flag through settings restores the ordinary two-argv-element mapping instead of leaking
        # ``model_reasoning_effort=...`` into an unrelated custom flag.
        overrides["effort_flag_value_template"] = None
    merged = replace(fallback, **overrides)
    _refuse_unpaired_vehicles(merged, owner, source)
    _refuse_bad_effort_template(merged, owner, source)
    return merged


def _refuse_unpaired_vehicles(merged: Harness, owner: str, source: str) -> None:
    """Post-merge pair rules: every declared vocabulary must resolve its delivery vehicle."""
    if bool(merged.effort_flag) != bool(merged.effort_flag_values):
        raise AgenticSettingsError(
            f"{owner}: effortFlag and effortFlagValues must be declared together "
            f"(a flag without a vocabulary reintroduces the silent-degrade risk): {source}"
        )
    if bool(merged.effort_session_values) != bool(merged.effort_session_command):
        raise AgenticSettingsError(
            f"{owner}: effortSessionValues and effortSessionCommand must be declared "
            f"together (session values need their delivery command): {source}"
        )


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/_agentic_settings_harness.py:186).
def _refuse_bad_effort_template(
    merged: Harness, owner: str, source: str
) -> None:  # pragma: no cover
    """The ``effortSessionCommand`` template must render with ONLY ``{value}`` (260703-L18 finding 4).

    ``serving.harnesses.effort_session_commands`` renders it via ``.format(value=…)`` at spawn; a stray
    replacement field (``/set {mode}={value}``), a positional ``{}``, or an unmatched brace raises a
    RAW ``KeyError``/``ValueError``/``IndexError`` there instead of the structured refusal every other
    bad knob gets. A builtin override may supply JUST the command (merged over the builtin's session
    values), so the check lives post-merge next to the pairing rule -- once validated here, the raw
    error at ``serving/harnesses.py`` is unreachable from settings. Only checked when a template is
    present (an absent one has already passed the pairing rule)."""
    template = merged.effort_session_command
    if template is None:
        return
    try:
        field_names = [name for _, name, _, _ in string.Formatter().parse(template)]
    except ValueError as error:  # an unmatched / stray brace: not a parseable format template
        raise AgenticSettingsError(
            f"{owner}: effortSessionCommand {template!r} is not a valid format template "
            f"({error}); it may contain no replacement field other than {{value}}: {source}"
        ) from error
    unexpected = sorted({name for name in field_names if name is not None and name != "value"})
    if unexpected:
        shown = ", ".join(repr(name) for name in unexpected)
        raise AgenticSettingsError(
            f"{owner}: effortSessionCommand {template!r} may reference only the {{value}} field; "
            f"unexpected replacement field(s): {shown}: {source}"
        )
    try:
        template.format(value="probe")
    except (KeyError, IndexError, ValueError) as error:
        raise AgenticSettingsError(
            f"{owner}: effortSessionCommand {template!r} failed to render with value=… "
            f"({error!r}): {source}"
        ) from error
