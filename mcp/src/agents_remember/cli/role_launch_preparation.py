"""Resolve role launch defaults, role folders, and canonical AR handovers.

The handover is the assignment data of one launch. Its host-specific part names Paseo as the
host, the tool server a role agent calls AR tools on, and the two AR tools through which role
agents start and message each other.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import TOOL_SERVER_NAME
from agents_remember.application.context_packet import ContextPacketRequest, build_context_packet
from agents_remember.application.role_capsules.launch import compile_launch_capsule
from agents_remember.application.role_launch_context import (
    LEAF_ROLES,
    ROLE_LEVELS,
    TASKLESS_ROLES,
    RoleLaunchContext,
    resolve_role_launch_context,
    selection_binding,
)
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.task_scoped_mcp import task_scoped_mcp_config_for_reader
from agents_remember.application.worktree_tool_requests import StartExecution, TaskIdentity
from agents_remember.application.worktree_tools import worktree_start_tool, worktree_status_tool
from agents_remember.cli.leaf_enclosure_start import start_leaf_enclosure_in_child
from agents_remember.cli.paseo_catalog import launcher_options, resolve_agent_selection
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.role_launch_receipts import (
    _bind_task_report_access,
    _message_binding_projection_reference,
)
from agents_remember.cli.role_launch_receipts import (
    digest as _digest,
)
from agents_remember.controlplane.durable_store import declared_process_role
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsules.vocabulary import CapsuleOperation
from agents_remember.models.role_launcher import (
    LauncherRole,
    RoleAgentOverride,
    RoleLauncherOptionsRequest,
    RoleSelection,
)
from agents_remember.models.task_document_ref import TaskScopedReaderContext
from agents_remember.serving.launch_capsule import LaunchCapsuleRequest
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path, slugify

ROLE_START_OPERATIONS: dict[LauncherRole, CapsuleOperation] = {
    "architect": "planning",
    "system-specialist": "orientation",
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
# The folder a role's report and handover artifact are written in: under a task's notes/reports,
# or under the Projects folder's own report folder for a taskless role.
REPORTS_DIRECTORY = "role-launch"
# The launch's tool server as a harness that prefixes tool names spells it, and one of its tools.
_UNDERSCORED_TOOL_SERVER = TOOL_SERVER_NAME.replace("-", "_")
_PREFIXED_TOOL_SERVER = f"mcp__{_UNDERSCORED_TOOL_SERVER}"
_PREFIXED_TOOL_EXAMPLE = f"{_PREFIXED_TOOL_SERVER}__server_info"
# Where a launched agent finds its AR tools. The first message says it twice: in plain text
# before the capsule, and as host.arMcpUsage of the handover. It names no harness and no
# harness's tool: every harness shows a tool server's tools in its own way.
_AR_TOOL_SERVER_USAGE = (
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
    'its answer "server not found" speaks only for that tool. Do not '
    "connect to, list, describe or call a tool server named "
    f"{OTHER_INSTALLATION_TOOL_SERVER} or any of its tools, nor an AR tool server under any "
    "other name: it belongs to another installation, its tools carry the same tool names, and "
    "nothing found there serves this assignment. After a start or a resume a tool server can "
    f"take some seconds to appear: look once more before reporting {TOOL_SERVER_NAME} missing, "
    "and report that instead of substituting another. For a leaf, pass the exact "
    "arMcpContext.readerArguments; add the requested files to read_ar_files as a list of "
    'objects such as {"path": "<path in the repository>", "source": "full"}. If either tool '
    "schema lacks the declared task_context fields, stop and report the missing AR reader "
    "capability; do not drop task_context or substitute another root."
)


def role_start_operation(role: LauncherRole) -> CapsuleOperation:
    """Select the one existing operation appropriate to a manually selected role."""

    return ROLE_START_OPERATIONS[role]


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


@dataclass(frozen=True, slots=True)
class PreparedRoleHandover:
    """The validated inputs of one role launch, from the launcher or from a role agent."""

    context: RoleLaunchContext
    workspace: dict[str, str]
    agent_id: str
    session_options: dict[str, str]
    ar_mcp_context: dict[str, Any]
    handover: dict[str, Any]
    request_id: uuid.UUID


def prepare_role_handover(
    config: McpRuntimeConfig,
    context: RoleLaunchContext,
    *,
    agent_override: RoleAgentOverride | None,
    request_id: uuid.UUID,
    started_by: StartingAgent | None = None,
) -> PreparedRoleHandover:
    """Prepare the canonical role handover and the exact launch inputs of one role."""

    # The selection is validated against the cached catalog before anything is created for it.
    defaults, harness_order = _role_defaults(config, context)
    agent_id, session_options = resolve_agent_selection(
        config, defaults, harness_order, agent_override
    )
    workspace = _resolve_workspace(config, context)
    if context.role in LEAF_ROLES:
        # Creating the enclosure records it in the leaf's task document. The handover describes
        # the documents as they stand now, so the same request compiles to the same binding again.
        context = _current_role_context(config, context)
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
) -> tuple[dict[str, str | None], tuple[str, ...]]:
    repository = context.effective_task.ref.repository if context.effective_task else None
    repo_scope = config.repositories.get(repository) if repository else None
    settings = load_agentic_settings(
        config.coordination_root,
        repo_root=repo_scope.path if repo_scope else None,
    )
    knobs = settings.resolved_role_knobs(context.role, ROLE_LEVELS[context.role])
    agent_id = knobs.harness or settings.spawn_harness
    return (
        {"agent": agent_id, "model": knobs.model, "effort": knobs.effort},
        tuple(harness.id for harness in settings.harnesses),
    )


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
                    "include_providers": False,
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
            "context_packet": {"repo_id": repository_id, "include_providers": False},
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


def _verify_leaf_revival_scope(context: RoleLaunchContext, receipt: dict[str, Any]) -> None:
    """Refuse the revive of a leaf-bound agent whose task scope changed since its launch.

    The scope is the task reference and the contract path. The one computed now comes from the
    leaf document alone, so the comparison creates and starts nothing.
    """

    if context.task is None:
        raise ValueError("A leaf role requires its canonical task document for a revive.")
    current = TaskScopedReaderContext(
        task_document_ref=context.task.ref,
        contract_path=_leaf_contract_path(context.task).as_posix(),
    ).model_dump(mode="json")
    recorded = _recorded_leaf_scope(receipt)
    if recorded != current:
        raise ValueError(
            "The task scope recorded at launch differs from the scope computed now, so the "
            "agent was not revived. Recorded at launch: "
            f"{json.dumps(recorded, sort_keys=True)}. Computed now: "
            f"{json.dumps(current, sort_keys=True)}. Start a new execution for the current scope."
        )


def _recorded_leaf_scope(receipt: dict[str, Any]) -> dict[str, Any] | None:
    """The task reference and contract path the launch wrote into the receipt, if it did."""

    reader_context = receipt.get("arMcpContext")
    scope = reader_context.get("taskContext") if isinstance(reader_context, dict) else None
    return scope if isinstance(scope, dict) else None


def _leaf_contract_path(leaf: ResolvedTaskDocument) -> Path:
    """The contract path of a leaf's enclosure, derived from the leaf document alone.

    Nothing is created here: a launch goes on to open the enclosure, a revive only compares.
    """

    if leaf.document.kind != "subTask":
        raise ValueError("Only a canonical leaf can open a leaf enclosure.")
    expected = leaf_enclosure_path(leaf.path.parent, leaf.document.id).resolve()
    if not leaf.document.enclosures:
        return expected
    enclosure = leaf.document.enclosures[0]
    if len(leaf.document.enclosures) != 1 or enclosure.leafId != leaf.document.id:
        raise ValueError("The selected leaf has conflicting enclosure bindings.")
    contract_path = Path(enclosure.enclosurePath).resolve()
    if contract_path != expected:
        raise ValueError("The selected leaf enclosure does not match its canonical task binding.")
    return contract_path


def _resolve_workspace(config: McpRuntimeConfig, context: RoleLaunchContext) -> dict[str, str]:
    """The folder the role class is entitled to: Projects, or the leaf's enclosure group folder.

    Only the folder is resolved here. The Paseo workspace of that folder is obtained from the
    runtime when the launch call runs; no workspace id is kept.
    """

    if context.role not in LEAF_ROLES:
        return workspace_folder(config.workspace_root)
    assert context.task is not None and context.sprint is not None
    contract_path, status = _ensure_leaf_enclosure(
        config,
        context.task,
        parent_task=context.sprint.path.parent.name,
    )
    group = _require_directory(status, "worktree_group")
    code = _require_directory(status, "code_worktree")
    memory = _require_directory(status, "memory_worktree")
    workspace = workspace_folder(group)
    task_reports = context.task.path.parent / "notes" / "reports"
    task_reports.mkdir(parents=True, exist_ok=True)
    report_access = _bind_task_report_access(group, task_reports)
    workspace.update(
        contractPath=contract_path.as_posix(),
        codeRoot=code.as_posix(),
        memoryRoot=memory.as_posix(),
        taskReportRoot=task_reports.resolve().as_posix(),
        taskReportAccessRoot=report_access.as_posix(),
    )
    return workspace


def _ensure_leaf_enclosure(
    config: McpRuntimeConfig,
    leaf: ResolvedTaskDocument,
    *,
    parent_task: str,
) -> tuple[Path, dict[str, Any]]:
    contract_path = _leaf_contract_path(leaf)
    task_root = leaf.path.parent

    status = worktree_status_tool(
        config,
        TaskRef(repo_id=leaf.ref.repository, contract_path=contract_path.as_posix()),
    )
    if status.get("ok") is not True and not contract_path.exists():
        created = _start_leaf_enclosure(
            config,
            TaskIdentity(
                repo_id=leaf.ref.repository,
                task_name=task_root.name,
                worktree_name=(
                    f"{slugify(leaf.document.slug)[:40]}-"
                    f"{hashlib.sha256(leaf.ref.key.encode('utf-8')).hexdigest()[:10]}"
                ),
                leaf_id=leaf.document.id,
                parent_task=parent_task,
            ),
        )
        if created.get("ok") is not True:
            raise ValueError(
                str(
                    created.get("summary")
                    or created.get("state")
                    or "AR refused to create the leaf enclosure."
                )
            )
        status = worktree_status_tool(
            config,
            TaskRef(repo_id=leaf.ref.repository, contract_path=contract_path.as_posix()),
        )
    if status.get("ok") is not True:
        raise ValueError(
            str(status.get("detail") or "AR could not resolve the selected leaf enclosure.")
        )
    return contract_path, status


def _start_leaf_enclosure(config: McpRuntimeConfig, identity: TaskIdentity) -> dict[str, Any]:
    """Create the enclosure with AR's worktree start, in the process entitled to run it.

    The dashboard backend is not a writer of the worktree stores, so from there the start runs in
    a short-lived child process of this build. Every other process calls the worktree owner
    directly.
    """

    if declared_process_role() == "dashboard":
        return start_leaf_enclosure_in_child(config, identity)
    return worktree_start_tool(config, identity, execution=StartExecution(skip_provider_setup=True))


def workspace_folder(path: Path) -> dict[str, str]:
    # The runtime keys a workspace by the path text, so the folder is always its resolved path.
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return {"path": root.as_posix()}


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
            context.role in {"orchestrator", "manager"}
            and Path(workspace["path"]).resolve() == role_config.workspace_root.resolve()
        ),
    )
    capsule = compile_launch_capsule(role_config, capsule_request, operation=operation)
    if capsule.is_refusal:
        raise ValueError(capsule.explain())
    if capsule.codex_delivery is None:
        raise ValueError("The selected agent has no supported AR instruction carrier.")
    documents = [doc for doc in (context.sprint, context.master, context.task) if doc]
    task_reads = [_read_task_doc(config, document) for document in documents]
    primary = context.effective_task
    context_packet = None
    if context.role in LEAF_ROLES and context.task is not None:
        context_packet = build_context_packet(
            role_config,
            ContextPacketRequest(repo_id=context.task.ref.repository, include_providers=False),
        )
    elif primary and primary.ref.repository in config.repositories:
        context_packet = build_context_packet(
            config,
            ContextPacketRequest(repo_id=primary.ref.repository, include_providers=False),
        )
    repository_context = None
    if context.role in TASKLESS_ROLES:
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
    ar_binding = {
        "requestId": str(request_id),
        "role": context.role,
        "operation": operation,
        "selection": selection_binding(context),
        "taskDocumentDigest": task_document_digest,
        "taskReportPath": Path(report_path).resolve(strict=False).as_posix(),
        "capsuleDigest": capsule.codex_delivery.semantic_digest,
    }
    message_binding_projection = _message_binding_projection_reference(
        config, request_id, ar_binding
    )
    started_by = request.started_by
    handover = {
        "schema": "ar-role-handover/v1",
        "requestId": str(request_id),
        "role": context.role,
        "operation": operation,
        "assignment": _role_assignment(context, report_path),
        "agent": agent_id,
        "selection": selection_binding(context),
        "documents": task_reads,
        "taskDocumentDigest": task_document_digest,
        "workspace": workspace,
        "repositoryContext": repository_context,
        "taskReportPath": report_path,
        "canonicalTaskReportPath": Path(report_path).resolve(strict=False).as_posix(),
        "contextPacket": context_packet,
        "arMcpContext": ar_mcp_context,
        "capsule": {
            "role": capsule.role,
            "operation": capsule.codex_delivery.binding.operation,
            "semanticDigest": capsule.codex_delivery.semantic_digest,
            "binding": capsule.codex_delivery.binding.as_report(),
        },
        "host": {
            "name": "Paseo",
            "entryMode": "agent-role-start" if started_by else "dashboard-role-start",
            "instructionSource": {
                "kind": "compiled-role-operation-capsule",
                "role": capsule.codex_delivery.binding.role,
                "operation": capsule.codex_delivery.binding.operation,
                "semanticDigest": capsule.codex_delivery.semantic_digest,
                "ambientRoleFilesSelected": False,
            },
            "parent": (
                {
                    "agentId": started_by.agent_id,
                    "role": started_by.role,
                    "task": started_by.subject,
                }
                if started_by
                else None
            ),
            "ownerRelation": _owner_relation(started_by),
            "developerQuestions": (
                "Put every question for the developer in your own chat: write it as your reply "
                "in this session and end your turn. The developer reads this chat in the "
                "dashboard and answers in it. Never send a developer question to another agent."
            ),
            "arToolServer": TOOL_SERVER_NAME,
            "arMcpUsage": _AR_TOOL_SERVER_USAGE,
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
                    "interrupts a running turn. A recipient that waits for a permission "
                    "decision is refused as busy, because a message would answer the permission "
                    "with a denial: the developer answers it in that agent's chat. A recipient "
                    "whose start has not finished is refused as busy as well, and a role "
                    "address never means the caller itself. With wait it returns when the turn "
                    "that took the message has ended: the recipient's final text, or that the "
                    "turn failed or was cancelled; or it says that a permission is pending or "
                    "that the time was up, and the message stays delivered then. It can answer "
                    "accepted without a text when the host cannot say which turn took the "
                    "message, and the text of a message delivered during a turn can be the "
                    "running turn's own; detail says which. Do not wait on an agent that may be "
                    "waiting on you: two agents that wait on each other both stand still until "
                    "one wait runs out. A message another agent sent you begins with a line "
                    '"From <role> · <task> · agent <agent id>": your reply in that turn, in '
                    "your own chat, is what the sender receives, so answer the message there; "
                    f"send {ROLE_MESSAGE_TOOL} to that agent id only for a message of your own. "
                    "A refusal names its "
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
            "This exact compiled role and operation plus this handover are the role brief for "
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
            f"Resolve each task document by calling task_doc on {TOOL_SERVER_NAME} "
            "with the row's taskDocReadArgs exactly, then read its canonical JSON at the returned "
            "docPath before acting. Do not add .json to the slug. Each contentDigest records the "
            "document snapshot used for this launch. Never claim AR review, curation, lifecycle, "
            "or Git acceptance from a finished turn."
        ),
    }
    prompt = (
        f"AR ROLE BRIEF: {_started_from(started_by)} started this role with the role and "
        "operation identified below. "
        "The following compiled capsule and canonical handover are the complete AR "
        "role/operation instructions and assignment for this agent. Do not reopen ambient AR role, "
        "lifecycle, or coordination-level role-routing text to infer a different assignment. "
        "Preserve system/developer instructions, approvals, sandbox policy, and "
        "repository-specific coding/tool rules.\n\n"
        # Before the capsule, which names AR tools: where this session holds them.
        f"AR tools, before your first tool call: {_AR_TOOL_SERVER_USAGE}\n\n"
        + capsule.codex_delivery.trusted_instructions
        + "\n\nAR owner assignment and canonical task handover:\n"
        + json.dumps(handover, ensure_ascii=False, separators=(",", ":"))
    )
    return {
        "prompt": prompt,
        "handover": handover,
        "capsuleOperation": operation,
        "capsuleDigest": capsule.codex_delivery.semantic_digest,
        "taskDocumentDigest": task_document_digest,
        "messageBindingProjection": {
            "requestId": str(request_id),
            "binding": ar_binding,
            **message_binding_projection,
        },
        "taskReportPath": report_path,
        "canonicalTaskReportPath": Path(report_path).resolve(strict=False).as_posix(),
        "arMcpContext": ar_mcp_context,
    }


def _started_from(started_by: StartingAgent | None) -> str:
    """Who started the role, as the first sentence of its brief says it."""

    if started_by is None:
        return "the dashboard launcher"
    return f"agent {started_by.agent_id} ({started_by.role} · {started_by.subject})"


def _owner_relation(started_by: StartingAgent | None) -> str:
    """Whom this role answers to: the agent that started it, or nobody but the developer."""

    if started_by is None:
        return (
            "This role was started from the dashboard launcher. It has no parent agent and needs "
            "none: the developer who reads this chat owns its decisions. The selected AR "
            "sprint/master/leaf is work scope, not a parent."
        )
    return (
        f"Agent {started_by.agent_id} ({started_by.role} · {started_by.subject}) started this "
        "role and is its parent in Paseo. Send that agent your questions about the assignment "
        f"and your result with {ROLE_MESSAGE_TOOL}, addressed to its agent id. The selected AR "
        "sprint/master/leaf is work scope; the parent is the agent named here and no other."
    )


def _read_task_doc(config: McpRuntimeConfig, resolved: ResolvedTaskDocument) -> dict[str, Any]:
    read_args = {
        "repo_id": resolved.ref.repository,
        "operation": "get",
        "task_name": resolved.path.parent.name,
        "slug": resolved.path.stem,
    }
    target = TaskDocTarget(
        repo_id=read_args["repo_id"],
        task_name=read_args["task_name"],
        slug=read_args["slug"],
    )
    result = task_doc_tool(
        config,
        target,
        operation=read_args["operation"],
        edit=TaskDocEdit(),
        call=TaskDocCall(),
    )
    if result.get("ok") is not True or result.get("docPath") != resolved.path.as_posix():
        raise ValueError(
            str(result.get("detail") or "AR task_doc did not resolve the selected canonical task.")
        )
    document = resolved.document.model_dump(mode="json", by_alias=True, exclude_none=True)
    return {
        "taskDocumentRef": resolved.ref.model_dump(mode="json"),
        "canonicalTaskPath": resolved.ref.key,
        "taskDocReadArgs": read_args,
        "documentIdentity": {
            "id": document["id"],
            "slug": document["slug"],
            "kind": document["kind"],
            "title": document["title"],
            "repositoryId": document["repo"],
        },
        "contentDigest": _digest(document),
    }


def _role_assignment(context: RoleLaunchContext, report_path: str) -> str:
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
    if context.role == "system-specialist":
        return f"Project-scoped system-specialist launch. Ask the developer to identify the provider degradation or system concern and the desired investigation scope before acting. Preserve the role's provider-only and report-first boundaries. Write the report to {report_path}. Do not invent a degradation event or task identity."
    assert selected is not None
    return (
        f"Perform the {context.role} role for canonical AR document "
        f"{selected.ref.repository}:{selected.ref.path}. Follow its current requirements, references, "
        f"steps and the role capsule. Write the role handoff to {report_path}."
    )


def _role_report_path(
    context: RoleLaunchContext,
    workspace: dict[str, str],
    *,
    request_id: uuid.UUID | None = None,
) -> str:
    if context.role in TASKLESS_ROLES:
        if request_id is None:
            raise ValueError("A taskless role report requires its durable requestId.")
        report_root = (
            Path(workspace["path"])
            / ".agents-remember"
            / "reports"
            / REPORTS_DIRECTORY
            / context.role
        )
        report_name = f"{request_id}.md"
    else:
        selected = context.effective_task
        if selected is None:
            raise ValueError("A task-bound role report requires its canonical task document.")
        if request_id is None:
            raise ValueError("A task-bound role report requires its durable requestId.")

        report_root = (selected.path.parent / "notes" / "reports").resolve(strict=False)
        if context.role in LEAF_ROLES:
            if context.task is None:
                raise ValueError("A leaf role report requires its canonical task document.")
            access_path = workspace.get("taskReportAccessRoot")
            if not isinstance(access_path, str) or not access_path:
                raise ValueError(
                    "The selected leaf workspace has no canonical task-report access path."
                )
            report_root = Path(access_path)
            if report_root.resolve(strict=False) != (
                selected.path.parent / "notes" / "reports"
            ).resolve(strict=False):
                raise ValueError(
                    "The leaf task-report access path does not resolve to its canonical reports."
                )
        report_root = report_root / REPORTS_DIRECTORY
        report_name = f"{selected.document.id}-{context.role}-{request_id}.md"
    report = report_root / report_name
    report.parent.mkdir(parents=True, exist_ok=True)
    return report.as_posix()


def _require_directory(payload: dict[str, Any], key: str) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"AR worktree status did not return {key}.")
    path = Path(value).resolve()
    if not path.is_dir():
        raise ValueError(f"The selected AR enclosure {key} directory is unavailable.")
    return path
