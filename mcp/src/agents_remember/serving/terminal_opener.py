"""The shared hosted-session opener: one spawn path for the dashboard route and the MCP tool.

Slice L2 (agent-facing dispatch) extracts the opener composition that used to live inline in
``app.py``'s ``POST /api/terminal/{session}`` handler so the FastAPI route AND the agent-facing
``spawn_agent_session`` MCP tool spawn through the *same* function -- the invariant is **no parallel
spawn path**. It mirrors ``terminal_task_assignment.assign_terminal_session_to_task``: a serving-layer
policy helper both call paths reuse instead of duplicating the task-seat claim + ensure + upsert sequence.

The opener is transport-agnostic. It returns a small :class:`OpenTerminalResult`; ``app.py`` maps it to
an HTTP ``JSONResponse`` (200 / 409 / 400) and the MCP tool maps it to a validated tool payload. The
structural-seat uniqueness check stays **server-arbitrated**: a taken seat returns ``seat-taken`` and the
caller surfaces it, never overrides it.

One open is described by exactly two values plus the runtime it lands in: a
:class:`TerminalLaunchRequest` (the process to run -- the launch *identity* a live row is later
compared against) and a :class:`SpawnProvenance` (what the dispatcher declares about the seat --
recorded on the durable row, never part of the argv). Everything the opener passes down its internal
chain is one of those two, so the chain carries concepts rather than re-listing fields at each layer.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, NamedTuple

import agents_remember
from agents_remember.kernel.harnesses import Harness
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
    ControlState,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
    TerminalSessionKind,
    migrated_seat_role,
)
from agents_remember.observer.events import now_iso
from agents_remember.serving.harness_control_adapter import protocol_adapter_status
from agents_remember.serving.harness_control_ipc import LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
)
from agents_remember.serving.harness_control_runner import RunnerConfig, control_runner_command
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.harnesses import (
    Which,
    find_harness,
    invalid_effort_detail,
    invalid_model_detail,
    is_detected,
    knob_argv,
    unknown_harness_detail,
)
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.terminal import (
    TerminalHost,
    TerminalSessionBinding,
    TerminalSessionSpec,
)
from agents_remember.serving.terminal_task_assignment import (
    replacement_binding_conflict_owner,
    task_binding_conflict_owner,
)
from agents_remember.serving.terminal_tmux import tmux_session_name
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

OpenTerminalStatus = Literal[
    "opened",
    "seat-taken",
    "launch-conflict",
    "bad-kind",
    "task-binding-required",
    "task-binding-invalid",
]


@dataclass(frozen=True)
class SpawnKnobs:
    """The settings-owned free-form escape hatch, recorded VERBATIM and never validated.

    The three arrive together from one ``RoleKnobs`` rung and are recorded together on the durable
    row, because they are one decision made at three moments of the same launch: ``launch_args`` ride
    the harness argv, ``session_commands`` are applied by the hosted runner before the brief, and
    ``prompt_keywords`` are prepended to the brief the caller pastes afterwards. Splitting them across
    parameter lists is what let them drift out of sync in the first place.
    """

    launch_args: Sequence[str] | None = None
    prompt_keywords: Sequence[str] | None = None
    session_commands: Sequence[str] | None = None


@dataclass(frozen=True)
class ControlRunnerRequest:
    """The caller-supplied half of the hosted control runner's configuration.

    The rest of :class:`~agents_remember.serving.harness_control_runner.RunnerConfig` is spawn
    identity the opener derives. This is what the *caller* gets to say about the protocol bridge:
    which resolved launch selection the adapter must apply, which vendor thread to resume, and where
    the session's private control socket is placed.
    """

    resolved_launch: ResolvedLaunch | None = None
    resume_thread_id: str | None = None
    endpoint: Path | None = None
    """An explicit socket path; ``None`` mints one for this session under :attr:`endpoint_root`."""
    endpoint_root: Path | None = None
    """The directory per-session sockets are minted in; ``None`` uses the workspace-local default."""


@dataclass(frozen=True)
class TerminalLaunchRequest:
    """What the server was asked to launch: the process identity of one hosted session.

    The opener resolves this to a :class:`LaunchCommand` (:func:`resolve_terminal_launch`) and later
    compares it against a live row's recorded provenance (:func:`_launch_identity_conflict`) -- it IS
    the launch identity of a hosted session, which is why it is one value and not a dozen parameters
    repeated at every layer. Nothing here describes the *seat*; that is :class:`SpawnProvenance`.
    """

    kind: str
    workspace_root: Path
    shell: str
    harness: str | None = None
    which: Which | None = None
    """How installed-ness is probed; ``None`` means :func:`shutil.which`."""
    harnesses: Sequence[Harness] | None = None
    """The EFFECTIVE registry ids resolve against; ``None`` means the builtin defaults."""
    env: Mapping[str, str] | None = None
    """Spawn env seeded at creation -- the L2 knob-injection seam, and the carrier of AR_SPAWN_ROLE."""
    knobs: SpawnKnobs = field(default_factory=SpawnKnobs)
    control: ControlRunnerRequest = field(default_factory=ControlRunnerRequest)
    flag_model: str | None = None
    flag_effort: str | None = None
    """The model/effort selection for a settings-defined non-native harness, delivered as the
    ``modelFlag``/``effortFlag`` argv its registry entry declares (:func:`harnesses.knob_argv`).
    Native adapters take their selection through :attr:`control` instead."""

    @property
    def resolved_kind(self) -> TerminalSessionKind:
        """The durable row's kind: anything that is not an explicit harness is a plain terminal."""

        return "harness" if self.kind == "harness" else "terminal"

    @property
    def is_controlled_harness(self) -> bool:
        """Whether this launch runs a harness behind the protocol bridge (never a plain shell)."""

        return self.resolved_kind == "harness" and self.harness is not None


