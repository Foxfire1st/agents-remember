"""Payload builders for dashboard terminal-session catalog tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agents_remember.controlplane.expectation_rows import ExpectationRowStore, write_expectation_row
from agents_remember.kernel.agentic_settings import (
    AgenticSettings,
    RoleKnobs,
    load_agentic_settings,
)
from agents_remember.observer import observer_root
from agents_remember.observer.ambient import ambient
from agents_remember.observer.events import now_iso
from agents_remember.serving.harness_logs import HarnessSessionLog
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
from agents_remember.serving.injector import (
    DeliveryRow,
    deliver,
    verify_or_reissue_command,
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
    escape hatch, the RESOLVED session-command list (effort vehicle first), and the level provenance.
    """

    harness_id: str
    registry: tuple[Harness, ...]
    model: str | None
    effort: str | None
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
    model/effort are refused per-harness (``model-invalid``/``effort-invalid``) before any tmux exists.
    A session-vocabulary effort (claude ``ultracode``) contributes the FIRST session command. Returns
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
    # Validate the settings-resolved values. Caller-provided AR_SPAWN_MODEL/AR_SPAWN_EFFORT keys are
    # rejected before this function runs, so env cannot replace the developer's settings.
    effective_model = model
    effective_effort = effort
    refusal = _knob_refusal(found, effective_model, effective_effort)
    if refusal is not None:
        return None, refusal
    # A session-vocabulary effort is delivered as the FIRST post-launch session command, ahead of
    # any settings-owned free-form ones, then the brief.
    resolved_session_commands = (
        effort_session_commands(found, effective_effort) + resolved_session_commands
    )
    return (
        _HarnessDispatch(
            harness_id=found.id,
            registry=settings.harnesses,
            model=model,
            effort=effort,
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
    """The ``model-invalid``/``effort-invalid`` pre-spawn refusal, or ``None`` when the knobs apply."""
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
        "spawn_agent_session with role (env.AR_SPAWN_ROLE), level, leaf_key, context, submit, label, "
        "env, and provenance only."
    )
    return _spawn_refusal(
        "spend-override-unsupported",
        harness,
        kind,
        detail=detail,
    )


def _brief_packet(context: str | None, prompt_keywords: list[str] | None) -> str | None:
    """The brief paste text: promptKeywords ride as its first line (delivered alone with no brief)."""
    if not prompt_keywords:
        return context
    keyword_line = " ".join(prompt_keywords)
    return f"{keyword_line}\n\n{context}" if context else keyword_line


_EMPTY_PANE_CAPTURE = "(empty pane capture)"
"""Explicit stand-in when a failed delivery's pane capture is empty (a vanished/unreadable pane):
review N3 alignment with ``inbox_delivery`` -- a False outcome NEVER ships evidence-less, so the
``deliveryCapture`` field is present (with this marker) rather than silently omitted."""


@dataclass(frozen=True)
class _SpawnDelivery:
    """Delivery outcomes for one spawn's session layer (``None`` = that piece was not sent).

    ``failure_capture`` is the pane capture attached by the paster to the latest failed paste --
    the 260707-HFX-L3 loud-failure evidence (SF-1: a bare ``contextDelivered:true`` once masked a
    codex seat that booted clean with no payload). ``None`` when every sent piece verified.
    """

    session_commands_delivered: bool | None = None
    context_delivered: bool | None = None
    submitted: bool | None = None
    failure_capture: str | None = None
    bound_entry_id: str | None = None
    session_log_path: Path | None = None


def _deliver_spawn_pastes(
    paster: TerminalPaster,
    tmux_name: str,
    session_commands: list[str],
    packet: str | None,
    submit: bool,
    *,
    entry_id: str,
    harness: str | None,
    cwd: Path,
    created_at: str,
    session_log: HarnessSessionLog | None = None,
) -> _SpawnDelivery:
    """Deliver commands first, bind on the id-bearing brief, then verify every input in that log.

    260707-HFX2-L3 (R3, ONE PATH): this is no longer a separate raw-spawn delivery loop -- every
    paste here goes through ``serving.injector.deliver``, the same path ``inbox_delivery.py`` calls.
    Commands remain verbatim local inputs; the brief gains the standard id envelope that binds the
    spawn-cwd session log. A failed final outcome alone captures the pane for diagnostics.

    Numeric bound (L15 FIX-K/f): one Claude input has three calibrated 40.3 s log windows (initial,
    Enter re-press, re-paste), at most seven 5 s transport operations, two 5 s absence captures, two
    100 ms settles, and one 5 s failure capture: <=171.1 s. Codex substitutes 29.0 s windows:
    <=137.2 s. A visible prior payload takes the shorter clear-or-fail branch. Commands sent before the
    brief do one unverified transport, then incur the same bound only when retroactive evidence is
    missing/errored. No screen wait or switch flow remains.
    """
    session_commands_delivered: bool | None = None
    context_delivered: bool | None = None
    submitted: bool | None = None
    failure_capture: str | None = None
    bound_entry_id: str | None = None
    session_log_path: Path | None = None
    failed = False
    log = session_log or HarnessSessionLog(
        harness=harness or "",
        cwd=cwd,
        started_at=datetime.fromisoformat(created_at),
    )
    command_rows: list[DeliveryRow] = []
    if session_commands:
        session_commands_delivered = True
        for command_line in session_commands:
            row = DeliveryRow(
                kind="session-command",
                entry_id=entry_id,
                text=command_line,
                submit=True,
                envelope=False,
            )
            command_rows.append(row)
            result = deliver(
                row,
                tmux_name=tmux_name,
                paster=paster,
                harness=harness,
                session_log=log,
            )
            if result.outcome in ("blocked", "failed"):
                failure_capture = result.capture or failure_capture
    if packet:
        row = DeliveryRow(
            kind="brief", entry_id=entry_id, text=packet, submit=submit, envelope=True
        )
        result = deliver(
            row,
            tmux_name=tmux_name,
            paster=paster,
            harness=harness,
            session_log=log,
        )
        context_delivered = result.outcome == "acked"
        submitted = result.submitted if submit else None
        bound_entry_id = result.bound_entry_id
        session_log_path = result.session_log_path
        if result.outcome in ("blocked", "failed") or (submit and result.outcome != "acked"):
            failed = True
            failure_capture = result.capture or failure_capture
    if command_rows:
        if log.bound_path is None:
            session_commands_delivered = False
            failed = True
        else:
            for row in command_rows:
                result = verify_or_reissue_command(
                    row,
                    tmux_name=tmux_name,
                    paster=paster,
                    harness=harness,
                    session_log=log,
                )
                if result.outcome != "acked":
                    session_commands_delivered = False
                    failed = True
                    failure_capture = result.capture or failure_capture
            session_log_path = log.bound_path
            bound_entry_id = entry_id
    if failed and failure_capture is None:
        failure_capture = _EMPTY_PANE_CAPTURE
    return _SpawnDelivery(
        session_commands_delivered=session_commands_delivered,
        context_delivered=context_delivered,
        submitted=submitted,
        failure_capture=failure_capture,
        bound_entry_id=bound_entry_id,
        session_log_path=session_log_path,
    )


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
    session_log: HarnessSessionLog | None = None,
    which: Which | None = None,
) -> dict[str, Any]:
    """Spawn one role-configured, leaf-attached, context-primed hosted session (L2 dispatch).

    Composes the EXISTING session primitives -- the shared serving opener (create + leaf claim +
    detached tmux ensure with argv/session-command-pinned knobs), then a log-verified context input
    with optional submit (``contextDelivered:true`` only after the id-bearing user record exists) --
    so orchestrators spawn
    managers and managers spawn workers without dashboard clicks. The harness is resolved per-use
    through the agentic settings (role/level knobs, repo-local over global
    ``orchestration.spawn.harness``), else the detection-gated default. Leaf uniqueness stays
    server-arbitrated: a taken leaf returns ``leaf-taken`` (never overridden).
    ``host``/``paster``/``which``/``session_id`` are injectable seams for tests.

    Per-level knob resolution (ruling 2026-07-07T08:15): the settings rungs come from
    ``resolved_role_knobs(AR_SPAWN_ROLE, level)`` -- the ``orchestration.rolesPerLevel[level]``
    override deep-merged over the flat ``orchestration.roles`` default -- realizing the settings-only
    chain repo-local level override > global level override > repo-local role default > global role
    default > detection-gated default. ``level`` is the dispatcher's declaration (leaf|master|portfolio,
    default leaf); the RESOLVED level + its source (explicit/default) are recorded in spawn provenance.

    Knob application (260703-L16): settings-resolved ``model``/``effort`` keep riding the spawn env
    AND are mapped onto the harness argv per-harness via the EFFECTIVE registry -- the builtin table
    merged with the ``orchestration.harnesses`` settings family (new ids add a harness, builtin ids
    can be pre-customized; an id known nowhere refuses loudly pointing at
    ``docs/reference/harnesses.md``, never a crash). Env-only builtins get no flags; a
    settings-defined harness with no declared mapping refuses the knob with guidance
    (``model-invalid``/``effort-invalid``). ``effort`` is validated against the resolved harness's
    known vocabulary BEFORE anything spawns -- an unknown value returns the ``effort-invalid``
    refusal naming the harness and its valid sets (the CLI would warn-and-silently-degrade). A
    session-level effort value (claude ``ultracode``) rides a post-launch session command instead of
    the flag. The settings-owned free-form escape hatch is never validated, only recorded in spawn
    provenance: ``launch_args`` (verbatim argv), ``session_commands`` (each line pasted + submitted
    into the fresh session BEFORE the brief; the resolved list -- effort vehicle first, then
    settings commands -- is what gets recorded), ``prompt_keywords`` (prepended as the first line of
    the brief paste).
    """
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
    resolved_model: str | None = None
    resolved_effort: str | None = None
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
        model = dispatch.model
        effort = dispatch.effort
        launch_args = dispatch.launch_args
        prompt_keywords = dispatch.prompt_keywords
        resolved_session_commands = dispatch.session_commands
        spawn_level = dispatch.spawn_level
        spawn_level_source = dispatch.spawn_level_source
        resolved_model = dispatch.model
        resolved_effort = dispatch.effort
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
        resolved_model=resolved_model,
        resolved_effort=resolved_effort,
        spawned_by_session=spawned_by_session,
        spawned_by_lifecycle=provenance_lifecycle,
        which=which,
        harnesses=harnesses,
    )

    if result.status == "bad-kind":
        return _spawn_refusal("bad-kind", harness, kind, detail=result.detail)
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
    spawn_paster = paster if paster is not None else TerminalPaster()
    _write_spawn_expectation_rows(config, entry)

    packet = _brief_packet(context, prompt_keywords)
    delivery = _SpawnDelivery()
    if resolved_session_commands or packet:
        delivery = _deliver_spawn_pastes(
            spawn_paster,
            entry.tmux_name,
            resolved_session_commands,
            packet,
            submit,
            entry_id=entry.id,
            harness=harness,
            cwd=entry.cwd,
            created_at=entry.created_at,
            session_log=session_log,
        )
        if delivery.session_log_path is not None and delivery.bound_entry_id is not None:
            bound = catalog.bind_session_log(
                entry.id,
                entry_id=delivery.bound_entry_id,
                path=delivery.session_log_path,
            )
            if bound is not None:
                entry = bound

    return _tool_payload("spawn_agent_session", _spawned_payload(entry, delivery))


