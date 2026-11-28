"""What a ``knowledge_diff`` answer says about the changes it compared.

The answer holds counts by kind and every changed record in the order a reviewer should decide
on them; the whole comparison goes to a report beside the answer.
"""

from __future__ import annotations

from typing import Any

from agents_remember.models.knowledge.review_trees import ReviewKnowledgeTreeDiff


def change_counts(diff: ReviewKnowledgeTreeDiff) -> dict[str, int]:
    """The count of changes by kind: changed files by Git status, groups by listing."""

    counts = {
        "changedFiles": diff.changed_files,
        "records": len(diff.records),
        "sources": len(diff.sources),
        "history": len(diff.history),
        "other": len(diff.other),
    }
    for group in (*diff.records, *diff.sources):
        for change in group.files:
            counts[change.status] = counts.get(change.status, 0) + 1
    for change in (*diff.history, *diff.other):
        counts[change.status] = counts.get(change.status, 0) + 1
    return counts


# A changed record asks for a decision soonest when knowledge appeared or
# vanished (an added or deleted record file), next when a code-side claim
# about it changed (its entries), then renames, type changes and edits.
_DECISION_ORDER = {
    "added": 0,
    "deleted": 1,
    "entries-changed": 2,
    "renamed": 3,
    "type-changed": 4,
    "modified": 5,
}


def _group_change(statuses: set[str], has_entries: bool) -> str:
    """The one change kind naming a record group, in decision order."""

    if "added" in statuses:
        return "added"
    if "deleted" in statuses:
        return "deleted"
    if has_entries:
        return "entries-changed"
    if "renamed" in statuses:
        return "renamed"
    if "type_changed" in statuses:
        return "type-changed"
    return "modified"


def decision_records(diff: ReviewKnowledgeTreeDiff) -> list[dict[str, Any]]:
    """Every changed record group as ID, kind, change and index path.

    The path is the authoritative record path inside the memory tree, not
    joined with the checkout: the answered absolute ``memoryRoot`` is the
    join base, so large comparisons stay within their bound on any root
    length.
    """

    named = []
    for group in diff.records:
        statuses: set[str] = {change.status for change in group.files}
        change = _group_change(statuses, bool(group.entries))
        path = group.files[0].path if group.files else None
        named.append(
            {
                "recordId": group.record_id,
                "kind": group.kind,
                "change": change,
                "path": path,
            }
        )
    named.sort(key=lambda entry: (_DECISION_ORDER[entry["change"]], entry["recordId"]))
    return named
