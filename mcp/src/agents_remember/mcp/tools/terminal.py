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
    master_of,
)
from agents_remember.serving.seat_events import log_rename_event, log_retire_event
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_leaf_assignment import assign_terminal_session_to_leaf
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


def attach_terminal_session_to_leaf_payload(
    config: McpRuntimeConfig,
    *,
    session_id: str,
    leaf_key: str,
) -> dict[str, Any]:
    """Move an existing hosted terminal/chat session to a durable leaf key."""

    try:
        leaf_key = resolve_catalog_leaf_key(config, leaf_key)
    except LeafRefResolutionError as exc:
        return leaf_ref_refusal_payload("attach_terminal_session_to_leaf", leaf_key, exc)
    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    result = assign_terminal_session_to_leaf(
        catalog,
        session_id=session_id,
        leaf_key=leaf_key,
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
        },
    )


def _spawn_env(
    model: str | None,
    effort: str | None,
    env: dict[str, str] | None,
) -> dict[str, str]:
    """Fold the role knobs into the spawn env the terminal host seeds at ``tmux new-session``.

    Model/effort ride as namespaced env vars (``AR_SPAWN_MODEL`` / ``AR_SPAWN_EFFORT``) alongside any
    caller-supplied ``env`` (explicit keys win). This is the injection SEAM the leaf owns; mapping these
    to a given harness's concrete CLI flags / config is the role-knob resolution layer's job (job-file
    defaults + settings overrides), deferred to that layer -- this tool just delivers the passthrough.
    """
    resolved = dict(env or {})
    if model:
        resolved.setdefault("AR_SPAWN_MODEL", model)
    if effort:
        resolved.setdefault("AR_SPAWN_EFFORT", effort)
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

    Precedence: explicit argument > repo-local settings > global settings >
    detection-gated default (the first EFFECTIVE-registry harness detected on
    PATH). Every id resolves against ``settings.harnesses`` -- the builtin
    registry merged with the ``orchestration.harnesses`` family, so users can
    teach the system a new TUI or pre-customize a builtin's launch. An id known
    nowhere refuses loudly pointing at the manual (never a crash); ``settings``
    comes from the per-use loader (a malformed file raises -- never a silent
    fallback). Returns ``(harness, refusal_payload)`` with exactly one side set.
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
            f"PATH; install one of: {ids} (or pass harness explicitly)"
        ),
    )


