"""Resolve role launch defaults, role folders, and canonical AR handovers.

The handover is the assignment data of one launch. Its host-specific part names Paseo as the
host, the tool server a role agent calls AR tools on, and the two AR tools through which role
agents start and message each other.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from agents_remember.application.agent_binding import (
    TOOL_SERVER_NAME,
    launch_server_exclusion,
)
from agents_remember.application.context_packet import (
    ContextPacketRequest,
    build_context_packet,
)
from agents_remember.application.role_capsules.launch import compile_launch_capsule
from agents_remember.application.role_launch_context import (
    LEAF_ROLES,
    RoleLaunchContext,
    resolve_role_launch_context,
    selection_binding,
)
from agents_remember.application.task_scoped_mcp import (
    task_scoped_mcp_config_for_reader,
)
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_catalog import (
    launcher_options,
    provider_accepts_tool_servers,
    read_host_role,
    resolve_agent_selection,
)
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.role_brief_message import BriefParts, brief_prompt
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.cli.role_instructions import (
    instruction_metadata,
    launch_choice_metadata,
)
from agents_remember.cli.role_launch_defaults import role_defaults
from agents_remember.cli.role_launch_receipts import (
    _message_binding_projection_reference,
)
from agents_remember.cli.role_launch_receipts import (
    digest as _digest,
)
from agents_remember.cli.role_launch_workspace import (
    _direct_leaf_grant,
    _leaf_contract_path,
    _resolve_workspace,
)
from agents_remember.cli.role_report_preparation import role_report_path as _role_report_path
from agents_remember.cli.role_step_up_runtime import selected_defaults
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsules.vocabulary import CapsuleOperation
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import (
    LauncherRole,
    RoleAgentOverride,
    RoleLauncherOptionsRequest,
    RoleSelection,
)
from agents_remember.models.task_document_ref import TaskScopedReaderContext
from agents_remember.serving.launch_capsule import LaunchCapsule, LaunchCapsuleRequest
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.task_document_copies import prepare_task_document_copies
from agents_remember.worktrees.task_resolver import series_contract_path

ROLE_START_OPERATIONS: dict[LauncherRole, CapsuleOperation] = {
    "architect": "planning",
    "investigator": "orientation",
    "orchestrator": "coordination",
    "manager": "coordination",
    "worker": "implementation",
    "reviewer": "review",
    "curator": "curation",
}


# The two AR tools a role agent's instructions name for starting and messaging role agents.
ROLE_START_TOOL = "role_start"
ROLE_MESSAGE_TOOL = "role_message"
# The name under which a harness may carry the AR tool server of another installation.
OTHER_INSTALLATION_TOOL_SERVER = "agents-remember"
# The launch's tool server as a harness that prefixes tool names spells it, and one of its tools.
_UNDERSCORED_TOOL_SERVER = TOOL_SERVER_NAME.replace("-", "_")
_PREFIXED_TOOL_SERVER = f"mcp__{_UNDERSCORED_TOOL_SERVER}"
_PREFIXED_TOOL_EXAMPLE = f"{_PREFIXED_TOOL_SERVER}__server_info"
# Where a launched agent finds its AR tools. The first message says it twice: in plain text
# before the capsule, and as host.arMcpUsage of the handover. It names no harness and no
# harness's tool: every harness shows a tool server's tools in its own way.
_AR_TOOL_SERVER_USAGE_HEAD = (
    f"Call every Agents Remember tool on the tool server named {TOOL_SERVER_NAME}: the AR build "
    "that launched this agent started it for this agent, and its tools are in this session. A "
    "harness shows them in its own way, so find them by the server's name in either spelling, "
    f"{TOOL_SERVER_NAME} or {_UNDERSCORED_TOOL_SERVER}. Either a tool is declared to you under a "
    "name that contains that spelling and ends in the tool's own name, for example "
    f"{_PREFIXED_TOOL_EXAMPLE}: call it. Or your harness's own instructions to you list a server "
    f"or namespace in that spelling, for example {_PREFIXED_TOOL_SERVER}, and say how its tools "
    "are reached, for example through the harness's tool search or its script tool: reach them "
    "that way, and learn a tool's arguments there too. A tool that lists, describes, connects "
    "or proxies tool servers holds only the servers it was configured with. When it does not "
    f"list {TOOL_SERVER_NAME} itself, do not use it for AR tools at all, not even to search for "
    "or describe a tool by its name: what it answers then comes from another installation, and "
    'its answer "server not found" speaks only for that tool. '
)
# The server-choice sentence (MIK-R112 rule 6, last line; MIK-R132 rule 5). It stays in what
# an agent reads unless its launch hands the host what keeps the other server out.
_AR_OTHER_SERVER_CHOICE = (
    "Do not connect to, list, describe or call a tool server named "
    f"{OTHER_INSTALLATION_TOOL_SERVER} or any of its tools, nor an AR tool server under any "
    "other name: it belongs to another installation, its tools carry the same tool names, and "
    "nothing found there serves this assignment. "
)
_AR_TOOL_SERVER_USAGE_TAIL = (
    "After a start or a resume a tool server can "
    f"take some seconds to appear: look once more before reporting {TOOL_SERVER_NAME} missing, "
    "and report that instead of substituting another. For a leaf, pass the exact "
    "arMcpContext.readerArguments; add the requested files to read_ar_files as a list of "
    'path strings such as "<path in the repository>". A read names where each file is and '
    "carries no file text: read the named files with your harness's own file tool. If either tool "
    "schema lacks the declared task_context fields, stop and report the missing AR reader "
    "capability; do not drop task_context or substitute another root."
)
# MIK-R193 rule 11: the one constant every role's first message carries. Another role's work is
# a role_start, never a helper; a harness without sub-agents is one of the named exceptions.
SPLIT_WORK_DIRECTION = (
    "Inside your own assignment, split independent work across helpers (your harness's "
    "sub-agents) from the start, where your harness has them; helpers have no cap. Another "
    f"role's work goes to {ROLE_START_TOOL}, never to a helper. Working through independent parts "
    "in series is the exception and names its reason: a gate, a shared-file one-writer "
    "dependency, a ruling, or a harness without sub-agents. You stay the single writer of your "
    "stage's records and verify every helper's part. Your report says how you split the work, "
    "or why it could not be split."
)
_AR_TOOL_SERVER_USAGE = (
    f"{_AR_TOOL_SERVER_USAGE_HEAD}{_AR_OTHER_SERVER_CHOICE}{_AR_TOOL_SERVER_USAGE_TAIL}"
)


def _ar_tool_server_usage(harness_id: str | None, *, servers: tuple[str, ...] | None = None) -> str:
    """The AR-tool guidance for one launch: the choice sentence unless the copy is kept out.

    The wording follows the same record the launch call carries (``launch_server_exclusion``
    for this provider and these servers), so the first message and the role start's answer
    cannot say different things. servers=None means the launch's tool-server acceptance is
    unknown: the sentence stays, so the brief claims no exclusion it cannot know.
    """
    if servers is not None and launch_server_exclusion(harness_id, servers=servers) is not None:
        return f"{_AR_TOOL_SERVER_USAGE_HEAD}{_AR_TOOL_SERVER_USAGE_TAIL}"
    return _AR_TOOL_SERVER_USAGE


def _brief_servers(accepts_tool_servers: bool | None) -> tuple[str, ...] | None:
    """The servers tuple the brief exclusion predicate reads, if known."""
    if accepts_tool_servers is None:
        return None
    return (TOOL_SERVER_NAME,) if accepts_tool_servers else ()


def role_start_operation(role: LauncherRole) -> CapsuleOperation:
    """Select the one existing operation appropriate to a manually selected role."""

    return ROLE_START_OPERATIONS[cast(LauncherRole, canonical_role(role))]


@dataclass(frozen=True, slots=True)
class RoleHandoverRequest:
    config: McpRuntimeConfig
    context: RoleLaunchContext
    workspace: dict[str, str]
    agent_id: str
    ar_mcp_context: dict[str, Any]
    request_id: uuid.UUID | None = None
    # The role agent that starts this role; none when the launcher starts it.
    started_by: StartingAgent | None = None
    # A chat without a role started this through role_start (MIK-R113 rule 6);
    # the launch record says so. False for every dashboard and role-agent start.
    chat_started: bool = False
    session_options: dict[str, Any] = field(default_factory=dict)
    # The resolved agent (the host's provider id) this launch will run on; none when
    # unresolved. The brief keeps the server-choice sentence unless this launch hands the
    # host what keeps the other server out (MIK-R132 rule 5).
    harness_id: str | None = None
    # Whether the launch's provider accepts tool servers (None when the cached
    # catalog cannot say without a live call): the brief exclusion predicate reads
    # this, never the harness name alone.
    accepts_tool_servers: bool | None = None


@dataclass(frozen=True, slots=True)
class PreparedRoleHandover:
    """The validated inputs of one role launch, from the launcher or from a role agent."""

    context: RoleLaunchContext
    workspace: dict[str, str]
    agent_id: str
    session_options: dict[str, Any]
    ar_mcp_context: dict[str, Any]
    handover: dict[str, Any]
    request_id: uuid.UUID


def prepare_role_handover(  # noqa: PLR0913 -- chat_started rides the rule-6 launch record
    config: McpRuntimeConfig,
    context: RoleLaunchContext,
    *,
    agent_override: RoleAgentOverride | None,
    request_id: uuid.UUID,
    started_by: StartingAgent | None = None,
    chat_started: bool = False,
    step_up: str | None = None,
) -> PreparedRoleHandover:
    """Prepare the canonical role handover and the exact launch inputs of one role."""

    # The selection is validated against the cached catalog before anything is created for it.
    defaults, harness_order = _role_defaults(config, context)
    defaults = selected_defaults(config, context, defaults, step_up)
    agent_id, session_options = resolve_agent_selection(
        config, defaults, harness_order, agent_override
    )
    try:
        handover_accepts = provider_accepts_tool_servers(config, agent_id)
    except PaseoBridgeFailure:
        # The cached catalog cannot say without a live call that is not made
        # here: the brief keeps the server-choice sentence (no claim).
        handover_accepts = None
    workspace = _resolve_workspace(config, context)
    _prepare_role_folders(context, workspace, request_id)
    if context.role in LEAF_ROLES:
        # Creating the enclosure records it in the leaf's task document. The handover describes
        # the documents as they stand now, so the same request compiles to the same binding again.
        context = _current_role_context(config, context)
    _prepare_task_document_files(config, context, workspace)
    ar_mcp_context = _ar_mcp_context(config, context, workspace)
    handover = _compile_handover(
        RoleHandoverRequest(
            config=config,
            context=context,
            workspace=workspace,
            agent_id=agent_id,
            ar_mcp_context=ar_mcp_context,
            request_id=request_id,
            started_by=started_by,
            chat_started=chat_started,
            session_options=session_options,
            harness_id=agent_id,
            accepts_tool_servers=handover_accepts,
        )
    )
    return PreparedRoleHandover(
        context=context,
        workspace=workspace,
        agent_id=agent_id,
        session_options=session_options,
        ar_mcp_context=ar_mcp_context,
        handover=handover,
        request_id=request_id,
    )


def _prepare_task_document_files(
    config: McpRuntimeConfig, context: RoleLaunchContext, workspace: dict[str, str]
) -> None:
    if context.role not in LEAF_ROLES:
        return
    if "contractPath" not in workspace:
        raise ValueError("A leaf role has no enclosure for its task document copies")
    root = Path(workspace["path"]) / "task-documents"
    for key in ("codeRoot", "memoryRoot"):
        if key in workspace and root.resolve().is_relative_to(Path(workspace[key]).resolve()):
            raise ValueError(
                "Task document copies must be outside the code and memory Git worktrees"
            )
    if context.task is None:
        raise ValueError("A leaf role has no canonical leaf document")
    prepare_task_document_copies(
        config.coordination_root, Path(workspace["path"]), context.task.ref
    )


def _task_document_copy_lists(workspace: dict[str, str]) -> dict[str, Any]:
    """The published task-document copies one handover names (MIK-R111 rule 3).

    Absolute copy paths the agent opens with its own file tools, and the
    repo-relative requirement files the leaf names but that do not exist.
    No index (a controller, or a leaf before its copies) names nothing.
    """

    copies: list[str] = []
    missing: list[str] = []
    try:
        rows = json.loads(
            (Path(workspace["path"]) / "task-documents" / "INDEX.json").read_text(encoding="utf-8")
        ).get("documents", [])
    except (OSError, ValueError, KeyError):
        rows = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                continue
            if row.get("missing") is True:
                missing.append(str(row.get("reference", row["path"])))
            else:
                copies.append((Path(workspace["path"]) / "task-documents" / row["path"]).as_posix())
    return {"copies": copies, "missingCopies": missing}


def _task_document_read_metadata(
    config: McpRuntimeConfig,
    context: RoleLaunchContext,
    workspace: dict[str, str],
    task_reads: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    own_document = context.effective_task
    own_ref = own_document.ref.model_dump(mode="json") if own_document is not None else None
    for row in task_reads:
        reference = row["taskDocumentRef"]
        if reference != own_ref:
            continue
        if context.role in LEAF_ROLES:
            row["renderedReadPath"] = (
                Path(workspace["path"])
                / "task-documents"
                / Path(reference["path"]).with_suffix(".md")
            ).as_posix()
        else:
            row["renderedReadPath"] = (
                config.coordination_root
                / "tasks"
                / reference["repository"]
                / Path(reference["path"]).with_suffix(".md")
            ).as_posix()
    primary_files = [
        row["renderedReadPath"] for row in task_reads if row["taskDocumentRef"] == own_ref
    ]
    document_files = {
        "paths": primary_files,
        "state": "taskless"
        if not primary_files
        else "controller-document-outside-working-folder"
        if any(not Path(path).is_relative_to(Path(workspace["path"])) for path in primary_files)
        else "inside-working-folder",
        **_task_document_copy_lists(workspace),
    }
    first_read = (
        "Read this rendered task document first with your harness's own file tools: "
        + ", ".join(primary_files)
        + ". Change documents through task_doc only; edits to a copy are never read back and are lost on the next publication."
        if primary_files
        else "This taskless role has no task document to read."
    )
    return document_files, first_read


def _current_role_context(
    config: McpRuntimeConfig, context: RoleLaunchContext
) -> RoleLaunchContext:
    """Resolve the selection's task documents again, as they are on disk at this moment."""

    return resolve_role_launch_context(
        config,
        RoleSelection(
            role=context.role,
            sprintDocumentRef=context.sprint.ref if context.sprint else None,
            masterDocumentRef=context.master.ref if context.master else None,
            taskDocumentRef=context.task.ref if context.task else None,
        ),
    )


