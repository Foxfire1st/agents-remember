"""Task-document tools plus the two task-state transitions: finalize and reopen."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.closeout_queue import CloseoutQueueRequest
from agents_remember.application.task_doc_tools import TaskDocEdit, TaskDocTarget
from agents_remember.application.worktree_tools import FinalizeTaskDocs
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from ..tools import (
    closeout_queue_payload,
    lifecycle_finalize_task_payload,
    task_doc_payload,
    task_reopen_payload,
)


def _register_closeout_queue_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def closeout_queue(request: CloseoutQueueRequest) -> dict[str, Any]:
        """Declare reviewed/curated leaf candidates before closeout, withdraw or update
        their explicit scheduling inputs, select/release the ready candidate, transition atomic
        blockers, or read the recomputed sprint frontier. Mutations require a caller-stable
        request_id plus the revision returned by status; retries keep both values, while stale
        mutations read status and use a new request id. Declaration requires the exact structured
        curator attestation and binds its checklist and structured source-change disposition
        evidence. Manager declaration cannot carry priority; the sprint orchestrator applies the
        separate set-grade action as a small assertion resolved against exact canonical Priority
        and Judgment Register rows; ordering
        is critical/high/normal/low, graph-node order, then leaf identity. Atomic blocker release
        requires canonical master completion; abort requires an exact strategist/orchestrator
        judgment. The caller is derived from the plane-owned hosted seat, never request data. The
        bounded canonical sprint artifact validates Git, full route-review records and evidence,
        memory mode/readiness, ledger, transitive lineage, graph, predecessor, blocker,
        task-completion, and admission facts without inventing judgment. Public responses and
        artifacts never expose lifecycle operation keys; task-addressed closeout/integration
        cancellation and recovery own later transitions and the irreversible integration seam
        revalidates the complete claim. The 'status' read never fails on a missing executionGraph
        or missing/malformed registers: it reports the degraded projection (mode, registers,
        laneOwner, legalNextOperations) instead. A closeout or integration whose recorded base
        pair no longer matches the current source tips is refused with worktree_sync named as the
        recovery; a completed landing reports stale-by-evidence siblings. An in-flight atomic
        block owns the sprint landing lane for its entire lifetime, acquisition reports in-flight
        organizational leafs as facts, and a certified candidate no longer occupies the lane."""
        return closeout_queue_payload(config, request)


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


def _register_task_document_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
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
    ) -> dict[str, Any]:
        """Author the JSON-primary task document (ar-task-document/v1) and re-render its
        markdown. The JSON is the source of truth; task.md / <slug>.md is generated and never
        parsed back. Mutating (writes the doc's .json and .md) except operation='get'.

        operation: 'create' | 'replace' | 'set_status' | 'set_step' | 'skip_step' | 'set_subtask' | 'remove_subtask' |
        'set_section' | 'append_decision' | 'record_route_review' |
        'author_execution_graph' | 'set_field' | 'get'. Locate the doc by task_name (also resolves the
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
        file?, status?, scope?}; 'remove_subtask' (master) takes subtask={number, keep_file?} and drops that
        sub-task row AND deletes its leaf doc (json+md) unless keep_file=true; 'set_section' (master) takes
        section={heading, kind?, body?} — a section carrying a canonical register heading must keep
        the exact register table shape (write-time validation);
        'record_route_review' takes review={verdict, verdictRef, routes:[{route, verdict,
        evidenceRef}]}; the control plane stamps the current Git candidate tree and time, and every
        evidence path must be a real task-relative file. It overwrites the prior candidate's review.
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
        'append_decision' takes decision={at, decision, rationale}; 'set_field' takes fields with
        scalar/list updates; 'set_status' takes fields.status. Completed refuses while any declared
        step/substep (or master row) remains unresolved. dry_run=true builds + validates and
        returns rendered/diff/wouldLose WITHOUT writing — the preview before adopting a hand .md."""
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
            dry_run=dry_run,
        )


def register_task_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    _register_task_reopen_tools(server, config)
    _register_task_finalizer_tools(server, config)
    _register_task_document_tools(server, config)
    _register_closeout_queue_tools(server, config)
