"""Resolve Orca launch defaults, workspaces, and canonical AR handovers."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.application.agent_binding import TOOL_SERVER_NAME
from agents_remember.application.context_packet import ContextPacketRequest, build_context_packet
from agents_remember.application.orca_task_context import (
    LEAF_ROLES,
    ROLE_LEVELS,
    TASKLESS_ROLES,
    OrcaRoleContext,
    resolve_orca_role_context,
    selection_binding,
)
from agents_remember.application.role_capsules.launch import compile_launch_capsule
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
from agents_remember.cli.orca_runtime import (
    digest as _digest,
)
from agents_remember.cli.orca_task_receipts import _message_binding_projection_reference
from agents_remember.cli.paseo_catalog import launcher_options, resolve_agent_selection
from agents_remember.controlplane.durable_store import declared_process_role
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import (
    OrcaAgentOverride,
    OrcaLauncherOptionsRequest,
    OrcaRole,
    OrcaSelection,
)
from agents_remember.models.role_capsules.vocabulary import CapsuleOperation
from agents_remember.models.task_document_ref import TaskScopedReaderContext
from agents_remember.serving.launch_capsule import LaunchCapsuleRequest
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path, slugify

ROLE_START_OPERATIONS: dict[OrcaRole, CapsuleOperation] = {
    "architect": "planning",
    "system-specialist": "orientation",
    "orchestrator": "coordination",
    "manager": "coordination",
    "worker": "implementation",
    "reviewer": "review",
    "curator": "curation",
}


def role_start_operation(role: OrcaRole) -> CapsuleOperation:
    """Select the one existing operation appropriate to a manually selected role."""

    return ROLE_START_OPERATIONS[role]


@dataclass(frozen=True, slots=True)
class OrcaHandoverRequest:
    config: McpRuntimeConfig
    context: OrcaRoleContext
    workspace: dict[str, str]
    agent_id: str
    ar_mcp_context: dict[str, Any]
    request_id: uuid.UUID | None = None
    entry_mode: Literal["manual-dashboard-role-start", "native-orca-task"] = (
        "manual-dashboard-role-start"
    )


@dataclass(frozen=True, slots=True)
class PreparedOrcaRoleHandover:
    """The validated inputs shared by the dashboard launcher and native role preparation."""

    context: OrcaRoleContext
    workspace: dict[str, str]
    agent_id: str
    session_options: dict[str, str]
    agent_arg_tokens: tuple[str, ...]
    ar_mcp_context: dict[str, Any]
    handover: dict[str, Any]
    request_id: uuid.UUID


def prepare_orca_role_handover(
    config: McpRuntimeConfig,
    context: OrcaRoleContext,
    *,
    agent_override: OrcaAgentOverride | None,
    request_id: uuid.UUID,
    entry_mode: Literal["manual-dashboard-role-start", "native-orca-task"] = (
        "manual-dashboard-role-start"
    ),
) -> PreparedOrcaRoleHandover:
    """Prepare the existing canonical role handover and exact native launch inputs."""

    # The selection is validated against the cached catalog before anything is created for it.
    defaults, harness_order = _role_defaults(config, context)
    agent_id, session_options, agent_arg_tokens = _resolve_agent_selection(
        config, defaults, harness_order, agent_override
    )
    workspace = _resolve_workspace(config, context)
    if context.role in LEAF_ROLES:
        # Creating the enclosure records it in the leaf's task document. The handover describes
        # the documents as they stand now, so the same request compiles to the same binding again.
        context = _current_role_context(config, context)
    ar_mcp_context = _ar_mcp_context(config, context, workspace)
    handover = _compile_handover(
        OrcaHandoverRequest(
            config=config,
            context=context,
            workspace=workspace,
            agent_id=agent_id,
            ar_mcp_context=ar_mcp_context,
            request_id=request_id,
            entry_mode=entry_mode,
        )
    )
    return PreparedOrcaRoleHandover(
        context=context,
        workspace=workspace,
        agent_id=agent_id,
        session_options=session_options,
        agent_arg_tokens=agent_arg_tokens,
        ar_mcp_context=ar_mcp_context,
        handover=handover,
        request_id=request_id,
    )


def _current_role_context(config: McpRuntimeConfig, context: OrcaRoleContext) -> OrcaRoleContext:
    """Resolve the selection's task documents again, as they are on disk at this moment."""

    return resolve_orca_role_context(
        config,
        OrcaSelection(
            role=context.role,
            sprintDocumentRef=context.sprint.ref if context.sprint else None,
            masterDocumentRef=context.master.ref if context.master else None,
            taskDocumentRef=context.task.ref if context.task else None,
        ),
    )