def _launcher_catalog(
    config: McpRuntimeConfig,
    context: RoleLaunchContext,
    request: RoleLauncherOptionsRequest,
) -> dict[str, Any]:
    """Role defaults plus the Paseo runtime's cached catalog; only an explicit refresh rediscovers."""

    defaults, harness_order = _role_defaults(config, context)
    return launcher_options(config, defaults, harness_order, refresh=request.refresh_catalog)


def _role_defaults(
    config: McpRuntimeConfig, context: RoleLaunchContext
) -> tuple[dict[str, Any], tuple[str, ...]]:
    return role_defaults(config, context, read_host_role(config, context.role))


def _ar_mcp_context(
    config: McpRuntimeConfig,
    context: RoleLaunchContext,
    workspace: dict[str, str],
) -> dict[str, Any]:
    """Declare the exact arguments role agents pass to the readers of their AR tool server."""

    if context.role in LEAF_ROLES:
        if context.task is None:
            raise ValueError("A leaf role requires its canonical task document for AR MCP reads.")
        task_context = TaskScopedReaderContext(
            task_document_ref=context.task.ref,
            contract_path=Path(workspace["contractPath"]).resolve().as_posix(),
        )
        task_context_args = task_context.model_dump(mode="json")
        return {
            "schema": "ar-mcp-reader-context/v1",
            "scopeKind": "canonical-leaf",
            "repositoryId": context.task.ref.repository,
            "taskContext": task_context_args,
            "readerArguments": {
                "context_packet": {
                    "repo_id": context.task.ref.repository,
                    "task_context": task_context_args,
                },
                "read_ar_files": {
                    "repo_id": context.task.ref.repository,
                    "task_context": task_context_args,
                },
            },
            "requiredArguments": {
                "context_packet": ["repo_id", "task_context"],
                "read_ar_files": ["repo_id", "files", "task_context"],
            },
            "readArFilesNote": "Add the requested files list to readerArguments.read_ar_files.",
            "requiredCapability": "ar-task-scoped-readers/v1",
            "missingCapabilityAction": (
                f"If the context_packet or the read_ar_files schema of {TOOL_SERVER_NAME} lacks "
                "task_context with both task_document_ref and contract_path, stop and report "
                "the missing ar-task-scoped-readers/v1 capability. Do not call a task reader "
                "without task_context or substitute caller-selected roots."
            ),
        }

    repository_id = context.effective_task.ref.repository if context.effective_task else None
    if repository_id is not None and repository_id not in config.repositories:
        raise ValueError("The selected Projects repository is not admitted by MCP settings.")
    reader_arguments = (
        {
            "context_packet": {"repo_id": repository_id},
            "read_ar_files": {"repo_id": repository_id},
        }
        if repository_id
        else None
    )
    return {
        "schema": "ar-mcp-reader-context/v1",
        "scopeKind": "configured-projects",
        "repositoryId": repository_id,
        "availableRepositoryIds": sorted(config.repositories),
        "readerArguments": reader_arguments,
        "repositorySelection": (
            None
            if repository_id
            else "Choose repo_id from availableRepositoryIds for each reader call."
        ),
        "missingCapabilityAction": (
            f"If the context_packet or the read_ar_files schema of {TOOL_SERVER_NAME} is "
            "unavailable, report that; do not invent a repository id or pass caller-selected "
            "roots."
        ),
    }


