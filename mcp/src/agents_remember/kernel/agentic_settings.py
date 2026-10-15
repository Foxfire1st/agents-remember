"""Two-layer AGENTIC settings loader (260703-L13 settings unification).

The agentic settings family -- everything under the top-level ``orchestration``
key: gate delegation, the three-party-loop knobs, per-role knob overrides,
concurrency caps, the spawn harness preference, and the harness ids a file adds
(``orchestration.harnesses``) -- lives in
TWO JSON files that are merged on every read; the full quality gate's optional
hard-cap override is ``orchestration.qualityGate``:

- USER: ``~/.config/agents-remember/settings.json`` (or under ``XDG_CONFIG_HOME``)
- LOCAL:  ``<code_repo>/system/settings.json`` (optional; merged OVER global)

Semantics (``docs/reference/settings-json.md`` is the schema reference):

- Deep merge at leaf-key granularity: a local scalar or object leaf overrides
  the global one; arrays REPLACE (never concatenate).
- Unknown keys anywhere inside the ``orchestration.*`` family FAIL LOUD naming
  the offending file -- the MCP-authority-file discipline, extended. This also
  fixes the historic silent drop of ``orchestration.roles``/``concurrency``.
- Unknown TOP-LEVEL families are tolerated-not-parsed: the same user
  file doubles as the c-08 memory-settings fallback, and it is the earmarked
  future home of further families (``contextProviders`` first in line, gate
  amendment 2026-07-06), so the fail-loud scope must not foreclose them.
- Absent files (or an absent ``orchestration`` key) mean the documented
  defaults: all-human gate delegation, the L12 loop defaults, no role
  overrides, no concurrency caps, no spawn harness preference.
- Read PER-USE: consumers call :func:`load_agentic_settings` at each use so a
  settings edit takes effect on the next use without a server restart. The ONE
  boot-snapshot consumer is the runtime-config loader's ``gateDelegation`` (enforcement
  plumbing keeps its boot-cached shape; a change needs a restart -- documented).

- Keys an earlier build read and this one does not (``UNUSED_*`` in the core
  module) load without an error and are not read; ``AgenticSettings.unused_keys``
  names the ones the merged files hold (MIK-R113 rule 4).

Doctrine floors are NOT knobs: no key here touches the master-exit seam gate or
the strategist's mandatory pre-run (L12 ruling; restated in the schema doc).

The typed models, constants, validators, and the per-section parsers live in
responsibility-split sibling modules; this module owns the loader and the
orchestration block assembly and re-exports the public surface.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.kernel._agentic_settings_core import (
    COMPLEXITY_SCALE,
    DEFAULT_COMMIT_CHECK_TIMEOUT_SECONDS,
    DEFAULT_LOOP_MAX_ROUNDS,
    DEFAULT_LOOP_PER_LEVEL,
    DEFAULT_REVIEWER_REUSE,
    DEFAULT_ROLE_MESSAGE_LIMIT,
    HARNESS_IDS,
    KNOWN_CONCURRENCY_FIELDS,
    KNOWN_GATE_DELEGATION_FIELDS,
    KNOWN_GATE_POLICY_KIND_FIELDS,
    KNOWN_LANDING_FIELDS,
    KNOWN_LOOP_COMPLEXITY_FIELDS,
    KNOWN_LOOP_DEFAULTS_FIELDS,
    KNOWN_LOOP_LEVEL_FIELDS,
    KNOWN_LOOP_LEVELS,
    KNOWN_LOOPS_FIELDS,
    KNOWN_ORCHESTRATION_FIELDS,
    KNOWN_QUALITY_GATE_FIELDS,
    KNOWN_ROLE_KNOB_FIELDS,
    KNOWN_ROLES,
    KNOWN_SPAWN_FIELDS,
    NON_PERMISSION_FEATURES,
    UNUSED_HARNESS_ENTRY_FIELDS,
    UNUSED_ORCHESTRATION_FIELDS,
    UNUSED_ROLE_KNOB_FIELDS,
    AgenticSettings,
    AgenticSettingsError,
    ConcurrencySettings,
    LandingSettings,
    LoopComplexity,
    LoopDefaults,
    LoopSettings,
    QualityGateSettings,
    RoleChoiceOrigin,
    RoleKnobs,
    _refuse_unknown,
    _require_harness_id,
    _require_object,
    _require_positive_int,
    _require_string,
    agentic_settings_path,
    agentic_settings_upgrade_command,
    default_agentic_settings_seed,
    default_agentic_settings_seed_text,
    merge_settings,
    require_user_agentic_settings_path,
    role_feature_values,
    unused_orchestration_keys,
    user_agentic_settings_path,
)
from agents_remember.kernel._agentic_settings_policy import (
    _parse_gate_policy_rule,
    parse_gate_delegation,
)
from agents_remember.kernel._agentic_settings_sections import (
    _parse_concurrency,
    _parse_harnesses,
    _parse_landing,
    _parse_loop_complexity,
    _parse_loop_defaults,
    _parse_loop_levels,
    _parse_loops,
    _parse_quality_gate,
    _parse_roles,
    _parse_roles_per_level,
    _parse_spawn,
    _resolve_role_keys,
)
from agents_remember.kernel.recorded_reads import observed_exists, observed_text

__all__ = [
    "COMPLEXITY_SCALE",
    "DEFAULT_COMMIT_CHECK_TIMEOUT_SECONDS",
    "DEFAULT_LOOP_MAX_ROUNDS",
    "DEFAULT_LOOP_PER_LEVEL",
    "DEFAULT_REVIEWER_REUSE",
    "DEFAULT_ROLE_MESSAGE_LIMIT",
    "HARNESS_IDS",
    "KNOWN_CONCURRENCY_FIELDS",
    "KNOWN_GATE_DELEGATION_FIELDS",
    "KNOWN_GATE_POLICY_KIND_FIELDS",
    "KNOWN_LANDING_FIELDS",
    "KNOWN_LOOPS_FIELDS",
    "KNOWN_LOOP_COMPLEXITY_FIELDS",
    "KNOWN_LOOP_DEFAULTS_FIELDS",
    "KNOWN_LOOP_LEVELS",
    "KNOWN_LOOP_LEVEL_FIELDS",
    "KNOWN_QUALITY_GATE_FIELDS",
    "KNOWN_ROLES",
    "KNOWN_ROLE_KNOB_FIELDS",
    "KNOWN_SPAWN_FIELDS",
    "UNUSED_HARNESS_ENTRY_FIELDS",
    "UNUSED_ORCHESTRATION_FIELDS",
    "UNUSED_ROLE_KNOB_FIELDS",
    "AgenticSettings",
    "AgenticSettingsError",
    "ConcurrencySettings",
    "LandingSettings",
    "LoopComplexity",
    "LoopDefaults",
    "LoopSettings",
    "QualityGateSettings",
    "RoleKnobs",
    "_parse_concurrency",
    "_parse_gate_policy_rule",
    "_parse_harnesses",
    "_parse_landing",
    "_parse_loop_complexity",
    "_parse_loop_defaults",
    "_parse_loop_levels",
    "_parse_loops",
    "_parse_quality_gate",
    "_parse_roles",
    "_parse_roles_per_level",
    "_parse_spawn",
    "_require_harness_id",
    "_require_object",
    "_require_positive_int",
    "_require_string",
    "agentic_settings_path",
    "agentic_settings_upgrade_command",
    "default_agentic_settings_seed",
    "default_agentic_settings_seed_text",
    "load_agentic_settings",
    "merge_settings",
    "parse_gate_delegation",
    "require_user_agentic_settings_path",
    "user_agentic_settings_path",
]


def load_agentic_settings(
    coordination_root: Path,
    repo_root: Path | None = None,
) -> AgenticSettings:
    """Read + merge the global (and optional repo-local) agentic settings, per use.

    Each present file's ``orchestration`` block is validated individually first
    so key/type errors name the offending file; the merged block is then parsed
    into the typed models. Absent files contribute nothing (defaults apply).
    """
    layers: list[tuple[Path, dict[str, Any]]] = []
    role_alias_notices: list[str] = []
    paths = [("global", require_user_agentic_settings_path(coordination_root))]
    if repo_root is not None:
        paths.append(("local", agentic_settings_path(repo_root)))
    for layer, path in paths:
        data = _read_settings_file(path)
        if data is None:
            continue
        orchestration, aliases = _validated_orchestration_block(data, path)
        role_alias_notices.extend(f"{path}: {key}" for key in aliases)
        if layer == "local":
            _refuse_repository_role_authority(orchestration, path)
        if layer == "local" and "gateDelegation" in orchestration:
            # Gate posture is workspace-wide enforcement state: the boot snapshot
            # reads the GLOBAL file only, so a repo-local value would validate and
            # then silently do nothing -- a fail-open shape. Refuse it loudly.
            raise AgenticSettingsError(
                "orchestration.gateDelegation is global-layer only (the boot "
                "snapshot reads the user's settings file); remove it from the "
                f"repo-local settings: {path}"
            )
        layers.append((path, orchestration))

    merged: dict[str, Any] = {}
    for _, orchestration in layers:
        merged = merge_settings(merged, orchestration)
    sources = tuple(path for path, _ in layers)
    source = _source_label(sources)
    settings = _parse_orchestration(merged, source=source, sources=sources)
    _attach_role_origins(settings, layers)
    for role, knobs in settings.roles.items():
        role_feature_values(knobs, f"{source}: orchestration.roles.{role}")
    for level, roles in settings.roles_per_level.items():
        for role in roles:
            role_feature_values(
                settings.resolved_role_knobs(role, level),
                f"{source}: orchestration.rolesPerLevel.{level}.{role}",
            )
    _warn_role_aliases(role_alias_notices)
    return settings


def _warn_role_aliases(notices: list[str]) -> None:
    if notices:
        warnings.warn(
            "system-specialist is the earlier id of investigator; settings keys apply to "
            "investigator: " + "; ".join(notices),
            UserWarning,
            stacklevel=3,
        )


def _attach_role_origins(
    settings: AgenticSettings, layers: list[tuple[Path, dict[str, Any]]]
) -> None:
    """Carry each authored choice's binding without changing the existing value merge."""
    flat: dict[str, dict[str, RoleChoiceOrigin]] = {}
    levels: dict[str, dict[str, dict[str, RoleChoiceOrigin]]] = {}
    provider = None
    for rank, (path, raw) in enumerate(layers):
        provider = raw.get("spawn", {}).get("harness", provider)
        for role, row in raw.get("roles", {}).items():
            flat[role] = _role_row_origins(
                flat.get(role, {}),
                row,
                source=path,
                owner=f"orchestration.roles.{role}",
                scope=(provider, rank),
            )
        for level, roles in raw.get("rolesPerLevel", {}).items():
            scope = levels.setdefault(level, {})
            for role, row in roles.items():
                authored = _role_row_origins(
                    {**flat.get(role, {}), **scope.get(role, {})},
                    row,
                    source=path,
                    owner=f"orchestration.rolesPerLevel.{level}.{role}",
                    scope=(provider, len(layers) + rank),
                )
                # Inherited flat fields supply this layer's scope but are not level overrides.
                scope[role] = {
                    key: origin
                    for key, origin in authored.items()
                    if origin.precedence >= len(layers)
                }
    for role, knobs in settings.roles.items():
        settings.roles[role] = replace(knobs, origins=flat.get(role, {}))
    for level, roles in settings.roles_per_level.items():
        for role, knobs in roles.items():
            roles[role] = replace(knobs, origins=levels[level][role])


