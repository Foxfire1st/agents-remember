"""Two-layer AGENTIC settings loader (260703-L13 settings unification).

The agentic settings family -- everything under the top-level ``orchestration``
key: gate delegation, the three-party-loop knobs, per-role knob overrides,
concurrency caps, the spawn harness preference, and the harness-definition
extension/override table (``orchestration.harnesses``, 260703-L16) -- lives in
TWO JSON files that are merged on every read:

- GLOBAL: ``<coordination_root>/system/settings.json``
- LOCAL:  ``<code_repo>/system/settings.json`` (optional; merged OVER global)

Semantics (``docs/reference/settings-json.md`` is the schema reference):

- Deep merge at leaf-key granularity: a local scalar or object leaf overrides
  the global one; arrays REPLACE (never concatenate).
- Unknown keys anywhere inside the ``orchestration.*`` family FAIL LOUD naming
  the offending file -- the MCP-authority-file discipline, extended. This also
  fixes the historic silent drop of ``orchestration.roles``/``concurrency``.
- Unknown TOP-LEVEL families are tolerated-not-parsed: the same coordinator
  file doubles as the c-08 memory-settings fallback, and it is the earmarked
  future home of further families (``contextProviders`` first in line, gate
  amendment 2026-07-06), so the fail-loud scope must not foreclose them.
- Absent files (or an absent ``orchestration`` key) mean the documented
  defaults: all-human gate delegation, the L12 loop defaults, no role
  overrides, no concurrency caps, no spawn harness preference.
- Read PER-USE: consumers call :func:`load_agentic_settings` at each use so a
  settings edit takes effect on the next use without a server restart. The ONE
  boot-snapshot consumer is ``mcp/config.py``'s ``gateDelegation`` (enforcement
  plumbing keeps its boot-cached shape; a change needs a restart -- documented).

Doctrine floors are NOT knobs: no key here touches the master-exit seam gate or
the strategist's mandatory pre-run (L12 ruling; restated in the schema doc).

The typed models, constants, validators, and the per-section parsers live in
responsibility-split sibling modules; this module owns the loader and the
orchestration block assembly and re-exports the public surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.kernel._agentic_settings_core import (
    COMPLEXITY_SCALE,
    DEFAULT_ESCALATION_RUNG_SECONDS,
    DEFAULT_ESCALATION_SLA_SECONDS,
    DEFAULT_EXPECTATION_SLA_SECONDS,
    DEFAULT_LOOP_MAX_ROUNDS,
    DEFAULT_LOOP_PER_LEVEL,
    DEFAULT_RESPAWN_AFTER_RUNG,
    DEFAULT_REVIEWER_REUSE,
    DEFAULT_SUPERVISOR_ESCALATION_BUDGET,
    DEFAULT_SUPERVISOR_INTERVAL_SECONDS,
    DEFAULT_SUPERVISOR_REDELIVER_BUDGET,
    DEFAULT_SUPERVISOR_STALE_CUTOFF_SECONDS,
    HARNESS_IDS,
    KNOWN_CONCURRENCY_FIELDS,
    KNOWN_ESCALATION_FIELDS,
    KNOWN_ESCALATION_MESSAGE_KINDS,
    KNOWN_ESCALATION_RUNGS,
    KNOWN_EXPECTATION_KINDS,
    KNOWN_EXPECTATIONS_FIELDS,
    KNOWN_GATE_DELEGATION_FIELDS,
    KNOWN_GATE_POLICY_KIND_FIELDS,
    KNOWN_HARNESS_ENTRY_FIELDS,
    KNOWN_LOOP_COMPLEXITY_FIELDS,
    KNOWN_LOOP_DEFAULTS_FIELDS,
    KNOWN_LOOP_LEVEL_FIELDS,
    KNOWN_LOOP_LEVELS,
    KNOWN_LOOPS_FIELDS,
    KNOWN_ORCHESTRATION_FIELDS,
    KNOWN_ROLE_KNOB_FIELDS,
    KNOWN_ROLES,
    KNOWN_SPAWN_FIELDS,
    KNOWN_SUPERVISOR_FIELDS,
    AgenticSettings,
    AgenticSettingsError,
    ConcurrencySettings,
    EscalationSettings,
    ExpectationSettings,
    LoopComplexity,
    LoopDefaults,
    LoopSettings,
    RoleKnobs,
    SupervisorSettings,
    _refuse_unknown,
    _require_bool,
    _require_harness_id,
    _require_object,
    _require_positive_int,
    _require_positive_number,
    _require_string,
    _require_string_list,
    agentic_settings_path,
    default_agentic_settings_seed,
    default_agentic_settings_seed_text,
    merge_settings,
)
from agents_remember.kernel._agentic_settings_harness import (
    _entry_string,
    _entry_string_list,
    _HarnessEntry,
    _merged_harness,
    _parse_harness_entry,
    _parse_harnesses,
    _refuse_bad_effort_template,
    _refuse_unpaired_vehicles,
    _resolved_launch,
)
from agents_remember.kernel._agentic_settings_policy import (
    _parse_gate_policy_rule,
    parse_gate_delegation,
)
from agents_remember.kernel._agentic_settings_sections import (
    _parse_concurrency,
    _parse_escalation,
    _parse_escalation_rung_seconds,
    _parse_escalation_sla_seconds,
    _parse_expectations,
    _parse_loop_complexity,
    _parse_loop_defaults,
    _parse_loop_levels,
    _parse_loops,
    _parse_respawn_after_rung,
    _parse_roles,
    _parse_roles_per_level,
    _parse_spawn,
    _parse_supervisor,
    _require_supervisor_floor_seconds,
)

__all__ = [
    "COMPLEXITY_SCALE",
    "DEFAULT_ESCALATION_RUNG_SECONDS",
    "DEFAULT_ESCALATION_SLA_SECONDS",
    "DEFAULT_EXPECTATION_SLA_SECONDS",
    "DEFAULT_LOOP_MAX_ROUNDS",
    "DEFAULT_LOOP_PER_LEVEL",
    "DEFAULT_RESPAWN_AFTER_RUNG",
    "DEFAULT_REVIEWER_REUSE",
    "DEFAULT_SUPERVISOR_ESCALATION_BUDGET",
    "DEFAULT_SUPERVISOR_INTERVAL_SECONDS",
    "DEFAULT_SUPERVISOR_REDELIVER_BUDGET",
    "DEFAULT_SUPERVISOR_STALE_CUTOFF_SECONDS",
    "HARNESS_IDS",
    "KNOWN_CONCURRENCY_FIELDS",
    "KNOWN_ESCALATION_FIELDS",
    "KNOWN_ESCALATION_MESSAGE_KINDS",
    "KNOWN_ESCALATION_RUNGS",
    "KNOWN_EXPECTATIONS_FIELDS",
    "KNOWN_EXPECTATION_KINDS",
    "KNOWN_GATE_DELEGATION_FIELDS",
    "KNOWN_GATE_POLICY_KIND_FIELDS",
    "KNOWN_HARNESS_ENTRY_FIELDS",
    "KNOWN_LOOPS_FIELDS",
    "KNOWN_LOOP_COMPLEXITY_FIELDS",
    "KNOWN_LOOP_DEFAULTS_FIELDS",
    "KNOWN_LOOP_LEVELS",
    "KNOWN_LOOP_LEVEL_FIELDS",
    "KNOWN_ROLES",
    "KNOWN_ROLE_KNOB_FIELDS",
    "KNOWN_SPAWN_FIELDS",
    "KNOWN_SUPERVISOR_FIELDS",
    "AgenticSettings",
    "AgenticSettingsError",
    "ConcurrencySettings",
    "EscalationSettings",
    "ExpectationSettings",
    "LoopComplexity",
    "LoopDefaults",
    "LoopSettings",
    "RoleKnobs",
    "SupervisorSettings",
    "_HarnessEntry",
    "_entry_string",
    "_entry_string_list",
    "_merged_harness",
    "_parse_concurrency",
    "_parse_escalation",
    "_parse_escalation_rung_seconds",
    "_parse_escalation_sla_seconds",
    "_parse_expectations",
    "_parse_gate_policy_rule",
    "_parse_harness_entry",
    "_parse_harnesses",
    "_parse_loop_complexity",
    "_parse_loop_defaults",
    "_parse_loop_levels",
    "_parse_loops",
    "_parse_respawn_after_rung",
    "_parse_roles",
    "_parse_roles_per_level",
    "_parse_spawn",
    "_parse_supervisor",
    "_refuse_bad_effort_template",
    "_refuse_unpaired_vehicles",
    "_require_bool",
    "_require_harness_id",
    "_require_object",
    "_require_positive_int",
    "_require_positive_number",
    "_require_string",
    "_require_string_list",
    "_require_supervisor_floor_seconds",
    "_resolved_launch",
    "agentic_settings_path",
    "default_agentic_settings_seed",
    "default_agentic_settings_seed_text",
    "load_agentic_settings",
    "merge_settings",
    "parse_gate_delegation",
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
    for layer, root in (("global", coordination_root), ("local", repo_root)):
        if root is None:
            continue
        path = agentic_settings_path(root)
        data = _read_settings_file(path)
        if data is None:
            continue
        orchestration = _validated_orchestration_block(data, path)
        if layer == "local" and "gateDelegation" in orchestration:
            # Gate posture is workspace-wide enforcement state: the boot snapshot
            # reads the GLOBAL file only, so a repo-local value would validate and
            # then silently do nothing -- a fail-open shape. Refuse it loudly.
            raise AgenticSettingsError(
                "orchestration.gateDelegation is global-layer only (the boot "
                "snapshot reads the coordination file); remove it from the "
                f"repo-local settings: {path}"
            )
        layers.append((path, orchestration))

    merged: dict[str, Any] = {}
    for _, orchestration in layers:
        merged = merge_settings(merged, orchestration)
    sources = tuple(path for path, _ in layers)
    source = _source_label(sources)
    return _parse_orchestration(merged, source=source, sources=sources)


def _read_settings_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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
) -> dict[str, Any]:  # pragma: no cover
    """One file's ``orchestration`` block, fully validated so errors name ``path``.

    Top-level keys other than ``orchestration`` are tolerated-not-parsed (other
    families own them -- see the module docstring).
    """
    raw = data.get("orchestration")
    if raw is None:
        return {}
    source = str(path)
    if not isinstance(raw, dict):
        raise AgenticSettingsError(f"orchestration settings must be an object: {source}")
    _refuse_null_families(raw, source)
    # strict=False: one LAYER may legitimately be partial (a repo-local file overriding a single
    # leaf of a globally-defined harness entry, or referencing a harness id the OTHER layer
    # declares), so per-file validation checks shapes/keys only; cross-reference and completeness
    # rules run on the MERGED block in load_agentic_settings.
    _parse_orchestration(raw, source=source, sources=(path,), strict=False)
    return raw


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

    ``strict=True`` (the merged block) additionally enforces the cross-layer rules: harness-entry
    completeness (a NEW id must resolve a command; declared vocabularies must resolve their
    delivery vehicle) and harness-id membership for ``roles.<role>.harness``/``spawn.harness``
    against the EFFECTIVE registry. The per-file pass runs ``strict=False`` (shapes + unknown keys
    only) because a single layer may be a partial override of the other.
    """
    _refuse_unknown(raw, KNOWN_ORCHESTRATION_FIELDS, "orchestration", source)
    harnesses = _parse_harnesses(raw.get("harnesses"), source=source, strict=strict)
    harness_ids = tuple(harness.id for harness in harnesses) if strict else None
    gate_raw = raw.get("gateDelegation")
    gate_policy, at_seams = parse_gate_delegation(gate_raw, source=source)
    return AgenticSettings(
        gate_policy=gate_policy,
        require_reviewer_verdict_at_seams=at_seams,
        gate_delegation_configured=gate_raw is not None,
        loops=_parse_loops(raw.get("loops"), source=source),
        roles=_parse_roles(raw.get("roles"), source=source, harness_ids=harness_ids),
        roles_per_level=_parse_roles_per_level(
            raw.get("rolesPerLevel"), source=source, harness_ids=harness_ids
        ),
        concurrency=_parse_concurrency(raw.get("concurrency"), source=source),
        expectations=_parse_expectations(raw.get("expectations"), source=source),
        supervisor=_parse_supervisor(raw.get("supervisor"), source=source),
        escalation=_parse_escalation(raw.get("escalation"), source=source),
        spawn_harness=_parse_spawn(raw.get("spawn"), source=source, harness_ids=harness_ids),
        harnesses=harnesses,
        sources=sources,
    )