def _verify_leaf_revival_scope(
    config: McpRuntimeConfig, context: RoleLaunchContext, receipt: dict[str, Any]
) -> None:
    """Refuse the revive of a leaf-bound agent whose task scope changed since its launch.

    The scope is the task reference and the contract path. The one computed now comes from the
    leaf document alone, so the comparison creates and starts nothing. A direct leaf is
    bound through its grant to its master's series contract, never to a leaf enclosure.
    """

    if context.task is None:
        raise ValueError("A leaf role requires its canonical task document for a revive.")
    current = TaskScopedReaderContext(
        task_document_ref=context.task.ref,
        contract_path=_revival_contract_path(config, context.task).as_posix(),
    ).model_dump(mode="json")
    recorded = _recorded_leaf_scope(receipt)
    if recorded != current:
        raise ValueError(
            "The task scope recorded at launch differs from the scope computed now, so the "
            "agent was not revived. Recorded at launch: "
            f"{json.dumps(recorded, sort_keys=True)}. Computed now: "
            f"{json.dumps(current, sort_keys=True)}. Start a new execution for the current scope."
        )


def _revival_contract_path(config: McpRuntimeConfig, leaf: ResolvedTaskDocument) -> Path:
    """The contract binding a leaf's revival: the series contract through its grant.

    A direct leaf never owns a leaf enclosure, so its binding is the canonical
    leaf document with its master's series contract. Any other leaf keeps the
    enclosure binding. The grant is read, never inferred: an unreadable grant
    refuses the revive rather than falling back to an enclosure.
    """
    if _direct_leaf_grant(config, leaf):
        return series_contract_path(leaf.path.parent)
    return _leaf_contract_path(leaf)