def _launcher_catalog(
    config: McpRuntimeConfig,
    context: OrcaRoleContext,
    request: OrcaLauncherOptionsRequest,
) -> dict[str, Any]:
    """Role defaults plus the Paseo runtime's cached catalog; only an explicit refresh rediscovers."""

    defaults, harness_order = _role_defaults(config, context)
    return launcher_options(config, defaults, harness_order, refresh=request.refresh_catalog)


def _role_defaults(
    config: McpRuntimeConfig, context: OrcaRoleContext
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


def _resolve_agent_selection(
    config: McpRuntimeConfig,
    defaults: dict[str, str | None],
    harness_order: tuple[str, ...],
    override: OrcaAgentOverride | None,
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    """Validate the requested agent, model and effort against the Paseo runtime's catalog.

    Paseo takes the model and effort as data when the agent is created, so no harness argument
    tokens are derived any more; the empty tuple keeps the prepared-handover shape.
    """

    agent_id, options = resolve_agent_selection(config, defaults, harness_order, override)
    return agent_id, options, ()


def _ar_mcp_context(
    config: McpRuntimeConfig,
    context: OrcaRoleContext,
    workspace: dict[str, str],
) -> dict[str, Any]:
    """Declare the exact arguments native roles pass to the existing shared AR MCP tools."""

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
                "If either installed AR MCP tool schema lacks task_context with both "
                "task_document_ref and contract_path, stop and report the missing "
                "ar-task-scoped-readers/v1 capability. Do not call a task reader without "
                "task_context or substitute caller-selected roots."
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
            "If a registered AR MCP reader schema is unavailable, report that installation issue; "
            "do not invent a repository id or pass caller-selected roots."
        ),
    }


def _verify_leaf_revival_scope(
    config: McpRuntimeConfig,
    context: OrcaRoleContext,
    receipt: dict[str, Any],
) -> None:
    workspace = _resolve_workspace(config, context)
    expected = _ar_mcp_context(config, context, workspace)
    if receipt.get("arMcpContext") != expected:
        raise ValueError(
            "The saved leaf execution does not carry the current canonical AR MCP task reader "
            "context; prepare a new native handover before revive."
        )


def _resolve_workspace(config: McpRuntimeConfig, context: OrcaRoleContext) -> dict[str, str]:
    """The folder the role class is entitled to: Projects, or the leaf's enclosure group folder.

    Only the folder is resolved here. The Paseo workspace of that folder is obtained from the
    runtime when the launch call runs; no workspace id is kept.
    """

    if context.role not in LEAF_ROLES:
        return _workspace_folder(config.workspace_root)
    assert context.task is not None and context.sprint is not None
    contract_path, status = _ensure_leaf_enclosure(
        config,
        context.task,
        parent_task=context.sprint.path.parent.name,
    )
    group = _require_directory(status, "worktree_group")
    code = _require_directory(status, "code_worktree")
    memory = _require_directory(status, "memory_worktree")
    workspace = _workspace_folder(group)
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


def _bind_task_report_access(workspace: Path, task_reports: Path) -> Path:
    """Expose only this task's canonical reports from inside its Orca workspace."""
    link = workspace / "task-reports"
    target = task_reports.resolve()
    if link.is_symlink():
        if link.resolve() != target:
            raise ValueError(
                "The enclosure task-report link points outside the selected task's report folder."
            )
        return link
    if link.exists():
        raise ValueError(
            "The enclosure task-report path is occupied by a non-link; refusing to replace it."
        )
    link.symlink_to(target, target_is_directory=True)
    return link


def _ensure_leaf_enclosure(
    config: McpRuntimeConfig,
    leaf: ResolvedTaskDocument,
    *,
    parent_task: str,
) -> tuple[Path, dict[str, Any]]:
    if leaf.document.kind != "subTask":
        raise ValueError("Only a canonical leaf can open a leaf enclosure.")
    task_root = leaf.path.parent
    expected = leaf_enclosure_path(task_root, leaf.document.id).resolve()
    if leaf.document.enclosures:
        enclosure = leaf.document.enclosures[0]
        if len(leaf.document.enclosures) != 1 or enclosure.leafId != leaf.document.id:
            raise ValueError("The selected leaf has conflicting enclosure bindings.")
        contract_path = Path(enclosure.enclosurePath).resolve()
        if contract_path != expected:
            raise ValueError(
                "The selected leaf enclosure does not match its canonical task binding."
            )
    else:
        contract_path = expected

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


def _workspace_folder(path: Path) -> dict[str, str]:
    # The runtime keys a workspace by the path text, so the folder is always its resolved path.
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return {"path": root.as_posix()}


