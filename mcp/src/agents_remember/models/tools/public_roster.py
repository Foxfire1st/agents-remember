"""The advertised public MCP tool roster, as one cycle-free literal.

This tuple is the single definition; :data:`agents_remember.mcp.tools.base.PUBLIC_TOOLS`
re-exports it unchanged, so every existing consumer and the ``public_surface`` pin against
:data:`~agents_remember.models.tools.tool_registry.PUBLIC_TOOL_RESPONSE_MODELS` still name
the same object.

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
    "ping",
    "server_info",
    "context_packet",
    "read_ar_files",
    "resolve_context",
    "runtime_install",
    "skills_install",
    "dispatch_agent",
    "retire_child",
    "rename_child",
    "rename_self",
    "drift_check",
    "memory_quality_check",
    "citation_fix",
    "citation_migrate",
    "route_index_refresh",
    "memory_init",
    "memory_baseline_status",
    "memory_baseline_adopt",
    "memory_carryover_plan",
    "memory_carryover_apply",
    "provider_status",
    "provider_diagnostics",
    "provider_watchers",
    "grepai_search",
    "grepai_trace",
    "cgc_symbol_search",
    "cgc_callers",
    "cgc_callees",
    "cgc_dependencies",
    "cgc_complexity",
    "cgc_visualize",
    "worktree_start",
    "worktree_attach",
    "worktree_status",
    "worktree_sync",
    "worktree_pause",
    "direct_landing",
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
    "codex_benchmark_prepare",
    "codex_benchmark_run",
    "lifecycle_start",
    "lifecycle_resume",
    "lifecycle_turn_end_notification",
    "lifecycle_end",
    "switch_lifecycle",
    "lifecycle_phase",
    "lifecycle_gate",
    "gate_decide",
    "gate_list",
    "message_parent",
    "message_child",
    # The capsule operation and the skill discovery/read surface.
    "role_capsule_compile",
    "skill_catalog_list",
    "skill_catalog_read",
    # The knowledge operation family: read, change, diff, integrity and projection.
    "knowledge_read",
    "knowledge_change",
    "knowledge_diff",
    "knowledge_integrity_check",
    "knowledge_project",
)