def _recorded_leaf_scope(receipt: dict[str, Any]) -> dict[str, Any] | None:
    """The task reference and contract path the launch wrote into the receipt, if it did."""

    reader_context = receipt.get("arMcpContext")
    scope = reader_context.get("taskContext") if isinstance(reader_context, dict) else None
    return scope if isinstance(scope, dict) else None


def _prepare_role_folders(
    context: RoleLaunchContext, workspace: dict[str, str], request_id: uuid.UUID
) -> None:
    root = Path(workspace["path"])
    if context.role not in LEAF_ROLES:
        execution = root / ".agents-remember" / "role-executions" / str(request_id)
        local = execution / "role-reports"
        canonical = (
            (context.effective_task.path.parent / "notes" / "reports")
            if context.effective_task
            else root / ".agents-remember" / "reports"
        )
        workspace.update(taskReportAccessRoot=local.as_posix(), taskReportRoot=canonical.as_posix())
        scratch = execution / "scratch"
    else:
        workspace["taskReportAccessRoot"] = (
            root / "role-reports" / context.role / str(request_id)
        ).as_posix()
        scratch = root / "scratch" / context.role / str(request_id)
    workspace["taskReportRoot"] = (
        Path(workspace["taskReportRoot"]) / context.role / str(request_id)
    ).as_posix()
    for path in (Path(workspace["taskReportAccessRoot"]), scratch):
        if any(
            parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(root)
        ) or not path.resolve().is_relative_to(root):
            raise ValueError(f"Role folder {path} must be a real directory inside {root}.")
        path.mkdir(parents=True, exist_ok=True)
    workspace["scratchRoot"] = scratch.as_posix()


