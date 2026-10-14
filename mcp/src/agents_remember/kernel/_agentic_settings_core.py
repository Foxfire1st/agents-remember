"""Typed agentic settings models, constants, and validation primitives.

The settings family (``orchestration.*``) is merged from a global and an optional
repo-local settings file. This module owns the typed models, the fail-loud key
vocabularies, the shared shape/type validators, and the seeded defaults; the
parsers live in responsibility-split siblings and the loader in
:mod:`agents_remember.kernel.agentic_settings`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.harnesses import HARNESS_IDS
from agents_remember.kernel.primitives.gate_policy import (
    DEFAULT_GATE_POLICY,
    GatePolicy,
)
from agents_remember.kernel.primitives.user_settings_location import (
    UserSettingsLocationError,
    require_user_settings_path,
    settings_upgrade_command,
    user_settings_path,
)
from agents_remember.kernel.recorded_reads import observed_path_exists
from agents_remember.models.role_identity import canonical_role


class AgenticSettingsError(AgentsRememberError):
    """Raised when an agentic settings file is malformed or carries unknown ``orchestration.*`` keys."""


# The fail-loud key families. Every set names the complete schema of its level;
# anything else raises AgenticSettingsError with the offending file's path.
KNOWN_ORCHESTRATION_FIELDS = frozenset(
    {
        "gateDelegation",
        "loops",
        "roles",
        "rolesPerLevel",
        "concurrency",
        "spawn",
        "harnesses",
        "qualityGate",
        # MIK-R117: the bound on the characters of one role_message text.
        "roleMessageLimit",
    }
)
# MIK-R113 rule 4: keys an earlier build read and this one does not. A file that holds one
# loads; none is read or checked, and :func:`unused_orchestration_keys` names the ones it holds.
UNUSED_ORCHESTRATION_FIELDS = frozenset({"expectations", "supervisor", "agentNotifier"})
UNUSED_HARNESS_ENTRY_FIELDS = frozenset(
    {
        "name",
        "command",
        "argv",
        "modelFlag",
        "effortFlag",
        "effortFlagValues",
        "effortSessionValues",
        "effortSessionCommand",
    }
)
UNUSED_ROLE_KNOB_FIELDS = frozenset({"launchArgs", "promptKeywords", "sessionCommands"})
KNOWN_GATE_DELEGATION_FIELDS = frozenset({"policy", "kinds", "requireReviewerVerdictAtSeams"})
KNOWN_GATE_POLICY_KIND_FIELDS = frozenset({"role", "requireReviewerVerdict"})
# Optional hard cap for the full Dagger quality gate. When absent, the container runtime's
# host RAM and swap own pressure response; this scalar remains for constrained lifecycle runs.
KNOWN_QUALITY_GATE_FIELDS = frozenset({"memoryCapBytes"})
KNOWN_LOOPS_FIELDS = frozenset({"defaults", "perLevel", "perMaster"})
KNOWN_LOOP_DEFAULTS_FIELDS = frozenset({"maxRounds", "reviewerReuse", "complexity"})
KNOWN_LOOP_COMPLEXITY_FIELDS = frozenset({"fullLoopAt", "builderAt"})
KNOWN_LOOP_LEVELS = frozenset({"leaf", "master", "portfolio"})
KNOWN_LOOP_LEVEL_FIELDS = frozenset({"loop"})
# The dispatch-time complexity scale (blast radius x novelty x size) the loop
# thresholds are expressed on (l-01 The Three-Party Loop).
COMPLEXITY_SCALE = ("low", "medium", "high")
# The ten portable role lifecycles the l-01 registry defines. Kept in step with
# ``models.role_capsules.vocabulary.CAPSULE_ROLES`` by a test: an orchestration knob
# addressed to a role the registry does not publish must fail loud, and a role the
# registry publishes must be configurable.
KNOWN_ROLES = frozenset(
    {
        "architect",
        "orchestrator",
        "designer",
        "strategist",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "investigator",
        "bootstrap",
    }
)
KNOWN_ROLE_KNOB_FIELDS = frozenset(
    {
        "harness",
        "model",
        "effort",
        "serviceTier",
        "mode",
        "featureValues",
        "notes",
    }
)
# R71 rule 20: unknown features carry permission authority until explicitly classified.
NON_PERMISSION_FEATURES = frozenset({"service_tier", "fast_mode"})


def native_service_tier(value: str) -> str:
    return "priority" if value == "fast" else value


def role_feature_values(knobs: RoleKnobs, source: str) -> dict[str, str | bool]:
    values = dict(knobs.feature_values)
    if knobs.service_tier:
        tier = native_service_tier(knobs.service_tier)
        if "service_tier" in values and values["service_tier"] != tier:
            raise AgenticSettingsError(
                f"{source}: serviceTier and featureValues.service_tier conflict after alias normalization"
            )
        values["service_tier"] = tier
    return values


KNOWN_CONCURRENCY_FIELDS = frozenset({"maxParallelMasters", "maxParallelLeaves", "maxSubAgents"})
KNOWN_SPAWN_FIELDS = frozenset({"harness"})
# MIK-R117: where the settings name no role-message limit, one text holds this
# many characters. The message file itself has no limit.
DEFAULT_ROLE_MESSAGE_LIMIT = 1000

# The review budget is a tunable orchestration preference (MIK-R116): a review
# takes as many rounds without an approval as ``orchestration.loops.defaults.maxRounds``
# names, and any positive whole number is taken. Where the settings name no number,
# the limit is DEFAULT_LOOP_MAX_ROUNDS.
DEFAULT_LOOP_MAX_ROUNDS = 5
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
    per_level: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LOOP_PER_LEVEL))
    per_master: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleChoiceOrigin:
    """Internal provenance from the loader's exact layer reads, never a settings key."""

    source: str
    key: str
    provider: str | None
    model: str | None
    precedence: int


