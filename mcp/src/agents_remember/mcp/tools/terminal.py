"""Payload builders for dashboard terminal-session catalog tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.observer.ambient import ambient
from agents_remember.serving.harnesses import HARNESSES, Which, find_harness, is_detected
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.serving.terminal_leaf_assignment import assign_terminal_session_to_leaf
from agents_remember.serving.terminal_opener import open_terminal_session
from agents_remember.serving.terminal_paste import TerminalPaster

from .base import _tool_payload

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

_DEFAULT_SHELL = "/bin/bash"


def attach_terminal_session_to_leaf_payload(
    config: McpRuntimeConfig,
    *,
    session_id: str,
    leaf_key: str,
) -> dict[str, Any]:
    """Move an existing hosted terminal/chat session to a durable leaf key."""

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
    config: McpRuntimeConfig,
    harness: str | None,
    leaf_key: str | None,
    which: Which | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve the harness id for a spawn (260703-L13 seam).

    Precedence: explicit argument > repo-local settings > global settings >
    detection-gated default (the first registry harness detected on PATH).
    Settings are read PER-USE through the agentic-settings loader (which
    validates preference values against the harness registry ids); a malformed
    settings file raises -- never a silent fallback. Returns
    ``(harness_id, refusal_payload)`` with exactly one side set.
    """
    if harness is not None:
        found = find_harness(harness)
        if found is None:
            return None, _spawn_refusal(
                "harness-unknown", harness, "harness", detail=f"unknown harness: {harness!r}"
            )
        if not is_detected(found, which=which):
            return None, _spawn_refusal(
                "harness-not-detected",
                harness,
                "harness",
                detail=f"harness not installed: {harness!r}",
            )
        return harness, None

    settings = load_agentic_settings(
        config.coordination_root, repo_root=_spawn_repo_root(config, leaf_key)
    )
    preferred = settings.spawn_harness
    if preferred is not None:
        found = find_harness(preferred)
        assert found is not None  # the loader validates against the registry ids
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
        return preferred, None

    for candidate in HARNESSES:
        if is_detected(candidate, which=which):
            return candidate.id, None
    ids = ", ".join(candidate.id for candidate in HARNESSES)
    return None, _spawn_refusal(
        "harness-not-detected",
        None,
        "harness",
        detail=(
            "no harness given, none preferred in settings, and none detected on "
            f"PATH; install one of: {ids} (or pass harness explicitly)"
        ),
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
    detached tmux ensure with env-seeded knobs), then an echo-confirmed context-packet paste with an
    optional submit -- so orchestrators spawn managers and managers spawn workers without dashboard
    clicks. ``harness`` is optional (260703-L13): omitted, it resolves per-use through the agentic
    settings (repo-local over global ``orchestration.spawn.harness``), else the detection-gated
    default. Leaf uniqueness stays server-arbitrated: a taken leaf returns ``leaf-taken`` (never
    overridden). ``host``/``paster``/``which``/``session_id`` are injectable seams for tests.
    """
    if kind == "harness":
        harness, refusal = _resolve_spawn_harness(config, harness, leaf_key, which)
        if refusal is not None:
            return refusal

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
        spawned_by_session=spawned_by_session,
        spawned_by_lifecycle=provenance_lifecycle,
        which=which,
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

    context_delivered: bool | None = None
    submitted: bool | None = None
    if context:
        # Workers auto-start (paste + submit); a human draft-only flow leaves submit=False so the
        # draft stays editable. Echo-confirmed server-side (the frontend pasteAndConfirm mirror).
        spawn_paster = paster if paster is not None else TerminalPaster()
        outcome = spawn_paster.paste(entry.tmux_name, context, submit=submit)
        context_delivered = outcome.delivered
        submitted = outcome.submitted if submit else None

    return _tool_payload(
        "spawn_agent_session",
        {
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
            "contextDelivered": context_delivered,
            "submitted": submitted,
        },
    )


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
