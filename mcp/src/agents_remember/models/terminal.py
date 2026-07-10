"""Response models for dashboard terminal-session MCP tools."""

from __future__ import annotations

from typing import Literal

from agents_remember.models.base import ToolResponse

LeafAssignmentStatus = Literal[
    "attached",
    "leaf-taken",
    "unknown-session",
    "role-required",
    "leaf-ref-not-found",
    "leaf-ref-ambiguous",
]


class AttachTerminalSessionToLeafResponse(ToolResponse):
    """``attach_terminal_session_to_leaf``: move one hosted session to a leaf."""

    operation: Literal["attach_terminal_session_to_leaf"] = "attach_terminal_session_to_leaf"
    status: LeafAssignmentStatus
    session: str
    leafKey: str
    previousLeafKey: str | None = None
    ownerSession: str | None = None
    role: Literal["chat", "terminal"] | None = None
    seatRole: str | None = None
    previousSeatRole: str | None = None
    detail: str | None = None


SpawnAgentSessionStatus = Literal[
    "spawned",
    "leaf-taken",
    "harness-unknown",
    "harness-not-detected",
    # 260703-L16: the effort value is outside the resolved harness's known vocabulary (or a
    # settings-defined harness declares no effort mapping) -- refused at dispatch (naming the
    # harness + its valid sets) instead of letting the CLI warn-and-degrade.
    "effort-invalid",
    # 260703-L16: a settings-defined harness with no declared modelFlag got a model knob -- refused
    # with guidance (declare the flag or use launchArgs); explicit over guessing.
    "model-invalid",
    # 260707-HFX2-L10: ordinary spawn callers cannot choose spend knobs. Removed/legacy caller
    # harness/model/effort fields, direct free-form launch/session controls, AR_SPAWN_MODEL/
    # AR_SPAWN_EFFORT, and harness-native spend/endpoint env keys refuse before spawning; settings
    # are the authority.
    "spend-override-unsupported",
    # 260703-L16 (ruling 2026-07-07T08:15): the dispatch level is outside leaf|master|portfolio.
    "level-invalid",
    "leaf-ref-not-found",
    "leaf-ref-ambiguous",
    "bad-kind",
]


class SpawnAgentSessionResponse(ToolResponse):
    """``spawn_agent_session``: spawn a role-configured, leaf-attached, context-primed hosted session.

    Composes the existing session primitives (opener + leaf claim + log-verified input + optional
    submit). ``ok`` is true only for ``spawned``; ``leaf-taken`` surfaces the server-arbitrated
    refusal (the tool never overrides it), and the harness/kind statuses report a validation refusal
    before anything is spawned.
    """

    operation: Literal["spawn_agent_session"] = "spawn_agent_session"
    status: SpawnAgentSessionStatus
    session: str
    harness: str | None = None
    kind: Literal["harness", "terminal"] | None = None
    leafKey: str | None = None
    seatRole: str | None = None
    replacementForLeaf: str | None = None
    label: str | None = None
    cwd: str | None = None
    tmuxName: str | None = None
    # Spawned-by provenance recorded on the catalog row (the dashboard orchestration-tree seam).
    spawnedBySession: str | None = None
    spawnedByLifecycle: str | None = None
    # The AR_SPAWN_ROLE recorded on the catalog row (L14: the Chats command-tree grouping key).
    spawnRole: str | None = None
    # The RESOLVED dispatch level (leaf|master|portfolio) and whether the dispatcher supplied it
    # ("explicit") or it defaulted ("default") -- the rolesPerLevel knob-resolution input
    # (260703-L16, ruling 2026-07-07T08:15), recorded on the catalog row.
    spawnLevel: str | None = None
    spawnLevelSource: str | None = None
    resolvedModel: str | None = None
    resolvedEffort: str | None = None
    sessionLogEntryId: str | None = None
    sessionLogPath: str | None = None
    # Free-form spawn provenance (260703-L16), as recorded on the catalog row: launchArgs rode the
    # argv verbatim, sessionCommands were pasted post-launch before the brief (the resolved list --
    # a session-vocabulary effort like claude's ultracode arrives here as "/effort ultracode"),
    # promptKeywords were prepended to the brief paste. Never validated.
    launchArgs: list[str] | None = None
    promptKeywords: list[str] | None = None
    sessionCommands: list[str] | None = None
    # Whether every session command has both a bound-log command entry and non-error stdout.
    sessionCommandsDelivered: bool | None = None
    # Set on ``leaf-taken``: the running same-role session that already owns the leaf.
    ownerSession: str | None = None
    # Context-packet delivery outcome: true only after the id-bearing user entry is in the bound log.
    contextDelivered: bool | None = None
    submitted: bool | None = None
    # 260707-HFX-L3 loud-failure evidence: the final pane capture, attached whenever any delivery
    # outcome above reports False -- a blind seat is diagnosed from the payload itself, never
    # trusted from a bare boolean. Absent on full success.
    deliveryCapture: str | None = None
    detail: str | None = None


SessionRetireStatus = Literal[
    "retired",
    "already-retired",
    "unknown-session",
    "unknown-actor",
    "retire-refused",
]


class SessionRetireResponse(ToolResponse):
    """``session_retire`` (260707-HFX-L8, issue #12): terminate/park a tracked chat session.

    ``ok`` is true for ``retired``/``already-retired`` (idempotent); false for every refusal
    status. ``retire-refused`` is the server-side authority-policy refusal (owner-never-self-
    retires, manager-scoped-to-its-own-master, orchestrator-only-otherwise) -- ``detail`` names the
    exact clause that fired.
    """

    operation: Literal["session_retire"] = "session_retire"
    status: SessionRetireStatus
    session: str
    retiredAt: str | None = None
    retiredBySession: str | None = None
    retiredReason: str | None = None
    retiredEdge: str | None = None
    detail: str | None = None


SessionRenameStatus = Literal["renamed", "unknown-session"]


class SessionRenameResponse(ToolResponse):
    """``session_rename`` (260707-HFX-L8, issue #4): update a chat's display label post-spawn.

    Identity text only -- the seat's ``spawn_role`` (L6 role-seat immutability) never changes.
    ``spawnedLabel`` is the ORIGINAL spawn-time label, frozen for audit on the first rename.
    """

    operation: Literal["session_rename"] = "session_rename"
    status: SessionRenameStatus
    session: str
    label: str | None = None
    spawnedLabel: str | None = None