@dataclass(frozen=True)
class RoleKnobs:
    """One role's knob overrides (``orchestration.roles.<role>``); ``None`` = role-file default.

    ``harness``/``model``/``effort`` are free strings HERE. A launch validates provider and model
    choices against the host's catalog.
    """

    harness: str | None = None
    model: str | None = None
    effort: str | None = None
    service_tier: str | None = None
    mode: str | None = None
    feature_values: dict[str, str | bool] = field(default_factory=dict)
    notes: str | None = None
    origins: dict[str, RoleChoiceOrigin] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class ConcurrencySettings:
    """``orchestration.concurrency`` caps; ``None`` = uncapped (the default)."""

    max_parallel_masters: int | None = None
    max_parallel_leaves: int | None = None
    max_sub_agents: int | None = None


@dataclass(frozen=True)
class QualityGateSettings:
    """``orchestration.qualityGate`` -- the full quality gate's resource knobs.

    The repository profile owns its executor adapter. ``memory_cap_bytes=None`` leaves
    the full adapter under its runtime's host-managed RAM and swap policy. An explicit
    value is passed through the declared adapter contract. Targeted leaf runs never use it.
    """

    memory_cap_bytes: int | None = None


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
    # 260703-L16 (ruling 2026-07-07T08:15): per-LEVEL role-knob overrides -- the L12 doctrine's
    # per-level agent sets, expressible at last. ``roles`` stays the flat DEFAULTS; each
    # ``roles_per_level[level][role]`` deep-merges over the flat default at leaf-key granularity
    # (see :meth:`resolved_role_knobs`). Level vocabulary matches ``loops.perLevel``.
    roles_per_level: dict[str, dict[str, RoleKnobs]] = field(default_factory=dict)
    concurrency: ConcurrencySettings = field(default_factory=ConcurrencySettings)
    quality_gate: QualityGateSettings = field(default_factory=QualityGateSettings)
    # MIK-R117: the bound on the characters of one role_message text, read per
    # call so a changed number holds for the next call without a restart.
    role_message_limit: int = DEFAULT_ROLE_MESSAGE_LIMIT
    spawn_harness: str | None = None
    # The EFFECTIVE harness ids: the builtin ones, then each id ``orchestration.harnesses`` adds.
    harness_ids: tuple[str, ...] = HARNESS_IDS
    # MIK-R113 rule 4: the keys the merged files hold that are no longer read, as dotted names.
    unused_keys: tuple[str, ...] = ()
    # The settings files that existed and were merged, global first.
    sources: tuple[Path, ...] = ()

    def role_knobs(self, role: str) -> RoleKnobs:
        return self.roles.get(canonical_role(role), RoleKnobs())

    def resolved_role_knobs(self, role: str, level: str = "leaf") -> RoleKnobs:
        """The effective knobs for ``role`` dispatched at ``level`` (260703-L16).

        The per-level override deep-merges over the flat role default at leaf-key granularity: an
        unset override field inherits the default (harness inherited unless overridden), a set one
        replaces it. Because
        both families were already merged local-over-global per file layer, this realizes the full
        dispatch chain: repo-local level override > global level override > repo-local role
        default > global role default.
        """
        role = canonical_role(role)
        base = self.roles.get(role, RoleKnobs())
        override = self.roles_per_level.get(level, {}).get(role)
        if override is None:
            return base
        return RoleKnobs(
            harness=override.harness or base.harness,
            model=override.model or base.model,
            effort=override.effort or base.effort,
            service_tier=override.service_tier or base.service_tier,
            mode=override.mode or base.mode,
            feature_values={**base.feature_values, **override.feature_values},
            notes=override.notes or base.notes,
            origins={**base.origins, **override.origins},
        )


