"""Task-document tools plus the two task-state transitions: finalize and reopen."""

from importlib import import_module
from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.worktree_tool_requests import (
    FinalizeTaskDocs,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherenceRequest
from agents_remember.models.queue.closeout_queue import CloseoutQueueRequest

from .lazy import tools


def _register_closeout_queue_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def closeout_queue(request: CloseoutQueueRequest) -> dict[str, Any]:
        """Inspect or rebuild one sprint's disposable waiting-only closeout projection: exact-
        current valid-built or non-admitting invalid-empty, derived from the current task plus
        waiting-door facts. States: valid-built, invalid-empty, blocked. Reference: docs/reference/mcp-tools.md."""
        return tools.closeout_queue_payload(config, request)


def _register_curator_coherence_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def curator_coherence(request: CuratorCoherenceRequest) -> dict[str, Any]:
        """Prepare, publish, inspect or validate one leaf's curator-coherence authority. Publish requires semantic_requirement_revision, delivery_attempt, expected_predecessor_digest, expected_code_candidate_tree, expected_memory_candidate_tree, expected_task_topology_fingerprint, expected_task_intent, expected_attestation_sha256 and caller; supply judgments or judgments_file. The stable manifest is authoritative, never Markdown. Longer explanation: docs/tool-details.md."""
        return tools.curator_coherence_payload(config, request)


def _register_task_reopen_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def task_reopen(contract_path: str, dry_run: bool = False) -> dict[str, Any]:
        """Reopen a COMPLETED leaf under its exact same leaf id: review, closeout and integration
        state returns to virgin and the leaf document goes back to planning. A cleaned-up leaf
        behind its master's tip restarts from that tip when its recorded landing is an ancestor
        of it; a landing not on the line refuses. Afterwards edit
        steps via task_doc, then run a normal worktree_start. States: reopened, blocked;
        preview with dry_run. Reference: docs/reference/mcp-tools.md."""
        return tools.task_reopen_payload(config, contract_path, dry_run=dry_run)


def _register_task_finalizer_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def lifecycle_finalize_task(
        contract_path: str,
        task_doc_path: str | None = None,
        master_doc_path: str | None = None,
        subtask_number: str = "",
        dry_run: bool = False,
        *,
        teardown_providers: bool = True,
    ) -> dict[str, Any]:
        """Prove the landed edge and complete one parent-child task lifecycle edge: refuses before
        cleanup unless every declared step is done, then reconciles the exact parent row.
        Leaves require a master; ancestors are not completed. States: finalized,
        would-finalize, blocked. Answers the state, what blocks
        and the next call; preview with dry_run. Reference: docs/reference/mcp-tools.md."""
        return tools.lifecycle_finalize_task_payload(
            config,
            contract_path,
            docs=FinalizeTaskDocs(
                task_doc_path=task_doc_path,
                master_doc_path=master_doc_path,
                subtask_number=subtask_number,
            ),
            dry_run=dry_run,
            teardown_providers=teardown_providers,
        )


_TASK_DOC_TOOL_DESCRIPTION = """Write task documents: ar-task-document/v1 JSON and generated markdown. Read the .md as a file. A write names its kind ['subTask'|'master']. Operations: create, replace, set_status, set_step, add_step, remove_step, skip_step, set_subtask, remove_subtask, set_section, append_decision, put_open_question, begin_review, record_review, record_route_review, author_execution_graph, attach_master, detach_master, retire_master, archive_sprint, linkage_report, set_field. Writes answer ok; dry runs answer wouldLose and the diff. Reference: docs/reference/mcp-tools.md."""


def _register_task_document_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
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


def register_task_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    _register_task_reopen_tools(server, config)
    _register_task_finalizer_tools(server, config)
    _register_task_document_tools(server, config)
    _register_curator_coherence_tools(server, config)
    _register_closeout_queue_tools(server, config)