def _assembled_brief(
    request: RoleHandoverRequest,
    handover: dict[str, Any],
    *,
    report_path: str,
    texts: tuple[str, str],
    capsule: LaunchCapsule,
) -> tuple[str, dict[str, Any], dict[str, Any], str]:
    """The first message, its projection and the file it names, from what this module gathered.

    The assembly itself lives in its own module (MIK-R193 rule 1 and rule 10); this call gives it
    the role's own values: who started the seat, the one direction its brief carries, where this
    session holds the AR tools, and the compiled capsule.
    """

    context = request.context
    bound_document = (
        context.effective_task.ref.model_dump(mode="json") if context.effective_task else None
    )
    first_read, instructions = texts
    parts = BriefParts(
        started_from=_started_from(request.started_by),
        direction=SPLIT_WORK_DIRECTION,
        ar_tools=_ar_tool_server_usage(
            request.harness_id, servers=_brief_servers(request.accepts_tool_servers)
        ),
        first_read=first_read,
        instructions=instructions,
        shared_block=capsule.shared_block,
    )
    return brief_prompt(
        handover,
        report_path=report_path,
        bound_document=bound_document,
        parts=parts,
    )


def _compile_handover(
    request: RoleHandoverRequest,
) -> dict[str, Any]:
    config = request.config
    context = request.context
    workspace = request.workspace
    agent_id = request.agent_id
    ar_mcp_context = request.ar_mcp_context
    request_id = request.request_id
    operation = role_start_operation(context.role)
    task_ref = context.effective_task.ref if context.effective_task else None
    role_config = config
    if context.role in LEAF_ROLES:
        if context.task is None:
            raise ValueError(
                "A leaf role requires its canonical task document for role preparation."
            )
        task_context = TaskScopedReaderContext.model_validate(ar_mcp_context["taskContext"])
        role_config = task_scoped_mcp_config_for_reader(
            config,
            repository_id=context.task.ref.repository,
            task_context=task_context,
        )
    capsule_request = LaunchCapsuleRequest(
        role=context.role,
        workspace_root=Path(workspace["path"]),
        task_document_ref=task_ref,
        allow_project_task_binding=(
            context.role in {"orchestrator", "manager", "investigator"}
            and Path(workspace["path"]).resolve() == role_config.workspace_root.resolve()
        ),
    )
    capsule = compile_launch_capsule(role_config, capsule_request, operation=operation)
    if capsule.is_refusal:
        raise ValueError(capsule.explain())
    if capsule.delivery is None:
        raise ValueError("The selected agent has no supported AR instruction carrier.")
    documents = [doc for doc in (context.sprint, context.master, context.task) if doc]
    task_reads = [_read_task_doc(config, document) for document in documents]
    document_files, first_read = _task_document_read_metadata(
        config, context, workspace, task_reads
    )
    primary = context.effective_task
    context_packet = None
    if context.role in LEAF_ROLES and context.task is not None:
        context_packet = build_context_packet(
            role_config,
            ContextPacketRequest(repo_id=context.task.ref.repository),
        )
    elif primary and primary.ref.repository in config.repositories:
        context_packet = build_context_packet(
            config,
            ContextPacketRequest(repo_id=primary.ref.repository),
        )
    repository_context = None
    if context.effective_task is None:
        repository_context = {
            "selectedRepository": None,
            "availableRegisteredRepositoryIds": sorted(config.repositories),
            "workspaceIsExecutionScopeOnly": True,
            "capsuleRepositoryIdMeaning": (
                "For this taskless seat, the capsule's required repositoryId names the execution "
                "workspace scope only; it does not select a registered repository or trust packet."
            ),
        }
    report_path = _role_report_path(context, workspace, request_id=request_id)
    if request_id is None:
        raise ValueError("A message binding requires its durable requestId.")
    task_document_digest = _digest(task_reads)
    instructions, instruction_record = compose_launch_instructions(
        capsule.delivery,
        report_path,
        request.session_options.get("choices", {}).get("notes", {}),
    )
    effective_digest = instruction_record["semanticDigest"]
    canonical_report_path = (
        Path(workspace["taskReportRoot"])
        / Path(report_path).relative_to(Path(workspace["taskReportAccessRoot"]))
    ).as_posix()
    ar_binding = {
        "requestId": str(request_id),
        "role": context.role,
        "operation": operation,
        "selection": selection_binding(context),
        "taskDocumentDigest": task_document_digest,
        "taskReportPath": canonical_report_path,
        "capsuleDigest": effective_digest,
    }
    message_binding_projection = _message_binding_projection_reference(
        config, request_id, ar_binding
    )
    started_by = request.started_by
    chat_started = request.chat_started
    entry_mode = _entry_mode(started_by, chat_started)
    handover = {
        "schema": "ar-role-handover/v1",
        "requestId": str(request_id),
        "role": context.role,
        "operation": operation,
        "assignment": _role_assignment(context, report_path, started_by=started_by),
        "agent": agent_id,
        "selection": selection_binding(context),
        "documents": task_reads,
        "taskDocumentFiles": document_files,
        "taskDocumentDigest": task_document_digest,
        "workspace": workspace,
        "repositoryContext": repository_context,
        "taskReportPath": report_path,
        "canonicalTaskReportPath": canonical_report_path,
        "launchChoices": launch_choice_metadata(request.session_options.get("choices", {})),
        "instructionComposition": instruction_metadata(instruction_record),
        "contextPacket": context_packet,
        "arMcpContext": ar_mcp_context,
        "capsule": {
            "role": capsule.role,
            "operation": capsule.delivery.binding.operation,
            "semanticDigest": effective_digest,
            "baselineSemanticDigest": capsule.delivery.semantic_digest,
            "blocks": instruction_metadata(instruction_record)["blocks"],
            "baselineReplaced": instruction_record["baselineReplaced"],
            "binding": capsule.delivery.binding.as_report(),
        },
        "host": {
            "name": "Paseo",
            "entryMode": entry_mode,
            "instructionSource": {
                "kind": instruction_record["kind"],
                "role": capsule.delivery.binding.role,
                "operation": capsule.delivery.binding.operation,
                "semanticDigest": effective_digest,
                "ambientRoleFilesSelected": False,
            },
            "parent": (
                {
                    "agentId": started_by.agent_id,
                    "role": started_by.role,
                    "task": started_by.subject,
                    "sprintDocumentRef": (
                        started_by.sprint_ref.model_dump(mode="json")
                        if started_by.sprint_ref is not None
                        else None
                    ),
                    "masterDocumentRef": (
                        started_by.master_ref.model_dump(mode="json")
                        if started_by.master_ref is not None
                        else None
                    ),
                    "taskDocumentRef": (
                        started_by.task_ref.model_dump(mode="json")
                        if started_by.task_ref is not None
                        else None
                    ),
                    "returnAddress": _return_address(started_by, context),
                }
                if started_by
                else None
            ),
            "ownerRelation": _owner_relation(
                started_by, role=context.role, context=context, chat_started=chat_started
            ),
            "developerQuestions": (
                "Because another agent started this role, send what needs the "
                "developer's decision to that parent with role_message on "
                f"agents-remember-task, addressed to agent {started_by.agent_id}. "
                "This rule takes precedence over generic own-chat guidance for this case. "
                "Say what you hold back, and what you recommend and why. "
                "Keep working on everything independent of the answer; do not end your turn "
                "on the question or hold a wait for the developer. When no independent work "
                "remains, record your state in your report and say you await your parent's "
                "message; put no question to the developer in your own chat."
                if started_by is not None
                else (
                    "Put every question for the developer in your own chat: write it as your reply "
                    "in this session and end your turn. The developer reads this chat in the "
                    "dashboard and answers in it. Never send a developer question to another agent."
                )
            ),
            "arToolServer": TOOL_SERVER_NAME,
            "arMcpUsage": _ar_tool_server_usage(
                request.harness_id,
                servers=_brief_servers(request.accepts_tool_servers),
            ),
            "roleTools": {
                "toolServer": TOOL_SERVER_NAME,
                "start": ROLE_START_TOOL,
                "message": ROLE_MESSAGE_TOOL,
                "usage": (
                    f"{ROLE_START_TOOL} on {TOOL_SERVER_NAME} starts one role agent for one "
                    "canonical selection, when this role may start that role. Choose the "
                    "request id yourself (a UUID) and repeat the same id to reconcile an "
                    "uncertain start; a new id is a new agent. It returns the new agent's id, "
                    "report path, handover artifact path and launch status. "
                    f"{ROLE_MESSAGE_TOOL} on {TOOL_SERVER_NAME} sends one message to one role "
                    "agent, addressed by its agent id or by its role plus task references. The "
                    "recipient reads your role, task and agent id in the first line. It never "
                    "interrupts a running turn. A message the host cannot take now is held by "
                    "the product instead of refused: to a recipient that waits for a permission "
                    "decision, whose start has not finished, or that is in a turn the host cannot "
                    "write into. The answer says held and why, and the product delivers what it "
                    "holds in the order it took it once the recipient can take it. The host is "
                    "never sent a message for a recipient that waits at a permission prompt, "
                    "because it would answer the permission with a denial: the developer answers "
                    "it in that agent's chat. A role "
                    "address never means the caller itself. Without wait the call answers accepted "
                    "once the host took the message, or held once the product held it. With wait "
                    "it returns when the turn "
                    "that took the message has ended: the recipient's final text, or that the "
                    "turn failed or was cancelled; or it says that a permission is pending, also "
                    "for a wait on a held message, or "
                    "that the time was up, and the message stays delivered then. It can answer "
                    "accepted without a text when the host cannot say which turn took the "
                    "message, and the text of a message delivered during a turn can be the "
                    "running turn's own; detail says which. Do not wait on an agent that may be "
                    "waiting on you: two agents that wait on each other both stand still until "
                    "one wait runs out. A message another agent sent you begins with a line "
                    '"From <role> · <task> · agent <agent id>": your reply in that turn '
                    "reaches the sender only when it waits on its own call; otherwise answer "
                    f"with {ROLE_MESSAGE_TOOL} to that agent id. A refusal names its "
                    "reason: act on that reason, never guess a recipient, and never start a "
                    "second agent for an uncertain result. Do not create or message role agents "
                    "with any other tool: an agent created another way has no capsule and no "
                    "binding."
                ),
            },
            "messageBinding": {
                "arBinding": ar_binding,
                "projection": message_binding_projection,
                "use": (
                    "The immutable file at projection.path records which task, role and capsule "
                    f"this launch was bound to. {ROLE_MESSAGE_TOOL} names you from the binding "
                    "of your own tool server; you do not attach this record to a message."
                ),
            },
        },
        "ownerHandover": (
            "The effective role instructions and this handover are the role brief for "
            "this request. Older ambient AR lifecycle/router/role files and coordination-level "
            "role-routing prose were not selected by this launch; do not load them as a second "
            "role, operation, hierarchy, parent, or transport. "
            "Keep higher-priority system and developer instructions and applicable repository "
            "coding, tool, and safety rules. AR owns task requirements, review and curation "
            "decisions, task lifecycle, and paired Git acceptance. This agent performs only the "
            "assignment above. For leaf context_packet/read_ar_files calls, use the exact "
            "arMcpContext.readerArguments; task scope is per call and includes no "
            f"caller-selected roots. If the {TOOL_SERVER_NAME} tool schema does not expose "
            "task_context with both required fields, stop and report that capability mismatch. "
            + first_read
            + " Each contentDigest records the "
            "document snapshot used for this launch. Never claim AR review, curation, lifecycle, "
            "or Git acceptance from a finished turn."
        ),
    }
    if context.role in LEAF_ROLES and context.task is not None:
        handover["leafSeats"] = {
            "roles": ["worker", "reviewer", "curator"],
            "roleMessageArguments": {
                role: {
                    "role": role,
                    "sprint_document_ref": ar_binding["selection"]["sprintDocumentRef"],
                    "master_document_ref": ar_binding["selection"]["masterDocumentRef"],
                    "task_document_ref": context.task.ref.model_dump(mode="json"),
                }
                for role in ("worker", "reviewer", "curator")
                if role != context.role
            },
        }
    # The complete object is written beside the message before the start is sent; the message
    # names it and carries only the projection (MIK-R193 rules 1 and 2).
    prompt, projection, handover_file, handover_file_content = _assembled_brief(
        request,
        handover,
        report_path=report_path,
        texts=(first_read, instructions),
        capsule=capsule,
    )
    return {
        "prompt": prompt,
        "handover": handover,
        "projection": projection,
        "handoverFile": handover_file,
        "handoverFileContent": handover_file_content,
        "capsuleOperation": operation,
        "capsuleDigest": effective_digest,
        "instructionComposition": instruction_record,
        "taskDocumentDigest": task_document_digest,
        "messageBindingProjection": {
            "requestId": str(request_id),
            "binding": ar_binding,
            **message_binding_projection,
        },
        "taskReportPath": report_path,
        "canonicalTaskReportPath": canonical_report_path,
        "arMcpContext": ar_mcp_context,
        "taskDocumentFiles": document_files,
    }


