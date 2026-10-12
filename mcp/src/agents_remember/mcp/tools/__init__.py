"""Pure payload builders for Agents Remember MCP tools.

This package is the stable import surface for tool payload builders. The
builders are grouped by domain into submodules; import them from
``agents_remember.mcp.tools`` regardless of which submodule owns them.
"""

from __future__ import annotations

from .base import PUBLIC_TOOLS, RESERVED_TOOLS, TRANSPORT
from .base import _tool_payload as _tool_payload  # re-exported for tool-response tests
from .closeout_queue import closeout_queue_payload
from .core import (
    context_packet_payload,
    resolve_context_payload,
    runtime_install_payload,
    server_info_payload,
)
from .curator_coherence import curator_coherence_payload
from .direct_execution import direct_execution_grant_payload
from .direct_landing import direct_landing_payload
from .enclosure_adoption import worktree_enclosure_adopt_payload
from .gates import (
    gate_create_payload,
    gate_response_wait_payload,
    gate_wait_payload,
)
from .hosted_readiness import hosted_session_readiness_payload
from .lifecycle import lifecycle_block_payload
from .lifecycle_finalize import lifecycle_finalize_task_payload
from .memory import (
    citation_fix_payload,
    memory_baseline_adopt_payload,
    memory_baseline_status_payload,
    memory_carryover_apply_payload,
    memory_carryover_plan_payload,
    memory_init_payload,
    memory_quality_check_payload,
    memory_quality_check_poll_payload,
    memory_quality_check_start_payload,
    route_index_refresh_payload,
)
from .operator_inbox import (
    operator_inbox_consume_payload,
    operator_inbox_poll_payload,
    operator_inbox_post_payload,
    operator_inbox_supersede_payload,
)
from .orchestration import orchestration_nudge_manager_payload
from .read_files import read_ar_files_payload
from .role_agents import role_message_payload, role_start_payload
from .structural_agent import dispatch_agent_payload
from .task_doc import task_doc_payload, task_reopen_payload
from .terminal import (
    attach_terminal_session_to_task_payload,
    session_rename_payload,
    session_retire_payload,
    spawn_agent_session_payload,
)
from .worktree import (
    worktree_abandon_payload,
    worktree_attach_payload,
    worktree_checkpoint_landing_payload,
    worktree_cleanup_payload,
    worktree_closeout_apply_payload,
    worktree_closeout_preview_payload,
    worktree_integrate_payload,
    worktree_operation_control_payload,
    worktree_pause_payload,
    worktree_record_landing_payload,
    worktree_start_payload,
    worktree_status_payload,
    worktree_sync_payload,
)

# The split gate/block/wait payload builders are retained for internal
# compatibility and tests. They are not advertised: ``create_server`` serves
# no public agent-facing gate tool (``lifecycle_gate`` was removed by MIK-R112).

__all__ = [
    "PUBLIC_TOOLS",
    "RESERVED_TOOLS",
    "TRANSPORT",
    "attach_terminal_session_to_task_payload",
    "citation_fix_payload",
    "closeout_queue_payload",
    "context_packet_payload",
    "curator_coherence_payload",
    "direct_execution_grant_payload",
    "direct_landing_payload",
    "dispatch_agent_payload",
    "gate_create_payload",
    "gate_response_wait_payload",
    "gate_wait_payload",
    "hosted_session_readiness_payload",
    "lifecycle_block_payload",
    "lifecycle_finalize_task_payload",
    "memory_baseline_adopt_payload",
    "memory_baseline_status_payload",
    "memory_carryover_apply_payload",
    "memory_carryover_plan_payload",
    "memory_init_payload",
    "memory_quality_check_payload",
    "memory_quality_check_poll_payload",
    "memory_quality_check_start_payload",
    "operator_inbox_consume_payload",
    "operator_inbox_poll_payload",
    "operator_inbox_post_payload",
    "operator_inbox_supersede_payload",
    "orchestration_nudge_manager_payload",
    "read_ar_files_payload",
    "resolve_context_payload",
    "role_message_payload",
    "role_start_payload",
    "route_index_refresh_payload",
    "runtime_install_payload",
    "server_info_payload",
    "session_rename_payload",
    "session_retire_payload",
    "spawn_agent_session_payload",
    "task_doc_payload",
    "task_reopen_payload",
    "worktree_abandon_payload",
    "worktree_attach_payload",
    "worktree_checkpoint_landing_payload",
    "worktree_cleanup_payload",
    "worktree_closeout_apply_payload",
    "worktree_closeout_preview_payload",
    "worktree_enclosure_adopt_payload",
    "worktree_integrate_payload",
    "worktree_operation_control_payload",
    "worktree_pause_payload",
    "worktree_record_landing_payload",
    "worktree_start_payload",
    "worktree_status_payload",
    "worktree_sync_payload",
]
