"""Payload builders for dashboard terminal-session catalog tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agents_remember.errors import HarnessControlError
from agents_remember.kernel.agentic_settings import (
    AgenticSettings,
    RoleKnobs,
    load_agentic_settings,
)
from agents_remember.observer.ambient import ambient
from agents_remember.observer.events import now_iso
from agents_remember.serving.harness_control_adapter import BUILTIN_PROTOCOL_HARNESSES
from agents_remember.serving.harness_launch import ResolvedLaunch, resolve_settings_launch
from agents_remember.serving.harnesses import (
    Harness,
    Which,
    effort_session_commands,
    find_harness,
    invalid_effort_detail,
    invalid_model_detail,
    is_detected,
    unknown_harness_detail,
)
from agents_remember.serving.leaf_ref_validation import resolve_catalog_leaf_key
from agents_remember.serving.retire import retire_entry
from agents_remember.serving.retire_policy import (
    RetirePolicyError,
    SeatRef,
    check_retire_authority,
)
from agents_remember.serving.seat_events import log_rename_event, log_retire_event
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_leaf_assignment import (
    LeafAssignmentHost,
    assign_terminal_session_to_leaf,
)
from agents_remember.serving.terminal_opener import open_terminal_session
from agents_remember.serving.terminal_paste import TerminalPaster
from agents_remember.worktrees.leaf_refs import LeafRefResolutionError

from .base import _tool_payload
from .leaf_ref import leaf_ref_refusal_payload

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

_DEFAULT_SHELL = "/bin/bash"

# The dispatch levels (260703-L16, ruling 2026-07-07T08:15) -- the same vocabulary as
# orchestration.loops.perLevel / orchestration.rolesPerLevel so the per-level families stay
# congruent. The dispatcher knows its level: a manager dispatching leaf seats = leaf, the seam
# reviewer = master, portfolio/end-to-end seats = portfolio. Omitted = leaf.
_SPAWN_LEVELS = ("leaf", "master", "portfolio")
_REMOVED_CALLER_SPEND_FIELDS = (
    "harness",
    "model",
    "effort",
    "launch_args",
    "prompt_keywords",
    "session_commands",
)
# The retained caller env field reaches the spawned harness process through tmux -e. Block
# harness-native model, effort, endpoint, and credential env vars so env cannot bypass the
# developer-owned settings surface.
_HARNESS_NATIVE_SPEND_ENV_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "MAX_THINKING_TOKENS",
    "DISABLE_PROMPT_CACHING",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "OPENAI_MODEL",
    "OPENAI_DEFAULT_MODEL",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_ORGANIZATION",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT",
)
_SPEND_ENV_KEYS = ("AR_SPAWN_MODEL", "AR_SPAWN_EFFORT", *_HARNESS_NATIVE_SPEND_ENV_KEYS)


def attach_terminal_session_to_leaf_payload(
    config: McpRuntimeConfig,
    *,
    session_id: str,
    leaf_key: str,
    role: str | None = None,
    host: LeafAssignmentHost | None = None,
) -> dict[str, Any]:
    """Move an existing hosted terminal/chat session to a durable leaf key."""

    try:
        leaf_key = resolve_catalog_leaf_key(config, leaf_key)
    except LeafRefResolutionError as exc:
        return leaf_ref_refusal_payload("attach_terminal_session_to_leaf", leaf_key, exc)
    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    assignment_host = host if host is not None else TerminalHost()
    result = assign_terminal_session_to_leaf(
        catalog,
        assignment_host,
        session_id=session_id,
        leaf_key=leaf_key,
        role=role,
    )
    return _tool_payload(
        "attach_terminal_session_to_leaf",
        {
            "ok": result.status == "attached",
            "operation": "attach_terminal_session_to_leaf",
            "status": result.status,
            "session": result.session_id,
            "leafKey": result.leaf_key,
            "previousLeafKey": result.previous_leaf_key,
            "ownerSession": result.owner_session_id,
            "role": result.role,
            "seatRole": result.seat_role,
            "previousSeatRole": result.previous_seat_role,
        },
    )


def _spawn_env(
    model: str | None,
    effort: str | None,
    env: dict[str, str] | None,
) -> dict[str, str]:
    """Fold the role knobs into the spawn env the terminal host seeds at ``tmux new-session``.

    Model/effort ride as namespaced env vars (``AR_SPAWN_MODEL`` / ``AR_SPAWN_EFFORT``) alongside any
    caller-supplied ``env``. Caller-supplied spend env keys are rejected before this helper runs; settings
    are the only authority for model/effort selection.
    """
    resolved = dict(env or {})
    if model:
        resolved["AR_SPAWN_MODEL"] = model
    if effort:
        resolved["AR_SPAWN_EFFORT"] = effort
    return resolved


def _ambient_lifecycle_id() -> str | None:
    """The active (spawning) lifecycle id, for default spawned-by provenance. Best-effort, never raises."""
    amb = ambient()
    if amb is not None and amb.current is not None:
        return amb.current.id
    return None


def _spawn_repo_root(config: McpRuntimeConfig, leaf_key: str | None) -> Path | None:
    """The code-repo root whose repo-local agentic settings apply to this spawn.

    Derived from the qualified leaf key (``<repository>/<master>/<docId>`` -- the
    l-01 catalog-binding contract): a leaf-attached spawn works on that repo, so
    its ``<repo>/system/settings.json`` layer participates. Leafless spawns (and
    unconfigured repo segments) resolve against the global layer only.
    """
    if not leaf_key:
        return None
    repo_id = leaf_key.split("/", 1)[0]
    scope = config.repositories.get(repo_id)
    return scope.path if scope is not None else None


def _resolve_spawn_harness(
    settings: AgenticSettings,
    harness: str | None,
    which: Which | None,
) -> tuple[Harness | None, dict[str, Any] | None]:
    """Resolve the harness for a spawn (260703-L13 seam; effective registry 260703-L16).

    Precedence: role/level settings > spawn settings > detection-gated default
    (the first EFFECTIVE-registry harness detected on PATH). Every id resolves
    against ``settings.harnesses`` -- the builtin registry merged with the
    ``orchestration.harnesses`` family, so users can teach the system a new TUI
    or pre-customize a builtin's launch. An id known nowhere refuses loudly
    pointing at the manual (never a crash); ``settings`` comes from the per-use
    loader (a malformed file raises -- never a silent fallback). Returns
    ``(harness, refusal_payload)`` with exactly one side set.
    """
    registry = settings.harnesses
    if harness is not None:
        found = find_harness(harness, registry=registry)
        if found is None:
            return None, _spawn_refusal(
                "harness-unknown",
                harness,
                "harness",
                detail=unknown_harness_detail(harness, registry=registry),
            )
        if not is_detected(found, which=which):
            return None, _spawn_refusal(
                "harness-not-detected",
                harness,
                "harness",
                detail=f"harness not installed: {harness!r}",
            )
        return found, None

    preferred = settings.spawn_harness
    if preferred is not None:
        found = find_harness(preferred, registry=registry)
        assert found is not None  # the loader validates against the effective ids
        if not is_detected(found, which=which):
            source = ", ".join(str(path) for path in settings.sources)
            return None, _spawn_refusal(
                "harness-not-detected",
                preferred,
                "harness",
                detail=(
                    f"configured spawn harness not installed: {preferred!r} "
                    f"(orchestration.spawn.harness in {source})"
                ),
            )
        return found, None

    for candidate in registry:
        if is_detected(candidate, which=which):
            return candidate, None
    ids = ", ".join(candidate.id for candidate in registry)
    return None, _spawn_refusal(
        "harness-not-detected",
        None,
        "harness",
        detail=(
            "no harness given, none preferred in settings, and none detected on "
            f"PATH; install one of: {ids} or configure orchestration.roles / orchestration.spawn"
        ),
    )


@dataclass(frozen=True)
class _HarnessDispatch:
    """The pre-spawn knob bundle for one harness-kind dispatch (260703-L16).

    Everything the settings rungs resolved for this seat: the harness id (effective-registry
    validated), the effective registry itself, the resolved model/effort, the settings-owned free-form
    escape hatch, the resolved free-form session-command list, and the level provenance.
    """

    harness_id: str
    registry: tuple[Harness, ...]
    resolved_launch: ResolvedLaunch | None
    legacy_model: str | None
    legacy_effort: str | None
    launch_args: list[str] | None
    prompt_keywords: list[str] | None
    session_commands: list[str]
    spawn_level: str
    spawn_level_source: str


def _resolve_harness_dispatch(
    config: McpRuntimeConfig,
    *,
    leaf_key: str | None,
    level: str | None,
    env: dict[str, str] | None,
    which: Which | None,
) -> tuple[_HarnessDispatch | None, dict[str, Any] | None]:
    """Resolve + validate every knob BEFORE anything spawns (260703-L16).

    Realizes the settings-only dispatch chain repo-local level override > global level override >
    repo-local role default > global role default > spawn preference > detection-gated default: the
    settings rungs come from ``resolved_role_knobs(AR_SPAWN_ROLE, level)`` (per-use read; the repo-local
    layer selected by the qualified leaf key), the harness resolves against the EFFECTIVE registry, and
    model/effort must both be present. Their vendor validity is checked token-free against L1's
    dynamic catalog inside the hosted runner before the real harness process starts. Returns
    ``(dispatch, refusal)`` with exactly one side set.
    """
    spawn_level = level or "leaf"
    spawn_level_source = "explicit" if level is not None else "default"
    if spawn_level not in _SPAWN_LEVELS:
        valid = ", ".join(_SPAWN_LEVELS)
        return None, _spawn_refusal(
            "level-invalid",
            None,
            "harness",
            detail=f"unknown dispatch level {spawn_level!r}; valid levels: [{valid}]",
        )
    settings = load_agentic_settings(
        config.coordination_root, repo_root=_spawn_repo_root(config, leaf_key)
    )
    # The settings rungs, keyed by the AR_SPAWN_ROLE riding the caller's env (no role = no settings
    # rung, today's behavior); role/level settings are the sole spend source for ordinary spawns.
    role = (env or {}).get("AR_SPAWN_ROLE")
    knobs = settings.resolved_role_knobs(role, spawn_level) if role else RoleKnobs()
    model = knobs.model
    effort = knobs.effort
    launch_args = list(knobs.launch_args) if knobs.launch_args else None
    prompt_keywords = list(knobs.prompt_keywords) if knobs.prompt_keywords else None
    resolved_session_commands = list(knobs.session_commands)
    # Harness rung order: role knobs (level-merged) > spawn preference > detection-gated default
    # (the last two inside _resolve_spawn_harness).
    found, refusal = _resolve_spawn_harness(settings, knobs.harness, which)
    if refusal is not None:
        return None, refusal
    assert found is not None  # no refusal => a resolved effective-registry harness
    # Caller-provided AR_SPAWN_MODEL/AR_SPAWN_EFFORT keys are rejected before this function runs,
    # so settings remain the only authority. Native adapters require one complete structured
    # selection; unknown/model-gated values fail against dynamic advertise in the runner before the
    # configured vendor process starts. Settings-defined non-native harnesses keep their explicit
    # registry mapping contract because they have no normalized native adapter.
    resolved_launch = None
    legacy_model = None
    legacy_effort = None
    if role is not None and found.id in BUILTIN_PROTOCOL_HARNESSES:
        try:
            resolved_launch = resolve_settings_launch(
                harness_id=found.id,
                model=model,
                effort=effort,
                workspace=config.workspace_root,
            )
        except HarnessControlError as exc:
            return None, _spawn_refusal(
                "launch-selection-invalid",
                found.id,
                "harness",
                detail=str(exc),
            )
    elif found.id not in BUILTIN_PROTOCOL_HARNESSES:
        refusal = _knob_refusal(found, model, effort)
        if refusal is not None:
            return None, refusal
        resolved_session_commands = (
            effort_session_commands(found, effort) + resolved_session_commands
        )
        legacy_model = model
        legacy_effort = effort
    return (
        _HarnessDispatch(
            harness_id=found.id,
            registry=settings.harnesses,
            resolved_launch=resolved_launch,
            legacy_model=legacy_model,
            legacy_effort=legacy_effort,
            launch_args=launch_args,
            prompt_keywords=prompt_keywords,
            session_commands=resolved_session_commands,
            spawn_level=spawn_level,
            spawn_level_source=spawn_level_source,
        ),
        None,
    )


def _knob_refusal(
    found: Harness, effective_model: str | None, effective_effort: str | None
) -> dict[str, Any] | None:
    """Preserve explicit static validation for settings-defined non-native harnesses."""

    checks = (
        (
            "model-invalid",
            invalid_model_detail(found, effective_model) if effective_model else None,
        ),
        (
            "effort-invalid",
            invalid_effort_detail(found, effective_effort) if effective_effort else None,
        ),
    )
    for status, detail in checks:
        if detail is not None:
            return _spawn_refusal(status, found.id, "harness", detail=detail)
    return None


def _caller_spend_override_refusal(
    *,
    harness: str | None,
    model: str | None,
    effort: str | None,
    env: dict[str, str] | None,
    launch_args: list[str] | None,
    prompt_keywords: list[str] | None,
    session_commands: list[str] | None,
    kind: str,
) -> dict[str, Any] | None:
    """Reject legacy caller-controlled spend knobs before any spawn-side effect."""

    removed = []
    values = {
        "harness": harness,
        "model": model,
        "effort": effort,
        "launch_args": launch_args,
        "prompt_keywords": prompt_keywords,
        "session_commands": session_commands,
    }
    for field in _REMOVED_CALLER_SPEND_FIELDS:
        if values[field] is not None:
            removed.append(field)
    for key in _SPEND_ENV_KEYS:
        if env is not None and key in env:
            removed.append(f"env.{key}")
    if not removed:
        return None
    fields = ", ".join(removed)
    detail = (
        "spawn_agent_session no longer accepts caller-selected spend fields "
        f"({fields}) for ordinary agent-driven spawns. Configure harness/model/effort and "
        "launch/session spend controls in agentic settings under orchestration.roles, "
        "orchestration.rolesPerLevel, orchestration.spawn, or orchestration.harnesses; call "
        "spawn_agent_session with role (env.AR_SPAWN_ROLE), level, leaf_key, label, env, and "
        "provenance only, with context omitted and submit=false."
    )
    return _spawn_refusal(
        "spend-override-unsupported",
        harness,
        kind,
        detail=detail,
    )


def _brief_delivery_separate_refusal(
    context: str | None, submit: bool, *, kind: str
) -> dict[str, Any] | None:
    """Refuse the retired one-call brief contract before any settings, catalog, or spawn work."""

    if context is None and not submit:
        return None
    detail = (
        "brief delivery is separate: call spawn_agent_session without context and with submit=false; "
        "then call hosted_session_readiness(session_id=<returned session>, wait_seconds=<bound>); "
        "only after status='ready', post one operator_inbox entry with the exact agent_id, "
        "message_kind='dispatch-brief', and deliver_to_hosted=true; treat the seat as briefed only "
        "when deliveryState='delivered' and adapterDeliveryState is accepted or queued."
    )
    return _spawn_refusal("brief-delivery-separate", None, kind, detail=detail)


@dataclass(frozen=True)
class _SpawnDelivery:
    """Launch-phase session-command outcome; task instructions are never represented here."""

    session_commands_delivered: bool | None = None
    failure_capture: str | None = None


def _resolve_spawn_leaf(
    config: McpRuntimeConfig, leaf_ref: str | None, *, kind: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve one optional spawn leaf reference, preserving the public refusal payload."""
    if leaf_ref is None:
        return None, None
    try:
        return resolve_catalog_leaf_key(config, leaf_ref), None
    except LeafRefResolutionError as exc:
        return None, leaf_ref_refusal_payload("spawn_agent_session", leaf_ref, exc, kind=kind)