@dataclass(frozen=True)
class SpawnProvenance:
    """What the dispatcher declares ABOUT the seat it is creating, recorded on the durable row.

    None of it reaches the argv: it is the seat's identity (its label, the lifecycle it correlates
    to), its claim (the task document it occupies or replaces) and its origin (who dispatched it, at
    which level, and whether that level was explicit). Reopening a session may relaunch the process,
    but it must not rewrite this -- :func:`_preserved` keeps every field write-once, which is only
    checkable because they travel as one value.
    """

    label: str | None = None
    lifecycle_id: str | None = None
    task_document_ref: TaskDocumentRef | None = None
    replacement_for_task_document_ref: TaskDocumentRef | None = None
    spawned_by_session: str | None = None
    spawned_by_lifecycle: str | None = None
    spawn_level: str | None = None
    spawn_level_source: str | None = None


NO_SPAWN_PROVENANCE = SpawnProvenance()
"""A hand-opened seat: no dispatcher declared anything about it (the opener's default)."""


class LaunchCommand(NamedTuple):
    """The server-resolved launch: where the process runs and the exact argv it runs as."""

    cwd: Path
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ReopenState:
    """What a prior catalog row still owns when a second open reaches the same session id.

    Only a row whose PROCESS IS GONE ever reaches this point: :func:`_live_open_result` answers
    first and returns for every row whose tmux session is still alive, so reaching here is always a
    respawn. A dead row therefore keeps exactly its durable identity -- its tmux name, and the
    write-once spawn provenance :func:`_preserved` carries forward -- and owns nothing the departed
    process decided: creation time restarts at this attach and control metadata is minted fresh.
    """

    existing: TerminalCatalogEntry | None
    created_at: str
    tmux_name: str


@dataclass(frozen=True)
class SpawnOutcome:
    """What one open actually produced, once the tmux session is ensured and the seat resolved."""

    binding: TerminalSessionBinding
    label: str
    seat_role: str | None
    attached_at: str
    control_endpoint: Path | None


@dataclass(frozen=True)
class OpenTerminalResult:
    """Outcome of the shared opener -- an upserted catalog row, a leaf conflict, or a bad kind."""

    status: OpenTerminalStatus
    entry: TerminalCatalogEntry | None = None
    kind: TerminalSessionKind | None = None
    seat_role: str | None = None
    owner_session_id: str | None = None
    detail: str | None = None