def _role_row_origins(
    prior: dict[str, RoleChoiceOrigin],
    row: dict[str, Any],
    *,
    source: Path,
    owner: str,
    scope: tuple[str | None, int],
) -> dict[str, RoleChoiceOrigin]:
    fallback_provider, rank = scope
    provider = row.get("harness") or (
        prior["harness"].provider if "harness" in prior else fallback_provider
    )
    model = row.get("model") or (
        prior["model"].model
        if "model" in prior and prior["model"].provider in (None, provider)
        else None
    )
    result = dict(prior)
    for key in ("harness", "model", "effort", "mode", "serviceTier", "notes"):
        if key in row and row[key].strip():
            result[key] = RoleChoiceOrigin(str(source), f"{owner}.{key}", provider, model, rank)
    for key in row.get("featureValues", {}):
        result[f"featureValues.{key}"] = RoleChoiceOrigin(
            str(source), f"{owner}.featureValues.{key}", provider, model, rank
        )
    return result


def _refuse_repository_role_authority(raw: dict[str, Any], path: Path) -> None:
    families = [("orchestration.roles", raw.get("roles", {}))]
    families.extend(
        (f"orchestration.rolesPerLevel.{level}", roles)
        for level, roles in raw.get("rolesPerLevel", {}).items()
    )
    for owner, roles in families:
        for role, knobs in roles.items():
            keys = [key for key in ("mode", "notes") if key in knobs]
            keys.extend(
                f"featureValues.{key}"
                for key in knobs.get("featureValues", {})
                if key not in NON_PERMISSION_FEATURES
            )
            if keys:
                raise AgenticSettingsError(
                    f"{owner}.{role}.{keys[0]} is user-layer only; remove it from {path} and configure the user's settings at {user_agentic_settings_path()} or host profile"
                )


