"""The advertised public MCP tool roster, as one cycle-free literal.

This tuple is the single definition; :data:`agents_remember.mcp.tools.base.PUBLIC_TOOLS`
re-exports it unchanged, so every consumer and the pin against
:data:`~agents_remember.models.tools.tool_registry.TOOL_RESPONSE_MODELS` name the same
object.

It lives here, in the ``models`` package, because ``models`` needs to read the roster and
may not import ``mcp`` (``layers.toml`` ranks ``models`` 2 and ``mcp`` 22, and the ``mcp``
charter forbids any import from below). Defining it in the adapter and reading it back down
would be the cycle the ``models`` charter names outright: "A status, kind or state that
crosses the wire is DEFINED here and imported by whoever decides it -- never defined by the
decider and imported back down, which is a cycle with a type annotation on it." The roster
is advertised wire vocabulary, and ``models/tools/`` is already the home of that vocabulary.

This module deliberately imports nothing, so ``models.worktree`` can import it at module
scope while ``models.tools.tool_registry`` is still initializing.
"""

from __future__ import annotations

PUBLIC_TOOLS = (
    "server_info",
    "context_packet",
    "read_ar_files",
    "runtime_install",
    # MIK-R100 rule 8 restores only retire_child; the L112 removals otherwise stand.
    "retire_child",
    "memory_quality_check",
    "citation_fix",
    "route_index_refresh",
    "memory_init",
    "memory_baseline_status",
    "memory_baseline_adopt",
    "memory_carryover_plan",
    "memory_carryover_apply",
    "worktree_start",
    "worktree_enclosure_adopt",
    "worktree_attach",
    "worktree_status",
    "worktree_sync",
    "worktree_pause",
    "direct_landing",
    "direct_execution_grant",
    "worktree_closeout_preview",
    "worktree_closeout_apply",
    "worktree_integrate",
    "worktree_checkpoint_landing",
    "worktree_record_landing",
    "worktree_operation_control",
    "worktree_cleanup",
    "worktree_abandon",
    "task_reopen",
    "lifecycle_finalize_task",
    "task_doc",
    "curator_coherence",
    "closeout_queue",
    # The capsule operation and the skill discovery/read surface.
    # Role start and role messaging by role agents.
    "role_start",
    "role_start_set",
    "role_message",
    # Knowledge operations over a memory tree: read, diff and integrity.
    "knowledge_read",
    "knowledge_diff",
    "knowledge_integrity_check",
)
