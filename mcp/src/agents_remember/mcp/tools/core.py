"""Server, context, install, and skills payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.application.agent_binding import (
    TOOL_SERVER_NAME,
    read_agent_binding,
)
from agents_remember.application.context_packet import (
    ContextPacketRequest,
    build_context_packet,
    slim_context_packet,
)
from agents_remember.application.runtime.install import (
    RuntimeInstallRequest,
    run_runtime_install,
)
from agents_remember.application.task_scoped_mcp import (
    task_scoped_mcp_config_for_reader,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.primitives.tool_reports import write_tool_report
from agents_remember.models.core import ServingBuildPayload
from agents_remember.models.task_document_ref import TaskScopedReaderContext
from agents_remember.models.tools.tool_answer import (
    RUNTIME_INSTALL_ANSWER_FIELDS,
    TOOL_ANSWER_SHORT_STRING_MAX,
)

from .. import SERVER_NAME, SERVER_VERSION
from .base import TRANSPORT, _tool_payload


def server_info_payload(
    config: McpRuntimeConfig, serving_build: ServingBuildPayload
) -> dict[str, Any]:
    # A server that a role launch started for one agent says which agent and which AR work, and
    # the name under which that agent was given it: ``server`` is the package's own name, which
    # the agent's instructions use for another installation's server.
    binding = read_agent_binding()
    return _tool_payload(
        "server_info",
        {
            "ok": True,
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "transport": TRANSPORT,
            "configPath": config.config_path.as_posix(),
            "coordinationRoot": config.coordination_root.as_posix(),
            "workspaceRoot": config.workspace_root.as_posix(),
            "transcriptRoot": config.transcript_root.as_posix(),
            "harnessSkillRoot": (
                config.harness_skill_root.as_posix() if config.harness_skill_root else None
            ),
            "allowedRepoIds": list(config.allowed_repo_ids),
            "unusedProviderKeys": list(config.allowed_provider_ids),
            "servingBuild": serving_build.model_dump(mode="json", exclude_none=True),
            **(
                {"toolServer": TOOL_SERVER_NAME, "agentBinding": binding.as_report()}
                if binding is not None
                else {}
            ),
        },
    )


def context_packet_payload(
    config: McpRuntimeConfig,
    request: ContextPacketRequest,
    *,
    task_context: TaskScopedReaderContext | None = None,
    serving_build_commit: str | None = None,
) -> dict[str, Any]:
    """The agent's slim answer from one whole context packet (MIK-R109 rule 4).

    What leaves the server is the roots, the task, the action lines and
    the short values. The framework's log preserves the delivered answer
    behind it; the whole packet is rebuilt on demand through
    :func:`build_context_packet` (the dashboard and command line route),
    which carries every omitted policy fact.
    """

    scoped_config = task_scoped_mcp_config_for_reader(
        config,
        repository_id=request.repo_id,
        task_context=task_context,
    )
    full = build_context_packet(scoped_config, request)
    return _tool_payload(
        "context_packet",
        slim_context_packet(full, serving_build_commit=serving_build_commit),
    )


def runtime_install_payload(
    config: McpRuntimeConfig,
    request: RuntimeInstallRequest,
) -> dict[str, Any]:
    """Build the runtime-install payload for one run from that run's own request.

    The tool builds the request and hands it over unchanged, so the knobs the tool exposes and the
    fields the application entry point reads cannot drift apart. ``request.experiment`` is this
    run's own selection and the primary input for it; the ``AR_EXPERIMENT`` environment variable of
    the server process is the documented fallback for a short-lived CLI/developer run. The install
    payload names which one supplied the mode in its record's ``selectionSource``, so a long-lived
    server cannot leave an ambient switch behind unnoticed.
    """

    full = run_runtime_install(config, request)
    # A dry run is a preview and files nothing (rule 6); only an executed install files
    # its report, and only then does the answer carry the path.
    report_path = (
        None
        if request.dry_run
        else write_tool_report(
            config.coordination_root,
            "runtime_install",
            full,
            label="install",
        ).as_posix()
    )
    return _tool_payload(
        "runtime_install",
        compact_runtime_install_payload(full, report_path),
    )


def compact_runtime_install_payload(
    full: dict[str, Any], report_path: str | None
) -> dict[str, Any]:
    """Project the whole install result onto the row's final answer fields.

    Only the row's fields ride (``operation`` and any undeclared field stay
    in the whole result for the report and the log); messages keep the first
    five entries cut to the short-string bound each, with the rest counted
    into the report; the rebind digest keeps attempted/ok and
    phase/action/ok per run. ``log`` is attached by the response boundary.
    """
    compact = {key: value for key, value in full.items() if key in RUNTIME_INSTALL_ANSWER_FIELDS}
    messages = full.get("messages")
    if "messages" in full and isinstance(messages, list):
        kept = [message for message in messages[:5] if isinstance(message, str)]
        cut = [
            message
            if len(message) <= TOOL_ANSWER_SHORT_STRING_MAX
            else message[:TOOL_ANSWER_SHORT_STRING_MAX] + "…"
            for message in kept
        ]
        if len(messages) > 5:
            cut.append(f"... {len(messages) - 5} more in the report")
        compact["messages"] = cut
    rebind = full.get("providerWatcherRebind")
    if isinstance(rebind, dict):
        compact["providerWatcherRebind"] = {
            "attempted": rebind.get("attempted"),
            "ok": rebind.get("ok"),
            "phases": [
                {
                    "phase": run.get("phase"),
                    "action": run.get("action"),
                    "ok": run.get("ok"),
                }
                for run in rebind.get("runs", [])
                if isinstance(run, dict)
            ],
        }
    if report_path is not None:
        compact["reportPath"] = report_path
    return compact