def resolve_terminal_launch(launch: TerminalLaunchRequest) -> LaunchCommand:
    """Resolve one launch request to ``(cwd, argv)`` -- the server owns the command.

    ``terminal`` spawns ``launch.shell`` at the workspace root (the dashboard-owned scratch terminal,
    slice 6e-2a). ``harness`` spawns the registered TUI harness ``launch.harness`` (its id) at the
    same root (slice 6e-2b), rejecting an absent id, an unknown id, or one whose CLI is not installed
    (``launch.which`` defaults to :func:`shutil.which`). Every other kind raises ``ValueError`` -- the
    opener endpoint turns that into a 400. (Lived in ``app.py`` before L2; moved here so the opener
    owns launch resolution and both the route and the MCP tool share it without importing ``app``.)

    ``launch.harnesses`` is the EFFECTIVE registry to resolve ids against (the builtin table merged
    with the ``orchestration.harnesses`` settings family -- ``AgenticSettings.harnesses``); ``None``
    means the builtin defaults. An id known nowhere raises the loud teach-it-via-settings refusal
    (``unknown_harness_detail``), never a crash.

    L2 keeps native-adapter launches focused on the settings-owned base command and free-form launch
    args. Their typed :class:`ResolvedLaunch` rides the runner payload; the adapter validates it
    against dynamic advertise and applies model/effort after this function returns. The
    ``flag_model``/``flag_effort`` pair is how a settings-defined non-native harness receives the
    same selection. A plain ``terminal`` spawn ignores harness launch material.
    """
    if launch.kind == "terminal":
        return LaunchCommand(launch.workspace_root, (launch.shell,))
    if launch.kind == "harness":
        harness = launch.harness
        if harness is None:
            raise ValueError("harness kind requires a harness id")
        found = find_harness(harness, registry=launch.harnesses)
        if found is None:
            raise ValueError(unknown_harness_detail(harness, registry=launch.harnesses))
        if not is_detected(found, which=launch.which):
            raise ValueError(f"harness not installed: {harness!r}")
        model = launch.flag_model
        effort = launch.flag_effort
        for detail in (
            invalid_model_detail(found, model) if model else None,
            invalid_effort_detail(found, effort) if effort else None,
        ):
            if detail is not None:
                raise ValueError(detail)
        argv = list(found.argv)
        argv += knob_argv(found, model=model, effort=effort)
        if launch.knobs.launch_args:
            argv += [str(arg) for arg in launch.knobs.launch_args]
        return LaunchCommand(launch.workspace_root, tuple(argv))
    raise ValueError(f"unknown terminal kind: {launch.kind!r}")


def _terminal_label(kind: TerminalSessionKind, harness: str | None, fallback: str) -> str:
    if kind == "terminal":
        return "Terminal"
    return harness or fallback


def _control_metadata(
    *,
    kind: TerminalSessionKind,
    harness: str | None,
    endpoint: Path | None,
) -> tuple[ControlState | None, Path | None, str | None]:
    """The control columns of a row this open MINTS, for the process it is about to spawn.

    Every open that gets this far spawns a new process (:func:`_live_open_result` already returned
    for a row whose process still runs), so there is no prior control state to carry forward: the
    row starts at the adapter's advertised status, on this protocol version, bound to the endpoint
    this open resolved. A plain terminal has no bridge and therefore no control columns at all.
    """

    if kind != "harness" or harness is None:
        return None, None, None
    return protocol_adapter_status(harness), endpoint, CONTROL_PROTOCOL_VERSION


def _previous(existing: TerminalCatalogEntry | None, name: str) -> Any:
    return getattr(existing, name) if existing is not None else None


def _preserved(
    existing: TerminalCatalogEntry | None,
    new_value: object,
    name: str,
) -> Any:
    """Keep write-once spawn provenance when a session is reopened."""

    return _previous(existing, name) or new_value


def _live_launch_conflict(
    existing: TerminalCatalogEntry | None,
    *,
    host: TerminalHost,
    launch: TerminalLaunchRequest,
    cwd: Path,
) -> tuple[TerminalCatalogEntry, str | None] | None:
    """Return the immutable live row and any attempted launch-identity conflict.

    A durable tmux session keeps the command and environment with which it was created. Reopening
    therefore treats the catalog row as process truth; it must not rewrite that provenance merely
    because ``ensure`` would idempotently reuse the process.
    """

    if existing is None or not host.has_session(existing.tmux_name):
        return None
    return existing, _launch_identity_conflict(existing, launch=launch, cwd=cwd)