def _started_from(started_by: StartingAgent | None) -> str:
    """Who started the role, as the first sentence of its brief says it."""

    if started_by is None:
        return "the dashboard launcher"
    return f"agent {started_by.agent_id} ({started_by.role} · {started_by.subject})"


def _return_address(
    started_by: StartingAgent | None, context: RoleLaunchContext
) -> dict[str, Any] | None:
    """The address a return goes to: the parent's role with its task references.

    A leaf's parent is the current Manager of its master, whoever started the
    agent; any other role's parent is the agent above it, whoever started it
    (MIK-R117 rule 10). An id read from an earlier message is never the
    address of a return: after a replacement it names an archived agent.
    """

    def dumped(reference: Any) -> dict[str, Any] | None:
        return reference.model_dump(mode="json") if reference is not None else None

    if context.role in LEAF_ROLES and context.sprint is not None and context.master is not None:
        return {
            "role": "manager",
            "sprint_document_ref": dumped(context.sprint.ref),
            "master_document_ref": dumped(context.master.ref),
        }
    # A leaf without its master in context still names its starter's role;
    # the references ride along where they are known.
    if started_by is None:
        return None
    parent = canonical_role(started_by.role)
    wanted: tuple[str, ...] = {
        "manager": ("sprint", "master"),
        "orchestrator": ("sprint",),
        "investigator": ("sprint", "master"),
    }.get(parent, ())
    starters = {
        "sprint": started_by.sprint_ref,
        "master": started_by.master_ref,
        "task": started_by.task_ref,
    }
    fallbacks = {
        "sprint": context.sprint.ref if context.sprint else None,
        "master": context.master.ref if context.master else None,
        "task": context.task.ref if context.task else None,
    }
    address: dict[str, Any] = {"role": started_by.role}
    for name in wanted:
        reference = starters[name] or fallbacks[name]
        if reference is not None:
            address[f"{name}_document_ref"] = dumped(reference)
    return address


