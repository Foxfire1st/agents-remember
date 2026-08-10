"""Hosted agent-session tools: spawn, attach, readiness, rename, retire."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.terminal_tools import RetiredSpawnInputs, SpawnedBy, SpawnSeat

from ..config import McpRuntimeConfig
from ..tools import (
    attach_terminal_session_to_leaf_payload,
    hosted_session_readiness_payload,
    session_rename_payload,
    session_retire_payload,
    spawn_agent_session_payload,
)


def register_session_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the session tools, split by what the caller is doing to a session.

    `spawn_agent_session` gets a helper to itself: its signature IS the published MCP input
    schema and its docstring IS the contract a caller reads, so neither can be shortened
    without a wire change.
    """
    _register_session_attachment_tools(server, config)
    _register_session_spawn_tools(server, config)
    _register_session_seat_tools(server, config)


def _register_session_attachment_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Bind an already-running hosted session to a durable task leaf."""

    @server.tool()
    def attach_terminal_session_to_leaf(
        session_id: str,
        leaf_key: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Move an existing hosted terminal/chat session to a durable task leaf.

        Reuses the dashboard terminal catalog's server-authoritative `(leaf, role)` uniqueness
        rules. Returns status 'attached', 'leaf-taken', or 'unknown-session'; it does not spawn a
        new session or require a worktree enclosure.
        """
        return attach_terminal_session_to_leaf_payload(
            config,
            session_id=session_id,
            leaf_key=leaf_key,
            role=role,
        )


