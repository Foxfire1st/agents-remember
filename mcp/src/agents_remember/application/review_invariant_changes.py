"""Which invariants differ between the comparison a page shows and a newer one (D576).

When a leaf moves, the page refreshes only the invariants that changed and leaves every other one
as it is, with no read. One function names them, and the marks, the refresh and any count all
follow from it. An invariant differs between two comparisons when:

1. it is in one and not the other;
2. its record's bytes changed: the blob of ``knowledge/invariants/<id>-*.json`` in the two memory
   candidate trees (statement, applicability, status, ...);
3. its overview row changed: every field of :class:`ReviewSubjectSummary` is compared --
   counts, changed paths, currentness, proofs and ``content``, the identity of the invariant's
   entries with their sides -- unless a test has shown a field to be volatile; none has.

Nothing positional enters: rows are compared by their invariant key and records by their id, so a
pure reorder differs in nothing. The inputs are the two comparisons' overview answers -- kept
answers the page and the build already ask for, recomputed when one is not held -- and the two
trees' record listings: two reads per comparison, whatever the number of invariants.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.review_tree_overview import (
    ReviewInvariantChanges,
    ReviewOverview,
)
from agents_remember.models.knowledge.review_trees import ReviewTreesResult

__all__ = [
    "ReviewInvariantChanges",
    "invariant_record_blobs",
    "invariants_that_differ",
    "review_invariant_changes",
]

#: The fields of an overview row that change with every computation of the same entries and so
#: are not compared. An exclusion goes in only with the test that shows the field volatile.
VOLATILE_ROW_FIELDS: frozenset[str] = frozenset()

_RECORD = re.compile(r"^knowledge/invariants/(INV-[0-9A-Z]+)-[^/]*\.json$")


def invariants_that_differ(
    before: ReviewOverview,
    after: ReviewOverview,
    records_before: Mapping[str, str],
    records_after: Mapping[str, str],
) -> tuple[str, ...]:
    """The keys of the invariants that differ, sorted; the one rule of this module's docstring."""

    def rows(overview: ReviewOverview) -> dict[str, dict[str, object]]:
        return {
            row.invariant_key: row.model_dump(mode="json", exclude=set(VOLATILE_ROW_FIELDS))
            for row in overview.subjects
        }

    shown, newer = rows(before), rows(after)
    keys = shown.keys() | newer.keys() | records_before.keys() | records_after.keys()
    return tuple(
        sorted(
            key
            for key in keys
            if shown.get(key) != newer.get(key) or records_before.get(key) != records_after.get(key)
        )
    )


def invariant_record_blobs(repository: Path, tree: str) -> dict[str, str] | None:
    """Each invariant record's blob in ``tree``, by its key; ``None`` when the tree is unreadable."""

    try:
        listed = run_git(repository, ["ls-tree", "-r", "-z", tree, "--", "knowledge/invariants"])
    except (OSError, RuntimeError):
        return None
    if listed.returncode != 0:
        return None
    blobs: dict[str, str] = {}
    for row in listed.stdout.split("\0"):
        head, _, path = row.partition("\t")
        fields = head.split()
        matched = _RECORD.match(path)
        if matched and len(fields) == 3 and fields[1] == "blob":
            blobs[text_uuid("identity", matched.group(1))] = fields[2]
    return blobs


def review_invariant_changes(
    overview: Callable[[int], ReviewTreesResult],
    leaf: tuple[str, str, str],
    shown: int,
    newer: int,
) -> ReviewInvariantChanges:
    """Name the invariants that differ between comparison ``shown`` and comparison ``newer``.

    ``overview`` is the page's own overview read of one comparison of ``leaf``. Anything that
    cannot be read is ``unavailable`` -- never an empty list, which would claim nothing differs.
    """

    del leaf  # the overview port is the leaf's; the triple names the question in the route's log
    answers = (overview(shown), overview(newer))
    records: list[dict[str, str]] = []
    overviews: list[ReviewOverview] = []
    for number, answer in zip((shown, newer), answers, strict=True):
        record = answer.comparison
        if (
            answer.state != "trees"
            or answer.overview is None
            or answer.overview.state != "ready"
            or record is None
            or record.number != number
        ):
            return _unavailable(shown, newer, f"comparison #{number} has no readable overview")
        blobs = invariant_record_blobs(
            Path(record.memory_candidate.repository), record.memory_candidate.tree
        )
        if blobs is None:
            return _unavailable(shown, newer, f"comparison #{number}'s records cannot be listed")
        records.append(blobs)
        overviews.append(answer.overview)
    return ReviewInvariantChanges(
        state="named",
        from_comparison=shown,
        to_comparison=newer,
        invariants=invariants_that_differ(overviews[0], overviews[1], records[0], records[1]),
    )


def _unavailable(shown: int, newer: int, reason: str) -> ReviewInvariantChanges:
    return ReviewInvariantChanges(
        state="unavailable", from_comparison=shown, to_comparison=newer, reason=reason
    )