def agentic_settings_path(root: Path) -> Path:
    """The repository-local agentic settings file; never the user's layer."""
    return root / "system" / "settings.json"


def user_agentic_settings_path() -> Path:
    """The user's single settings file, independent of the working directory."""
    try:
        return user_settings_path()
    except UserSettingsLocationError as error:
        raise AgenticSettingsError(str(error)) from error


def agentic_settings_upgrade_command(coordination_root: Path) -> str:
    return settings_upgrade_command(coordination_root)


def require_user_agentic_settings_path(coordination_root: Path) -> Path:
    """Apply the location guard through the settings loader's recorded reads."""
    try:
        return require_user_settings_path(coordination_root, observed_path_exists)
    except UserSettingsLocationError as error:
        raise AgenticSettingsError(str(error)) from error


def default_agentic_settings_seed() -> dict[str, Any]:
    """Reference representation of built-in agentic defaults for inspection and fixtures.

    No installer or setup writer consumes this helper; absent knobs use the
    dataclass/parser defaults. The c-13 interview writes chosen preferences. No spawn harness preference is seeded --
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
                    level: {"loop": posture} for level, posture in DEFAULT_LOOP_PER_LEVEL.items()
                },
            },
        },
    }


def default_agentic_settings_seed_text() -> str:
    return json.dumps(default_agentic_settings_seed(), indent=2) + "\n"


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


def unused_orchestration_keys(raw: dict[str, Any]) -> tuple[str, ...]:
    """The keys of one validated ``orchestration`` block that are no longer read, dotted."""
    names = [f"orchestration.{key}" for key in sorted(UNUSED_ORCHESTRATION_FIELDS & raw.keys())]
    for harness_id, entry in raw.get("harnesses", {}).items():
        owner = f"orchestration.harnesses.{harness_id}"
        names.extend(f"{owner}.{key}" for key in sorted(UNUSED_HARNESS_ENTRY_FIELDS & entry.keys()))
    role_tables = [("orchestration.roles", raw.get("roles", {}))]
    role_tables.extend(
        (f"orchestration.rolesPerLevel.{level}", roles)
        for level, roles in raw.get("rolesPerLevel", {}).items()
    )
    for owner, roles in role_tables:
        for role, knobs in roles.items():
            names.extend(
                f"{owner}.{role}.{key}" for key in sorted(UNUSED_ROLE_KNOB_FIELDS & knobs.keys())
            )
    return tuple(names)


def _refuse_unknown(raw: dict[str, Any], known: frozenset[str], owner: str, source: str) -> None:
    unknown = sorted(set(raw) - known)
    if unknown:
        unknown_text = ", ".join(unknown)
        allowed = ", ".join(sorted(known))
        raise AgenticSettingsError(
            f"unsupported {owner} setting(s): {unknown_text}; allowed: {allowed}; "
            f"offending file: {source}"
        )


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/_agentic_settings_core.py:433).
def _require_object(raw: object, owner: str, source: str) -> dict[str, Any]:  # pragma: no cover
    if not isinstance(raw, dict):
        raise AgenticSettingsError(f"{owner} must be an object: {source}")
    return raw


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/kernel/_agentic_settings_core.py:439).
def _require_string(raw: object, owner: str, source: str) -> str:  # pragma: no cover
    if not isinstance(raw, str) or not raw:
        raise AgenticSettingsError(f"{owner} must be a non-empty string: {source}")
    return raw


def _require_positive_int(raw: object, owner: str, source: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise AgenticSettingsError(f"{owner} must be a positive integer: {source}")
    return raw


def _require_harness_id(
    raw: object, owner: str, source: str, harness_ids: tuple[str, ...] | None
) -> str:
    """A harness reference: shape-checked always, membership-checked against the EFFECTIVE id set.

    ``harness_ids`` is the effective id set (builtin, then ``orchestration.harnesses``);
    ``None`` skips membership -- the per-file (non-strict) pass, where the other layer may declare
    the id.
    """
    value = _require_string(raw, owner, source)
    if harness_ids is not None and value not in harness_ids:
        allowed = ", ".join(harness_ids)
        raise AgenticSettingsError(
            f"{owner} must be a builtin harness id or an orchestration.harnesses-defined id "
            f"({allowed}), got {value!r}: {source}"
        )
    return value