def _compile_handover(
    request: OrcaHandoverRequest,
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
        raise ValueError("The selected Orca agent has no supported AR instruction carrier.")
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
        raise ValueError("An Orca message binding requires its durable requestId.")
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
    handover = {
        "schema": "ar-orca-role-handover/v1",
        "requestId": str(request_id),
        "role": context.role,
        "operation": operation,
        "assignment": _role_assignment(context, report_path),
        "orcaAgent": agent_id,
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
        "nativeOrca": {
            "entryMode": request.entry_mode,
            "instructionSource": {
                "kind": "compiled-role-operation-capsule",
                "role": capsule.codex_delivery.binding.role,
                "operation": capsule.codex_delivery.binding.operation,
                "semanticDigest": capsule.codex_delivery.semantic_digest,
                "ambientRoleFilesSelected": False,
            },
            "ownerRelation": _native_owner_relation(request.entry_mode),
            "identitySource": (
                "Orca supplies sender identity in ORCA_TERMINAL_HANDLE and ORCA_PANE_KEY; never infer an AR session identity."
            ),
            "guidesOnDemand": [
                "orca skills get orca-cli",
                "orca skills get orchestration",
            ],
            "arToolServer": TOOL_SERVER_NAME,
            "arMcpUsage": (
                f"Use the tool server named {TOOL_SERVER_NAME} for every Agents Remember tool: "
                "the AR build that launched this session started it for this agent. An AR tool "
                "server under any other name belongs to another AR installation; do not use it "
                "for this assignment. After a start or a resume a tool server can take some "
                f"seconds to appear: if a call to {TOOL_SERVER_NAME} is not available, make the "
                "call once more before reporting the server missing, and report that instead of "
                "substituting another. For a leaf, pass the "
                "exact arMcpContext.readerArguments; add the requested files list to "
                "read_ar_files. If either "
                "installed tool schema lacks the declared task_context fields, stop and report the "
                "missing AR reader capability; do not drop task_context or substitute another root."
            ),
            "messageSemantics": (
                "For a cross-workspace message, address the exact native recipient supplied by the active Orca preamble. "
                "If absent, use the assignment's explicitly named recipient workspace; resolve its exact native workspace selector "
                "through Orca's native workspace/project listing, then use `orca terminal list --worktree <exact-selector>` and "
                "`orca orchestration run-list`/`run-show` there. Never infer a parent from directory ancestry or choose a "
                "first/latest/ancestral session. If no recipient workspace is named, ask the active native user. "
                "Prefer `run:<id>` or `dispatch:<id>`; a bare terminal handle is non-durable and may carry a native warning. "
                "If discovery is ambiguous, ask the active native user before sending. Load the immutable JSON at "
                "messageBinding.projection.path, verify its raw-byte SHA-256 against messageBinding.projection.sha256, "
                "parse it, and structurally compare it to this handover's messageBinding.arBinding. For an incoming peer binding, "
                "compare it structurally only to that sender's expected binding in the active native Task/Dispatch spec or an "
                "explicitly selected peer record; never compare it to this session's own binding. If no peer binding is established, "
                "mark it unverified and keep communication usable without claiming equality. Do not compare by visual inspection or "
                "retype opaque values. For ordinary "
                "`send`, serialize it as JSON text for `--payload`; this flag is mutually exclusive with "
                "typed lifecycle payload flags. Use native `ask` for coordinator questions only inside an active supervised "
                "Dispatch; ask has only a question string, so include the serialized binding in `--question` and resume the same "
                "question by its message ID after timeout. In a manual session without a Dispatch, use ordinary native `send` "
                "with `--type question`, an explicit `run:<id>` or `dispatch:<id>` recipient, the question in its string body, "
                "and the serialized binding in `--payload`; then use native check to read the reply and reply to that exact message. "
                "Do not invent a Dispatch or sender identity. `reply` has only a body string, so include the serialized binding "
                "in `--body` when needed; neither ask nor reply accepts a custom payload flag. In a supervised native Run, put "
                "the explicit assignment and projection path/digest "
                "in the string spec passed to Orca `task-create`; then follow the injected Task/Dispatch preamble and typed flags "
                "for heartbeat/worker_done, never attach "
                "a raw `--payload` to those structured lifecycle sends. Include native Run/Task/Dispatch IDs only when Orca "
                "supplied or confirmed them, omitting absent IDs. A send receipt/message ID means queued, not read: use native "
                "check, reply to the exact message/thread, and acknowledge only after processing. On uncertain send outcome, retry "
                "the same native request with identical arguments and payload using Orca's `--retry-request <original-request-uuid>`; "
                "do not create a fresh request or claim delivery/read until native evidence confirms it. Surface stale, ambiguous, "
                "or unavailable recipients as such. Save native `--json` stdout byte-for-byte from the command directly to the "
                "supplied evidence path; a model-authored summary is a report, never a native receipt. Only an active Dispatch "
                "worker may emit worker_done; a plain manual session does not."
            ),
            "messageBinding": {
                "payloadType": "The compact AR binding is a canonical JSON object; each native verb uses its own documented string field.",
                "arBinding": ar_binding,
                "projection": message_binding_projection,
                "nativeIdsRule": "Add native Run/Task/Dispatch IDs only when the active Orca preamble or an exact native read supplies their values; omit absent IDs.",
            },
        },
        "ownerHandover": (
            _native_owner_handover(request.entry_mode)
            + " Older ambient AR lifecycle/router/role files and coordination-level "
            "role-routing prose were not selected by this launcher; do not load them as a second role, "
            "operation, hierarchy, parent, or transport. "
            "Keep higher-priority native instructions and applicable repository coding, tool, and safety "
            "rules. AR owns task requirements, review and curation decisions, task lifecycle, and paired "
            "Git acceptance. This native Orca session performs only the assignment above. For leaf "
            "context_packet/read_ar_files calls, use the exact arMcpContext.readerArguments; task scope "
            "is per call and includes no caller-selected roots. If the existing AR MCP schema does not "
            "expose task_context with both required fields, stop and report that capability mismatch. "
            "Resolve each task document by calling task_doc "
            "with the row's taskDocReadArgs exactly, then read its canonical JSON at the returned "
            "docPath before acting. Do not add .json to the slug. Each contentDigest records the "
            "document snapshot used for this launch. Never claim AR review, curation, lifecycle, or "
            "Git acceptance from native session completion."
        ),
    }
    brief_header = (
        "PREPARED NATIVE AR ROLE BRIEF: this native session starts idle. After Orca injects its exact "
        "Task and Dispatch preamble, load and verify the prepared handover artifact named by the native "
        "Task spec and follow its compiled role/operation prompt. "
        if request.entry_mode == "native-orca-task"
        else "EXPERIMENTAL MANUAL AR ROLE BRIEF: the launcher explicitly selected the role and operation "
        "identified below. "
    )
    prompt = (
        brief_header + "The following compiled capsule and canonical handover are the complete AR "
        "role/operation instructions and assignment for this session. Do not reopen ambient AR role, "
        "lifecycle, or coordination-level role-routing text to infer a different assignment. Preserve native system/developer instructions, "
        "approvals, sandbox policy, and repository-specific coding/tool rules.\n\n"
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


def _native_owner_relation(
    entry_mode: Literal["manual-dashboard-role-start", "native-orca-task"],
) -> str:
    if entry_mode == "native-orca-task":
        return (
            "The active Orca Task and Dispatch own this execution. The selected AR sprint/master/leaf "
            "remains semantic work scope, not a native Run/Task identity. Use only exact native identity "
            "injected or returned by Orca."
        )
    return (
        "The active native user conversation owns decisions for this manual launch. The selected AR "
        "sprint/master/leaf is work scope, not a native Orca parent or Run identity. If the live Orca "
        "preamble supplies an active Run or Dispatch, follow those exact native references."
    )


def _native_owner_handover(
    entry_mode: Literal["manual-dashboard-role-start", "native-orca-task"],
) -> str:
    if entry_mode == "native-orca-task":
        return (
            "This exact compiled role and operation plus this handover are the native role instructions "
            "for the assignment. This terminal starts idle; do not begin until Orca injects its native "
            "Task and Dispatch."
        )
    return "This exact compiled role and operation plus this handover are the manual native role brief for this request."


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


def _role_assignment(context: OrcaRoleContext, report_path: str) -> str:
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
    context: OrcaRoleContext,
    workspace: dict[str, str],
    *,
    request_id: uuid.UUID | None = None,
) -> str:
    if context.role in TASKLESS_ROLES:
        if request_id is None:
            raise ValueError("A taskless Orca report requires its durable requestId.")
        report_root = (
            Path(workspace["path"]) / ".agents-remember" / "reports" / "orca-native" / context.role
        )
        report_name = f"{request_id}.md"
    else:
        selected = context.effective_task
        if selected is None:
            raise ValueError("A task-bound Orca report requires its canonical task document.")
        if request_id is None:
            raise ValueError("A task-bound Orca report requires its durable requestId.")

        report_root = (selected.path.parent / "notes" / "reports").resolve(strict=False)
        if context.role in LEAF_ROLES:
            if context.task is None:
                raise ValueError("A leaf Orca report requires its canonical task document.")
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
        report_root = report_root / "orca-native"
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