def _address_words(address: dict[str, Any]) -> str:
    """The return address in words, as the first message names it."""
    words = [f"role {address['role']}"]
    for key in ("sprint_document_ref", "master_document_ref", "task_document_ref"):
        reference = address.get(key)
        if isinstance(reference, dict):
            words.append(f"{key} {reference.get('repository')}:{reference.get('path')}")
    return ", ".join(words)


def _entry_mode(started_by: StartingAgent | None, chat_started: bool) -> str:
    """How the launch entered the host, as the launch record says it (MIK-R113 rule 6)."""

    if started_by is not None:
        return "agent-role-start"
    return "chat-role-start" if chat_started else "dashboard-role-start"


def _owner_relation(
    started_by: StartingAgent | None,
    *,
    role: LauncherRole,
    context: RoleLaunchContext,
    chat_started: bool = False,
) -> str:
    """Whom this role answers to: the agent that started it, or nobody but the developer.

    A return goes to the parent's role with its task references, named as the
    return address; an id read from an earlier message is never the address of
    a return (MIK-R117 rule 10).
    """

    if started_by is None:
        if chat_started:
            return (
                "This role was started by a chat without a role through role_start "
                "(MIK-R113 rule 6). It has no parent agent and needs none: the "
                "developer who reads this chat owns its decisions. The developer "
                "speaks with this agent in the dashboard. The selected AR "
                "sprint/master/leaf is work scope, not a parent."
            )
        return (
            "This role was started from the dashboard launcher. It has no parent agent and needs "
            "none: the developer who reads this chat owns its decisions. The selected AR "
            "sprint/master/leaf is work scope, not a parent."
        )
    address = _return_address(started_by, context)
    if address is None:
        addressed = (
            "no return address is recorded for this launch: ask the developer in your own "
            "chat where a return goes"
        )
    else:
        addressed = (
            f"addressed to the return address {_address_words(address)}. A return goes to "
            "the parent's role with its task references; an id read from an earlier message "
            "is not the address of a return."
        )
    if role in {"orchestrator", "manager"}:
        return (
            f"Agent {started_by.agent_id} ({started_by.role} · {started_by.subject}) started this "
            "role and is its parent in Paseo. Send that agent only what needs the developer's "
            "decision, as your role text defines it, "
            f"with {ROLE_MESSAGE_TOOL}, {addressed}. Send one further "
            "message with the path of your report when the whole assignment is finished or "
            "cannot be finished. Decide everything else yourself and record it in your "
            "rulings record, list of requirement sentences to change, and status file; send "
            "no other assignment questions, forwarded role messages or landing notices. "
            "The selected AR sprint/master/leaf is work scope; the parent is the agent "
            "named here and no other."
        )
    if role in LEAF_ROLES:
        return (
            f"Agent {started_by.agent_id} ({started_by.role} · {started_by.subject}) started this "
            "role. Its parent in Paseo is the current Manager of this master, whoever started "
            "it: send the parent only questions requiring the "
            "developer's decision or a decision above the leaf, and the start, authority, "
            "recovery, limit, disagreement, sync and closeout-readiness occasions assigned "
            f"by your role, with {ROLE_MESSAGE_TOOL}, {addressed}. Hand-overs, "
            "results and questions about another leaf seat's work go directly to the seat "
            "concerned, using the exact sibling role and task references in "
            "leafSeats.roleMessageArguments. Send no routine result to the parent. "
            "The selected AR sprint/master/leaf is work scope."
        )
    return (
        f"Agent {started_by.agent_id} ({started_by.role} · {started_by.subject}) started this "
        "role and is its parent in Paseo. Send that agent your questions about the assignment, "
        f"questions requiring the developer's decision, and your result with {ROLE_MESSAGE_TOOL}, "
        f"{addressed}. The selected AR "
        "sprint/master/leaf is work scope; the parent is the agent named here and no other."
    )


