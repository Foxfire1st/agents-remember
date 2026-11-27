"""Task-document tools plus the two task-state transitions: finalize and reopen."""

from importlib import import_module
from typing import Any

from mcp.server.mcpserver import MCPServer

from agents_remember.application.worktree_tool_requests import (
    FinalizeTaskDocs,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherenceRequest
from agents_remember.models.queue.closeout_queue import CloseoutQueueRequest

from .lazy import tools


def _register_closeout_queue_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    @server.tool()
    def closeout_queue(request: CloseoutQueueRequest) -> dict[str, Any]:
        """Inspect or rebuild a sprint's waiting-only closeout projection. States: valid-built, invalid-empty, blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.closeout_queue_payload(config, request)


def _register_curator_coherence_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    @server.tool()
    def curator_coherence(request: CuratorCoherenceRequest) -> dict[str, Any]:
        """Prepare, publish, inspect, or validate one leaf's curator-coherence authority. Publish requires semantic_requirement_revision, delivery_attempt, expected_predecessor_digest, expected_code_candidate_tree, expected_memory_candidate_tree, expected_task_topology_fingerprint, expected_task_intent, expected_attestation_sha256, caller, and judgments or judgments_file. Docs: docs/tool-details.md."""
        return tools.curator_coherence_payload(config, request)


def _register_task_reopen_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    @server.tool()
    def task_reopen(contract_path: str, dry_run: bool = False) -> dict[str, Any]:
        """Reopen a Completed leaf under the same id, resetting review/closeout/integration state to planning. Cleaned-up leaves restart from the master tip if their recorded landing is its ancestor, else refuse. States: reopened, blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.task_reopen_payload(config, contract_path, dry_run=dry_run)


def _register_task_finalizer_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    @server.tool()
    def lifecycle_finalize_task(
        contract_path: str,
        task_doc_path: str | None = None,
        master_doc_path: str | None = None,
        subtask_number: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Prove the landed edge, complete a task once all steps are done, and reconcile its parent row (ancestors are not completed; leaves require a master). States: finalized, would-finalize, blocked. Docs: docs/reference/mcp-tools.md."""
        return tools.lifecycle_finalize_task_payload(
            config,
            contract_path,
            docs=FinalizeTaskDocs(
                task_doc_path=task_doc_path,
                master_doc_path=master_doc_path,
                subtask_number=subtask_number,
            ),
            dry_run=dry_run,
        )


_TASK_DOC_TOOL_DESCRIPTION = """Write task docs (read .md as file). Kind: subTask or master. Ops: create, replace, set_status, set_field, set_step, add_step, remove_step, skip_step, set_subtask, remove_subtask, set_section, append_decision, put_open_question, begin_review, record_review, record_route_review, author_execution_graph, attach_master, detach_master, retire_master, archive_sprint, linkage_report. Packet writes add alignmentSweep. Docs: docs/reference/mcp-tools.md."""


def _register_task_document_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    @server.tool(description=_TASK_DOC_TOOL_DESCRIPTION)
    def task_doc(
        repo_id: str,
        operation: str,
        task_name: str | None = None,
        contract_path: str | None = None,
        slug: str | None = None,
        *,
        fields: dict[str, Any] | None = None,
        step: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        subtask: dict[str, Any] | None = None,
        section: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
        dry_run: bool = False,
        branch_addressed: bool = False,
        fields_file: str | None = None,
        section_body_file: str | None = None,
    ) -> dict[str, Any]:
        return tools.task_doc_payload(
            config,
            import_module("agents_remember.application.task_docs.task_doc_tools").TaskDocTarget(
                repo_id=repo_id,
                task_name=task_name,
                contract_path=contract_path,
                slug=slug,
            ),
            operation=operation,
            edit=import_module("agents_remember.application.task_docs.task_doc_tools").TaskDocEdit(
                fields=fields,
                step=step,
                decision=decision,
                subtask=subtask,
                section=section,
                review=review,
                fields_file=fields_file,
                section_body_file=section_body_file,
            ),
            call=import_module("agents_remember.application.task_docs.task_doc_tools").TaskDocCall(
                dry_run=dry_run, branch_addressed=branch_addressed
            ),
        )


def register_task_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    _register_task_reopen_tools(server, config)
    _register_task_finalizer_tools(server, config)
    _register_task_document_tools(server, config)
    _register_curator_coherence_tools(server, config)
    _register_closeout_queue_tools(server, config)
