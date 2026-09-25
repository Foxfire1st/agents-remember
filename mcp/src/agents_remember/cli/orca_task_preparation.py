"""Resolve Orca launch defaults, workspaces, and canonical AR handovers."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.context_packet import ContextPacketRequest, build_context_packet
from agents_remember.application.orca_task_context import (
    LEAF_ROLES,
    ROLE_LEVELS,
    TASKLESS_ROLES,
    OrcaRoleContext,
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
from agents_remember.application.worktree_tool_requests import StartExecution, TaskIdentity
from agents_remember.application.worktree_tools import worktree_start_tool, worktree_status_tool
from agents_remember.cli.orca_runtime import OrcaRuntimeFailure, orca_catalog_scope
from agents_remember.cli.orca_runtime import (
    digest as _digest,
)
from agents_remember.cli.orca_runtime import (
    runtime_call as _runtime_call,
)
from agents_remember.cli.orca_scoped_mcp import (
    ScopedNativeMcp,
    prepare_codex_projects_mcp,
    prepare_codex_scoped_mcp,
)
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import (
    OrcaAgentOverride,
    OrcaLauncherOptionsRequest,
    OrcaRole,
)
from agents_remember.models.role_capsules.vocabulary import CapsuleOperation
from agents_remember.serving.launch_capsule import LaunchCapsuleRequest
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path, slugify

_ORCA_CATALOG_CACHE: dict[tuple[str, str, str | None], dict[str, Any]] = {}
_ORCA_CATALOG_ORIGINS: dict[tuple[str, str], str] = {}

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
    native_mcp_scope: ScopedNativeMcp | None = None
    request_id: uuid.UUID | None = None


def _launcher_catalog(
    config: McpRuntimeConfig,
    context: OrcaRoleContext,
    request: OrcaLauncherOptionsRequest,
) -> dict[str, Any]:
    defaults, harness_order = _role_defaults(config, context)
    runtime_key, workspace_key = orca_catalog_scope(config)
    scope = (runtime_key, workspace_key)
    if request.refresh_catalog:
        for key in tuple(_ORCA_CATALOG_CACHE):
            if key[:2] == scope:
                del _ORCA_CATALOG_CACHE[key]
        _ORCA_CATALOG_ORIGINS[scope] = f"orca:{uuid.uuid4().hex}"
    catalog_origin = _ORCA_CATALOG_ORIGINS.setdefault(scope, f"orca:{uuid.uuid4().hex}")
    workspace: dict[str, str] | None = None
    agent_catalog = _ORCA_CATALOG_CACHE.get((*scope, None))
    if agent_catalog is None:
        workspace = _ensure_orca_workspace(config.workspace_root)
        catalog = _runtime_call("catalog", {"workspaceSelector": workspace["selector"]})
        agent_catalog = {"agents": catalog.get("agents", [])}
        _ORCA_CATALOG_CACHE[(*scope, None)] = agent_catalog
    installed = _agent_ids(agent_catalog)
    defaults["agent"] = _configured_or_detected_agent(defaults["agent"], harness_order, installed)
    selected_agent = request.agent_id or defaults["agent"]
    agents = agent_catalog.get("agents", [])
    selected_catalog: dict[str, Any] | None = None
    if selected_agent:
        selected_catalog = _ORCA_CATALOG_CACHE.get((*scope, selected_agent))
        if selected_catalog is None:
            if workspace is None:
                workspace = _ensure_orca_workspace(config.workspace_root)
            result = _runtime_call(
                "catalog",
                {"workspaceSelector": workspace["selector"], "agentId": selected_agent},
            )
            selected = result.get("selected")
            if isinstance(selected, dict):
                selected_catalog = selected
                _ORCA_CATALOG_CACHE[(*scope, selected_agent)] = selected_catalog
    if isinstance(selected_catalog, dict):
        agents = [
            {**agent, **selected_catalog}
            if isinstance(agent, dict) and agent.get("id") == selected_agent
            else agent
            for agent in agents
        ]
    return {
        "roleDefaults": defaults,
        "agents": _public_agent_options(agents),
        "catalogOrigin": catalog_origin,
    }


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


def _agent_ids(catalog: dict[str, Any]) -> set[str]:
    rows = catalog.get("agents", [])
    if not isinstance(rows, list):
        return set()
    return {row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}


def _public_agent_options(agents: Any) -> list[dict[str, Any]]:
    if not isinstance(agents, list):
        return []
    return [
        {
            **agent,
            "models": agent.get("models") if isinstance(agent.get("models"), list) else [],
        }
        for agent in agents
        if isinstance(agent, dict)
    ]


def _configured_or_detected_agent(
    configured: str | None, harness_order: tuple[str, ...], installed: set[str]
) -> str | None:
    if configured:
        return configured
    return next((harness for harness in harness_order if harness in installed), None)


def _resolve_agent_selection(
    selector: str,
    defaults: dict[str, str | None],
    harness_order: tuple[str, ...],
    override: OrcaAgentOverride | None,
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    available = _runtime_call("catalog", {"workspaceSelector": selector})
    installed = _agent_ids(available)
    default_agent = _configured_or_detected_agent(defaults["agent"], harness_order, installed)
    agent_id = override.agent_id if override else default_agent
    if not agent_id:
        raise ValueError(
            "No Orca agent is selected. Configure the AR role harness or choose an installed Orca agent."
        )
    catalog = _runtime_call("catalog", {"workspaceSelector": selector, "agentId": agent_id})
    selected = catalog.get("selected")
    if not isinstance(selected, dict):
        raise ValueError(f"The selected Orca agent {agent_id!r} is not installed on this runtime.")
    models = selected.get("models", []) if isinstance(selected, dict) else []
    same_default_agent = agent_id == default_agent
    model_id = (override.model_id if override and override.model_id else None) or (
        defaults["model"] if same_default_agent else None
    )
    effort_id = (override.effort_id if override and override.effort_id else None) or (
        defaults["effort"] if same_default_agent and model_id == defaults["model"] else None
    )
    if effort_id and not model_id:
        raise ValueError("An effort selection requires a model selection.")
    model = next(
        (item for item in models if isinstance(item, dict) and item.get("id") == model_id), None
    )
    if model_id and selected.get("catalogOrigin") != "probe":
        raise ValueError(
            "Orca has no live model probe for this agent, so its model selection cannot be validated."
        )
    if model_id and model is None:
        raise ValueError(
            f"Orca's live model probe does not advertise {model_id!r} for agent {agent_id!r}."
        )
    if effort_id and not any(
        isinstance(effort, dict) and effort.get("id") == effort_id
        for effort in (model or {}).get("efforts", [])
    ):
        raise ValueError(f"Orca does not advertise effort {effort_id!r} for the selected model.")
    options = {key: value for key, value in (("model", model_id), ("effort", effort_id)) if value}
    agent_args: tuple[str, ...] = ()
    if model_id:
        resolved = _runtime_call("option-launch", {"agentId": agent_id, "sessionOptions": options})
        applied = resolved.get("appliedValues", {})
        if applied.get("model") != model_id or (effort_id and applied.get("effort") != effort_id):
            raise ValueError(
                "Orca's native option resolver cannot apply the selected model or effort."
            )
        args = resolved.get("args", [])
        if isinstance(args, list) and all(isinstance(arg, str) for arg in args) and args:
            agent_args = tuple(args)
    return agent_id, options, agent_args


def _prepare_leaf_mcp_scope(
    config: McpRuntimeConfig,
    context: OrcaRoleContext,
    workspace: dict[str, str],
    agent_id: str,
) -> ScopedNativeMcp:
    if agent_id != "codex":
        raise OrcaRuntimeFailure(
            "task_scoped_mcp_unverified",
            (
                f"Leaf launch for {agent_id!r} is blocked until its native workspace-scoped "
                "AR MCP profile is verified. No Orca session was started."
            ),
        )
    if context.task is None:
        raise ValueError("A leaf-role MCP scope requires the selected canonical task.")
    return prepare_codex_scoped_mcp(
        config,
        task_document_ref=context.task.ref,
        workspace=workspace,
    )


def _prepare_projects_mcp_scope(
    config: McpRuntimeConfig,
    context: OrcaRoleContext,
    workspace: dict[str, str],
    agent_id: str,
) -> ScopedNativeMcp | None:
    if agent_id != "codex":
        return None
    repository_id = context.effective_task.ref.repository if context.effective_task else None
    return prepare_codex_projects_mcp(
        config,
        workspace_root=Path(workspace["path"]),
        repository_id=repository_id,
    )


def _verify_leaf_revival_scope(
    config: McpRuntimeConfig,
    context: OrcaRoleContext,
    receipt: dict[str, Any],
) -> None:
    workspace = _resolve_workspace(config, context)
    agent = receipt.get("agent")
    agent_id = agent.get("id") if isinstance(agent, dict) else None
    if not isinstance(agent_id, str):
        raise ValueError(
            "The saved leaf execution has no agent identity for MCP scope verification."
        )
    verified = _prepare_leaf_mcp_scope(config, context, workspace, agent_id)
    stored = receipt.get("nativeMcpScope")
    required_keys = (
        "taskDocumentRef",
        "contractPath",
        "workspaceRoot",
        "codeRoot",
        "memoryRoot",
        "contextPacketVerified",
        "readArFiles",
    )
    if not isinstance(stored, dict) or any(
        stored.get(key) != verified.verification.get(key) for key in required_keys
    ):
        raise ValueError(
            "The saved leaf session does not carry the verified MCP scope required for safe revive."
        )


def _resolve_workspace(config: McpRuntimeConfig, context: OrcaRoleContext) -> dict[str, str]:
    if context.role not in LEAF_ROLES:
        return _ensure_orca_workspace(config.workspace_root)
    assert context.task is not None and context.sprint is not None
    contract_path, status = _ensure_leaf_enclosure(
        config,
        context.task,
        parent_task=context.sprint.path.parent.name,
    )
    group = _require_directory(status, "worktree_group")
    code = _require_directory(status, "code_worktree")
    memory = _require_directory(status, "memory_worktree")
    workspace = _ensure_orca_workspace(group)
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
        created = worktree_start_tool(
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
            execution=StartExecution(skip_provider_setup=True),
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


def _ensure_orca_workspace(path: Path) -> dict[str, str]:
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspaces = _runtime_call("workspaces", {})
    matches = _matching_workspace(workspaces, root)
    if not matches:
        _runtime_call("add-folder", {"path": root.as_posix(), "displayName": root.name})
        workspaces = _runtime_call("workspaces", {})
        matches = _matching_workspace(workspaces, root)
    if len(matches) != 1:
        raise ValueError(f"Orca must expose exactly one registered workspace for {root}.")
    workspace_id = matches[0]["id"]
    return {
        "id": workspace_id,
        "selector": f"id:{workspace_id}",
        "path": root.as_posix(),
    }


def _matching_workspace(payload: dict[str, Any], target: Path) -> list[dict[str, str]]:
    rows = payload.get("worktrees", [])
    if not isinstance(rows, list):
        return []
    matches: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        path = row.get("path")
        if not isinstance(path, str):
            continue
        try:
            if Path(path).resolve() == target:
                matches.append({"id": row["id"]})
        except OSError:
            continue
    return matches


def _compile_handover(
    request: OrcaHandoverRequest,
) -> dict[str, Any]:
    config = request.config
    context = request.context
    workspace = request.workspace
    agent_id = request.agent_id
    native_mcp_scope = request.native_mcp_scope
    request_id = request.request_id
    operation = role_start_operation(context.role)
    task_ref = context.effective_task.ref if context.effective_task else None
    capsule_request = LaunchCapsuleRequest(
        role=context.role,
        workspace_root=Path(workspace["path"]),
        task_document_ref=task_ref,
        allow_project_task_binding=(
            context.role in {"orchestrator", "manager"}
            and Path(workspace["path"]).resolve() == config.workspace_root.resolve()
        ),
    )
    capsule = compile_launch_capsule(config, capsule_request, operation=operation)
    if capsule.is_refusal:
        raise ValueError(capsule.explain())
    if capsule.codex_delivery is None:
        raise ValueError("The selected Orca agent has no supported AR instruction carrier.")
    documents = [doc for doc in (context.sprint, context.master, context.task) if doc]
    task_reads = [_read_task_doc(config, document) for document in documents]
    primary = context.effective_task
    context_packet = None
    if native_mcp_scope is not None:
        context_packet = native_mcp_scope.context_packet
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
    handover = {
        "schema": "ar-orca-role-handover/v1",
        "role": context.role,
        "operation": operation,
        "assignment": _role_assignment(context, report_path),
        "orcaAgent": agent_id,
        "selection": selection_binding(context),
        "documents": task_reads,
        "workspace": workspace,
        "repositoryContext": repository_context,
        "taskReportPath": report_path,
        "canonicalTaskReportPath": Path(report_path).resolve(strict=False).as_posix(),
        "contextPacket": context_packet,
        "nativeMcpScope": native_mcp_scope.verification if native_mcp_scope else None,
        "capsule": {
            "role": capsule.role,
            "operation": capsule.codex_delivery.binding.operation,
            "semanticDigest": capsule.codex_delivery.semantic_digest,
            "binding": capsule.codex_delivery.binding.as_report(),
        },
        "nativeOrca": {
            "entryMode": "manual-dashboard-role-start",
            "instructionSource": {
                "kind": "compiled-role-operation-capsule",
                "role": capsule.codex_delivery.binding.role,
                "operation": capsule.codex_delivery.binding.operation,
                "semanticDigest": capsule.codex_delivery.semantic_digest,
                "ambientRoleFilesSelected": False,
            },
            "ownerRelation": (
                "The active native user conversation owns decisions for this manual launch. The selected AR "
                "sprint/master/leaf is work scope, not a native Orca parent or Run identity. If the live Orca "
                "preamble supplies an active Run or Dispatch, follow those exact native references."
            ),
            "identitySource": (
                "Orca supplies sender identity in ORCA_TERMINAL_HANDLE and ORCA_PANE_KEY; never infer an AR session identity."
            ),
            "guidesOnDemand": [
                "orca skills get orca-cli",
                "orca skills get orchestration",
            ],
            "messageSemantics": (
                "Native send enqueues; use check to read; reply addresses the read message. Follow the installed skill for acknowledgement. "
                "Only an active Dispatch worker may emit worker_done; a plain manual session does not."
            ),
        },
        "ownerHandover": (
            "This exact compiled role and operation plus this handover are the manual native role brief "
            "for this request. Older ambient AR lifecycle/router/role files and coordination-level "
            "role-routing prose were not selected by this launcher; do not load them as a second role, "
            "operation, hierarchy, parent, or transport. "
            "Keep higher-priority native instructions and applicable repository coding, tool, and safety "
            "rules. AR owns task requirements, review and curation decisions, task lifecycle, and paired "
            "Git acceptance. This native Orca session performs only the assignment above. Resolve each task document by calling task_doc "
            "with the row's taskDocReadArgs exactly, then read its canonical JSON at the returned "
            "docPath before acting. Do not add .json to the slug. Each contentDigest records the "
            "document snapshot used for this launch. Never claim AR review, curation, lifecycle, or "
            "Git acceptance from native session completion."
        ),
    }
    prompt = (
        "EXPERIMENTAL MANUAL AR ROLE BRIEF: the launcher explicitly selected the role and operation "
        "identified below. The following compiled capsule and canonical handover are the complete AR "
        "role/operation instructions and assignment for this session. Do not reopen ambient AR role, "
        "lifecycle, or coordination-level role-routing text to infer a different assignment. Preserve native system/developer instructions, "
        "approvals, sandbox policy, and repository-specific coding/tool rules.\n\n"
        + capsule.codex_delivery.trusted_instructions
        + "\n\nAR owner assignment and canonical task handover:\n"
        + json.dumps(handover, ensure_ascii=False, separators=(",", ":"))
    )
    return {
        "prompt": prompt,
        "capsuleOperation": operation,
        "capsuleDigest": capsule.codex_delivery.semantic_digest,
        "taskDocumentDigest": _digest(task_reads),
        "taskReportPath": report_path,
        "canonicalTaskReportPath": Path(report_path).resolve(strict=False).as_posix(),
        **(
            {"nativeMcpScope": native_mcp_scope.verification}
            if native_mcp_scope is not None
            else {}
        ),
    }


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