def _write_spawn_expectation_rows(config: McpRuntimeConfig, entry: TerminalCatalogEntry) -> None:
    """Every spawn atomically writes ``briefed-by`` (the id-bearing log entry must exist), plus a
    ``turn-report-by`` row when the spawn carries a bound or declared replacement leaf (a bare
    scratch/command chat with no ``leaf_key`` owes no turn report). Written in the SAME call as
    the catalog upsert -- never a forgettable follow-up step."""
    settings = load_agentic_settings(config.coordination_root)
    store = ExpectationRowStore(observer_root(config))
    now = datetime.now(UTC)
    expectation_leaf = entry.leaf_key or entry.replacement_for_leaf
    write_expectation_row(
        store,
        row_id=uuid4().hex,
        now=now,
        kind="briefed-by",
        sla_seconds=settings.expectations.sla_for("briefed-by"),
        source_id=entry.id,
        subject_agent_id=entry.id,
        subject_lifecycle_id=entry.lifecycle_id,
        leaf_key=expectation_leaf,
        seat_role=entry.binding_role,
        note=f"briefed-by: {entry.label} ({entry.spawn_role or entry.kind})",
    )
    if expectation_leaf is not None:
        write_expectation_row(
            store,
            row_id=uuid4().hex,
            now=now,
            kind="turn-report-by",
            sla_seconds=settings.expectations.sla_for("turn-report-by"),
            source_id=entry.id,
            subject_agent_id=entry.id,
            subject_lifecycle_id=entry.lifecycle_id,
            leaf_key=expectation_leaf,
            seat_role=entry.binding_role,
            note=f"turn-report-by: {expectation_leaf}",
        )


def _spawned_payload(entry: TerminalCatalogEntry, delivery: _SpawnDelivery) -> dict[str, Any]:
    """The ``spawned`` payload: the upserted row (incl. the L16 provenance) + delivery outcomes."""
    return {
        "ok": True,
        "operation": "spawn_agent_session",
        "status": "spawned",
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
        "sessionLogEntryId": entry.session_log_entry_id,
        "sessionLogPath": str(entry.session_log_path) if entry.session_log_path else None,
        # Free-form spawn provenance (260703-L16), echoed as recorded on the catalog row.
        "launchArgs": list(entry.launch_args) if entry.launch_args else None,
        "promptKeywords": list(entry.prompt_keywords) if entry.prompt_keywords else None,
        "sessionCommands": list(entry.session_commands) if entry.session_commands else None,
        "sessionCommandsDelivered": delivery.session_commands_delivered,
        "contextDelivered": delivery.context_delivered,
        "submitted": delivery.submitted,
        # 260707-HFX-L3 loud failure: on any False outcome above the pane capture is the attached
        # evidence -- the caller must treat the seat as blind, never assume the brief landed.
        "deliveryCapture": delivery.failure_capture,
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