def _launch_identity_conflict(
    existing: TerminalCatalogEntry,
    *,
    launch: TerminalLaunchRequest,
    cwd: Path,
) -> str | None:
    """Why the request disagrees with the live row's recorded provenance, or ``None`` if it agrees.

    Ordered from coarsest identity to finest: what the session is, where it runs, then the
    resolved launch it was created with. A request that carries no resolved launch cannot disagree
    about model/effort, so it stops after the cwd check.
    """

    if existing.kind != launch.resolved_kind or existing.harness != launch.harness:
        return (
            f"live session {existing.id!r} already runs {existing.kind} "
            f"harness {existing.harness!r}"
        )
    if existing.cwd != cwd:
        return (
            f"live session {existing.id!r} already runs in {str(existing.cwd)!r}; "
            f"requested {str(cwd)!r}"
        )
    requested_launch = launch.control.resolved_launch
    if requested_launch is None:
        return None
    if requested_launch.harness_id != existing.harness:
        return (
            f"live session {existing.id!r} already runs harness {existing.harness!r}; "
            f"resolved launch requested {requested_launch.harness_id!r}"
        )
    actual = (existing.resolved_model, existing.resolved_effort)
    requested = (requested_launch.model_key, requested_launch.effort)
    if actual != requested:
        return (
            f"live session {existing.id!r} already runs model/effort "
            f"{actual[0]!r}/{actual[1]!r}; requested {requested[0]!r}/{requested[1]!r}"
        )
    return None


def _live_open_result(
    existing: TerminalCatalogEntry | None,
    *,
    host: TerminalHost,
    launch: TerminalLaunchRequest,
    cwd: Path,
) -> OpenTerminalResult | None:
    live_existing = _live_launch_conflict(existing, host=host, launch=launch, cwd=cwd)
    if live_existing is None:
        return None
    actual, conflict = live_existing
    return OpenTerminalResult(
        status="launch-conflict" if conflict is not None else "opened",
        entry=actual,
        kind=actual.kind,
        seat_role=actual.binding_role,
        detail=conflict,
    )


def _reopen_state(
    existing: TerminalCatalogEntry | None,
    *,
    session_id: str,
    attached_at: str,
) -> ReopenState:
    """Resolve the reusable identity a dead catalog row still lends its replacement.

    A row whose process is gone keeps its tmux name so the replacement lands on the same session
    identity; a first open mints one. See :class:`ReopenState` for why no live row arrives here.
    """

    if existing is None:
        return ReopenState(None, attached_at, tmux_session_name(session_id))
    return ReopenState(existing, attached_at, existing.tmux_name)


def _open_label(
    existing: TerminalCatalogEntry | None,
    *,
    launch: TerminalLaunchRequest,
    provenance: SpawnProvenance,
    session_id: str,
) -> str:
    if provenance.label:
        return provenance.label
    if existing is not None:
        return existing.label
    return _terminal_label(launch.resolved_kind, launch.harness, session_id)


def _resolved_pair(resolved_launch: ResolvedLaunch | None) -> tuple[str | None, str | None]:
    if resolved_launch is None:
        return None, None
    return resolved_launch.model_key, resolved_launch.effort


HOSTED_SESSION_ENV = "AR_HOSTED_SESSION_ID"
_DAEMON_IDENTITY_ENV_EXACT = frozenset(
    {"CODEX_THREAD_ID", "CODEX_CI", "CLAUDE_DOC_FOCUS_PATHS", HOSTED_SESSION_ENV}
)


def _scrub_daemon_identity_env(env: Mapping[str, str]) -> dict[str, str]:
    """Strip the spawning session's own identity residue from a child spawn env.

    A terminal opened from a daemon/agent session inherits that session's identity variables
    (role/knob env, vendor thread ids, CI markers, game-data paths) whenever the caller forwards
    its own environment. A role spawn carries its own explicit ``AR_SPAWN_*`` values -- signalled
    by ``AR_SPAWN_ROLE`` -- so those survive; the vendor identity names are never explicit role
    values and are always removed. Ordinary spawns never carried the role marker, so their env
    is unchanged apart from the residue removal.
    """
    scrubbed = dict(env)
    role_spawn = "AR_SPAWN_ROLE" in scrubbed
    for key in list(scrubbed):
        if (
            key in _DAEMON_IDENTITY_ENV_EXACT
            or key.startswith("GAME_DATA_")
            or (key.startswith("AR_SPAWN_") and not role_spawn)
        ):
            del scrubbed[key]
    return scrubbed