def _read_settings_file(path: Path) -> dict[str, Any] | None:
    if not observed_exists(path):
        return None
    try:
        data = json.loads(observed_text(path))
    except json.JSONDecodeError as error:
        raise AgenticSettingsError(
            f"cannot parse agentic settings JSON: {path}: {error}"
        ) from error
    if not isinstance(data, dict):
        raise AgenticSettingsError(f"agentic settings must be a JSON object: {path}")
    return data


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/agentic_settings.py:179).
def _validated_orchestration_block(
    data: dict[str, Any], path: Path
) -> tuple[dict[str, Any], list[str]]:  # pragma: no cover
    """One file's ``orchestration`` block, fully validated so errors name ``path``.

    Top-level keys other than ``orchestration`` are tolerated-not-parsed (other
    families own them -- see the module docstring).
    """
    raw = data.get("orchestration")
    if raw is None:
        return {}, []
    source = str(path)
    if not isinstance(raw, dict):
        raise AgenticSettingsError(f"orchestration settings must be an object: {source}")
    _refuse_null_families(raw, source)
    # Normalize each layer before deep merge, so the two spellings share precedence.
    raw = dict(raw)
    aliases: list[str] = []
    if "roles" in raw:
        roles = _require_object(raw["roles"], "orchestration.roles", source)
        if "system-specialist" in roles:
            aliases.append("orchestration.roles.system-specialist")
        raw["roles"] = _resolve_role_keys(roles, owner="orchestration.roles", source=source)
    if "rolesPerLevel" in raw:
        levels = _require_object(raw["rolesPerLevel"], "orchestration.rolesPerLevel", source)
        normalized_levels = {}
        for level, value in levels.items():
            owner = f"orchestration.rolesPerLevel.{level}"
            roles = _require_object(value, owner, source)
            if "system-specialist" in roles:
                aliases.append(f"{owner}.system-specialist")
            normalized_levels[level] = _resolve_role_keys(roles, owner=owner, source=source)
        raw["rolesPerLevel"] = normalized_levels
    # strict=False: one LAYER may legitimately be partial (referencing a harness id the OTHER
    # layer declares), so per-file validation checks shapes/keys only; the cross-reference
    # rule runs on the MERGED block in load_agentic_settings.
    _parse_orchestration(raw, source=source, sources=(path,), strict=False)
    return raw, aliases


