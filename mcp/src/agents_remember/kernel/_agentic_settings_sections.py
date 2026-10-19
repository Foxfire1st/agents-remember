"""``orchestration`` section parsers: loops, roles, concurrency, harness ids, spawn, and the
quality gate."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agents_remember.kernel._agentic_settings_core import (
    CAPABILITY_IDS,
    COMPLEXITY_SCALE,
    DEFAULT_COMMIT_CHECK_TIMEOUT_SECONDS,
    DEFAULT_LOOP_MAX_ROUNDS,
    DEFAULT_LOOP_PER_LEVEL,
    DEFAULT_REVIEWER_REUSE,
    HARNESS_IDS,
    KNOWN_CONCURRENCY_FIELDS,
    KNOWN_LANDING_FIELDS,
    KNOWN_LOOP_COMPLEXITY_FIELDS,
    KNOWN_LOOP_DEFAULTS_FIELDS,
    KNOWN_LOOP_LEVEL_FIELDS,
    KNOWN_LOOP_LEVELS,
    KNOWN_LOOPS_FIELDS,
    KNOWN_QUALITY_GATE_FIELDS,
    KNOWN_ROLE_KNOB_FIELDS,
    KNOWN_ROLE_LAUNCH_FIELDS,
    KNOWN_ROLES,
    KNOWN_SPAWN_FIELDS,
    UNUSED_HARNESS_ENTRY_FIELDS,
    UNUSED_ROLE_KNOB_FIELDS,
    AgenticSettingsError,
    ConcurrencySettings,
    LandingSettings,
    LoopComplexity,
    LoopDefaults,
    LoopSettings,
    QualityGateSettings,
    RoleKnobs,
    RoleStepUp,
    _refuse_unknown,
    _require_harness_id,
    _require_object,
    _require_positive_int,
    _require_string,
    role_feature_values,
)


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/_agentic_settings_sections.py:58).
def _parse_loops(raw: object, *, source: str) -> LoopSettings:  # pragma: no cover
    if raw is None:
        return LoopSettings()
    loops = _require_object(raw, "orchestration.loops", source)
    _refuse_unknown(loops, KNOWN_LOOPS_FIELDS, "orchestration.loops", source)
    defaults = _parse_loop_defaults(loops.get("defaults"), source=source)
    per_level = dict(DEFAULT_LOOP_PER_LEVEL)
    per_level.update(
        _parse_loop_levels(
            loops.get("perLevel"), owner="orchestration.loops.perLevel", source=source
        )
    )
    per_master: dict[str, dict[str, str]] = {}
    raw_per_master = loops.get("perMaster")
    if raw_per_master is not None:
        masters = _require_object(raw_per_master, "orchestration.loops.perMaster", source)
        for master, levels in masters.items():
            if not isinstance(master, str) or not master:
                raise AgenticSettingsError(
                    f"orchestration.loops.perMaster keys must be non-empty master "
                    f"task names: {source}"
                )
            per_master[master] = _parse_loop_levels(
                levels,
                owner=f"orchestration.loops.perMaster.{master}",
                source=source,
            )
    return LoopSettings(defaults=defaults, per_level=per_level, per_master=per_master)


def _parse_loop_defaults(raw: object, *, source: str) -> LoopDefaults:
    if raw is None:
        return LoopDefaults()
    defaults = _require_object(raw, "orchestration.loops.defaults", source)
    _refuse_unknown(defaults, KNOWN_LOOP_DEFAULTS_FIELDS, "orchestration.loops.defaults", source)
    max_rounds = DEFAULT_LOOP_MAX_ROUNDS
    if "maxRounds" in defaults:
        # MIK-R116: any positive whole number is taken; the refusal of a number
        # above the old hard limit is gone.
        max_rounds = _require_positive_int(
            defaults["maxRounds"], "orchestration.loops.defaults.maxRounds", source
        )
    reviewer_reuse = DEFAULT_REVIEWER_REUSE
    if "reviewerReuse" in defaults:
        reviewer_reuse = _require_string(
            defaults["reviewerReuse"],
            "orchestration.loops.defaults.reviewerReuse",
            source,
        )
    complexity = _parse_loop_complexity(defaults.get("complexity"), source=source)
    return LoopDefaults(max_rounds=max_rounds, reviewer_reuse=reviewer_reuse, complexity=complexity)


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/_agentic_settings_sections.py:109).
def _parse_loop_complexity(raw: object, *, source: str) -> LoopComplexity:  # pragma: no cover
    if raw is None:
        return LoopComplexity()
    complexity = _require_object(raw, "orchestration.loops.defaults.complexity", source)
    _refuse_unknown(
        complexity,
        KNOWN_LOOP_COMPLEXITY_FIELDS,
        "orchestration.loops.defaults.complexity",
        source,
    )
    parsed = LoopComplexity()
    values: dict[str, str] = {
        "full_loop_at": parsed.full_loop_at,
        "builder_at": parsed.builder_at,
    }
    for json_key, attr in (("fullLoopAt", "full_loop_at"), ("builderAt", "builder_at")):
        if json_key not in complexity:
            continue
        owner = f"orchestration.loops.defaults.complexity.{json_key}"
        value = _require_string(complexity[json_key], owner, source)
        if value not in COMPLEXITY_SCALE:
            allowed = ", ".join(COMPLEXITY_SCALE)
            raise AgenticSettingsError(
                f"{owner} must be one of: {allowed}; got {value!r}: {source}"
            )
        values[attr] = value
    return LoopComplexity(**values)


def _parse_loop_levels(raw: object, *, owner: str, source: str) -> dict[str, str]:
    if raw is None:
        return {}
    levels = _require_object(raw, owner, source)
    _refuse_unknown(levels, KNOWN_LOOP_LEVELS, owner, source)
    parsed: dict[str, str] = {}
    for level, value in levels.items():
        entry = _require_object(value, f"{owner}.{level}", source)
        _refuse_unknown(entry, KNOWN_LOOP_LEVEL_FIELDS, f"{owner}.{level}", source)
        # Loop postures are model-interpreted doctrine names (scored,
        # seam-required, none, strategist, ...), deliberately not a closed set.
        parsed[level] = _require_string(entry.get("loop"), f"{owner}.{level}.loop", source)
    return parsed


def _parse_roles(
    raw: object,
    *,
    source: str,
    owner: str = "orchestration.roles",
) -> dict[str, RoleKnobs]:
    if raw is None:
        return {}
    roles = _resolve_role_keys(raw, owner=owner, source=source)
    parsed: dict[str, RoleKnobs] = {}
    for role, value in roles.items():
        knobs = _require_object(value, f"{owner}.{role}", source)
        _refuse_unknown(
            knobs,
            KNOWN_ROLE_KNOB_FIELDS | UNUSED_ROLE_KNOB_FIELDS | {"stepUp"},
            f"{owner}.{role}",
            source,
        )
        parsed[role] = _role_choices(knobs, f"{owner}.{role}", source)
        if "stepUp" in knobs:
            parsed[role] = replace(
                parsed[role],
                step_up=_parse_step_up(knobs["stepUp"], role, f"{owner}.{role}.stepUp", source),
            )
    return parsed


def _role_choices(knobs: dict[str, Any], owner: str, source: str) -> RoleKnobs:
    text = {
        key: _require_string(knobs[key], f"{owner}.{key}", source)
        for key in ("harness", "model", "effort")
        if key in knobs
    }
    return RoleKnobs(
        harness=text.get("harness"),
        model=text.get("model"),
        effort=text.get("effort"),
        service_tier=_parse_service_tier(knobs, owner, source),
        mode=_optional_role_text(knobs, "mode", owner, source),
        notes=_optional_role_text(knobs, "notes", owner, source),
        feature_values=_parse_feature_values(
            knobs.get("featureValues", {}), f"{owner}.featureValues", source
        ),
    )


def _parse_step_up(raw: object, role: str, owner: str, source: str) -> RoleStepUp:
    row = _require_object(raw, owner, source)
    _refuse_unknown(row, KNOWN_ROLE_LAUNCH_FIELDS | {"afterTries"}, owner, source)
    if "afterTries" in row and role not in {"worker", "curator"}:
        raise AgenticSettingsError(
            f"{owner}.afterTries is only counted for worker and curator: {source}"
        )
    after = (
        _require_positive_int(row["afterTries"], f"{owner}.afterTries", source)
        if "afterTries" in row
        else 3
        if role in {"worker", "curator"}
        else None
    )
    choices = _role_choices(row, owner, source)
    role_feature_values(choices, f"{source}: {owner}")
    return RoleStepUp(choices=choices, after_tries=after, source=source, key=owner)


def _resolve_role_keys(raw: object, *, owner: str, source: str) -> dict[str, Any]:
    """Admit the earlier role key without rewriting the settings file."""
    roles = _require_object(raw, owner, source)
    _refuse_unknown(roles, KNOWN_ROLES | CAPABILITY_IDS | {"system-specialist"}, owner, source)
    if "system-specialist" not in roles:
        return roles
    if "investigator" in roles and roles["investigator"] != roles["system-specialist"]:
        raise AgenticSettingsError(
            f"{owner}.system-specialist and {owner}.investigator have different content; "
            f"keep one key or make their content equal: {source}"
        )
    normalized = dict(roles)
    normalized["investigator"] = normalized.pop("system-specialist")
    return normalized


def _optional_role_text(knobs: dict[str, Any], key: str, owner: str, source: str) -> str | None:
    if key not in knobs:
        return None
    value = knobs[key]
    if not isinstance(value, str):
        raise AgenticSettingsError(f"{owner}.{key} must be text: {source}")
    return value if value.strip() else None


def _parse_feature_values(raw: object, owner: str, source: str) -> dict[str, str | bool]:
    values = _require_object(raw, owner, source)
    for key, value in values.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or not (isinstance(value, bool) or (isinstance(value, str) and value.strip()))
        ):
            raise AgenticSettingsError(
                f"{owner}.{key} must be a boolean or nonempty option id: {source}"
            )
    return dict(values)


def _parse_service_tier(knobs: dict[str, Any], owner: str, source: str) -> str | None:
    if "serviceTier" not in knobs:
        return None
    value = _require_string(knobs["serviceTier"], f"{owner}.serviceTier", source)
    if not value.strip():
        raise AgenticSettingsError(f"{owner}.serviceTier must be a nonempty string ({source})")
    return value


def _parse_roles_per_level(raw: object, *, source: str) -> dict[str, dict[str, RoleKnobs]]:
    """``orchestration.rolesPerLevel`` (260703-L16, ruling 2026-07-07T08:15): per-level role knobs.

    ``{leaf|master|portfolio: {role: knob-overrides}}`` -- the level vocabulary matches
    ``loops.perLevel`` so the two per-level families stay congruent; each override deep-merges over
    the flat ``orchestration.roles`` default at dispatch (:meth:`AgenticSettings.resolved_role_knobs`).
    Additive: absent key = no per-level overrides (existing files unchanged).
    """
    if raw is None:
        return {}
    levels = _require_object(raw, "orchestration.rolesPerLevel", source)
    _refuse_unknown(levels, KNOWN_LOOP_LEVELS, "orchestration.rolesPerLevel", source)
    parsed: dict[str, dict[str, RoleKnobs]] = {}
    for level, value in levels.items():
        parsed[level] = _parse_roles(
            value,
            source=source,
            owner=f"orchestration.rolesPerLevel.{level}",
        )
    return parsed


def _parse_concurrency(raw: object, *, source: str) -> ConcurrencySettings:
    if raw is None:
        return ConcurrencySettings()
    concurrency = _require_object(raw, "orchestration.concurrency", source)
    _refuse_unknown(concurrency, KNOWN_CONCURRENCY_FIELDS, "orchestration.concurrency", source)
    parsed: dict[str, int | None] = {
        "max_parallel_masters": None,
        "max_parallel_leaves": None,
        "max_sub_agents": None,
    }
    for json_key, attr in (
        ("maxParallelMasters", "max_parallel_masters"),
        ("maxParallelLeaves", "max_parallel_leaves"),
        ("maxSubAgents", "max_sub_agents"),
    ):
        if json_key in concurrency:
            parsed[attr] = _require_positive_int(
                concurrency[json_key], f"orchestration.concurrency.{json_key}", source
            )
    return ConcurrencySettings(**parsed)


def _parse_harnesses(raw: object, *, source: str) -> tuple[str, ...]:
    """``orchestration.harnesses``: the effective harness ids.

    The builtin ids come first; an entry under a new id ADDS that id. An entry is an object;
    the keys an earlier build read from it are accepted and not read.
    """
    if raw is None:
        return HARNESS_IDS
    block = _require_object(raw, "orchestration.harnesses", source)
    ids = list(HARNESS_IDS)
    for harness_id, value in block.items():
        if not isinstance(harness_id, str) or not harness_id:
            raise AgenticSettingsError(
                f"orchestration.harnesses keys must be non-empty harness ids: {source}"
            )
        owner = f"orchestration.harnesses.{harness_id}"
        entry = _require_object(value, owner, source)
        _refuse_unknown(entry, UNUSED_HARNESS_ENTRY_FIELDS, owner, source)
        if harness_id not in ids:
            ids.append(harness_id)
    return tuple(ids)


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/_agentic_settings_sections.py:369).
def _parse_spawn(
    raw: object, *, source: str, harness_ids: tuple[str, ...] | None
) -> str | None:  # pragma: no cover
    if raw is None:
        return None
    spawn = _require_object(raw, "orchestration.spawn", source)
    _refuse_unknown(spawn, KNOWN_SPAWN_FIELDS, "orchestration.spawn", source)
    if "harness" not in spawn:
        return None
    return _require_harness_id(spawn["harness"], "orchestration.spawn.harness", source, harness_ids)


def _parse_landing(raw: object, *, source: str) -> LandingSettings:
    """``orchestration.landing``: the landing commit-check knobs (MIK-R152).

    Absent -> the default commit-check bound. Unknown keys fail loud like every
    other orchestration family.
    """
    if raw is None:
        return LandingSettings()
    block = _require_object(raw, "orchestration.landing", source)
    _refuse_unknown(block, KNOWN_LANDING_FIELDS, "orchestration.landing", source)
    timeout_seconds = DEFAULT_COMMIT_CHECK_TIMEOUT_SECONDS
    if "commitCheckTimeoutSeconds" in block:
        timeout_seconds = _require_positive_int(
            block["commitCheckTimeoutSeconds"],
            "orchestration.landing.commitCheckTimeoutSeconds",
            source,
        )
    return LandingSettings(commit_check_timeout_seconds=timeout_seconds)


def _parse_quality_gate(raw: object, *, source: str) -> QualityGateSettings:
    """``orchestration.qualityGate``: the full gate's memory cap in bytes.

    Absent -> executor runtime-managed RAM and swap. Executor identity belongs to the
    repository certification profile. Unknown keys fail loud like every other orchestration
    family; a null at the family key is refused by
    :func:`_refuse_null_families` before this parser runs.
    """
    if raw is None:
        return QualityGateSettings()
    block = _require_object(raw, "orchestration.qualityGate", source)
    _refuse_unknown(block, KNOWN_QUALITY_GATE_FIELDS, "orchestration.qualityGate", source)
    memory_cap_bytes = None
    if "memoryCapBytes" in block:
        memory_cap_bytes = _require_positive_int(
            block["memoryCapBytes"],
            "orchestration.qualityGate.memoryCapBytes",
            source,
        )
    return QualityGateSettings(memory_cap_bytes=memory_cap_bytes)
