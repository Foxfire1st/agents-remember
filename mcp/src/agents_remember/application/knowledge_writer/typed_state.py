"""The refusal of a record state typed into a hand-off.

Whether a revision is proposed, reviewed or consolidated is derived by the product from the history
files of the tree it reads; ``accepted`` is an earlier format's word that names no state, and
nothing is kept beside the state in its place. None of the four words is a ``status`` a hand-off
names, in an entry or in a record's ``fields``. The words a kind has for its own life stay the
curator's: ``retired`` for every kind that retires, and a decision's ``active`` and
``under_reconsideration``.

This refusal covers the hand-off's ``status`` keys only; what a stored record already holds is the
conversion's to remove, and the writer drops an old word when it rewrites the record.
"""

from __future__ import annotations

from typing import Any, Final

TYPED_STATE_WORDS: Final = ("proposed", "accepted", "reviewed", "consolidated")


def typed_state_refusal(value: Any) -> str | None:
    """Why ``value`` is refused as a hand-off ``status``; ``None`` when it is no state word."""

    if value not in TYPED_STATE_WORDS:
        return None
    return (
        f"{value!r} is a record's state, and the product sets a record's state: no tool input "
        "names it. Leave 'status' out; name it only to retire a record ('retired'), or for a "
        "decision's own 'active' or 'under_reconsideration'"
    )
