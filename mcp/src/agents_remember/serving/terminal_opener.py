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
from agents_remember.serving.terminal import TerminalHost
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

    Knob application (260703-L16, harness kind only): ``model``/``effort`` are validated FIRST --
    an out-of-vocabulary effort (or a knob a settings-defined harness declares no mapping for)
    raises ``ValueError`` naming the harness and its valid sets rather than letting the CLI
    warn-and-silently-degrade -- then they are mapped onto the harness's registry flags
    (``knob_argv``: env-only builtins get no flags, and session-level effort values stay OFF the
    flag) and ``launch_args`` is appended VERBATIM (the free-form escape hatch -- never validated).
    A plain ``terminal`` spawn takes no knobs: model/effort/launch_args are harness launch material
    and are ignored for it.
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
    resolved_model: str | None = None,
    resolved_effort: str | None = None,
    spawned_by_session: str | None = None,
    spawned_by_lifecycle: str | None = None,
    which: Which | None = None,
    harnesses: Sequence[Harness] | None = None,
) -> OpenTerminalResult:
    """Spawn + own one hosted session, the single opener both the route and the MCP tool call.

    Resolves the launch command server-side (``resolve_terminal_launch`` -- a harness **id**, never a
    wire argv), claims ``leaf_key`` under the per-(leaf, role) uniqueness rule, ensures the detached
    tmux session (seeding ``env`` at spawn -- the L2 knob-injection seam), and upserts the durable
    catalog row (carrying spawned-by provenance). A taken leaf returns ``leaf-taken`` WITHOUT spawning
    or mutating; an unknown/undetected kind/harness returns ``bad-kind``.

    Knob application (260703-L16): the ``AR_SPAWN_MODEL``/``AR_SPAWN_EFFORT`` values riding ``env``
    are ALSO mapped onto the harness argv per-harness via the registry (the env keeps riding for
    session-start visibility); an out-of-vocabulary effort refuses (``bad-kind`` with the naming
    detail). ``harnesses`` is the effective registry (builtin merged with the
    ``orchestration.harnesses`` settings family) ids resolve against; ``None`` = builtin defaults.
    The free-form escape hatch is recorded on the durable row as spawn provenance:
    ``launch_args`` (appended verbatim to the argv), ``prompt_keywords`` (the caller prepends them
    to the brief paste), and ``session_commands`` (the caller pastes them post-launch, before the
    brief) -- all three are never validated, only recorded.
    """
    spawn_env = dict(env or {})
    try:
        cwd, command = resolve_terminal_launch(
            kind,
            workspace_root=workspace_root,
            shell=shell,
            harness=harness,
            which=which,
            model=spawn_env.get("AR_SPAWN_MODEL"),
            effort=spawn_env.get("AR_SPAWN_EFFORT"),
            launch_args=launch_args,
            harnesses=harnesses,
        )
    except ValueError as exc:
        return OpenTerminalResult(status="bad-kind", detail=str(exc))

    resolved_kind: TerminalSessionKind = "harness" if kind == "harness" else "terminal"
    existing = catalog.get(session_id)
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
    attached_at = now_iso()
    resolved_label = label or (
        existing.label if existing else _terminal_label(resolved_kind, harness, session_id)
    )

    def preserved(new_value: object, existing_value: object) -> Any:
        # Write-once-preserve provenance: an explicit value wins now; otherwise keep whatever the
        # row already recorded (a re-open / reconnect must never silently drop provenance).
        return new_value or (existing_value if existing is not None else None)

    entry = TerminalCatalogEntry(
        id=opened.sid,
        label=resolved_label,
        kind=resolved_kind,
        harness=harness,
        lifecycle_id=lifecycle_id,
        cwd=opened.cwd,
        tmux_name=opened.tmux_name,
        command=tuple(command),
        created_at=existing.created_at if existing is not None else attached_at,
        last_attached_at=attached_at,
        status="running",
        # An explicit leaf_key claims a leaf now; otherwise keep any leaf this session already owns.
        leaf_key=preserved(leaf_key, existing.leaf_key if existing else None),
        seat_role=seat_role,
        replacement_for_leaf=preserved(
            replacement_for_leaf, existing.replacement_for_leaf if existing else None
        ),
        # Provenance is set once at first spawn and preserved across a re-open.
        spawned_by_session=preserved(
            spawned_by_session, existing.spawned_by_session if existing else None
        ),
        spawned_by_lifecycle=preserved(
            spawned_by_lifecycle, existing.spawned_by_lifecycle if existing else None
        ),
        # The dispatch seam already rides the role as AR_SPAWN_ROLE in the spawn env (l-01); record
        # it on the durable row so the Chats command tree (L14) can group by role provenance.
        spawn_role=preserved(
            spawn_env.get("AR_SPAWN_ROLE"), existing.spawn_role if existing else None
        ),
        # Free-form spawn provenance (260703-L16): recorded verbatim, never validated; the same
        # write-once-preserve rule as the spawned-by pair.
        launch_args=preserved(
            tuple(launch_args) if launch_args else None,
            existing.launch_args if existing else None,
        ),
        prompt_keywords=preserved(
            tuple(prompt_keywords) if prompt_keywords else None,
            existing.prompt_keywords if existing else None,
        ),
        session_commands=preserved(
            tuple(session_commands) if session_commands else None,
            existing.session_commands if existing else None,
        ),
        # The resolved dispatch level + how it was supplied (rolesPerLevel resolution input).
        spawn_level=preserved(spawn_level, existing.spawn_level if existing else None),
        spawn_level_source=preserved(
            spawn_level_source, existing.spawn_level_source if existing else None
        ),
        resolved_model=preserved(resolved_model, existing.resolved_model if existing else None),
        resolved_effort=preserved(resolved_effort, existing.resolved_effort if existing else None),
        session_log_entry_id=existing.session_log_entry_id if existing else None,
        session_log_path=existing.session_log_path if existing else None,
    )
    catalog.upsert(entry)
    return OpenTerminalResult(
        status="opened", entry=entry, kind=resolved_kind, seat_role=entry.binding_role
    )