def _runner_spawn_env(env: Mapping[str, str]) -> dict[str, str]:
    """Spawn env for a harness-control runner with the daemon's own package root on PYTHONPATH.

    The runner (``python -m agents_remember.serving.harness_control_runner``) is created through
    the tmux *server* environment, which does not carry the daemon's PYTHONPATH -- a daemon running
    worktree code would otherwise spawn runners that fall back to the installed main-checkout
    package. Prepend the source root of the package this process actually imported
    (``.../mcp/src`` in the ``src`` layout), preserving any PYTHONPATH the caller already seeded.
    When the daemon runs the installed checkout this is the same root the runner resolves anyway,
    so a production spawn is semantically unchanged.
    """
    source_root = str(Path(agents_remember.__file__).resolve().parent.parent)
    seeded = dict(env)
    existing = seeded.get("PYTHONPATH")
    seeded["PYTHONPATH"] = f"{source_root}{os.pathsep}{existing}" if existing else source_root
    return seeded


def _session_command(
    *,
    identity: ControlIdentity,
    command: LaunchCommand,
    launch: TerminalLaunchRequest,
) -> tuple[list[str], Path | None]:
    """The argv this open actually spawns, plus the control endpoint it will listen on.

    Every open that gets this far is a fresh spawn, so a controlled harness always runs behind the
    bridge on a minted endpoint. A still-running session's recorded argv is never respawned from
    here: :func:`_live_open_result` returns that row untouched before any of this runs.
    """

    if not launch.is_controlled_harness:
        return list(command.argv), launch.control.endpoint
    endpoint_root = launch.control.endpoint_root or (
        launch.workspace_root / ".agents-remember-control"
    )
    endpoint = (
        launch.control.endpoint or LocalControlEndpoint.for_session(endpoint_root, identity).path
    )
    runner = control_runner_command(
        RunnerConfig(
            identity=identity,
            harness_id=launch.harness or "",
            cwd=command.cwd,
            argv=tuple(command.argv),
            endpoint_root=endpoint_root,
            session_commands=tuple(launch.knobs.session_commands or ()),
            resolved_launch=launch.control.resolved_launch,
            resume_thread_id=launch.control.resume_thread_id,
        )
    )
    return list(runner), endpoint


def _opened_catalog_entry(
    *,
    outcome: SpawnOutcome,
    reopen: ReopenState,
    launch: TerminalLaunchRequest,
    provenance: SpawnProvenance,
) -> TerminalCatalogEntry:
    """The durable row for the process this open just spawned.

    Only two things survive from a prior row of the same id, and they are the two the departed
    process never owned: the seat's write-once spawn provenance (:func:`_preserved`) and the tmux
    identity carried on :class:`ReopenState`. Everything the *process* decided -- its knobs, its
    resolved pair, its session log, every live control observation -- belongs to the process that
    is gone, so the new row states this spawn's own values and leaves the observations unset until
    the bridge reports them. A row whose process still runs never reaches here at all.
    """

    existing = reopen.existing
    knobs = launch.knobs
    spawn_env = launch.env or {}
    resolved_model, resolved_effort = _resolved_pair(launch.control.resolved_launch)
    control_state, resolved_endpoint, control_protocol = _control_metadata(
        kind=launch.resolved_kind,
        harness=launch.harness,
        endpoint=outcome.control_endpoint,
    )
    return TerminalCatalogEntry(
        id=outcome.binding.sid,
        label=outcome.label,
        kind=launch.resolved_kind,
        harness=launch.harness,
        lifecycle_id=provenance.lifecycle_id,
        cwd=outcome.binding.cwd,
        tmux_name=outcome.binding.tmux_name,
        command=outcome.binding.command,
        created_at=reopen.created_at,
        last_attached_at=outcome.attached_at,
        status="running",
        task_document_ref=_preserved(existing, provenance.task_document_ref, "task_document_ref"),
        seat_role=outcome.seat_role,
        replacement_for_task_document_ref=_preserved(
            existing,
            provenance.replacement_for_task_document_ref,
            "replacement_for_task_document_ref",
        ),
        spawned_by_session=_preserved(
            existing, provenance.spawned_by_session, "spawned_by_session"
        ),
        spawned_by_lifecycle=_preserved(
            existing, provenance.spawned_by_lifecycle, "spawned_by_lifecycle"
        ),
        spawn_role=spawn_env.get("AR_SPAWN_ROLE"),
        launch_args=tuple(knobs.launch_args) if knobs.launch_args else None,
        prompt_keywords=tuple(knobs.prompt_keywords) if knobs.prompt_keywords else None,
        session_commands=tuple(knobs.session_commands) if knobs.session_commands else None,
        spawn_level=provenance.spawn_level,
        spawn_level_source=provenance.spawn_level_source,
        resolved_model=resolved_model,
        resolved_effort=resolved_effort,
        control_state=control_state,
        control_endpoint=resolved_endpoint,
        control_protocol=control_protocol,
    )


