"""Two-layer AGENTIC settings loader (260703-L13 settings unification).

The agentic settings family -- everything under the top-level ``orchestration``
key: gate delegation, the three-party-loop knobs, per-role knob overrides,
concurrency caps, and the spawn harness preference -- lives in TWO JSON files
that are merged on every read:

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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.controlplane.gate_policy import (
    DEFAULT_GATE_POLICY,
    GatePolicy,
    GatePolicyRule,
    apply_seam_verdict_requirement,
    coerce_decision_role,
    make_gate_policy,
    named_gate_policy,
)
from agents_remember.controlplane.records import GateKind, coerce_gate_kind
from agents_remember.errors import AgentsRememberError
from agents_remember.serving.harnesses import HARNESSES


class AgenticSettingsError(AgentsRememberError):
    """Raised when an agentic settings file is malformed or carries unknown ``orchestration.*`` keys."""


# The fail-loud key families. Every set names the complete schema of its level;
# anything else raises AgenticSettingsError with the offending file's path.
KNOWN_ORCHESTRATION_FIELDS = frozenset(
    {"gateDelegation", "loops", "roles", "concurrency", "spawn"}
)
KNOWN_GATE_DELEGATION_FIELDS = frozenset(
    {"policy", "kinds", "requireReviewerVerdictAtSeams"}
)
KNOWN_GATE_POLICY_KIND_FIELDS = frozenset({"role", "requireReviewerVerdict"})
KNOWN_LOOPS_FIELDS = frozenset({"defaults", "perLevel", "perMaster"})
KNOWN_LOOP_DEFAULTS_FIELDS = frozenset({"maxRounds", "reviewerReuse", "complexity"})
KNOWN_LOOP_COMPLEXITY_FIELDS = frozenset({"fullLoopAt", "builderAt"})
KNOWN_LOOP_LEVELS = frozenset({"leaf", "master", "portfolio"})
KNOWN_LOOP_LEVEL_FIELDS = frozenset({"loop"})
# The dispatch-time complexity scale (blast radius x novelty x size) the loop
# thresholds are expressed on (l-01 The Three-Party Loop).
COMPLEXITY_SCALE = ("low", "medium", "high")
# The six portable role lifecycles the l-01 registry defines.
KNOWN_ROLES = frozenset(
    {"orchestrator", "designer", "strategist", "manager", "worker", "reviewer"}
)
KNOWN_ROLE_KNOB_FIELDS = frozenset({"harness", "model", "effort"})
KNOWN_CONCURRENCY_FIELDS = frozenset(
    {"maxParallelMasters", "maxParallelLeaves", "maxSubAgents"}
)
KNOWN_SPAWN_FIELDS = frozenset({"harness"})

# Harness preferences must be registry ids (claude|codex|pi) -- a settings
# value can never inject argv (the fixed-argv posture of serving/harnesses.py).
HARNESS_IDS = tuple(harness.id for harness in HARNESSES)

# The L12 loop defaults (docs/reference/settings-json.md, Orchestration Loops).
DEFAULT_LOOP_MAX_ROUNDS = 3
DEFAULT_REVIEWER_REUSE = "delta-verify"
DEFAULT_LOOP_PER_LEVEL: dict[str, str] = {
    "leaf": "scored",
    "master": "seam-required",
    "portfolio": "strategist",
}


@dataclass(frozen=True)
class LoopComplexity:
    """The complexity thresholds mapping the dispatch-time score to loop tiers."""

    full_loop_at: str = "high"
    builder_at: str = "medium"


@dataclass(frozen=True)
class LoopDefaults:
    """``orchestration.loops.defaults`` -- round cap, reviewer reuse, tier thresholds."""

    max_rounds: int = DEFAULT_LOOP_MAX_ROUNDS
    reviewer_reuse: str = DEFAULT_REVIEWER_REUSE
    complexity: LoopComplexity = field(default_factory=LoopComplexity)


@dataclass(frozen=True)
class LoopSettings:
    """``orchestration.loops`` -- the three-party-loop knobs (L12 schema).

    ``per_level`` maps level -> loop posture (loop postures are model-interpreted
    doctrine names, validated as non-empty strings, not a closed set);
    ``per_master`` maps a master task name -> sparse per-level overrides.
    The knobs govern the LOOP only: the master-exit SEAM gate is unconditional.
    """

    defaults: LoopDefaults = field(default_factory=LoopDefaults)
    per_level: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_LOOP_PER_LEVEL)
    )
    per_master: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleKnobs:
    """One role's knob overrides (``orchestration.roles.<role>``); ``None`` = role-file default."""

    harness: str | None = None
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True)
class ConcurrencySettings:
    """``orchestration.concurrency`` caps; ``None`` = uncapped (the default)."""

    max_parallel_masters: int | None = None
    max_parallel_leaves: int | None = None
    max_sub_agents: int | None = None