def _refuse_null_families(raw: dict[str, Any], source: str) -> None:
    """A JSON ``null`` at a known ``orchestration`` family key is REFUSED (260703-L18 finding 6,
    developer-ruled ``null`` = refuse, not reset-to-default).

    ``None`` reads as *absent* to every family parser, and ``merge_settings`` REPLACES (never
    deep-merges) a non-dict override -- so ``"concurrency": null`` in the repo-local layer would
    SILENTLY wipe the global caps, ``"roles": null`` -> ``{}``, ``"loops": null`` -> the defaults.
    It was the one scalar collision that did not fail loud, violating both documented invariants
    (deep-merge + fail-loud). Refuse it in EITHER layer, naming the offending file; ``gateDelegation``
    keeps its own stronger repo-local presence refusal (checked after this in ``load_agentic_settings``).
    The fix the guidance names: remove the key (absence inherits the global value) or give it a real
    object."""
    null_families = sorted(
        key for key in KNOWN_ORCHESTRATION_FIELDS if raw.get(key) is None and key in raw
    )
    if null_families:
        offending = ", ".join(f"orchestration.{key}" for key in null_families)
        raise AgenticSettingsError(
            f"{offending} is null; a null at a known orchestration family key is refused because it "
            f"would silently wipe the merged value. Remove the key to inherit the global value: {source}"
        )