def spawn_agent_session_payload(
    config: McpRuntimeConfig,
    *,
    harness: str | None = None,
    leaf_key: str | None = None,
    replacement_for_leaf: str | None = None,
    context: str | None = None,
    submit: bool = False,
    label: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    env: dict[str, str] | None = None,
    launch_args: list[str] | None = None,
    prompt_keywords: list[str] | None = None,
    session_commands: list[str] | None = None,
    level: str | None = None,
    spawned_by_session: str | None = None,
    spawned_by_lifecycle: str | None = None,
    kind: str = "harness",
    session_id: str | None = None,
    host: TerminalHost | None = None,
    paster: TerminalPaster | None = None,
    session_log: object | None = None,
    which: Which | None = None,
) -> dict[str, Any]:
    """Spawn one role-configured, leaf-attached hosted session without a leaf brief.

    Success is ``spawned-unbriefed``. The exact returned session must separately pass
    ``hosted_session_readiness`` before one durable ``dispatch-brief`` inbox row is created. Legacy
    ``context`` or ``submit=True`` refuses before settings resolution, leaf lookup, catalog access,
    or terminal creation. Settings-owned session commands remain post-launch configuration; prompt
    keywords remain catalog provenance until durable brief delivery.

    Per-level knob resolution (ruling 2026-07-07T08:15): the settings rungs come from
    ``resolved_role_knobs(AR_SPAWN_ROLE, level)`` -- the ``orchestration.rolesPerLevel[level]``
    override deep-merged over the flat ``orchestration.roles`` default -- realizing the settings-only
    chain repo-local level override > global level override > repo-local role default > global role
    default > detection-gated default. ``level`` is the dispatcher's declaration (leaf|master|portfolio,
    default leaf); the RESOLVED level + its source (explicit/default) are recorded in spawn provenance.

    Settings-resolved model/effort ride the spawn env and one typed runner payload. The adapter
    validates and applies them through its native launch channel; no model/effort session command
    is synthesized. Free-form settings values remain recorded verbatim and caller-controlled spend
    inputs still refuse before spawning.
    """
    del paster, session_log  # retained injection parameters; bridge runner owns launch commands
    brief_refusal = _brief_delivery_separate_refusal(context, submit, kind=kind)
    if brief_refusal is not None:
        return brief_refusal
    spend_refusal = _caller_spend_override_refusal(
        harness=harness,
        model=model,
        effort=effort,
        env=env,
        launch_args=launch_args,
        prompt_keywords=prompt_keywords,
        session_commands=session_commands,
        kind=kind,
    )
    if spend_refusal is not None:
        return spend_refusal
    leaf_key, refusal = _resolve_spawn_leaf(config, leaf_key, kind=kind)
    if refusal is not None:
        return refusal
    replacement_for_leaf, refusal = _resolve_spawn_leaf(config, replacement_for_leaf, kind=kind)
    if refusal is not None:
        return refusal

    resolved_session_commands = list(session_commands or [])
    spawn_level: str | None = None
    spawn_level_source: str | None = None
    resolved_launch: ResolvedLaunch | None = None
    harnesses: tuple[Harness, ...] | None = None
    if kind == "harness":
        dispatch, refusal = _resolve_harness_dispatch(
            config,
            leaf_key=leaf_key or replacement_for_leaf,
            level=level,
            env=env,
            which=which,
        )
        if refusal is not None:
            return refusal
        assert dispatch is not None  # no refusal => a resolved dispatch bundle
        harness = dispatch.harness_id
        resolved_launch = dispatch.resolved_launch
        model = resolved_launch.model_key if resolved_launch is not None else dispatch.legacy_model
        effort = resolved_launch.effort if resolved_launch is not None else dispatch.legacy_effort
        launch_args = dispatch.launch_args
        prompt_keywords = dispatch.prompt_keywords
        resolved_session_commands = dispatch.session_commands
        spawn_level = dispatch.spawn_level
        spawn_level_source = dispatch.spawn_level_source
        harnesses = dispatch.registry

    sid = session_id or uuid4().hex
    spawn_env = _spawn_env(model, effort, env)
    provenance_lifecycle = spawned_by_lifecycle or _ambient_lifecycle_id()

    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    spawn_host = host if host is not None else TerminalHost()
    shell = os.environ.get("SHELL") or _DEFAULT_SHELL
    result = open_terminal_session(
        catalog=catalog,
        host=spawn_host,
        session_id=sid,
        kind=kind,
        workspace_root=config.workspace_root,
        shell=shell,
        harness=harness,
        label=label,
        leaf_key=leaf_key,
        replacement_for_leaf=replacement_for_leaf,
        env=spawn_env,
        launch_args=launch_args,
        prompt_keywords=prompt_keywords,
        session_commands=resolved_session_commands or None,
        spawn_level=spawn_level,
        spawn_level_source=spawn_level_source,
        resolved_launch=resolved_launch,
        legacy_model=model if resolved_launch is None else None,
        legacy_effort=effort if resolved_launch is None else None,
        spawned_by_session=spawned_by_session,
        spawned_by_lifecycle=provenance_lifecycle,
        control_root=config.coordination_root / "runtime" / "harness-control",
        which=which,
        harnesses=harnesses,
    )

    if result.status == "bad-kind":
        return _spawn_refusal("bad-kind", harness, kind, detail=result.detail)
    if result.status == "launch-conflict":
        return _spawn_refusal("launch-selection-invalid", harness, kind, detail=result.detail)
    if result.status == "leaf-taken":
        return _tool_payload(
            "spawn_agent_session",
            {
                "ok": False,
                "operation": "spawn_agent_session",
                "status": "leaf-taken",
                "session": sid,
                "harness": harness,
                "kind": result.kind,
                "leafKey": leaf_key,
                "seatRole": result.seat_role,
                "ownerSession": result.owner_session_id,
            },
        )

    entry = result.entry
    assert entry is not None  # opened => an upserted row
    delivery = _SpawnDelivery()

    return _tool_payload("spawn_agent_session", _spawned_payload(entry, delivery))