@dataclass(frozen=True)
class AgenticSettings:
    """The merged, typed agentic settings for one read (global <- local)."""

    gate_policy: GatePolicy = DEFAULT_GATE_POLICY
    require_reviewer_verdict_at_seams: bool = False
    # Whether a settings file (not the default) set gateDelegation -- the
    # boot-snapshot consumer uses this to decide legacy-fallback handling.
    gate_delegation_configured: bool = False
    loops: LoopSettings = field(default_factory=LoopSettings)
    roles: dict[str, RoleKnobs] = field(default_factory=dict)
    concurrency: ConcurrencySettings = field(default_factory=ConcurrencySettings)
    spawn_harness: str | None = None
    # The settings files that existed and were merged, global first.
    sources: tuple[Path, ...] = ()

    def role_knobs(self, role: str) -> RoleKnobs:
        return self.roles.get(role, RoleKnobs())


def agentic_settings_path(root: Path) -> Path:
    """The agentic settings file under ``root`` (coordination root or code repo)."""
    return root / "system" / "settings.json"


def default_agentic_settings_seed() -> dict[str, Any]:
    """The seeded global-file content: every agentic knob at its documented default.

    ``runtime_install`` writes this copy-if-missing; the c-13 install interview
    then edits it with the developer. No spawn harness preference is seeded --
    the spawn seam stays detection-gated until a preference is configured.
    """
    return {
        "$comment": (
            "Agentic orchestration settings (GLOBAL layer). Schema: agents-remember "
            "docs/reference/settings-json.md (Agentic Settings). Repo-local overrides: "
            "<code-repo>/system/settings.json. Unknown orchestration.* keys fail loud."
        ),
        "version": 1,
        "orchestration": {
            "gateDelegation": {"policy": "all-human"},
            "loops": {
                "defaults": {
                    "maxRounds": DEFAULT_LOOP_MAX_ROUNDS,
                    "reviewerReuse": DEFAULT_REVIEWER_REUSE,
                    "complexity": {"fullLoopAt": "high", "builderAt": "medium"},
                },
                "perLevel": {
                    level: {"loop": posture}
                    for level, posture in DEFAULT_LOOP_PER_LEVEL.items()
                },
            },
        },
    }


def default_agentic_settings_seed_text() -> str:
    return json.dumps(default_agentic_settings_seed(), indent=2) + "\n"


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


def merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge at leaf-key granularity: object leaves recurse, everything else
    (scalars AND arrays) is REPLACED by the override."""
    merged = dict(base)
    for key, value in override.items():
        prior = merged.get(key)
        if isinstance(prior, dict) and isinstance(value, dict):
            merged[key] = merge_settings(prior, value)
        else:
            merged[key] = value
    return merged


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


def _validated_orchestration_block(data: dict[str, Any], path: Path) -> dict[str, Any]:
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
    _parse_orchestration(raw, source=source, sources=(path,))
    return raw


def _source_label(sources: tuple[Path, ...]) -> str:
    if not sources:
        return "agentic settings defaults (no settings file present)"
    return " merged with ".join(str(path) for path in sources)


def _refuse_unknown(
    raw: dict[str, Any], known: frozenset[str], owner: str, source: str
) -> None:
    unknown = sorted(set(raw) - known)
    if unknown:
        unknown_text = ", ".join(unknown)
        allowed = ", ".join(sorted(known))
        raise AgenticSettingsError(
            f"unsupported {owner} setting(s): {unknown_text}; allowed: {allowed}; "
            f"offending file: {source}"
        )


def _require_object(raw: object, owner: str, source: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgenticSettingsError(f"{owner} must be an object: {source}")
    return raw


def _require_string(raw: object, owner: str, source: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise AgenticSettingsError(f"{owner} must be a non-empty string: {source}")
    return raw


def _require_positive_int(raw: object, owner: str, source: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise AgenticSettingsError(f"{owner} must be a positive integer: {source}")
    return raw


def _require_harness_id(raw: object, owner: str, source: str) -> str:
    value = _require_string(raw, owner, source)
    if value not in HARNESS_IDS:
        allowed = ", ".join(HARNESS_IDS)
        raise AgenticSettingsError(
            f"{owner} must be a harness registry id ({allowed}), got {value!r}: {source}"
        )
    return value


def _parse_orchestration(
    raw: dict[str, Any], *, source: str, sources: tuple[Path, ...]
) -> AgenticSettings:
    _refuse_unknown(raw, KNOWN_ORCHESTRATION_FIELDS, "orchestration", source)
    gate_raw = raw.get("gateDelegation")
    gate_policy, at_seams = parse_gate_delegation(gate_raw, source=source)
    return AgenticSettings(
        gate_policy=gate_policy,
        require_reviewer_verdict_at_seams=at_seams,
        gate_delegation_configured=gate_raw is not None,
        loops=_parse_loops(raw.get("loops"), source=source),
        roles=_parse_roles(raw.get("roles"), source=source),
        concurrency=_parse_concurrency(raw.get("concurrency"), source=source),
        spawn_harness=_parse_spawn(raw.get("spawn"), source=source),
        sources=sources,
    )


def parse_gate_delegation(raw: object, *, source: str) -> tuple[GatePolicy, bool]:
    """Parse ``orchestration.gateDelegation`` into ``(policy, requireReviewerVerdictAtSeams)``.

    Shared by the agentic loader (the key's home) and ``mcp/config.py``'s
    one-cycle legacy authority-file fallback. ``None`` means the all-human
    default. Errors name ``source`` (the offending file).
    """
    if raw is None:
        return DEFAULT_GATE_POLICY, False
    delegation = _require_object(raw, "orchestration.gateDelegation", source)
    _refuse_unknown(
        delegation,
        KNOWN_GATE_DELEGATION_FIELDS,
        "orchestration.gateDelegation",
        source,
    )
    policy_name = delegation.get("policy", "all-human")
    policy_name = _require_string(
        policy_name, "orchestration.gateDelegation.policy", source
    )
    try:
        policy = named_gate_policy(policy_name)
    except ValueError as error:
        raise AgenticSettingsError(f"{error}; offending file: {source}") from error
    require_verdict_at_seams = delegation.get("requireReviewerVerdictAtSeams", False)
    if not isinstance(require_verdict_at_seams, bool):
        raise AgenticSettingsError(
            "orchestration.gateDelegation.requireReviewerVerdictAtSeams must be a "
            f"boolean: {source}"
        )
    kinds = _require_object(
        delegation.get("kinds", {}), "orchestration.gateDelegation.kinds", source
    )
    rules = {rule.kind: rule for rule in policy.rules}
    for raw_kind, raw_rule in kinds.items():
        if not isinstance(raw_kind, str) or not raw_kind:
            raise AgenticSettingsError(
                f"gate policy kind names must be non-empty strings: {source}"
            )
        try:
            kind = coerce_gate_kind(raw_kind)
            rules[kind] = _parse_gate_policy_rule(
                kind, raw_rule, prior=rules.get(kind), source=source
            )
        except AgenticSettingsError:
            raise
        except ValueError as error:
            raise AgenticSettingsError(f"{error}; offending file: {source}") from error
    try:
        policy = make_gate_policy(list(rules.values()))
    except ValueError as error:
        raise AgenticSettingsError(f"{error}; offending file: {source}") from error
    if require_verdict_at_seams:
        policy = apply_seam_verdict_requirement(policy)
    return policy, require_verdict_at_seams


def _parse_gate_policy_rule(
    kind: GateKind,
    raw_rule: object,
    *,
    prior: GatePolicyRule | None,
    source: str,
) -> GatePolicyRule:
    if isinstance(raw_rule, str):
        return GatePolicyRule(kind=kind, delegated_role=coerce_decision_role(raw_rule))
    rule = _require_object(
        raw_rule, f"orchestration.gateDelegation.kinds.{kind}", source
    )
    _refuse_unknown(
        rule,
        KNOWN_GATE_POLICY_KIND_FIELDS,
        f"orchestration.gateDelegation.kinds.{kind}",
        source,
    )
    role_raw = rule.get("role")
    delegated_role = prior.delegated_role if prior is not None else None
    if role_raw is not None:
        role_value = _require_string(
            role_raw, f"orchestration.gateDelegation.kinds.{kind}.role", source
        )
        delegated_role = coerce_decision_role(role_value)
    require_verdict = rule.get(
        "requireReviewerVerdict",
        prior.require_reviewer_verdict if prior is not None else False,
    )
    if not isinstance(require_verdict, bool):
        raise AgenticSettingsError(
            f"orchestration.gateDelegation.kinds.{kind}.requireReviewerVerdict "
            f"must be a boolean: {source}"
        )
    return GatePolicyRule(
        kind=kind,
        delegated_role=delegated_role,
        require_reviewer_verdict=require_verdict,
    )


def _parse_loops(raw: object, *, source: str) -> LoopSettings:
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
        masters = _require_object(
            raw_per_master, "orchestration.loops.perMaster", source
        )
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
    _refuse_unknown(
        defaults, KNOWN_LOOP_DEFAULTS_FIELDS, "orchestration.loops.defaults", source
    )
    max_rounds = DEFAULT_LOOP_MAX_ROUNDS
    if "maxRounds" in defaults:
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
    return LoopDefaults(
        max_rounds=max_rounds, reviewer_reuse=reviewer_reuse, complexity=complexity
    )


def _parse_loop_complexity(raw: object, *, source: str) -> LoopComplexity:
    if raw is None:
        return LoopComplexity()
    complexity = _require_object(
        raw, "orchestration.loops.defaults.complexity", source
    )
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
        parsed[level] = _require_string(
            entry.get("loop"), f"{owner}.{level}.loop", source
        )
    return parsed


def _parse_roles(raw: object, *, source: str) -> dict[str, RoleKnobs]:
    if raw is None:
        return {}
    roles = _require_object(raw, "orchestration.roles", source)
    _refuse_unknown(roles, KNOWN_ROLES, "orchestration.roles", source)
    parsed: dict[str, RoleKnobs] = {}
    for role, value in roles.items():
        knobs = _require_object(value, f"orchestration.roles.{role}", source)
        _refuse_unknown(
            knobs, KNOWN_ROLE_KNOB_FIELDS, f"orchestration.roles.{role}", source
        )
        harness = None
        if "harness" in knobs:
            harness = _require_harness_id(
                knobs["harness"], f"orchestration.roles.{role}.harness", source
            )
        model = None
        if "model" in knobs:
            model = _require_string(
                knobs["model"], f"orchestration.roles.{role}.model", source
            )
        effort = None
        if "effort" in knobs:
            effort = _require_string(
                knobs["effort"], f"orchestration.roles.{role}.effort", source
            )
        parsed[role] = RoleKnobs(harness=harness, model=model, effort=effort)
    return parsed


def _parse_concurrency(raw: object, *, source: str) -> ConcurrencySettings:
    if raw is None:
        return ConcurrencySettings()
    concurrency = _require_object(raw, "orchestration.concurrency", source)
    _refuse_unknown(
        concurrency, KNOWN_CONCURRENCY_FIELDS, "orchestration.concurrency", source
    )
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


def _parse_spawn(raw: object, *, source: str) -> str | None:
    if raw is None:
        return None
    spawn = _require_object(raw, "orchestration.spawn", source)
    _refuse_unknown(spawn, KNOWN_SPAWN_FIELDS, "orchestration.spawn", source)
    if "harness" not in spawn:
        return None
    return _require_harness_id(
        spawn["harness"], "orchestration.spawn.harness", source
    )
