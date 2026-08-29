"""Task-document tools plus the two task-state transitions: finalize and reopen."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.closeout_door import CloseoutDoorRequest
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
    closeout_door_payload,
    closeout_queue_payload,
    curator_coherence_payload,
    lifecycle_finalize_task_payload,
    task_doc_payload,
    task_reopen_payload,
)


def _register_closeout_queue_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def closeout_queue(request: CloseoutQueueRequest) -> dict[str, Any]:
        """Inspect or idempotently rebuild one sprint's disposable closeout projection.
        The artifact is either exact-current valid-built or non-admitting invalid-empty. Rebuild
        first persists invalid-empty, then derives membership and ordering only from current task
        truth and current waiting contract-owned door generations. Old rows are never seeds,
        history, or authority. A missing, malformed, or source-mismatched projection reports the
        exact task-or-sprint-addressed rebuild action. This surface never declares, selects,
        defers, withdraws, or otherwise mutates closeout intent."""
        return closeout_queue_payload(config, request)


def _register_closeout_door_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def closeout_door(request: CloseoutDoorRequest) -> dict[str, Any]:
        """Publish or inspect one exact contract-owned closeout-door generation.
        declare and update-provenance require complete current task, source, review, memory,
        ledger, admission, and scheduling evidence; defer, resume, and withdraw change only the
        exact current generation's disposition. Successful source publication refreshes the
        affected sprint projection after releasing the short task/door publication mutex. Same
        intent retries converge on the already-published generation. Claiming is intentionally
        absent here: worktree_closeout_apply validates first-ready and owns waiting-to-claimed."""
        return closeout_door_payload(config, request)


def _register_curator_coherence_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def curator_coherence(request: CuratorCoherenceRequest) -> dict[str, Any]:
        """Author, publish, inspect, or validate one leaf's curator-coherence authority.
        The structured stable manifest selects exactly one live content-addressed record;
        Markdown is a generated digest-bound projection and is never parsed as authority.
        `prepare` returns the exact code, memory, task-topology, attestation, predecessor, and
        source-candidate identities. `publish` requires those unchanged identities plus one
        curator/architect-authored disposition, rationale, and evidenceRef for every candidate.
        It rejects missing, extra, duplicate, malformed, or stale judgments, publishes atomically,
        and may freeze an immutable delivery-attempt snapshot. Evidence references use one explicit
        authority namespace—`code:`, `memory:`, or `task:`—and the lifecycle records and later
        revalidates the referenced bytes' digest. A semantic requirement revision, delivery
        attempt, and content digest are separate fields. `validate` is the same validator
        used by memory preflight and closeout admission. Historical files are never searched as
        fallbacks."""
        return curator_coherence_payload(config, request)


def _register_task_reopen_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def task_reopen(contract_path: str, dry_run: bool = False) -> dict[str, Any]:
        """Reopen a COMPLETED leaf under its exact same leaf id (no -rN suffix). A state
        reset, not a worktree creator: the enclosure contract's review/closeout/integration
        state returns to virgin (cleanup=reopened, stale lifecycle binding cleared) and the
        leaf's task document goes back to planning (lifecycleId cleared, master sub-task
        index entry flipped, audit decision appended). MUTATING (contract + task docs; no
        git effects). Refuses masters, in-flight leaves, and leaves whose worktrees still
        exist. Afterwards: edit the doc's steps via task_doc, then run a NORMAL
        worktree_start with the same leaf id — it recreates worktrees/branches off the
        current source tips, promotes/mints a fresh lifecycle, and restamps the doc, so
        doc/chat/dashboard bindings hold by construction. Preview with dry_run=true."""
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
        """Finalize one parent-child task lifecycle edge. The task's landed commit must be
        reachable from the contract's local target/source branch; PR-gated flows must complete the
        PR merge and pull first, making the proof structurally identical to a non-PR edge. After
        landed-state and memory carryover checks, the contract identity resolves the one exact leaf
        document (an omitted task_doc_path adopts it; a supplied path must match it) and refuses
        before cleanup unless every declared parent/nested step is done. When that leaf declares an
        existing immediate parent, the finalizer always derives and reconciles its exact row, even
        when both optional parent assertions are omitted. master_doc_path and subtask_number are
        independent identity assertions; when present, each must match that derived edge.
        Standalone/no-parent leaves remain supported; the parent task itself and recursive ancestors
        are not completed. No step is auto-checked and no squash-merge equivalence is attempted.
        Preview with dry_run=true; unresolved steps also refuse previews."""
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


_TASK_DOC_TOOL_DESCRIPTION = """Author the JSON-primary task document (ar-task-document/v1) and re-render its
        markdown. The JSON is the source of truth; task.md / <slug>.md is generated and never
        parsed back. Mutating (writes the doc's .json and .md) except operation='get'.

        operation: 'create' | 'replace' | 'set_status' | 'set_step' | 'skip_step' | 'set_subtask' | 'remove_subtask' |
        'set_section' | 'append_decision' | 'record_route_review' |
        'author_execution_graph' | 'attach_master' | 'detach_master' | 'linkage_report' |
        'set_field' | 'get'. Locate the doc by task_name (also resolves the
        contract for the lifecycle key) or contract_path; pass slug for a series sub-task
        ('<slug>.json'), omit for a standalone task ('task.json'). 'create' takes fields (id, slug,
        title, kind ['light'|'subTask'|'master'], repo, type, createdAt, objective, requirements,
        steps, ... — a master takes subTasks + ordered sections instead of steps, and an
        orchestration sprint is scaffolded with empty canonical Judgment and Priority Register
        sections); 'replace' takes a
        full replacement document in fields and rewrites the existing JSON+markdown after schema
        validation; 'set_step' takes
        step={id, title, status, parent?, note?}; an explicit status clears an earlier skip disposition.
        'skip_step' takes exact existing step={id, reason, parent?}, sets only that unit done, and
        records intentional-skip provenance without cascading. A nonblank reason is required.
        'set_subtask' (master) takes subtask={number, name,
        file?, status?, scope?}; 'remove_subtask' (master) normally takes
        subtask={number, keep_file?} for a terminal row and drops that row plus its leaf doc
        (json+md) unless keep_file=true. To discard planning work that never started, pass
        subtask={number, disposition:'discard-unstarted', reason}; this forbids keep_file, proves
        absence of canonical enclosure/operation/seat/review/commit evidence under the same short
        task-publication CAS used by worktree_start, removes the child sources, and retains a typed
        parent audit. Started or ambiguous evidence refuses with the exact next lifecycle route;
        retries converge from the parent audit. 'set_section' (master) takes
        section={heading, kind?, body?} — a section carrying a canonical register heading must keep
        the exact register table shape (write-time validation);
        'record_route_review' takes review={verdict, verdictRef, routes:[{route, verdict,
        evidenceRef}]}; the control plane stamps the current Git candidate tree and time, and every
        evidence path must be a real task-relative file. It overwrites the prior candidate's review.
        For sanctioned direct execution (no leaf worktree), pass branch_addressed=true to bind the
        task-root series contract and stamp the candidate tree from the branch HEAD (policy-gated).
        'author_execution_graph' applies one validated atomic batch of structural mutations to a
        sprint's executionGraph: fields={mutations:[...]} where each mutation is one of
        {op:'add_node', ref:{repository,path}, kind?:'master'|'segment', leafIds?:[...]},
        {op:'remove_node', ref, leafId?:sample}, {op:'add_edge', predecessor, successor, reason,
        judgmentId}, {op:'remove_edge', predecessor, successor, judgmentId}, {op:'move_leaf', ref,
        leafId, toSegment:sampleLeafId, judgmentId}, or {op:'set_nature', ref, executionNature,
        judgmentId}. On a graph-less sprint the first add_node batch bootstraps the graph (the
        result reports bootstrapped:true); final validation requires exact orchestrates membership
        and an explicit nature for every commanded master. Edge endpoints are a bare
        {repository,path} (the master's sole node) or
        {ref, leafId} addressing the segment containing that leaf. Judgment-bearing mutations
        (edges, segmentation, nature) require a judgmentId row in the sprint's Judgment Register
        section; the mechanism never invents one. The batch refuses segment nodes on atomic
        masters, leaf placements overlapping or incomplete against the live subTasks, and unknown
        leaf ids; unplaced-leaf derived placements and leaf-numbering inversions across waves are
        reported as facts (the latter never refuse). dry_run previews the rendered diff and
        wouldLose without writing.
        'attach_master' (orchestration sprint) attaches one master as a single validated atomic
        batch: fields={masterRef:{repository,path}, number, name?, scope?, status?,
        executionNature?, judgmentId?} write the typed subTask row (masterRef, rendered as a real
        link), the orchestrates slug, and — only when the sprint has an executionGraph — the lump
        graph node (graph-less sprints report graphNode:'deferred-no-graph-default' and keep the
        atomic-sequential default). A nature-less master requires executionNature plus a
        judgmentId from the sprint Judgment Register; disagreeing with an existing nature refuses.
        Validation precedes the one batch write, so a partial attach is structurally impossible.
        'detach_master' takes fields={masterRef} and removes the typed row, the membership slug,
        and the graph node; it refuses while any edge touches the node and never deletes files.
        'linkage_report' (sprint) is the read-only drift report: seat-doc rows, slug-only
        membership, row/membership mismatches, and uncommanded masters named in sprint decisions
        surface as facts, never as hard errors; 'get' on a sprint carries the same facts as
        linkageFacts.
        'append_decision' takes decision={at, decision, rationale}; 'set_field' takes fields with
        scalar/list updates; 'set_status' takes fields.status. Completed refuses while any declared
        step/substep (or master row) remains unresolved. dry_run=true builds + validates and
        returns rendered/diff/wouldLose WITHOUT writing — the preview before adopting a hand .md."""


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
            ),
            call=TaskDocCall(dry_run=dry_run, branch_addressed=branch_addressed),
        )


def register_task_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    _register_task_reopen_tools(server, config)
    _register_task_finalizer_tools(server, config)
    _register_task_document_tools(server, config)
    _register_curator_coherence_tools(server, config)
    _register_closeout_door_tools(server, config)
    _register_closeout_queue_tools(server, config)