def _spawned_payload(entry: TerminalCatalogEntry, delivery: _SpawnDelivery) -> dict[str, Any]:
    """The spawned-unbriefed row plus settings-owned launch-command outcome."""
    return {
        "ok": True,
        "operation": "spawn_agent_session",
        "status": "spawned-unbriefed",
        "session": entry.id,
        "harness": entry.harness,
        "kind": entry.kind,
        "leafKey": entry.leaf_key,
        "seatRole": entry.binding_role,
        "replacementForLeaf": entry.replacement_for_leaf,
        "label": entry.label,
        "cwd": str(entry.cwd),
        "tmuxName": entry.tmux_name,
        "spawnedBySession": entry.spawned_by_session,
        "spawnedByLifecycle": entry.spawned_by_lifecycle,
        "spawnRole": entry.spawn_role,
        # The resolved dispatch level + how it was supplied (rolesPerLevel resolution input).
        "spawnLevel": entry.spawn_level,
        "spawnLevelSource": entry.spawn_level_source,
        "resolvedModel": entry.resolved_model,
        "resolvedEffort": entry.resolved_effort,
        # Free-form spawn provenance (260703-L16), echoed as recorded on the catalog row.
        "launchArgs": list(entry.launch_args) if entry.launch_args else None,
        "promptKeywords": list(entry.prompt_keywords) if entry.prompt_keywords else None,
        "sessionCommands": list(entry.session_commands) if entry.session_commands else None,
        "sessionCommandsDelivered": delivery.session_commands_delivered,
        "deliveryCapture": delivery.failure_capture,
        "controlState": entry.control_state,
        "controlEndpoint": str(entry.control_endpoint) if entry.control_endpoint else None,
        "controlProtocol": entry.control_protocol,
    }


