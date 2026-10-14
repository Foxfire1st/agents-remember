"""Task-document tools plus the two task-state transitions: finalize and reopen."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.closeout_queue import CloseoutQueueRequest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
)
from agents_remember.application.worktree_tools import FinalizeTaskDocs
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherenceRequest

from ..tools import (
    closeout_queue_payload,
    curator_coherence_payload,
    lifecycle_finalize_task_payload,
    task_doc_payload,
    task_reopen_payload,
)


def _register_closeout_queue_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def closeout_queue(request: CloseoutQueueRequest) -> dict[str, Any]:
        """Inspect or rebuild one sprint's disposable waiting-only closeout projection: exact-
        current valid-built or non-admitting invalid-empty, derived from the current task plus
        waiting-door facts. States: valid-built, invalid-empty, blocked. Reference: docs/reference/mcp-tools.md."""
        return closeout_queue_payload(config, request)


def _register_curator_coherence_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def curator_coherence(request: CuratorCoherenceRequest) -> dict[str, Any]:
        """Author, publish, inspect, or validate one leaf's curator-coherence authority.
        The structured stable manifest selects exactly one live content-addressed record;
        Markdown is a generated digest-bound projection and is never parsed as authority.
        `prepare` writes the exact candidate set to the enclosure's
        `reports/curator-coherence-candidates.json` (with a `.md` reading rendering beside
        it) and answers with the count of candidates by kind, the set's digest and the
        locations; the curator reads the candidates from the file. `status` and `validate`
        answer with the state, the counts and what blocks. `publish` requires the unchanged
        identities plus one curator/architect-authored disposition, rationale, and evidenceRef
        for every candidate, and answers the outcome with the count of published judgments;
        on a refusal it names which expectation no longer holds. `publish` takes `judgments`
        and `review_assessments` inline, as before, or from one JSON file (`judgments_file`,
        an absolute path) holding the two lists; the file is read once and its SHA-256 is
        answered, and a call that supplies a list both ways is refused.
        `publish` also requires every one of `semantic_requirement_revision`, `delivery_attempt`,
        `expected_predecessor_digest`, `expected_code_candidate_tree`,
        `expected_memory_candidate_tree`, `expected_task_topology_fingerprint`,
        `expected_task_intent`, `expected_attestation_sha256` and `caller` to be supplied and
        non-null: the request carries one action's input shape at a time, so `status`, `prepare`
        and `validate` forbid all nine. It rejects missing, extra, duplicate, malformed, or stale
        judgments, publishes atomically, and may freeze an immutable delivery-attempt snapshot.
        Evidence references use one explicit authority namespace—`code:`, `memory:`, or `task:`—and
        the lifecycle records and later revalidates the referenced bytes' digest. A semantic
        requirement revision, delivery attempt, and content digest are separate fields. `validate`
        is the same validator used by memory preflight and closeout admission. Historical files
        are never searched as fallbacks."""
        return curator_coherence_payload(config, request)


def _register_task_reopen_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def task_reopen(contract_path: str, dry_run: bool = False) -> dict[str, Any]:
        """Reopen a COMPLETED leaf under its exact same leaf id: review, closeout and integration
        state returns to virgin and the leaf document goes back to planning. Afterwards edit
        steps via task_doc, then run a normal worktree_start. States: reopened, blocked;
        preview with dry_run. Reference: docs/reference/mcp-tools.md."""
        return task_reopen_payload(config, contract_path, dry_run=dry_run)


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
        Standalone leaves supported; ancestors are not completed. States: finalized,
        would-finalize, blocked. Answers the state, what blocks
        and the next call; preview with dry_run. Reference: docs/reference/mcp-tools.md."""
        return lifecycle_finalize_task_payload(
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


_TASK_DOC_TOOL_DESCRIPTION = """Write task documents: ar-task-document/v1 JSON plus rendered markdown (JSON rules; markdown is generated). Read the .md as a file. A write names its kind ['subTask'|'master']. Operations: create, replace, set_status, set_step, add_step, remove_step, skip_step, set_subtask, remove_subtask, set_section, append_decision, begin_review, record_review, record_route_review, author_execution_graph, attach_master, detach_master, retire_master, archive_sprint, linkage_report, set_field. Writes answer ok; dry runs answer wouldLose and the diff. Reference: docs/reference/mcp-tools.md."""


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
        return task_doc_payload(
            config,
            TaskDocTarget(
                repo_id=repo_id,
                task_name=task_name,
                contract_path=contract_path,
                slug=slug,
            ),
            operation=operation,
            edit=TaskDocEdit(
                fields=fields,
                step=step,
                decision=decision,
                subtask=subtask,
                section=section,
                review=review,
                fields_file=fields_file,
                section_body_file=section_body_file,
            ),
            call=TaskDocCall(dry_run=dry_run, branch_addressed=branch_addressed),
        )


def register_task_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    _register_task_reopen_tools(server, config)
    _register_task_finalizer_tools(server, config)
    _register_task_document_tools(server, config)
    _register_curator_coherence_tools(server, config)
    _register_closeout_queue_tools(server, config)
