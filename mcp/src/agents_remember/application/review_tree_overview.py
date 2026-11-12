"""Project already validated inventory/worklist facts for the compact review opening.

The full read remains the evidence owner. This projection does not change its inputs,
checks or rows; selecting a subject or the worklist reads their full evidence on demand.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any, cast

from agents_remember.kernel.recorded_reads import bytes_identity
from agents_remember.models.knowledge.review_tree_entries import ReviewTreeEntry
from agents_remember.models.knowledge.review_tree_overview import (
    EntryCurrentness,
    ReviewOverview,
    ReviewSubjectSummary,
    ReviewWorklistSummary,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewEntryInventory,
    ReviewWorklistView,
)

_RANK = {"stale": 0, "unverifiable": 1, "current": 3, "not measured": 4}


def _least(states: Iterable[EntryCurrentness]) -> EntryCurrentness:
    least: EntryCurrentness = "not measured"
    for state in states:
        if _RANK[state] < _RANK[least]:
            least = state
    return least


def _currentness(entry: ReviewTreeEntry) -> EntryCurrentness:
    return _least(
        side.currentness
        for side in (entry.before, entry.after)
        if side.recorded and side.currentness
    )


def _summary(key: str, entries: Sequence[ReviewTreeEntry]) -> ReviewSubjectSummary:
    changed = [entry for entry in entries if entry.change == "changed"]
    paths: set[str] = set()
    span = 0
    proofs: dict[EntryCurrentness, int] = defaultdict(int)
    for entry in entries:
        if entry.kind == "proof":
            proofs[_currentness(entry)] += 1
    for entry in changed:
        # Same identity-side rule as blockSide/blockIdentityOf in the review model.
        side = entry.before if not entry.after.recorded and entry.before.recorded else entry.after
        paths.add(side.path)
        if side.state == "resolved" and side.start_line is not None and side.end_line is not None:
            span += side.end_line - side.start_line + 1
    return ReviewSubjectSummary(
        invariant_key=key,
        changed=len(changed),
        unchanged=sum(entry.change == "unchanged" for entry in entries),
        not_comparable=sum(entry.change == "undetermined" for entry in entries),
        changed_span=span,
        changed_paths=tuple(sorted(paths)),
        entry_currentness=_least(_currentness(entry) for entry in entries),
        proofs=proofs,
        content=_content(entries),
    )


def _content(entries: Sequence[ReviewTreeEntry]) -> str:
    """The digest of the invariant's entries, in id order: nothing positional enters it."""

    ordered = sorted(entries, key=lambda entry: entry.id)
    return bytes_identity("\n".join(entry.model_dump_json() for entry in ordered).encode("utf-8"))


def summarize_inventory(inventory: ReviewEntryInventory) -> ReviewOverview:
    """Same entries as the full model's id-keyed merge; same-region entries stay counted."""

    if inventory.state != "ready":
        return ReviewOverview(state="unavailable", reason=inventory.reason)
    entries = {entry.id: entry for entry in inventory.entries}
    grouped: dict[str, list[ReviewTreeEntry]] = defaultdict(list)
    for entry in entries.values():
        grouped[entry.invariant_key].append(entry)
    return ReviewOverview(
        state="ready",
        subjects=tuple(_summary(key, grouped[key]) for key in sorted(grouped)),
    )


def _open_count(worklist: ReviewWorklistView, items: Sequence[dict[str, Any]]) -> int | None:
    # Mirrors the existing worklistState openCount: the gate alone supplies verdicts.
    if (
        not worklist.bound
        or worklist.source == "absent"
        or worklist.state != "complete"
        or worklist.incomplete
    ):
        return None
    answers = [worklist.item_states.get(item["id"]) for item in items]
    if any(answer is None or answer.state == "undecided" for answer in answers):
        return None
    return sum(answer is not None and answer.state == "open" for answer in answers)


def summarize_worklist(worklist: ReviewWorklistView) -> ReviewWorklistSummary:
    planning: dict[str, str] = {}
    files: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in worklist.items:
        if "planning" in item:
            planning[item["subject"]] = f"{item['kind'].replace('_', ' ')} · {item['planning']}"
        facts = cast(dict[str, Any], item.get("facts", {}))
        path = facts.get("path")
        if item["kind"] in ("unexplained_hunk", "unexplained_file") and isinstance(path, str):
            files[path].append(item)
    counts = {
        path: count
        for path, items in files.items()
        if (count := _open_count(worklist, items)) is not None
    }
    return ReviewWorklistSummary(
        open_count=_open_count(worklist, worklist.items),
        planning=planning,
        file_open_counts=counts,
    )
