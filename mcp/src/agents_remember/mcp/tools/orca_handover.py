"""Agent-facing preparation and native start for one canonical AR leaf role."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.cli.orca_runtime import OrcaRuntimeFailure
from agents_remember.cli.orca_task_routes import prepare_idle_native_role_session
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import OrcaDispatchRequest

from .base import _tool_payload


def orca_role_prepare_payload(
    config: McpRuntimeConfig, request: OrcaDispatchRequest
) -> dict[str, Any]:
    _require_native_runtime(config)
    task_document_ref = request.task_document_ref
    if task_document_ref is None:
        raise ValueError("orca_role_prepare requires a canonical task document reference.")
    prepared = prepare_idle_native_role_session(config, request)
    artifact = prepared.handover_artifact
    handover = artifact["handover"]
    workspace = handover["workspace"]
    execution = prepared.execution
    raw_identity = execution.get("execution")
    identity = raw_identity if isinstance(raw_identity, dict) else {}
    handle = identity.get("handle")
    ready = execution.get("status") == "running" and isinstance(handle, str) and bool(handle)
    native_action = None
    if ready:
        native_action = {
            "runObjective": (
                f"Coordinate the {request.role} role for canonical AR task {task_document_ref.key}."
            ),
            "workerStart": {
                "workspaceSelector": workspace["selector"],
                "terminalHandle": handle,
                "spec": _native_task_spec(artifact, prepared.handover_reference),
            },
        }
    status = "idle-session-ready" if ready else execution.get("status", "unknown")
    if status not in {"idle-session-ready", "unknown", "rejected"}:
        status = "unknown"
    return _tool_payload(
        "orca_role_prepare",
        {
            "ok": ready,
            "operation": "orca_role_prepare",
            "status": status,
            "detail": str(
                execution.get("detail")
                or "Orca has not confirmed an idle native role session. Reconcile the saved request before retrying."
            ),
            "requestId": str(prepared.request_id),
            "role": request.role,
            "taskReference": task_document_ref.key,
            "taskDocumentDigest": handover["taskDocumentDigest"],
            "capsuleDigest": handover["capsule"]["semanticDigest"],
            "candidateClass": "working-tree-diff",
            "baselineSourcePath": workspace["contractPath"],
            "handover": prepared.handover_reference,
            "taskReportPath": handover["taskReportPath"],
            "nativeIdentity": {
                key: identity[key]
                for key in ("handle", "sessionId", "worktreeId")
                if isinstance(identity.get(key), str)
            },
            "nativeNextAction": native_action,
            "workStarted": False,
        },
    )


def _require_native_runtime(config: McpRuntimeConfig) -> None:
    if getattr(config, "orca_runtime", None) is None:
        raise OrcaRuntimeFailure(
            "native_runtime_configuration_missing",
            "orca_role_prepare requires orcaRuntime.runtimeRoot and orcaRuntime.userDataPath "
            "in the shared Agents Remember MCP settings. Data-only MCP tools remain available.",
        )


def _native_task_spec(artifact: dict[str, Any], handover_reference: dict[str, str]) -> str:
    handover = artifact["handover"]
    workspace = handover["workspace"]
    task_ref = handover["selection"]["taskDocumentRef"]
    task_key = f"{task_ref['repository']}:{task_ref['path']}"
    primary = next(
        document
        for document in handover["documents"]
        if document.get("taskDocumentRef") == task_ref
    )
    role = handover["role"]
    writable_roots = [str(Path(handover["taskReportPath"]).parent)]
    if role == "worker":
        writable_roots.insert(0, workspace["codeRoot"])
    elif role == "curator":
        writable_roots.insert(0, workspace["memoryRoot"])
    value = {
        "schema": "ar-orca-native-task-spec/v1",
        "role": role,
        "arAssignment": {
            "taskReference": task_key,
            "taskDocumentReadArgs": primary["taskDocReadArgs"],
            "expectedTaskDocumentDigest": primary["contentDigest"],
            "taskDocumentSetDigest": handover["taskDocumentDigest"],
            "capsuleDigest": handover["capsule"]["semanticDigest"],
            "taskReportPath": handover["taskReportPath"],
            "writableRoots": writable_roots,
        },
        "candidate": {
            "class": "working-tree-diff",
            "codeRoot": workspace["codeRoot"],
            "baselineContractPath": workspace["contractPath"],
            "baselineCommitField": "code.base_commit",
        },
        "fixVerificationContext": (
            "For a repair, reuse the same native Worker terminal and put the exact Reviewer finding IDs, "
            "report path, and unchanged candidate baseline in the follow-up native Task spec. Verify each "
            "finding against this same working-tree candidate; do not widen the canonical AR task scope."
        ),
        "handoverArtifact": handover_reference,
        "handoverPromptField": "prompt",
        "nativeReceiptRule": (
            "Save each native Orca --json stdout byte-for-byte in this task's report directory, using exact "
            "returned Run, Task, and Dispatch IDs in filenames. A model-authored summary is not a native receipt."
        ),
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = ["orca_role_prepare_payload"]