@dataclass(frozen=True)
class _HarnessDispatch:
    """The pre-spawn knob bundle for one harness-kind dispatch (260703-L16).

    Everything the settings rungs resolved for this seat: the harness id (effective-registry
    validated), the effective registry itself, the folded model/effort (explicit args over role
    knobs), the free-form escape hatch, the RESOLVED session-command list (effort vehicle first),
    and the level provenance.
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
    harness: str | None,
    leaf_key: str | None,
    level: str | None,
    model: str | None,
    effort: str | None,
    env: dict[str, str] | None,
    launch_args: list[str] | None,
    prompt_keywords: list[str] | None,
    session_commands: list[str] | None,
    which: Which | None,
) -> tuple[_HarnessDispatch | None, dict[str, Any] | None]:
    """Resolve + validate every knob BEFORE anything spawns (260703-L16).

    Realizes the dispatch chain explicit args > repo-local level override > global level override >
    repo-local role default > global role default > spawn preference > detection-gated default: the
    settings rungs come from ``resolved_role_knobs(AR_SPAWN_ROLE, level)`` (per-use read; the
    repo-local layer selected by the qualified leaf key), the harness resolves against the EFFECTIVE
    registry, and model/effort are refused per-harness (``model-invalid``/``effort-invalid``) before
    any tmux exists. A session-vocabulary effort (claude ``ultracode``) contributes the FIRST
    session command. Returns ``(dispatch, refusal)`` with exactly one side set.
    """
    spawn_level = level or "leaf"
    spawn_level_source = "explicit" if level is not None else "default"
    if spawn_level not in _SPAWN_LEVELS:
        valid = ", ".join(_SPAWN_LEVELS)
        return None, _spawn_refusal(
            "level-invalid",
            harness,
            "harness",
            detail=f"unknown dispatch level {spawn_level!r}; valid levels: [{valid}]",
        )
    settings = load_agentic_settings(
        config.coordination_root, repo_root=_spawn_repo_root(config, leaf_key)
    )
    # The settings rungs, keyed by the AR_SPAWN_ROLE riding the caller's env (no role = no settings
    # rung, today's behavior); unset explicit args fold in from the level-merged role knobs.
    role = (env or {}).get("AR_SPAWN_ROLE")
    knobs = settings.resolved_role_knobs(role, spawn_level) if role else RoleKnobs()
    model = model or knobs.model
    effort = effort or knobs.effort
    if launch_args is None and knobs.launch_args:
        launch_args = list(knobs.launch_args)
    if prompt_keywords is None and knobs.prompt_keywords:
        prompt_keywords = list(knobs.prompt_keywords)
    resolved_session_commands = (
        list(session_commands) if session_commands is not None else list(knobs.session_commands)
    )
    # Harness rung order: explicit argument > role knobs (level-merged) > spawn preference >
    # detection-gated default (the last two inside _resolve_spawn_harness).
    found, refusal = _resolve_spawn_harness(settings, harness or knobs.harness, which)
    if refusal is not None:
        return None, refusal
    assert found is not None  # no refusal => a resolved effective-registry harness
    # Validate the EFFECTIVE values (env wins over arg wins over settings, mirroring _spawn_env).
    effective_model = (env or {}).get("AR_SPAWN_MODEL") or model
    effective_effort = (env or {}).get("AR_SPAWN_EFFORT") or effort
    refusal = _knob_refusal(found, effective_model, effective_effort)
    if refusal is not None:
        return None, refusal
    # A session-vocabulary effort is delivered as the FIRST post-launch session command, ahead of
    # any caller/settings free-form ones, then the brief.
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
        ("model-invalid", invalid_model_detail(found, effective_model) if effective_model else None),
        (
            "effort-invalid",
            invalid_effort_detail(found, effective_effort) if effective_effort else None,
        ),
    )
    for status, detail in checks:
        if detail is not None:
            return _spawn_refusal(status, found.id, "harness", detail=detail)
    return None


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


def _deliver_spawn_pastes(
    paster: TerminalPaster,
    tmux_name: str,
    session_commands: list[str],
    packet: str | None,
    submit: bool,
) -> _SpawnDelivery:
    """Deliver the session layer: session commands FIRST (each its own capture-verified paste, always
    submitted -- an unexecuted ``/effort ultracode`` would be a silent downgrade), THEN the brief.

    Every ``True`` here is capture-verified by the paster (260707-HFX-L3): a ``False`` means the
    pane provably shows no trace of the paste, and the failing capture rides the result.
    """
    session_commands_delivered: bool | None = None
    context_delivered: bool | None = None
    submitted: bool | None = None
    failure_capture: str | None = None
    failed = False
    if session_commands:
        session_commands_delivered = True
        for command_line in session_commands:
            outcome = paster.paste(tmux_name, command_line, submit=True)
            if not (outcome.delivered and outcome.submitted):
                session_commands_delivered = False
                failed = True
                failure_capture = outcome.capture or failure_capture
    if packet:
        # Workers auto-start (paste + submit); a human draft-only flow leaves submit=False so the
        # draft stays editable. Capture-verified server-side (the frontend pasteAndConfirm mirror).
        outcome = paster.paste(tmux_name, packet, submit=submit)
        context_delivered = outcome.delivered
        submitted = outcome.submitted if submit else None
        if not outcome.delivered or (submit and not outcome.submitted):
            failed = True
            failure_capture = outcome.capture or failure_capture
    if failed and failure_capture is None:
        failure_capture = _EMPTY_PANE_CAPTURE
    return _SpawnDelivery(
        session_commands_delivered, context_delivered, submitted, failure_capture
    )


def spawn_agent_session_payload(
    config: McpRuntimeConfig,
    *,
    harness: str | None = None,
    leaf_key: str | None = None,
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
    which: Which | None = None,
) -> dict[str, Any]:
    """Spawn one role-configured, leaf-attached, context-primed hosted session (L2 dispatch).

    Composes the EXISTING session primitives -- the shared serving opener (create + leaf claim +
    detached tmux ensure with env-seeded knobs), then a capture-verified context-packet paste with an
    optional submit (260707-HFX-L3: ``contextDelivered:true`` only after the pane provably shows the
    payload; an unverifiable delivery reports false WITH the pane capture) -- so orchestrators spawn
    managers and managers spawn workers without dashboard clicks. ``harness`` is optional
    (260703-L13): omitted, it resolves per-use through the agentic settings (repo-local over global
    ``orchestration.spawn.harness``), else the detection-gated default. Leaf uniqueness stays
    server-arbitrated: a taken leaf returns ``leaf-taken`` (never overridden).
    ``host``/``paster``/``which``/``session_id`` are injectable seams for tests.

    Per-level knob resolution (ruling 2026-07-07T08:15): the settings rungs come from
    ``resolved_role_knobs(AR_SPAWN_ROLE, level)`` -- the ``orchestration.rolesPerLevel[level]``
    override deep-merged over the flat ``orchestration.roles`` default -- realizing the chain
    explicit args > repo-local level override > global level override > repo-local role default >
    global role default > detection-gated default. ``level`` is the dispatcher's declaration
    (leaf|master|portfolio, default leaf); the RESOLVED level + its source (explicit/default) are
    recorded in spawn provenance.

    Knob application (260703-L16): ``model``/``effort`` keep riding the spawn env AND are mapped onto
    the harness argv per-harness via the EFFECTIVE registry -- the builtin table merged with the
    ``orchestration.harnesses`` settings family (new ids add a harness, builtin ids can be
    pre-customized; an id known nowhere refuses loudly pointing at ``docs/reference/harnesses.md``,
    never a crash). Env-only builtins get no flags; a settings-defined harness with no declared
    mapping refuses the knob with guidance (``model-invalid``/``effort-invalid``). ``effort`` is
    validated against the resolved harness's known vocabulary BEFORE anything spawns -- an unknown
    value returns the ``effort-invalid`` refusal naming the harness and its valid sets (the CLI
    would warn-and-silently-degrade). A session-level effort value (claude ``ultracode``) rides a
    post-launch session command instead of the flag. The free-form escape hatch is never validated,
    only recorded in spawn provenance: ``launch_args`` (verbatim argv), ``session_commands`` (each
    line pasted + submitted into the fresh session BEFORE the brief; the resolved list -- effort
    vehicle first, then the caller's -- is what gets recorded), ``prompt_keywords`` (prepended as
    the first line of the brief paste).
    """
    if leaf_key is not None:
        try:
            leaf_key = resolve_catalog_leaf_key(config, leaf_key)
        except LeafRefResolutionError as exc:
            return leaf_ref_refusal_payload("spawn_agent_session", leaf_key, exc, kind=kind)
    dispatch: _HarnessDispatch | None = None
    if kind == "harness":
        dispatch, refusal = _resolve_harness_dispatch(
            config,
            harness=harness,
            leaf_key=leaf_key,
            level=level,
            model=model,
            effort=effort,
            env=env,
            launch_args=launch_args,
            prompt_keywords=prompt_keywords,
            session_commands=session_commands,
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

    resolved_session_commands = (
        dispatch.session_commands if dispatch is not None else list(session_commands or [])
    )
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
        harness=harness if kind == "harness" else None,
        label=label,
        leaf_key=leaf_key,
        env=spawn_env,
        launch_args=launch_args,
        prompt_keywords=prompt_keywords,
        session_commands=resolved_session_commands or None,
        spawn_level=dispatch.spawn_level if dispatch is not None else None,
        spawn_level_source=dispatch.spawn_level_source if dispatch is not None else None,
        spawned_by_session=spawned_by_session,
        spawned_by_lifecycle=provenance_lifecycle,
        which=which,
        harnesses=dispatch.registry if dispatch is not None else None,
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
                "harness": harness if kind == "harness" else None,
                "kind": result.kind,
                "leafKey": leaf_key,
                "ownerSession": result.owner_session_id,
            },
        )

    entry = result.entry
    assert entry is not None  # opened => an upserted row
    _write_spawn_expectation_rows(config, entry)

    packet = _brief_packet(context, prompt_keywords)
    delivery = _SpawnDelivery()
    if resolved_session_commands or packet:
        spawn_paster = paster if paster is not None else TerminalPaster()
        delivery = _deliver_spawn_pastes(
            spawn_paster, entry.tmux_name, resolved_session_commands, packet, submit
        )

    return _tool_payload("spawn_agent_session", _spawned_payload(entry, delivery))


def _write_spawn_expectation_rows(config: McpRuntimeConfig, entry: TerminalCatalogEntry) -> None:
    """R2: every spawn atomically writes its ``briefed-by`` row (the composer must show the
    brief within T_boot), plus a ``turn-report-by`` row when the spawn claims a LEAF (a bare
    scratch/command chat with no ``leaf_key`` owes no turn report). Written in the SAME call as
    the catalog upsert -- never a forgettable follow-up step."""
    settings = load_agentic_settings(config.coordination_root)
    store = ExpectationRowStore(observer_root(config))
    now = datetime.now(UTC)
    write_expectation_row(
        store,
        row_id=uuid4().hex,
        now=now,
        kind="briefed-by",
        sla_seconds=settings.expectations.sla_for("briefed-by"),
        source_id=entry.id,
        subject_agent_id=entry.id,
        subject_lifecycle_id=entry.lifecycle_id,
        leaf_key=entry.leaf_key,
        note=f"briefed-by: {entry.label} ({entry.spawn_role or entry.kind})",
    )
    if entry.leaf_key is not None:
        write_expectation_row(
            store,
            row_id=uuid4().hex,
            now=now,
            kind="turn-report-by",
            sla_seconds=settings.expectations.sla_for("turn-report-by"),
            source_id=entry.id,
            subject_agent_id=entry.id,
            subject_lifecycle_id=entry.lifecycle_id,
            leaf_key=entry.leaf_key,
            note=f"turn-report-by: {entry.leaf_key}",
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
        "label": entry.label,
        "cwd": str(entry.cwd),
        "tmuxName": entry.tmux_name,
        "spawnedBySession": entry.spawned_by_session,
        "spawnedByLifecycle": entry.spawned_by_lifecycle,
        "spawnRole": entry.spawn_role,
        # The resolved dispatch level + how it was supplied (rolesPerLevel resolution input).
        "spawnLevel": entry.spawn_level,
        "spawnLevelSource": entry.spawn_level_source,
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
                role=actor_entry.spawn_role,
                master=master_of(actor_entry.leaf_key),
            ),
            SeatRef(
                session_id=target_entry.id,
                role=target_entry.spawn_role,
                master=master_of(target_entry.leaf_key),
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
