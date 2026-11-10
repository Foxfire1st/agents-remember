"""The declared publication operations an installed stale MCP build cannot execute."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents_remember.models.build_currentness import BuildCurrentnessPayload

_WRITERS = frozenset(
    {
        "worktree_closeout_apply",
        "worktree_integrate",
        "worktree_checkpoint_landing",
        "worktree_record_landing",
        "direct_landing",
        "worktree_sync",
        "lifecycle_finalize_task",
        "citation_note_revision",
        "route_index_refresh",
        "memory_init",
        "memory_baseline_adopt",
        "memory_carryover_apply",
        "task_reopen",
    }
)


def is_stale_build_write(name: str, arguments: Mapping[str, Any]) -> bool:
    """Match publication, not every side effect; reads and previews remain usable.

    L84-R2 prevents old loaded gates from publishing after an installed cutover.
    This does not police checkout work, unknown
    build identities, host operations, messages or newly introduced tool names.
    """
    if arguments.get("dry_run") is True:
        return False
    if name in _WRITERS or (name == "knowledge_write" and arguments.get("commit") is True):
        return True
    if name == "citation_fix":
        return arguments.get("mode", "sync") != "poll"
    if name == "curator_coherence":
        request = arguments.get("request")
        return isinstance(request, Mapping) and request.get("action") == "publish"
    if name == "task_doc":
        # The authoring owner is loaded only for this operation, never during discovery.
        from agents_remember.application.task_docs.task_doc_tools import (  # noqa: PLC0415
            VALID_OPERATIONS,
        )

        operation = arguments.get("operation")
        return operation in VALID_OPERATIONS and operation != "linkage_report"
    return False


def stale_build_refusal(name: str, reading: BuildCurrentnessPayload) -> dict[str, Any]:
    """Report both builds and the user-owned server restart, before a writer runs."""
    from agents_remember.application.tool_response import complete_tool_response  # noqa: PLC0415

    return complete_tool_response(
        name,
        {
            "ok": False,
            "code": "server-build-stale",
            "error": (
                f"This MCP server loaded build {reading.loadedDigest}; the installed build is "
                f"{reading.installedDigest}. Restart or reconnect this MCP server before "
                "publishing changes, or restart the dashboard if it serves this call."
            ),
            "buildCurrentness": reading.model_dump(mode="json", exclude_none=True),
        },
    )
