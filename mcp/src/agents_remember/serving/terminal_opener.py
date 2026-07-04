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

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.observer.events import now_iso
from agents_remember.serving.harnesses import Which, find_harness, is_detected
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    TerminalSessionKind,
    role_for_kind,
)
from agents_remember.serving.terminal_leaf_assignment import leaf_conflict_owner

OpenTerminalStatus = Literal["opened", "leaf-taken", "bad-kind"]


@dataclass(frozen=True)
class OpenTerminalResult:
    """Outcome of the shared opener -- an upserted catalog row, a leaf conflict, or a bad kind."""

    status: OpenTerminalStatus
    entry: TerminalCatalogEntry | None = None
    kind: TerminalSessionKind | None = None
    owner_session_id: str | None = None
    detail: str | None = None


def resolve_terminal_launch(
    kind: str,
    *,
    workspace_root: Path,
    shell: str,
    harness: str | None = None,
    which: Which | None = None,
) -> tuple[Path, list[str]]:
    """Resolve a launch ``kind`` to ``(cwd, argv)`` -- the server owns the command.

    ``terminal`` spawns ``shell`` at the workspace root (the dashboard-owned scratch terminal,
    slice 6e-2a). ``harness`` spawns the registered TUI harness ``harness`` (its id) at the same
    root (slice 6e-2b), rejecting an absent id, an unknown id, or one whose CLI is not installed
    (``which`` defaults to :func:`shutil.which`). Every other kind raises ``ValueError`` -- the
    opener endpoint turns that into a 400. (Lived in ``app.py`` before L2; moved here so the opener
    owns launch resolution and both the route and the MCP tool share it without importing ``app``.)
    """
    if kind == "terminal":
        return workspace_root, [shell]
    if kind == "harness":
        if harness is None:
            raise ValueError("harness kind requires a harness id")
        found = find_harness(harness)
        if found is None:
            raise ValueError(f"unknown harness: {harness!r}")
        if not is_detected(found, which=which):
            raise ValueError(f"harness not installed: {harness!r}")
        return workspace_root, list(found.argv)
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
    env: Mapping[str, str] | None = None,
    spawned_by_session: str | None = None,
    spawned_by_lifecycle: str | None = None,
    which: Which | None = None,
) -> OpenTerminalResult:
    """Spawn + own one hosted session, the single opener both the route and the MCP tool call.

    Resolves the launch command server-side (``resolve_terminal_launch`` -- a harness **id**, never a
    wire argv), claims ``leaf_key`` under the per-(leaf, role) uniqueness rule, ensures the detached
    tmux session (seeding ``env`` at spawn -- the L2 knob-injection seam), and upserts the durable
    catalog row (carrying spawned-by provenance). A taken leaf returns ``leaf-taken`` WITHOUT spawning
    or mutating; an unknown/undetected kind/harness returns ``bad-kind``.
    """
    try:
        cwd, command = resolve_terminal_launch(
            kind,
            workspace_root=workspace_root,
            shell=shell,
            harness=harness,
            which=which,
        )
    except ValueError as exc:
        return OpenTerminalResult(status="bad-kind", detail=str(exc))

    resolved_kind: TerminalSessionKind = "harness" if kind == "harness" else "terminal"
    # Server-authoritative uniqueness, scoped to the launch role: a taken leaf is refused so two chats
    # never mingle on one leaf. Checked immediately before the ensure/upsert in the single-process app
    # + atomic JSON store, so check-then-write is effectively atomic (the client guard is advisory).
    owner = leaf_conflict_owner(
        catalog,
        leaf_key=leaf_key,
        session_id=session_id,
        role=role_for_kind(resolved_kind),
    )
    if owner is not None:
        return OpenTerminalResult(
            status="leaf-taken",
            kind=resolved_kind,
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
        env=env or {},
    )
    attached_at = now_iso()
    existing = catalog.get(session_id)
    resolved_label = label or (
        existing.label if existing else _terminal_label(resolved_kind, harness, session_id)
    )
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
        # An explicit leaf_key claims a leaf now; otherwise keep any leaf this session already owns
        # (a re-open / reconnect must not silently drop the leaf binding).
        leaf_key=leaf_key or (existing.leaf_key if existing is not None else None),
        # Provenance is set once at first spawn and preserved across a re-open.
        spawned_by_session=spawned_by_session
        or (existing.spawned_by_session if existing is not None else None),
        spawned_by_lifecycle=spawned_by_lifecycle
        or (existing.spawned_by_lifecycle if existing is not None else None),
    )
    catalog.upsert(entry)
    return OpenTerminalResult(status="opened", entry=entry, kind=resolved_kind)
