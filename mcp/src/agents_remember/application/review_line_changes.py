"""Line counts for full resolved ranges, using the reviewer's Myers edit-distance rule.

The 4,000-edit walk ceiling is transferred from the frontend calculator, not an excerpt limit.
Common heads/tails are removed first; empty remainders have exact cheap counts. Otherwise a walk
that cannot finish within the ceiling is unknown, never zero. No alignment or code text is retained.
"""

from collections.abc import Sequence

from agents_remember.models.knowledge.review_tree_entries import ReviewLineChanges

MAX_LINE_EDITS = 4000


def _lines(text: str) -> list[str]:
    text = text[:-1] if text.endswith("\n") else text
    return [] if text == "" else text.split("\n")


def line_changes(before: str, after: str) -> ReviewLineChanges | None:
    """Count shortest insertions/deletions with the former frontend's exact line semantics."""

    a, b = _lines(before), _lines(after)
    head = 0
    while head < min(len(a), len(b)) and a[head] == b[head]:
        head += 1
    tail = 0
    remaining = min(len(a), len(b)) - head
    while tail < remaining and a[len(a) - tail - 1] == b[len(b) - tail - 1]:
        tail += 1
    a = a[head : len(a) - tail]
    b = b[head : len(b) - tail]
    if not a or not b:
        return ReviewLineChanges(added=len(b), removed=len(a))
    distance = _edit_distance(a, b)
    if distance is None:
        return None
    common = (len(a) + len(b) - distance) // 2
    return ReviewLineChanges(added=len(b) - common, removed=len(a) - common)


def _edit_distance(a: Sequence[str], b: Sequence[str]) -> int | None:
    total = len(a) + len(b)
    if abs(len(a) - len(b)) > MAX_LINE_EDITS:
        return None
    furthest = [0] * (2 * total + 2)
    for distance in range(min(total, MAX_LINE_EDITS) + 1):
        for diagonal in range(-distance, distance + 1, 2):
            down = diagonal == -distance or (
                diagonal != distance
                and furthest[total + diagonal - 1] < furthest[total + diagonal + 1]
            )
            x = furthest[total + diagonal + 1] if down else furthest[total + diagonal - 1] + 1
            while x < len(a) and x - diagonal < len(b) and a[x] == b[x - diagonal]:
                x += 1
            furthest[total + diagonal] = x
            if x >= len(a) and x - diagonal >= len(b):
                return distance
    return None
