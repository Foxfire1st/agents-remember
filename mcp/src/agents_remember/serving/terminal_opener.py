"""The shared hosted-session opener: one spawn path for the dashboard route and the MCP tool.

Slice L2 (agent-facing dispatch) extracts the opener composition that used to live inline in
``app.py``'s ``POST /api/terminal/{session}`` handler so the FastAPI route AND the agent-facing
``spawn_agent_session`` MCP tool spawn through the *same* function -- the invariant is **no parallel
spawn path**. It mirrors ``terminal_leaf_assignment.assign_terminal_session_to_leaf``: a serving-layer
policy helper both call paths reuse instead of duplicating the leaf-claim + ensure + upsert sequence.

The opener is transport-agnostic. It returns a small :class:`OpenTerminalResult`; ``app.py`` maps it to
an HTTP ``JSONResponse`` (200 / 409 / 400) and the MCP tool maps it to a validated tool payload. The
leaf-uniqueness check stays **server-arbitrated**: a taken leaf returns ``leaf-taken`` (the 409) and the
caller surfaces it, never overrides it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.observer.events import now_iso
from agents_remember.serving.harness_control_adapter import protocol_adapter_status
from agents_remember.serving.harness_control_ipc import LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    ControlIdentity,
    ControlState,
)
from agents_remember.serving.harness_control_runner import RunnerConfig, control_runner_command
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.harnesses import (
    Harness,
    Which,
    find_harness,
    invalid_effort_detail,
    invalid_model_detail,
    is_detected,
    knob_argv,
    unknown_harness_detail,
)
from agents_remember.serving.seat_binding import migrated_seat_role
from agents_remember.serving.terminal import (
    TerminalHost,
    TerminalSessionBinding,
    terminal_session_name,
)
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    TerminalSessionKind,
)
from agents_remember.serving.terminal_leaf_assignment import leaf_conflict_owner

OpenTerminalStatus = Literal["opened", "leaf-taken", "bad-kind"]


@dataclass(frozen=True)
class OpenTerminalResult:
    """Outcome of the shared opener -- an upserted catalog row, a leaf conflict, or a bad kind."""

    status: OpenTerminalStatus
    entry: TerminalCatalogEntry | None = None
    kind: TerminalSessionKind | None = None
    seat_role: str | None = None
    owner_session_id: str | None = None
    detail: str | None = None


def resolve_terminal_launch(
    kind: str,
    *,
    workspace_root: Path,
    shell: str,
    harness: str | None = None,
    which: Which | None = None,
    model: str | None = None,
    effort: str | None = None,
    launch_args: Sequence[str] | None = None,
    harnesses: Sequence[Harness] | None = None,
) -> tuple[Path, list[str]]:
    """Resolve a launch ``kind`` to ``(cwd, argv)`` -- the server owns the command.

    ``terminal`` spawns ``shell`` at the workspace root (the dashboard-owned scratch terminal,
    slice 6e-2a). ``harness`` spawns the registered TUI harness ``harness`` (its id) at the same
    root (slice 6e-2b), rejecting an absent id, an unknown id, or one whose CLI is not installed
    (``which`` defaults to :func:`shutil.which`). Every other kind raises ``ValueError`` -- the
    opener endpoint turns that into a 400. (Lived in ``app.py`` before L2; moved here so the opener
    owns launch resolution and both the route and the MCP tool share it without importing ``app``.)

    ``harnesses`` is the EFFECTIVE registry to resolve ids against (the builtin table merged with
    the ``orchestration.harnesses`` settings family -- ``AgenticSettings.harnesses``); ``None``
    means the builtin defaults. An id known nowhere raises the loud teach-it-via-settings refusal
    (``unknown_harness_detail``), never a crash.

    L2 keeps native-adapter launches focused on the settings-owned base command and free-form launch
    args. Their typed :class:`ResolvedLaunch` rides the runner payload; the adapter validates it
    against dynamic advertise and applies model/effort after this function returns. ``model`` and
    ``effort`` remain an explicit compatibility seam for settings-defined non-native harnesses,
    whose declared registry mappings are their only launch port. A plain ``terminal`` spawn ignores
    harness launch material.
    """
    if kind == "terminal":
        return workspace_root, [shell]
    if kind == "harness":
        if harness is None:
            raise ValueError("harness kind requires a harness id")
        found = find_harness(harness, registry=harnesses)
        if found is None:
            raise ValueError(unknown_harness_detail(harness, registry=harnesses))
        if not is_detected(found, which=which):
            raise ValueError(f"harness not installed: {harness!r}")
        for detail in (
            invalid_model_detail(found, model) if model else None,
            invalid_effort_detail(found, effort) if effort else None,
        ):
            if detail is not None:
                raise ValueError(detail)
        argv = list(found.argv)
        argv += knob_argv(found, model=model, effort=effort)
        if launch_args:
            argv += [str(arg) for arg in launch_args]
        return workspace_root, argv
    raise ValueError(f"unknown terminal kind: {kind!r}")


def _terminal_label(kind: TerminalSessionKind, harness: str | None, fallback: str) -> str:
    if kind == "terminal":
        return "Terminal"
    return harness or fallback


def _control_metadata(
    existing: TerminalCatalogEntry | None,
    *,
    kind: TerminalSessionKind,
    harness: str | None,
    endpoint: Path | None,
) -> tuple[ControlState | None, Path | None, str | None]:
    if kind != "harness" or harness is None:
        return None, None, None
    state = (
        existing.control_state
        if existing is not None and existing.control_endpoint == endpoint
        else None
    )
    resolved_endpoint = endpoint or (existing.control_endpoint if existing is not None else None)
    protocol = existing.control_protocol if existing is not None else None
    return (
        state or protocol_adapter_status(harness),
        resolved_endpoint,
        protocol or CONTROL_PROTOCOL_VERSION,
    )


def _previous(existing: TerminalCatalogEntry | None, name: str) -> Any:
    return getattr(existing, name) if existing is not None else None


def _preserved(
    existing: TerminalCatalogEntry | None,
    new_value: object,
    name: str,
) -> Any:
    """Keep write-once spawn provenance when a session is reopened."""

    return new_value or _previous(existing, name)


def _session_command(
    *,
    session_id: str,
    resolved_kind: TerminalSessionKind,
    harness: str | None,
    cwd: Path,
    vendor_command: list[str],
    existing: TerminalCatalogEntry | None,
    host: TerminalHost,
    workspace_root: Path,
    control_endpoint: Path | None,
    control_root: Path | None,
    session_commands: Sequence[str] | None,
    resolved_launch: ResolvedLaunch | None,
    created_at: str,
    tmux_name: str,
) -> tuple[list[str], Path | None, bool]:
    legacy_running = bool(
        resolved_kind == "harness"
        and existing is not None
        and existing.control_endpoint is None
        and host.has_session(existing.tmux_name)
    )
    if legacy_running:
        assert existing is not None
        return list(existing.command), None, True
    if resolved_kind != "harness" or harness is None:
        return vendor_command, control_endpoint, False
    endpoint_root = control_root or workspace_root / ".agents-remember-control"
    identity = ControlIdentity(
        ar_session_id=session_id,
        tmux_name=tmux_name,
        created_at=created_at,
    )
    endpoint = control_endpoint or LocalControlEndpoint.for_session(endpoint_root, identity).path
    command = control_runner_command(
        RunnerConfig(
            identity=identity,
            harness_id=harness,
            cwd=cwd,
            argv=tuple(vendor_command),
            endpoint_root=endpoint_root,
            session_commands=tuple(session_commands or ()),
            resolved_launch=resolved_launch,
        )
    )
    return list(command), endpoint, False


def _opened_catalog_entry(
    *,
    opened: TerminalSessionBinding,
    existing: TerminalCatalogEntry | None,
    resolved_label: str,
    resolved_kind: TerminalSessionKind,
    harness: str | None,
    lifecycle_id: str | None,
    command: list[str],
    created_at: str,
    attached_at: str,
    leaf_key: str | None,
    seat_role: str | None,
    replacement_for_leaf: str | None,
    spawned_by_session: str | None,
    spawned_by_lifecycle: str | None,
    spawn_env: Mapping[str, str],
    launch_args: Sequence[str] | None,
    prompt_keywords: Sequence[str] | None,
    session_commands: Sequence[str] | None,
    spawn_level: str | None,
    spawn_level_source: str | None,
    resolved_model: str | None,
    resolved_effort: str | None,
    legacy_running: bool,
    control_endpoint: Path | None,
) -> TerminalCatalogEntry:
    control_state, resolved_endpoint, control_protocol = _control_metadata(
        existing,
        kind=resolved_kind,
        harness=harness,
        endpoint=control_endpoint,
    )
    if legacy_running:
        control_state = "unsupported"
        resolved_endpoint = None
        control_protocol = None
    return TerminalCatalogEntry(
        id=opened.sid,
        label=resolved_label,
        kind=resolved_kind,
        harness=harness,
        lifecycle_id=lifecycle_id,
        cwd=opened.cwd,
        tmux_name=opened.tmux_name,
        command=tuple(command),
        created_at=created_at,
        last_attached_at=attached_at,
        status="running",
        leaf_key=_preserved(existing, leaf_key, "leaf_key"),
        seat_role=seat_role,
        replacement_for_leaf=_preserved(existing, replacement_for_leaf, "replacement_for_leaf"),
        spawned_by_session=_preserved(existing, spawned_by_session, "spawned_by_session"),
        spawned_by_lifecycle=_preserved(existing, spawned_by_lifecycle, "spawned_by_lifecycle"),
        spawn_role=_preserved(existing, spawn_env.get("AR_SPAWN_ROLE"), "spawn_role"),
        launch_args=_preserved(
            existing, tuple(launch_args) if launch_args else None, "launch_args"
        ),
        prompt_keywords=_preserved(
            existing,
            tuple(prompt_keywords) if prompt_keywords else None,
            "prompt_keywords",
        ),
        session_commands=_preserved(
            existing,
            tuple(session_commands) if session_commands else None,
            "session_commands",
        ),
        spawn_level=_preserved(existing, spawn_level, "spawn_level"),
        spawn_level_source=_preserved(existing, spawn_level_source, "spawn_level_source"),
        resolved_model=_preserved(existing, resolved_model, "resolved_model"),
        resolved_effort=_preserved(existing, resolved_effort, "resolved_effort"),
        session_log_entry_id=_previous(existing, "session_log_entry_id"),
        session_log_path=_previous(existing, "session_log_path"),
        control_state=control_state,
        control_endpoint=resolved_endpoint,
        control_protocol=control_protocol,
        control_activity=("unknown" if legacy_running else _previous(existing, "control_activity")),
        control_acceptance=(
            "unsupported" if legacy_running else _previous(existing, "control_acceptance")
        ),
        control_vendor_session_id=_previous(existing, "control_vendor_session_id"),
        control_pending_interaction=_previous(existing, "control_pending_interaction"),
        control_last_event_sequence=_previous(existing, "control_last_event_sequence"),
        control_raw=(
            {"detail": "legacy raw-TUI session has no protocol bridge"}
            if legacy_running
            else _previous(existing, "control_raw")
        ),
    )


def open_terminal_session(
    *,
    catalog: TerminalCatalog,
    host: TerminalHost,
    session_id: str,
    kind: str,
    workspace_root: Path,
    shell: str,
    harness: str | None = None,
    label: str | None = None,
    lifecycle_id: str | None = None,
    leaf_key: str | None = None,
    replacement_for_leaf: str | None = None,
    env: Mapping[str, str] | None = None,
    launch_args: Sequence[str] | None = None,
    prompt_keywords: Sequence[str] | None = None,
    session_commands: Sequence[str] | None = None,
    spawn_level: str | None = None,
    spawn_level_source: str | None = None,
    resolved_launch: ResolvedLaunch | None = None,
    legacy_model: str | None = None,
    legacy_effort: str | None = None,
    spawned_by_session: str | None = None,
    spawned_by_lifecycle: str | None = None,
    control_endpoint: Path | None = None,
    control_root: Path | None = None,
    which: Which | None = None,
    harnesses: Sequence[Harness] | None = None,
) -> OpenTerminalResult:
    """Spawn + own one hosted session, the single opener both the route and the MCP tool call.

    Resolves the launch command server-side (``resolve_terminal_launch`` -- a harness **id**, never a
    wire argv), claims ``leaf_key`` under the per-(leaf, role) uniqueness rule, ensures the detached
    tmux session (seeding ``env`` at spawn -- the L2 knob-injection seam), and upserts the durable
    catalog row (carrying spawned-by provenance). A taken leaf returns ``leaf-taken`` WITHOUT spawning
    or mutating; an unknown/undetected kind/harness returns ``bad-kind``.

    L2 carries one typed ``ResolvedLaunch`` into the hosted runner. The runner performs token-free
    dynamic catalog validation and applies adapter-native launch knobs before the real vendor
    process starts. ``legacy_model``/``legacy_effort`` are used only for explicitly mapped
    settings-defined non-native harnesses. The namespaced spawn env remains as settings provenance.
    ``harnesses`` is the effective registry (builtin merged with the
    ``orchestration.harnesses`` settings family) ids resolve against; ``None`` = builtin defaults.
    The free-form escape hatch is recorded on the durable row as spawn provenance:
    ``launch_args`` (appended verbatim to the argv), ``prompt_keywords`` (the caller prepends them
    to the brief paste), and ``session_commands`` (the caller pastes them post-launch, before the
    brief) -- all three are never validated, only recorded.
    """
    spawn_env = dict(env or {})
    try:
        cwd, vendor_command = resolve_terminal_launch(
            kind,
            workspace_root=workspace_root,
            shell=shell,
            harness=harness,
            which=which,
            model=legacy_model,
            effort=legacy_effort,
            launch_args=launch_args,
            harnesses=harnesses,
        )
    except ValueError as exc:
        return OpenTerminalResult(status="bad-kind", detail=str(exc))

    resolved_kind: TerminalSessionKind = "harness" if kind == "harness" else "terminal"
    existing = catalog.get(session_id)
    attached_at = now_iso()
    created_at = existing.created_at if existing is not None else attached_at
    tmux_name = existing.tmux_name if existing is not None else terminal_session_name(session_id)
    command, resolved_control_endpoint, legacy_running = _session_command(
        session_id=session_id,
        resolved_kind=resolved_kind,
        harness=harness,
        cwd=cwd,
        vendor_command=vendor_command,
        existing=existing,
        host=host,
        workspace_root=workspace_root,
        control_endpoint=control_endpoint,
        control_root=control_root,
        session_commands=session_commands,
        resolved_launch=resolved_launch,
        created_at=created_at,
        tmux_name=tmux_name,
    )
    seat_role = migrated_seat_role(
        persisted=existing.seat_role if existing is not None else None,
        spawn_role=spawn_env.get("AR_SPAWN_ROLE") or (existing.spawn_role if existing else None),
        kind=resolved_kind,
    )
    # Server-authoritative uniqueness is scoped to the derived seat role. Checked immediately before
    # ensure/upsert so only a live owner of the same pair can refuse this launch.
    owner = leaf_conflict_owner(
        catalog,
        leaf_key=leaf_key,
        session_id=session_id,
        seat_role=seat_role,
        host=host,
    )
    if owner is not None:
        return OpenTerminalResult(
            status="leaf-taken",
            kind=resolved_kind,
            seat_role=seat_role,
            owner_session_id=owner,
        )

    opened = host.ensure(
        session_id,
        cwd=cwd,
        command=command,
        lifecycle_id=lifecycle_id,
        # A harness is a bare pane with no shell to `fg`; the host strips Ctrl-Z for it. A plain
        # shell keeps Ctrl-Z so its job control works (slice 6f hardening).
        suspend_unsafe=resolved_kind == "harness",
        env=spawn_env,
    )
    resolved_label = label or (
        existing.label if existing else _terminal_label(resolved_kind, harness, session_id)
    )

    entry = _opened_catalog_entry(
        opened=opened,
        existing=existing,
        resolved_label=resolved_label,
        resolved_kind=resolved_kind,
        harness=harness,
        lifecycle_id=lifecycle_id,
        command=command,
        created_at=created_at,
        attached_at=attached_at,
        leaf_key=leaf_key,
        seat_role=seat_role,
        replacement_for_leaf=replacement_for_leaf,
        spawned_by_session=spawned_by_session,
        spawned_by_lifecycle=spawned_by_lifecycle,
        spawn_env=spawn_env,
        launch_args=launch_args,
        prompt_keywords=prompt_keywords,
        session_commands=session_commands,
        spawn_level=spawn_level,
        spawn_level_source=spawn_level_source,
        resolved_model=(resolved_launch.model_key if resolved_launch is not None else None),
        resolved_effort=(resolved_launch.effort if resolved_launch is not None else None),
        legacy_running=legacy_running,
        control_endpoint=resolved_control_endpoint,
    )
    catalog.upsert(entry)
    return OpenTerminalResult(
        status="opened", entry=entry, kind=resolved_kind, seat_role=entry.binding_role
    )