def _source_label(sources: tuple[Path, ...]) -> str:
    if not sources:
        return "agentic settings defaults (no settings file present)"
    return " merged with ".join(str(path) for path in sources)


def _parse_orchestration(
    raw: dict[str, Any],
    *,
    source: str,
    sources: tuple[Path, ...],
    strict: bool = True,
) -> AgenticSettings:
    """Parse one ``orchestration`` block into the typed settings.

    ``strict=True`` (the merged block) additionally enforces the cross-layer rule: harness-id
    membership for ``spawn.harness`` against the EFFECTIVE id set. The per-file pass runs
    ``strict=False`` (shapes + unknown keys only) because a single layer may be a partial
    override of the other.
    """
    _refuse_unknown(
        raw, KNOWN_ORCHESTRATION_FIELDS | UNUSED_ORCHESTRATION_FIELDS, "orchestration", source
    )
    harness_ids = _parse_harnesses(raw.get("harnesses"), source=source)
    gate_raw = raw.get("gateDelegation")
    gate_policy, at_seams = parse_gate_delegation(gate_raw, source=source)
    limit_raw = raw.get("roleMessageLimit")
    role_message_limit = (
        DEFAULT_ROLE_MESSAGE_LIMIT
        if limit_raw is None
        else _require_positive_int(limit_raw, "orchestration.roleMessageLimit", source)
    )
    return AgenticSettings(
        gate_policy=gate_policy,
        require_reviewer_verdict_at_seams=at_seams,
        gate_delegation_configured=gate_raw is not None,
        loops=_parse_loops(raw.get("loops"), source=source),
        roles=_parse_roles(raw.get("roles"), source=source),
        roles_per_level=_parse_roles_per_level(raw.get("rolesPerLevel"), source=source),
        concurrency=_parse_concurrency(raw.get("concurrency"), source=source),
        landing=_parse_landing(raw.get("landing"), source=source),
        quality_gate=_parse_quality_gate(raw.get("qualityGate"), source=source),
        role_message_limit=role_message_limit,
        spawn_harness=_parse_spawn(
            raw.get("spawn"), source=source, harness_ids=harness_ids if strict else None
        ),
        harness_ids=harness_ids,
        unused_keys=unused_orchestration_keys(raw),
        sources=sources,
    )
