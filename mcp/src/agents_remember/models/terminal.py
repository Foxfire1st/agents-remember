"""Response models for dashboard terminal-session MCP tools."""

from __future__ import annotations

from typing import Literal, get_args

from agents_remember.models.base import ToolResponse
from agents_remember.models.task_document_ref import TaskDocumentRef

# These three vocabularies are declared here rather than beside the payload builders that write
# them, and the direction is deliberate: `application.terminal_tools` imports them, not the reverse.
# `mcp.tools.base` -> `models.tool_registry` -> `models.terminal` is an existing import edge, so
# a `models.terminal` -> `application.terminal_tools` import would close a cycle. The invariant
# is ONE declaration, not a particular module owning it: `application.terminal_tools` annotates
# its status seams with these aliases, so a refusal status the tool invents is a pyright error
# at the tool rather than a ValidationError escaping the MCP handler. `Literal` flattens nested
# aliases, so the published enum is unchanged.
LeafRefStatus = Literal["leaf-ref-not-found", "leaf-ref-ambiguous"]

TaskAssignmentStatus = Literal[
    "attached",
    "seat-taken",
    "unknown-session",
    "role-required",
    "task-binding-invalid",
    "task-document-not-found",
    "task-document-invalid",
    "task-document-repo-mismatch",
]


class AttachTerminalSessionToTaskResponse(ToolResponse):
    """Trusted administration: move one hosted session to a document-owned role seat."""

    operation: Literal["attach_terminal_session_to_task"] = "attach_terminal_session_to_task"
    status: TaskAssignmentStatus
    session: str
    taskDocumentRef: TaskDocumentRef
    previousTaskDocumentRef: TaskDocumentRef | None = None
    ownerSession: str | None = None
    role: Literal["chat", "terminal"] | None = None
    seatRole: str | None = None
    previousSeatRole: str | None = None
    detail: str | None = None


SpawnAgentSessionStatus = Literal[
    "spawned-unbriefed",
    "brief-delivery-separate",
    "seat-taken",
    "harness-unknown",
    "harness-not-detected",
    # 260703-L16: the effort value is outside the resolved harness's known vocabulary (or a
    # settings-defined harness declares no effort mapping) -- refused at dispatch (naming the
    # harness + its valid sets) instead of letting the CLI warn-and-degrade.
    "effort-invalid",
    # 260703-L16: a settings-defined harness with no declared modelFlag got a model knob -- refused
    # with guidance (declare the flag or use launchArgs); explicit over guessing.
    "model-invalid",
    # ACPUI L2: a role launch omitted model/effort before spawn. Unknown or model-gated values
    # pass this structural gate and are refused by the runner's dynamic catalog validation.
    "launch-selection-invalid",
    # 260707-HFX2-L10: ordinary spawn callers cannot choose spend knobs. Removed/legacy caller
    # harness/model/effort fields, direct free-form launch/session controls, AR_SPAWN_MODEL/
    # AR_SPAWN_EFFORT, and harness-native spend/endpoint env keys refuse before spawning; settings
    # are the authority.
    "spend-override-unsupported",
    # 260703-L16 (ruling 2026-07-07T08:15): the dispatch level is outside leaf|master|portfolio.
    "level-invalid",
    "task-binding-required",
    "task-binding-invalid",
    "task-document-not-found",
    "task-document-invalid",
    "task-document-repo-mismatch",
    "bad-kind",
]

# The runtime half of each alias, derived from it rather than retyped beside it, so a member can
# only ever be added in one place.
VALID_SPAWN_AGENT_SESSION_STATUSES: frozenset[SpawnAgentSessionStatus] = frozenset(
    get_args(SpawnAgentSessionStatus)
)


class SpawnAgentSessionResponse(ToolResponse):
    """``spawn_agent_session``: create and bind a hosted seat without delivering its brief."""

    operation: Literal["spawn_agent_session"] = "spawn_agent_session"
    status: SpawnAgentSessionStatus
    session: str
    harness: str | None = None
    kind: Literal["harness", "terminal"] | None = None
    taskDocumentRef: TaskDocumentRef | None = None
    seatRole: str | None = None
    replacementForTaskDocumentRef: TaskDocumentRef | None = None
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
    # Free-form spawn provenance (260703-L16): launchArgs rode the base launch argv verbatim;
    # sessionCommands were applied as explicit launch-phase configuration; model/effort are never
    # synthesized into session commands. promptKeywords remain provenance for the later brief.
    launchArgs: list[str] | None = None
    promptKeywords: list[str] | None = None
    sessionCommands: list[str] | None = None
    # Settings-owned launch/session commands remain spawn-phase configuration. Without a bound
    # brief log, ``False`` means their application was not proven; it is never a brief-delivery claim.
    sessionCommandsDelivered: bool | None = None
    # Set internally on ``seat-taken``: the running occupant of the document+role seat.
    ownerSession: str | None = None
    # Failure-only launch-command evidence. Task instructions are delivered by dispatch-brief.
    deliveryCapture: str | None = None
    controlState: str | None = None
    controlEndpoint: str | None = None
    controlProtocol: str | None = None
    detail: str | None = None


HostedSessionReadinessStatus = Literal[
    "ready",
    "not-ready",
    "unknown-session",
    "terminated",
]


class HostedSessionReadinessResponse(ToolResponse):
    """``hosted_session_readiness``: bounded, read-only exact-session readiness."""

    operation: Literal["hosted_session_readiness"] = "hosted_session_readiness"
    status: HostedSessionReadinessStatus
    session: str
    harness: str | None = None
    tmuxName: str | None = None
    controlState: str | None = None
    activity: str | None = None
    acceptance: str | None = None
    vendorSessionId: str | None = None
    pendingInteraction: dict[str, object] | None = None
    detail: str | None = None


SessionRetireStatus = Literal[
    "retired",
    "already-retired",
    "unknown-session",
    "unknown-actor",
    "retire-refused",
]

VALID_SESSION_RETIRE_STATUSES: frozenset[SessionRetireStatus] = frozenset(
    get_args(SessionRetireStatus)
)


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

VALID_SESSION_RENAME_STATUSES: frozenset[SessionRenameStatus] = frozenset(
    get_args(SessionRenameStatus)
)


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
