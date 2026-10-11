"""Bounds for MCP tool descriptions (MIK-R112 rule 7, amended in place under D126).

A description says in plain sentences what the tool does and when to call it, and names
the arguments an ordinary call needs where their names do not say it. It holds no example
of an answer, no list of refusal codes, no list of every argument and no history. It ends
by naming the one place where the longer explanation is — every argument, every operation,
every refusal: the skill that teaches the tool, or the tool's section of the reference.
``TOOL_DESCRIPTION_MAX_CHARS`` holds for every registered tool; there is no exception
table. The schema description of an argument, where one exists, holds to
``TOOL_ARGUMENT_DESCRIPTION_MAX_CHARS``.

Who writes which: L109, L110 and L111 write the descriptions of the tools whose answers
they rebuild (MIK-R108 rule 7), and L73 those of the tools it adds; this leaf writes all
others. Until its leaf lands, a tool with an interim row of L109, L110 or L111 in the
table of MIK-R108 rule 6 keeps its description, held to the length it has when this leaf
lands. ``INTERIM_DESCRIPTION_OWNERS`` names that ownership; after L108 lands the test
reads the interim marking from its table instead.
"""

from __future__ import annotations

TOOL_DESCRIPTION_MAX_CHARS = 600
TOOL_ARGUMENT_DESCRIPTION_MAX_CHARS = 120

# `dispatch_agent` stays registered until L113 removes it (D128); it keeps its
# description, held to the length it has when this leaf lands.
RETAINED_DESCRIPTION_LENGTHS: dict[str, int] = {
    "dispatch_agent": 1114,
}

INTERIM_DESCRIPTION_OWNERS: dict[str, str] = {
    "context_packet": "L109",
    "read_ar_files": "L109",
    "resolve_context": "L109",
    "knowledge_read": "L109",
    "knowledge_diff": "L109",
    "memory_quality_check": "L110",
    "citation_fix": "L110",
    "route_index_refresh": "L110",
    "memory_init": "L110",
    "memory_baseline_status": "L110",
    "memory_baseline_adopt": "L110",
    "memory_carryover_plan": "L110",
    "memory_carryover_apply": "L110",
    "knowledge_integrity_check": "L110",
    "curator_coherence": "L110",
    "worktree_start": "L111",
    "worktree_attach": "L111",
    "worktree_status": "L111",
    "worktree_sync": "L111",
    "worktree_pause": "L111",
    "direct_landing": "L111",
    "worktree_closeout_preview": "L111",
    "worktree_closeout_apply": "L111",
    "worktree_integrate": "L111",
    "worktree_checkpoint_landing": "L111",
    "worktree_record_landing": "L111",
    "worktree_operation_control": "L111",
    "worktree_cleanup": "L111",
    "worktree_abandon": "L111",
    "role_start": "L111",
    "role_message": "L111",
    "task_doc": "L111",
    "worktree_enclosure_adopt": "L111",
    "lifecycle_finalize_task": "L111",
    "closeout_queue": "L111",
    "task_reopen": "L111",
}
