"""Task-document tools plus the two task-state transitions: finalize and reopen."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.controllers.task_doc_tools import TaskDocEdit, TaskDocTarget
from agents_remember.controllers.worktree_tools import FinalizeTaskDocs

from ..config import McpRuntimeConfig
from ..tools import (
    lifecycle_finalize_task_payload,
    task_doc_payload,
    task_reopen_payload,
)


def register_task_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
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

    @server.tool()
    def lifecycle_finalize_task(
        contract_path: str,
        task_doc_path: str | None = None,
        master_doc_path: str | None = None,
        subtask_number: str = "",
        dry_run: bool = False,
        teardown_providers: bool = True,
    ) -> dict[str, Any]:
        """Finalize one parent-child task lifecycle edge. The task's landed commit must be
        reachable from the contract's local target/source branch; PR-gated flows must complete the
        PR merge and pull first, making the proof structurally identical to a non-PR edge. After
        landed-state and memory carryover checks, this runs or verifies cleanup and reconciles the
        supplied JSON-primary task documents. No squash-merge equivalence is attempted. Preview with
        dry_run=true."""
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

    @server.tool()
    def task_doc(
        repo_id: str,
        operation: str,
        task_name: str | None = None,
        contract_path: str | None = None,
        slug: str | None = None,
        fields: dict[str, Any] | None = None,
        step: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        subtask: dict[str, Any] | None = None,
        section: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Author the JSON-primary task document (ar-task-document/v1) and re-render its
        markdown. The JSON is the source of truth; task.md / <slug>.md is generated and never
        parsed back. Mutating (writes the doc's .json and .md) except operation='get'.

        operation: 'create' | 'replace' | 'set_status' | 'set_step' | 'set_subtask' | 'remove_subtask' |
        'set_section' | 'append_decision' | 'set_field' | 'get'. Locate the doc by task_name (also resolves the
        contract for the lifecycle key) or contract_path; pass slug for a series sub-task
        ('<slug>.json'), omit for a standalone task ('task.json'). 'create' takes fields (id, slug,
        title, kind ['light'|'subTask'|'master'], repo, type, createdAt, objective, requirements,
        steps, ... — a master takes subTasks + ordered sections instead of steps); 'replace' takes a
        full replacement document in fields and rewrites the existing JSON+markdown after schema
        validation; 'set_step' takes
        step={id, title, status, parent?, note?}; 'set_subtask' (master) takes subtask={number, name,
        file?, status?, scope?}; 'remove_subtask' (master) takes subtask={number, keep_file?} and drops that
        sub-task row AND deletes its leaf doc (json+md) unless keep_file=true; 'set_section' (master) takes
        section={heading, kind?, body?};
        'append_decision' takes decision={at, decision, rationale}; 'set_field' takes fields with
        scalar/list updates; 'set_status' takes fields.status. dry_run=true builds + validates and
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
            ),
            dry_run=dry_run,
        )
