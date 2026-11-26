"""Registry of served skill:// guidance pointers for MCP refusal payloads.

Each key maps one refusal family or status to the served ``skill://`` URI of its
Tier-2 recovery procedure. Production lookups fail safe (returning ``None`` or
unmodified text on an unknown key) so a refusal path never converts into an
unhandled exception; key registration and anchor resolution are verified in CI.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

SKILL_URI_PREFIX: Final[str] = "skill://agents-remember/skills/l-01-agent-lifecycles"

REFUSAL_REFERENCES: Final[Mapping[str, str]] = MappingProxyType(
    {
        # Family 1: role_message
        "role-message-held-store-full": (
            f"{SKILL_URI_PREFIX}/reference/message-handover.md#handle-a-full-store-refusal"
        ),
        "role-message-too-long": (
            f"{SKILL_URI_PREFIX}/reference/message-handover.md#send-a-message-that-names-the-files"
        ),
        "role-message-file-failed": (
            f"{SKILL_URI_PREFIX}/reference/message-handover.md#send-a-message-that-names-the-files"
        ),
        "role-message-recipient-archived": (
            f"{SKILL_URI_PREFIX}/reference/message-handover.md#find-filed-messages-and-receipts"
        ),
        # Family 2: task_doc review and route-review refusals
        "review-admission-invalid": (
            f"{SKILL_URI_PREFIX}/reference/review/rounds-and-late-findings.md"
            "#verify-repaired-findings-at-the-round-limit"
        ),
        "review-candidate-invalid": (
            f"{SKILL_URI_PREFIX}/reference/review/rounds-and-late-findings.md"
            "#recheck-a-repaired-worker-record"
        ),
        "review-kind-invalid": (
            f"{SKILL_URI_PREFIX}/reference/review/rounds-and-late-findings.md"
            "#recheck-a-repaired-worker-record"
        ),
        "route-review-invalid": (
            f"{SKILL_URI_PREFIX}/reference/review/rounds-and-late-findings.md#post-seal-routes"
        ),
        # Family 3: worktree_sync conflict and choice states
        "sync-resolution-required": (
            f"{SKILL_URI_PREFIX}/reference/worker/sync-and-repairs.md#synchronize-the-leaf"
        ),
        "memory-sync-choice-required": (
            f"{SKILL_URI_PREFIX}/reference/worker/sync-and-repairs.md#synchronize-the-leaf"
        ),
        "sync-active": (
            f"{SKILL_URI_PREFIX}/reference/worker/sync-and-repairs.md#synchronize-the-leaf"
        ),
        # Family 4: knowledge_write and memory_quality_check refusals
        "knowledge-write-invalid-input": f"{SKILL_URI_PREFIX}/reference/refused-write.md",
        "knowledge-write-refused": (
            f"{SKILL_URI_PREFIX}/reference/curator/publication-report.md"
            "#read-the-ingest-report-before-claiming-publication"
        ),
        "memory-quality-worklist": (
            f"{SKILL_URI_PREFIX}/reference/curator/worklist.md#answer-rows-and-repair-the-list"
        ),
        # Family 5: worktree_closeout_* and worktree_operation_control refusals
        "closeout-gate-blocked": (
            f"{SKILL_URI_PREFIX}/reference/coordination/shared/closeout-recovery.md"
            "#recover-an-unfinished-closeout"
        ),
        "curator-coherence-knowledge-gate-refused": (
            f"{SKILL_URI_PREFIX}/reference/coordination/shared/closeout-recovery.md"
            "#recover-an-unfinished-closeout"
        ),
        "closeout-door-route-review-blocked": (
            f"{SKILL_URI_PREFIX}/reference/coordination/shared/closeout-recovery.md"
            "#recover-an-unfinished-closeout"
        ),
        "worktree-operation-control-refused": (
            f"{SKILL_URI_PREFIX}/reference/coordination/shared/closeout-recovery.md"
            "#recover-an-unfinished-closeout"
        ),
        # Family 6: retire_child refusals
        "retire-child-outside-scope": (
            f"{SKILL_URI_PREFIX}/reference/coordination/shared/seat-recovery.md"
            "#retire-child-scope-and-active-turns"
        ),
        "retire-child-agent-in-turn": (
            f"{SKILL_URI_PREFIX}/reference/coordination/shared/seat-recovery.md"
            "#retire-child-scope-and-active-turns"
        ),
        "retire-child-open-children": (
            f"{SKILL_URI_PREFIX}/reference/coordination/shared/seat-recovery.md"
            "#retire-child-scope-and-active-turns"
        ),
    }
)


def refusal_reference(key: str) -> str | None:
    """Return the served skill:// URI for ``key``, or None if unregistered."""
    return REFUSAL_REFERENCES.get(key)


def with_refusal_reference(text: str, key: str) -> str:
    """Append ``See <uri>.`` once when ``key`` is registered; return ``text`` unchanged otherwise."""
    uri = refusal_reference(key)
    if uri is None or uri in text:
        return text
    trimmed = text.rstrip()
    if not trimmed:
        return f"See {uri}."
    if trimmed.endswith((".", "!", "?")):
        return f"{trimmed} See {uri}."
    return f"{trimmed}. See {uri}."


__all__ = [
    "REFUSAL_REFERENCES",
    "SKILL_URI_PREFIX",
    "refusal_reference",
    "with_refusal_reference",
]
