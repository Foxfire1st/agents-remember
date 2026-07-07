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
"""

from __future__ import annotations

import json
import string
from dataclasses import dataclass, field, replace
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
from agents_remember.serving.harnesses import HARNESSES, Harness


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
    }
)
# One ``orchestration.harnesses.<id>`` entry (260703-L16): define a NEW harness or override a
# builtin's defaults. Schema + worked example: ``docs/reference/harnesses.md`` (THE manual).
KNOWN_HARNESS_ENTRY_FIELDS = frozenset(
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
KNOWN_GATE_DELEGATION_FIELDS = frozenset({"policy", "kinds", "requireReviewerVerdictAtSeams"})
KNOWN_GATE_POLICY_KIND_FIELDS = frozenset({"role", "requireReviewerVerdict"})
KNOWN_LOOPS_FIELDS = frozenset({"defaults", "perLevel", "perMaster"})
KNOWN_LOOP_DEFAULTS_FIELDS = frozenset({"maxRounds", "reviewerReuse", "complexity"})
KNOWN_LOOP_COMPLEXITY_FIELDS = frozenset({"fullLoopAt", "builderAt"})
KNOWN_LOOP_LEVELS = frozenset({"leaf", "master", "portfolio"})
KNOWN_LOOP_LEVEL_FIELDS = frozenset({"loop"})
# The dispatch-time complexity scale (blast radius x novelty x size) the loop
# thresholds are expressed on (l-01 The Three-Party Loop).
COMPLEXITY_SCALE = ("low", "medium", "high")
# The eight portable role lifecycles the l-01 registry defines.
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
    }
)
KNOWN_ROLE_KNOB_FIELDS = frozenset(
    {"harness", "model", "effort", "launchArgs", "promptKeywords", "sessionCommands"}
)
KNOWN_CONCURRENCY_FIELDS = frozenset({"maxParallelMasters", "maxParallelLeaves", "maxSubAgents"})
KNOWN_SPAWN_FIELDS = frozenset({"harness"})

# The BUILTIN registry ids (claude|codex|pi). Harness references (roles.<role>.harness,
# spawn.harness) are validated against the EFFECTIVE id set -- these plus any
# orchestration.harnesses-defined ids -- so a settings value can never inject argv through a
# reference; argv is definable only through the explicit harnesses family (260703-L16).
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
    per_level: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LOOP_PER_LEVEL))
    per_master: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleKnobs:
    """One role's knob overrides (``orchestration.roles.<role>``); ``None`` = role-file default.

    ``harness`` must be a registry id; ``model``/``effort`` are free strings HERE -- the per-harness
    effort vocabulary is enforced at DISPATCH time by the spawn path (the harness is only known
    then), refusing loudly instead of letting the CLI warn-and-silently-degrade (260703-L16). The
    free-form escape hatch (``launch_args``/``prompt_keywords``/``session_commands``, JSON keys
    ``launchArgs``/``promptKeywords``/``sessionCommands``) is shape-checked only (a list of
    non-empty strings) and NEVER content-validated; the spawn path records it in spawn provenance.
    Empty tuple = not configured.
    """

    harness: str | None = None
    model: str | None = None
    effort: str | None = None
    launch_args: tuple[str, ...] = ()
    prompt_keywords: tuple[str, ...] = ()
    session_commands: tuple[str, ...] = ()


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
    # 260703-L16 (ruling 2026-07-07T08:15): per-LEVEL role-knob overrides -- the L12 doctrine's
    # per-level agent sets, expressible at last. ``roles`` stays the flat DEFAULTS; each
    # ``roles_per_level[level][role]`` deep-merges over the flat default at leaf-key granularity
    # (see :meth:`resolved_role_knobs`). Level vocabulary matches ``loops.perLevel``.
    roles_per_level: dict[str, dict[str, RoleKnobs]] = field(default_factory=dict)
    concurrency: ConcurrencySettings = field(default_factory=ConcurrencySettings)
    spawn_harness: str | None = None
    # The EFFECTIVE harness registry (260703-L16): the builtin defaults merged with the
    # ``orchestration.harnesses`` family -- new ids added, builtin ids possibly pre-customized.
    # The spawn path resolves harness ids against THIS set, never the builtin table directly.
    harnesses: tuple[Harness, ...] = HARNESSES
    # The settings files that existed and were merged, global first.
    sources: tuple[Path, ...] = ()

    def role_knobs(self, role: str) -> RoleKnobs:
        return self.roles.get(role, RoleKnobs())

    def resolved_role_knobs(self, role: str, level: str = "leaf") -> RoleKnobs:
        """The effective knobs for ``role`` dispatched at ``level`` (260703-L16).

        The per-level override deep-merges over the flat role default at leaf-key granularity: an
        unset override field inherits the default (harness inherited unless overridden), a set one
        replaces it (the free-form lists REPLACE, never concatenate -- the array rule). Because
        both families were already merged local-over-global per file layer, this realizes the full
        dispatch chain: repo-local level override > global level override > repo-local role
        default > global role default.
        """
        base = self.roles.get(role, RoleKnobs())
        override = self.roles_per_level.get(level, {}).get(role)
        if override is None:
            return base
        return RoleKnobs(
            harness=override.harness or base.harness,
            model=override.model or base.model,
            effort=override.effort or base.effort,
            launch_args=override.launch_args or base.launch_args,
            prompt_keywords=override.prompt_keywords or base.prompt_keywords,
            session_commands=override.session_commands or base.session_commands,
        )

    def find_harness(self, harness_id: str) -> Harness | None:
        """The effective :class:`Harness` for ``harness_id`` (builtin merged with settings)."""
        return next((harness for harness in self.harnesses if harness.id == harness_id), None)


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
                    level: {"loop": posture} for level, posture in DEFAULT_LOOP_PER_LEVEL.items()
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
    null_families = sorted(key for key in KNOWN_ORCHESTRATION_FIELDS if raw.get(key) is None and key in raw)
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


def _refuse_unknown(raw: dict[str, Any], known: frozenset[str], owner: str, source: str) -> None:
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


def _require_string_list(raw: object, owner: str, source: str) -> tuple[str, ...]:
    """A free-form list of non-empty strings (shape-checked only; content never validated).

    An EMPTY list is refused (PR #100 review, Codex P2): ``RoleKnobs`` cannot distinguish
    "absent" from "explicitly cleared" (empty tuple = not configured), so a per-level ``[]``
    meant to clear a flat default would silently INHERIT it instead — the same silent-degrade
    shape as a ``null`` family key (260703-L18 finding 6). Omit the key to inherit; list
    values to override.
    """
    if not isinstance(raw, list):
        raise AgenticSettingsError(f"{owner} must be a list of non-empty strings: {source}")
    if not raw:
        raise AgenticSettingsError(
            f"{owner} must not be an empty list (omit the key to inherit, or list the values "
            f"to override): {source}"
        )
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            raise AgenticSettingsError(f"{owner} must be a list of non-empty strings: {source}")
        values.append(item)
    return tuple(values)


def _require_harness_id(
    raw: object, owner: str, source: str, harness_ids: tuple[str, ...] | None
) -> str:
    """A harness reference: shape-checked always, membership-checked against the EFFECTIVE id set.

    ``harness_ids`` is the effective registry (builtin merged with ``orchestration.harnesses``);
    ``None`` skips membership -- the per-file (non-strict) pass, where the other layer may declare
    the id.
    """
    value = _require_string(raw, owner, source)
    if harness_ids is not None and value not in harness_ids:
        allowed = ", ".join(harness_ids)
        raise AgenticSettingsError(
            f"{owner} must be a harness registry id or an orchestration.harnesses-defined id "
            f"({allowed}), got {value!r}: {source} (see docs/reference/harnesses.md)"
        )
    return value


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
        spawn_harness=_parse_spawn(raw.get("spawn"), source=source, harness_ids=harness_ids),
        harnesses=harnesses,
        sources=sources,
    )


def _parse_harnesses(raw: object, *, source: str, strict: bool) -> tuple[Harness, ...]:
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


def _parse_harness_entry(harness_id: str, value: object, source: str) -> _HarnessEntry:
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


def _refuse_bad_effort_template(merged: Harness, owner: str, source: str) -> None:
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
    policy_name = _require_string(policy_name, "orchestration.gateDelegation.policy", source)
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
    rule = _require_object(raw_rule, f"orchestration.gateDelegation.kinds.{kind}", source)
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


def _parse_loop_complexity(raw: object, *, source: str) -> LoopComplexity:
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
    harness_ids: tuple[str, ...] | None,
    owner: str = "orchestration.roles",
) -> dict[str, RoleKnobs]:
    if raw is None:
        return {}
    roles = _require_object(raw, owner, source)
    _refuse_unknown(roles, KNOWN_ROLES, owner, source)
    parsed: dict[str, RoleKnobs] = {}
    for role, value in roles.items():
        knobs = _require_object(value, f"{owner}.{role}", source)
        _refuse_unknown(knobs, KNOWN_ROLE_KNOB_FIELDS, f"{owner}.{role}", source)
        harness = None
        if "harness" in knobs:
            harness = _require_harness_id(
                knobs["harness"],
                f"{owner}.{role}.harness",
                source,
                harness_ids,
            )
        model = None
        if "model" in knobs:
            model = _require_string(knobs["model"], f"{owner}.{role}.model", source)
        effort = None
        if "effort" in knobs:
            # Deliberately a free string here: the per-harness effort vocabulary (flag values +
            # session values) is enforced at DISPATCH, where the harness is known (260703-L16).
            effort = _require_string(knobs["effort"], f"{owner}.{role}.effort", source)
        # The free-form escape hatch (260703-L16): shape-checked, never content-validated.
        launch_args: tuple[str, ...] = ()
        if "launchArgs" in knobs:
            launch_args = _require_string_list(
                knobs["launchArgs"], f"{owner}.{role}.launchArgs", source
            )
        prompt_keywords: tuple[str, ...] = ()
        if "promptKeywords" in knobs:
            prompt_keywords = _require_string_list(
                knobs["promptKeywords"],
                f"{owner}.{role}.promptKeywords",
                source,
            )
        session_commands: tuple[str, ...] = ()
        if "sessionCommands" in knobs:
            session_commands = _require_string_list(
                knobs["sessionCommands"],
                f"{owner}.{role}.sessionCommands",
                source,
            )
        parsed[role] = RoleKnobs(
            harness=harness,
            model=model,
            effort=effort,
            launch_args=launch_args,
            prompt_keywords=prompt_keywords,
            session_commands=session_commands,
        )
    return parsed


def _parse_roles_per_level(
    raw: object, *, source: str, harness_ids: tuple[str, ...] | None
) -> dict[str, dict[str, RoleKnobs]]:
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
            harness_ids=harness_ids,
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


def _parse_spawn(raw: object, *, source: str, harness_ids: tuple[str, ...] | None) -> str | None:
    if raw is None:
        return None
    spawn = _require_object(raw, "orchestration.spawn", source)
    _refuse_unknown(spawn, KNOWN_SPAWN_FIELDS, "orchestration.spawn", source)
    if "harness" not in spawn:
        return None
    return _require_harness_id(spawn["harness"], "orchestration.spawn.harness", source, harness_ids)