def _read_task_doc(config: McpRuntimeConfig, resolved: ResolvedTaskDocument) -> dict[str, Any]:
    # MIK-R111 rule 2: a task document is read as a file. The resolution
    # already loaded the document; the tool's removed read operation is not
    # called to read it back.
    del config
    json_path = resolved.path.with_suffix(".json")
    rendered_path = resolved.path.with_suffix(".md")
    if not json_path.is_file():
        raise ValueError(
            f"AR task_doc did not resolve the selected canonical task: {json_path} is not a file."
        )
    document = resolved.document.model_dump(mode="json", by_alias=True, exclude_none=True)
    return {
        "taskDocumentRef": resolved.ref.model_dump(mode="json"),
        "canonicalTaskPath": resolved.ref.key,
        "taskDocReadArgs": {
            "repo_id": resolved.ref.repository,
            "task_name": resolved.path.parent.name,
            "slug": resolved.path.stem,
            "json_path": json_path.as_posix(),
            "rendered_path": rendered_path.as_posix(),
        },
        "documentIdentity": {
            "id": document["id"],
            "slug": document["slug"],
            "kind": document["kind"],
            "title": document["title"],
            "repositoryId": document["repo"],
        },
        "contentDigest": _digest(document),
    }


def _role_assignment(
    context: RoleLaunchContext,
    report_path: str,
    *,
    started_by: StartingAgent | None = None,
) -> str:
    selected = context.effective_task
    if context.role == "architect":
        return (
            "Project-scoped architect launch. Ask the developer what outcome they want and which "
            "registered repository it concerns. The shared Projects workspace is an execution "
            "location, not a repository selection. Until the developer names a repository, do no "
            "repository-specific trust-packet or task-corpus work; do not infer a target from the "
            "workspace name or from a singleton repository registry. Then orient from the selected "
            f"repository's existing AR task portfolio. Do not invent a sprint or task identity. "
            f"Write this session's report to {report_path}."
        )
    if context.role == "investigator":
        concern = (
            "The concern arrives in the parent's first role_message. Ask that parent once for a missing concern or scope and wait; do not invent one."
            if started_by
            else "Ask the developer for the scoped concern before acting."
        )
        return f"Investigator launch: investigate one scoped concern of any kind. {concern} Write one report to {report_path}. Change no code checkout or leaf enclosure, memory, task document or status; make no commit, closeout or integration and start no role. Change provider or system state only after the report and an explicit authorized order."
    assert selected is not None
    return (
        f"Perform the {context.role} role for canonical AR document "
        f"{selected.ref.repository}:{selected.ref.path}. Follow its current requirements, references, "
        f"steps and the effective role instructions. Write the role handoff to {report_path}."
    )
