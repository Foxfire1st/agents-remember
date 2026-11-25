"""The canonical MCP tool allowlist of each AR role, as one authoritative table.

A role-launched ``agents-remember-task`` server advertises and dispatches only the tools of
the role it was started for: least privilege over one static table, and a stable tool order
across turns so the prompt cache of a harness holds. Every entry is a subsequence of
:data:`agents_remember.models.tools.public_roster.PUBLIC_TOOLS`, in that roster's exact
order; the union of the eight role entries is the whole public roster, so no tool is unreachable
and none appears twice in an entry.

The table lives in ``models`` because the advertised roster is wire vocabulary: the server
narrows itself with it and the seat's preapproval policy grants from it, and neither may own the
other's copy. It imports the roster and role spelling from this package alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from agents_remember.models.role_identity import canonical_role
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

#: The tools each role may use, in ``PUBLIC_TOOLS`` order.
ROLE_TOOL_ALLOWLISTS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "architect": (
            "server_info",
            "context_packet",
            "read_ar_files",
            "runtime_install",
            "retire_child",
            "memory_init",
            "memory_baseline_status",
            "memory_baseline_adopt",
            "worktree_start",
            "worktree_status",
            "worktree_sync",
            "direct_execution_grant",
            "worktree_closeout_preview",
            "worktree_closeout_apply",
            "worktree_integrate",
            "worktree_record_landing",
            "worktree_operation_control",
            "task_reopen",
            "task_doc",
            "closeout_queue",
            "role_start",
            "role_message",
            "knowledge_read",
            "knowledge_diff",
            "knowledge_integrity_check",
            "explainer_control",
        ),
        "orchestrator": (
            "server_info",
            "context_packet",
            "read_ar_files",
            "retire_child",
            "worktree_start",
            "worktree_enclosure_adopt",
            "worktree_attach",
            "worktree_status",
            "worktree_sync",
            "worktree_pause",
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
            "closeout_queue",
            "role_start",
            "role_message",
            "knowledge_read",
            "knowledge_diff",
            "knowledge_integrity_check",
        ),
        "manager": (
            "server_info",
            "context_packet",
            "read_ar_files",
            "retire_child",
            "memory_quality_check",
            "worktree_start",
            "worktree_enclosure_adopt",
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
            "role_start",
            "role_start_set",
            "role_message",
            "knowledge_read",
            "knowledge_diff",
            "knowledge_integrity_check",
            "knowledge_write",
        ),
        "worker": (
            "server_info",
            "context_packet",
            "read_ar_files",
            "worktree_status",
            "worktree_sync",
            "task_doc",
            "role_message",
            "knowledge_read",
        ),
        "curator": (
            "server_info",
            "context_packet",
            "read_ar_files",
            "memory_quality_check",
            "citation_fix",
            "citation_note_revision",
            "route_index_refresh",
            "memory_carryover_plan",
            "memory_carryover_apply",
            "worktree_status",
            "task_doc",
            "curator_coherence",
            "role_message",
            "knowledge_read",
            "knowledge_diff",
            "knowledge_integrity_check",
            "knowledge_write",
        ),
        "reviewer": (
            "server_info",
            "context_packet",
            "read_ar_files",
            "memory_quality_check",
            "worktree_status",
            "task_doc",
            "curator_coherence",
            "role_message",
            "knowledge_read",
            "knowledge_diff",
            "knowledge_integrity_check",
        ),
        "investigator": (
            "server_info",
            "context_packet",
            "read_ar_files",
            "memory_quality_check",
            "worktree_status",
            "task_doc",
            "role_message",
            "knowledge_read",
            "knowledge_diff",
            "knowledge_integrity_check",
        ),
        "bootstrap": (
            "server_info",
            "context_packet",
            "read_ar_files",
            "runtime_install",
            "memory_init",
            "memory_baseline_status",
            "memory_baseline_adopt",
            "user_settings_init",
        ),
    }
)


def tools_for_role(role: str) -> tuple[str, ...]:
    """The canonical tool allowlist of ``role``, with its supported alias normalized.

    An unknown role raises ``ValueError`` naming it: a role the table does not know must not
    silently receive the whole public roster.
    """

    canonical = canonical_role(role)
    try:
        return ROLE_TOOL_ALLOWLISTS[canonical]
    except KeyError:
        raise ValueError(f"the role {role!r} has no canonical tool allowlist") from None


def role_tool_roster(role: str | None) -> tuple[str, ...]:
    """The tools a seat of ``role`` is offered; ``None`` is unbound and keeps the public roster."""

    return PUBLIC_TOOLS if role is None else tools_for_role(role)


__all__ = ["ROLE_TOOL_ALLOWLISTS", "role_tool_roster", "tools_for_role"]