def _spawn_refusal(
    status: str,
    harness: str | None,
    kind: str,
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    """A pre-spawn refusal payload (unknown/undetected harness or bad kind) -- nothing was spawned."""
    return _tool_payload(
        "spawn_agent_session",
        {
            "ok": False,
            "operation": "spawn_agent_session",
            "status": status,
            "session": "",
            "harness": harness,
            "kind": kind if kind in ("harness", "terminal") else None,
            "detail": detail,
        },
    )


def session_retire_payload(
    config: McpRuntimeConfig,
    *,
    actor_session_id: str,
    session_id: str,
    reason: str = "manual retire",
    host: TerminalHost | None = None,
) -> dict[str, Any]:
    """Retire ``session_id`` (issue #12): terminal mark + provenance, authority enforced server-side.

    ``actor_session_id`` is the RETIRING seat's own catalog session id (self-declared, mirroring the
    ``spawned_by_session`` provenance pattern -- there is no ambient "who am I" session-id
    resolution). Authority: owner-never-self-retires; a manager retires only worker/reviewer seats
    of its own master; the orchestrator retires anything. Idempotent against an already-retired
    target -- a second retire call reports ``already-retired``, never re-stamps provenance.
    """
    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    target_entry = catalog.get(session_id)
    if target_entry is None:
        return _tool_payload(
            "session_retire",
            {
                "ok": False,
                "operation": "session_retire",
                "status": "unknown-session",
                "session": session_id,
                "detail": f"no catalog entry for session {session_id!r}",
            },
        )
    actor_entry = catalog.get(actor_session_id)
    if actor_entry is None:
        return _tool_payload(
            "session_retire",
            {
                "ok": False,
                "operation": "session_retire",
                "status": "unknown-actor",
                "session": session_id,
                "detail": f"no catalog entry for actor session {actor_session_id!r}",
            },
        )
    if target_entry.status == "terminated":
        return _tool_payload(
            "session_retire",
            {
                "ok": True,
                "operation": "session_retire",
                "status": "already-retired",
                "session": session_id,
                "retiredAt": target_entry.retired_at,
                "retiredBySession": target_entry.retired_by_session,
                "retiredReason": target_entry.retired_reason,
                "retiredEdge": target_entry.retired_edge,
            },
        )
    try:
        check_retire_authority(
            SeatRef(
                session_id=actor_entry.id,
                leaf_key=actor_entry.binding_leaf_key,
                seat_role=actor_entry.binding_role,
            ),
            SeatRef(
                session_id=target_entry.id,
                leaf_key=target_entry.binding_leaf_key,
                seat_role=target_entry.binding_role,
            ),
        )
    except RetirePolicyError as exc:
        return _tool_payload(
            "session_retire",
            {
                "ok": False,
                "operation": "session_retire",
                "status": "retire-refused",
                "session": session_id,
                "detail": str(exc),
            },
        )
    retire_host = host if host is not None else TerminalHost()
    updated = retire_entry(
        catalog,
        retire_host,
        target_entry,
        at=now_iso(),
        by_session=actor_session_id,
        reason=reason,
        edge="manual",
    )
    assert updated is not None  # the entry existed above; nothing between here removes rows
    log_retire_event(config, updated)
    return _tool_payload(
        "session_retire",
        {
            "ok": True,
            "operation": "session_retire",
            "status": "retired",
            "session": session_id,
            "retiredAt": updated.retired_at,
            "retiredBySession": updated.retired_by_session,
            "retiredReason": updated.retired_reason,
            "retiredEdge": updated.retired_edge,
        },
    )


def session_rename_payload(
    config: McpRuntimeConfig,
    *,
    session_id: str,
    label: str,
) -> dict[str, Any]:
    """Rename ``session_id``'s display label post-spawn (issue #4). Identity text only -- never role."""
    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    entry = catalog.get(session_id)
    if entry is None or entry.status == "terminated":
        return _tool_payload(
            "session_rename",
            {
                "ok": False,
                "operation": "session_rename",
                "status": "unknown-session",
                "session": session_id,
                "label": label,
            },
        )
    updated = catalog.set_label(session_id, label)
    assert updated is not None
    log_rename_event(config, updated)
    return _tool_payload(
        "session_rename",
        {
            "ok": True,
            "operation": "session_rename",
            "status": "renamed",
            "session": session_id,
            "label": updated.label,
            "spawnedLabel": updated.spawned_label,
        },
    )
