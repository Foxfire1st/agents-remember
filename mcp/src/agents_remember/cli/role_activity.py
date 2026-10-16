"""Publish role identity before the host can emit that role's first event."""

from __future__ import annotations

from typing import Any

from agents_remember.cli.paseo_status import ResumeOutcome, resume_agent
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.serving.paseo.paseo_plugin_files import publish_role_agent


def publish_role_agent_for_plugin(
    config: McpRuntimeConfig,
    request: RoleSelection,
    agent_id: str,
    report_path: str | None,
) -> bool:
    """Publish the started agent's facts for the rule-11 plugin (MIK-R117).

    Best-effort: without a configured runtime home, without a report path,
    or on any filesystem trouble the start still succeeds and the agent is
    simply silent to the plugin. A repeat start republishes the same record.
    """
    if not report_path:
        return False
    try:
        runtime = config.paseo_runtime
    except Exception:
        return False
    if runtime is None:
        return False
    reference = (
        request.task_document_ref or request.master_document_ref or request.sprint_document_ref
    )
    try:
        return publish_role_agent(
            runtime.home,
            agent_id,
            {
                "role": request.role,
                "task": reference.key if reference is not None else "Projects",
                "reportPath": report_path,
            },
        )
    except Exception:
        return False


def publish_receipt_role_agent(config: McpRuntimeConfig, receipt: dict[str, Any]) -> bool:
    selection = RoleSelection.model_validate(receipt["selection"])
    report = receipt.get("report")
    path = report.get("path") if isinstance(report, dict) else None
    agent_id = receipt.get("agentId")
    if not isinstance(agent_id, str) or not isinstance(path, str):
        return False
    return publish_role_agent_for_plugin(config, selection, agent_id, path)


def resume_role_agent(config: McpRuntimeConfig, receipt: dict[str, Any]) -> ResumeOutcome:
    """Restore a role's identity before opening its host session again."""
    publish_receipt_role_agent(config, receipt)
    return resume_agent(config, receipt["agentId"])