def _register_session_spawn_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Spawn a role-configured, leaf-attached hosted agent session."""

    @server.tool()
    def spawn_agent_session(
        harness: str | None = None,
        leaf_key: str | None = None,
        replacement_for_leaf: str | None = None,
        context: str | None = None,
        submit: bool = False,
        *,
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
    ) -> dict[str, Any]:
        """Spawn a role-configured, leaf-attached hosted agent session without its brief.

        Composes the EXISTING session primitives so an orchestrator can spawn a manager and a manager
        a worker without dashboard clicks: create a hosted session via the serving opener, attach it
        to `leaf_key` (server-arbitrated uniqueness — a taken leaf returns status 'leaf-taken', never
        overridden), resolve the role knobs from developer-owned agentic settings, seed resolved
        `model`/`effort` into spawn env, map them onto the harness argv per-harness via the registry,
        and return the exact catalog session id as `spawned-unbriefed`. Ordinary callers declare the
        seat (`env.AR_SPAWN_ROLE`) and dispatch `level`; architect, orchestrator, and manager seats
        also require a repository+sprint scope from their leaf reference or spawning seat. They do
        not choose harness/model/effort or direct launch/session spend controls. Legacy non-null
        `context` or `submit=true` returns
        `brief-delivery-separate` before settings, catalog, or terminal side effects. The caller must
        then obtain `hosted_session_readiness(...)=ready` for the returned id and post one exact-agent
        durable `dispatch-brief`; only `delivered` plus adapter acceptance proves brief delivery.
        Legacy non-null `harness`, `model`, `effort`, `launch_args`,
        `prompt_keywords`, `session_commands`, `env.AR_SPAWN_MODEL`, `env.AR_SPAWN_EFFORT`, or
        harness-native spend/endpoint env keys such as `ANTHROPIC_MODEL` and `OPENAI_BASE_URL`
        return status 'spend-override-unsupported' before spawning, with guidance to configure
        `orchestration.roles`, `orchestration.rolesPerLevel`, `orchestration.spawn`, or
        `orchestration.harnesses` instead.
        An unbound replacement declares its actual work with `replacement_for_leaf`; that provenance
        is the leaf-chain discriminator while `leaf_key` remains free for the occupied role seat.
        `level` declares the dispatch level (leaf|master|portfolio, default leaf — a manager
        dispatching leaf seats passes leaf, the seam reviewer master, portfolio seats portfolio):
        knobs resolve from the agentic settings as `orchestration.rolesPerLevel[level]` deep-merged
        over the flat `orchestration.roles` default, keyed by the AR_SPAWN_ROLE riding `env`; the
        resolved level + source land in spawn provenance. Settings-owned session commands remain
        launch-phase configuration; prompt keywords stay on the catalog row until that later brief.
        If role settings do not choose a harness, the
        dispatch falls through to repo-local/global `orchestration.spawn.harness`, then the first
        detected registry harness. Each spawned session is its own harness process (the
        ambient-lifecycle singleton is untouched). Spawned-by provenance (`spawned_by_session` + the
        active/`spawned_by_lifecycle` lifecycle) is recorded on the catalog row so the dashboard can
        render the orchestration tree. Status `spawned-unbriefed` on success;
        `brief-delivery-separate`, `spend-override-unsupported`, `harness-unknown`/
        `harness-not-detected`/`effort-invalid`/`model-invalid`/`launch-selection-invalid`/
        `level-invalid`/`sprint-binding-required`/`sprint-binding-conflict`/`bad-kind` are
        pre-spawn refusals."""
        return spawn_agent_session_payload(
            config,
            seat=SpawnSeat(
                kind=kind,
                leaf_key=leaf_key,
                replacement_for_leaf=replacement_for_leaf,
                level=level,
                label=label,
                env=env,
            ),
            retired=RetiredSpawnInputs(
                context=context,
                submit=submit,
                harness=harness,
                model=model,
                effort=effort,
                launch_args=launch_args,
                prompt_keywords=prompt_keywords,
                session_commands=session_commands,
            ),
            spawned_by=SpawnedBy(
                session_id=spawned_by_session,
                lifecycle_id=spawned_by_lifecycle,
            ),
        )


def _register_session_seat_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Operate on a seat that already exists: check it, retire it, relabel it."""

    @server.tool()
    def hosted_session_readiness(session_id: str, wait_seconds: float = 0.0) -> dict[str, Any]:
        """Check whether one exact spawned session is ready for durable brief delivery.

        This is read-only: it verifies the same running catalog identity and exact protocol adapter
        snapshot, including negotiated readiness and acceptance capability. Pane text, copy mode,
        and log timing are not authority. It returns as soon as ready or when the caller's finite
        wait (maximum 60 seconds) expires; it never sends input.
        """
        return hosted_session_readiness_payload(
            config,
            session_id=session_id,
            wait_seconds=wait_seconds,
        )

    @server.tool()
    def session_retire(
        actor_session_id: str,
        session_id: str,
        reason: str = "manual retire",
    ) -> dict[str, Any]:
        """Retire a tracked chat/terminal session (260707-HFX-L8, issue #12): kill its tmux session,
        mark the catalog row terminated with retirement provenance (who/why/when/edge), and remove
        it from the active rail. Transcripts are never deleted.

        `actor_session_id` is the RETIRING seat's own catalog session id (self-declared -- there is
        no ambient "who am I" resolution, mirroring `spawn_agent_session`'s `spawned_by_session`).
        Authority is enforced server-side and refusals are loud and policy-naming: a seat never
        retires itself (`retire-refused`); a manager may retire only worker/reviewer seats of its
        OWN master; the orchestrator may retire any seat, including a completed manager. Status
        'retired' on success, 'already-retired' when the target was already terminated (idempotent),
        'unknown-session'/'unknown-actor' when a session id has no catalog row, 'retire-refused' for
        every authority-policy refusal."""
        return session_retire_payload(
            config,
            actor_session_id=actor_session_id,
            session_id=session_id,
            reason=reason,
        )

    @server.tool()
    def session_rename(session_id: str, label: str) -> dict[str, Any]:
        """Update a chat/terminal session's display label post-spawn (260707-HFX-L8, issue #4).

        Identity text only: the seat's spawned role never changes (L6 role-seat immutability). The
        FIRST rename freezes the original spawn-time label into spawn provenance for audit (later
        renames leave it alone). Status 'renamed' on success, 'unknown-session' when the session has
        no catalog row or is already retired."""
        return session_rename_payload(config, session_id=session_id, label=label)
