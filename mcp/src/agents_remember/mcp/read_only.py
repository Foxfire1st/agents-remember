"""The one list of read-only marks for every registered tool (MIK-R109 rule 7).

Every registered tool that writes nothing in the repositories, the
worktrees, the task's record and the host carries ``readOnlyHint: true``;
every other registered tool carries ``readOnlyHint: false``. The marks come
from this list, for the tools of every registration module, and
:func:`apply_read_only_marks` is the place where the registration applies
them: :func:`create_server` runs it after the registrars, so the advertised
``annotations`` always match this file. A tool in neither part breaks server
creation with ``UnknownReadOnlyMark`` -- a new tool arrives with its mark,
never without one. (Rule 7 lands the marks for the tools registered at this
task's tip; L110-L112 keep their own tools' part complete, and the sweep
test holds the list to that.)

``knowledge_diff`` is a read with ``readOnlyHint: false``: with no
``afterRevision`` it captures the working tree as a Git tree, which writes
loose, unreferenced objects into the memory repository's object store. The
call reads knowledge; the mark states the write.

``context_packet`` is a read with ``readOnlyHint: false``: with
``include_freshness`` it fetches the configured remotes, which writes
``FETCH_HEAD`` and remote-tracking state into the repositories' object
stores. A call without the option writes nothing, but the mark describes
the advertised capability including the option, so it states the write.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

__all__ = [
    "EXPLAINER_MUTATING_TOOLS",
    "EXPLAINER_READ_ONLY_TOOLS",
    "MUTATING_TOOLS",
    "READ_ONLY_TOOLS",
    "UnknownReadOnlyMark",
    "apply_read_only_marks",
    "read_only_hint",
]

# Tools that write nothing in the repositories, the worktrees, the task's
# record and the host. The sweep test runs each of these in its world and
# fails where one changes a file of it (the derived index and the logs do
# not count).
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        # L109: the naming reads (context_packet admits a fetching option,
        # so it stands with the writes below).
        "read_ar_files",
        "knowledge_read",
        # Memory diagnostics that change nothing.
        "knowledge_integrity_check",
        # One status read and one non-mutating preview.
        "worktree_status",
        "worktree_closeout_preview",
        # Liveness and identity.
        "server_info",
    }
)

# MIK-R211 v5 rule 3: of the explainer's own four tools, served on this same server when a launch
# binds it as an explainer seat, render is marked as it always was; it writes only its own run folder
# and the task's next revision through the frame store's own lock.
EXPLAINER_READ_ONLY_TOOLS: frozenset[str] = frozenset({"render"})

# Every other registered tool writes something somewhere the rule names, so
# a harness that asks before a call that may write holds it for approval.
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        # L109: the comparison writes loose Git objects without afterRevision,
        # and the packet fetches remotes with include_freshness.
        "knowledge_diff",
        "context_packet",
        # Reads that file a scratch report under temp/: outside the two
        # exclusions the sweep allows, so marked as writes.
        "memory_baseline_status",
        "memory_carryover_plan",
        # Memory writers. knowledge_write plans by default but commits files
        # with `commit`, so the mark describes what the call may write.
        "knowledge_write",
        "memory_quality_check",
        "citation_fix",
        "citation_note_revision",
        "route_index_refresh",
        "memory_init",
        "memory_baseline_adopt",
        "memory_carryover_apply",
        "curator_coherence",
        # Task and landing writers.
        "task_doc",
        "task_reopen",
        "direct_landing",
        "lifecycle_finalize_task",
        "closeout_queue",
        "worktree_start",
        "worktree_attach",
        "worktree_sync",
        "worktree_pause",
        "worktree_closeout_apply",
        "worktree_integrate",
        "worktree_checkpoint_landing",
        "worktree_record_landing",
        "worktree_operation_control",
        "worktree_cleanup",
        "worktree_abandon",
        "worktree_enclosure_adopt",
        # Roles and seats.
        "role_start",
        "role_message",
        # MIK-R186: the architect's Update, Retire and claim write the seat, the
        # environment and the master-to-architect record.
        "explainer_control",
        # L100: starting and retiring agents changes seats.
        "role_start_set",
        "retire_child",
        # L94 rule 2: giving, revoking or reclaiming a direct-execution
        # grant writes the grant record only.
        "direct_execution_grant",
        # Installs.
        "runtime_install",
        # MIK-R196 rule 11: the one user settings file, previewed then written once.
        "user_settings_init",
    }
)

# The explainer's writers (MIK-R186 @ v2 rule 3): a category, a grade and a removed category write.
EXPLAINER_MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "define_category",
        "grade",
        "remove_category",
    }
)


class UnknownReadOnlyMark(Exception):
    """One advertised tool stands in neither part of the read-only list."""


def read_only_hint(tool_name: str) -> bool:
    """Whether the named tool is marked as a read; raises when unlisted."""

    if tool_name in READ_ONLY_TOOLS or tool_name in EXPLAINER_READ_ONLY_TOOLS:
        return True
    if tool_name in MUTATING_TOOLS or tool_name in EXPLAINER_MUTATING_TOOLS:
        return False
    raise UnknownReadOnlyMark(
        f"tool {tool_name!r} stands in neither part of the read-only list; "
        "name it as a read or as a write in mcp/read_only.py"
    )


def apply_read_only_marks(server: FastMCP) -> None:
    """Carry this list onto one registered server's advertised annotations.

    Runs after the registrars: every advertised tool gets exactly the mark
    this list states for it, and an advertised tool the list does not name
    breaks server creation instead of shipping unmarked.
    """

    tools: dict[str, Any] = server._tool_manager._tools
    for name in sorted(tools):
        tools[name].annotations = ToolAnnotations.model_validate(
            {"readOnlyHint": read_only_hint(name)}
        )