def _binding_conflict_owner(
    runtime: HostedSessionRuntime,
    provenance: SpawnProvenance,
    *,
    session_id: str,
    seat_role: str,
) -> str | None:
    """Return the occupant that prevents this task-role binding, if any."""

    if provenance.task_document_ref is not None:
        return task_binding_conflict_owner(
            runtime.catalog,
            task_document_ref=provenance.task_document_ref,
            session_id=session_id,
            seat_role=seat_role,
            host=runtime.host,
        )
    return replacement_binding_conflict_owner(
        runtime.catalog,
        task_document_ref=provenance.replacement_for_task_document_ref,
        session_id=session_id,
        seat_role=seat_role,
        host=runtime.host,
    )


def _open_terminal_transaction(
    *,
    runtime: HostedSessionRuntime,
    session_id: str,
    launch: TerminalLaunchRequest,
    provenance: SpawnProvenance,
    command: LaunchCommand,
) -> OpenTerminalResult:
    catalog = runtime.catalog
    host = runtime.host
    resolved_kind = launch.resolved_kind
    existing = catalog.get(session_id)
    live_result = _live_open_result(existing, host=host, launch=launch, cwd=command.cwd)
    if live_result is not None:
        return live_result

    # Past this point the open is a SPAWN: ``_live_open_result`` has already returned for every row
    # whose process still runs, so ``existing`` is either absent or dead and nothing below has to
    # weigh a live process's decisions against this request's.
    attached_at = now_iso()
    reopen = _reopen_state(existing, session_id=session_id, attached_at=attached_at)
    argv, resolved_control_endpoint = _session_command(
        identity=ControlIdentity(
            ar_session_id=session_id,
            tmux_name=reopen.tmux_name,
            created_at=reopen.created_at,
        ),
        command=command,
        launch=launch,
    )
    spawn_env = _scrub_daemon_identity_env(launch.env or {})
    # Each harness starts its own MCP child process. Seed the exact hosted identity only into that
    # process environment so structural tools can resolve the caller without any model argument.
    # Scrubbing above guarantees a parent process's identity can never leak into its child.
    spawn_env[HOSTED_SESSION_ENV] = session_id
    seat_role = migrated_seat_role(
        persisted=existing.seat_role if existing is not None else None,
        spawn_role=spawn_env.get("AR_SPAWN_ROLE") or (existing.spawn_role if existing else None),
        kind=resolved_kind,
    )
    binding_refusal = _task_binding_refusal(runtime, provenance, seat_role)
    if binding_refusal is not None:
        return OpenTerminalResult(status=binding_refusal)
    owner = _binding_conflict_owner(
        runtime,
        provenance,
        session_id=session_id,
        seat_role=seat_role,
    )
    if owner is not None:
        return OpenTerminalResult(
            status="seat-taken",
            kind=resolved_kind,
            seat_role=seat_role,
            owner_session_id=owner,
        )

    # A fresh harness spawn launches the control runner under the tmux *server* environment,
    # which lacks the daemon's PYTHONPATH -- seed the daemon's own package source root so the
    # runner imports the same agents_remember code. Plain terminal spawns keep their env
    # byte-identical.
    runner_env = _runner_spawn_env(spawn_env) if launch.is_controlled_harness else spawn_env
    opened = host.ensure(
        session_id,
        TerminalSessionSpec(
            cwd=command.cwd,
            command=tuple(argv),
            lifecycle_id=provenance.lifecycle_id,
            suspend_unsafe=resolved_kind == "harness",
            env=runner_env,
        ),
    )
    entry = _opened_catalog_entry(
        outcome=SpawnOutcome(
            binding=opened,
            label=_open_label(
                existing, launch=launch, provenance=provenance, session_id=session_id
            ),
            seat_role=seat_role,
            attached_at=attached_at,
            control_endpoint=resolved_control_endpoint,
        ),
        reopen=reopen,
        launch=launch,
        provenance=provenance,
    )
    catalog.upsert(entry)
    return OpenTerminalResult(
        status="opened", entry=entry, kind=resolved_kind, seat_role=entry.binding_role
    )


def _task_binding_refusal(
    runtime: HostedSessionRuntime,
    provenance: SpawnProvenance,
    seat_role: str,
) -> Literal["task-binding-required", "task-binding-invalid"] | None:
    """Validate one role against the real task document before any host side effect."""

    if (
        provenance.task_document_ref is not None
        and provenance.replacement_for_task_document_ref is not None
    ):
        return "task-binding-invalid"
    ref = provenance.task_document_ref or provenance.replacement_for_task_document_ref
    structural_role = seat_role not in {"chat", "terminal"}
    if ref is None:
        return "task-binding-required" if structural_role else None
    topology = TaskDocumentTopology(runtime.catalog.path.parent.parent.parent)
    try:
        topology.resolve(ref)
        if structural_role:
            topology.validate_role(ref, seat_role)
    except TaskDocumentRefError:
        return "task-binding-invalid"
    return None


def open_terminal_session(
    *,
    runtime: HostedSessionRuntime,
    session_id: str,
    launch: TerminalLaunchRequest,
    provenance: SpawnProvenance = NO_SPAWN_PROVENANCE,
) -> OpenTerminalResult:
    """Spawn + own one hosted session, the single opener both the route and the MCP tool call.

    Resolves the launch command server-side (``resolve_terminal_launch`` -- a harness **id**, never a
    wire argv), claims ``provenance.task_document_ref`` under the per-(document, role) uniqueness rule,
    ensures the
    detached tmux session (seeding ``launch.env`` at spawn -- the L2 knob-injection seam), and upserts
    the durable catalog row (carrying spawned-by provenance). A taken seat returns ``seat-taken``
    WITHOUT spawning or mutating; an unknown/undetected kind/harness returns ``bad-kind``.

    L2 carries one typed ``ResolvedLaunch`` into the hosted runner (``launch.control``). The runner
    performs token-free dynamic catalog validation and applies adapter-native launch knobs before the
    real vendor process starts. The legacy model/effort pair is used only for explicitly mapped
    settings-defined non-native harnesses. The namespaced spawn env remains as settings provenance.
    The free-form escape hatch (``launch.knobs``) is never validated, only recorded.
    ``launch.control.resume_thread_id`` is a codex-only native-identity selector in the same
    authority class: it rides the runner payload to the adapter factory, and the opener never
    validates or authorizes the target.
    """
    resume_thread_id = launch.control.resume_thread_id
    if resume_thread_id is not None and (launch.kind != "harness" or launch.harness != "codex"):
        return OpenTerminalResult(
            status="bad-kind",
            detail="resume_thread_id is only supported for the codex harness",
        )
    if resume_thread_id is not None and (
        not resume_thread_id or resume_thread_id != resume_thread_id.strip()
    ):
        return OpenTerminalResult(
            status="bad-kind",
            detail="resume_thread_id must be non-empty with no outer whitespace",
        )
    try:
        command = resolve_terminal_launch(launch)
    except ValueError as exc:
        return OpenTerminalResult(status="bad-kind", detail=str(exc))

    # ``batch`` is the existing cross-thread/process session-open fence. It must span the complete
    # read/probe/ensure/upsert transaction: otherwise two callers can both observe no row, the tmux
    # loser can reuse the winner's process, and then overwrite the catalog with its attempted argv.
    with runtime.catalog.batch():
        return _open_terminal_transaction(
            runtime=runtime,
            session_id=session_id,
            launch=launch,
            provenance=provenance,
            command=command,
        )
