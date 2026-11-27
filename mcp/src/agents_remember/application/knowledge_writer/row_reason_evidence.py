"""The foreign-evidence segments a row's reason carries across rewrites (MIK-R53 rule 8).

When a run updates a record another owner authored, the evidence of that update is stored in this
owner's history row about the record, appended to the row's ``reason`` as one
``" Evidence (<hand-off entry>): ..."`` segment per update. A later rewrite of the row keeps every
segment a prior run appended, byte-exact and never twice.
"""

from __future__ import annotations

from typing import Final

EVIDENCE_MARKER: Final = " Evidence ("


def carried_evidence_segments(reason: str) -> list[str]:
    """Previously appended foreign-evidence segments of a stored row reason.

    The writer is the sole producer of ``" Evidence ("`` segments, so scanning for the marker
    recovers every segment a prior run appended byte-exact -- including a trailing empty one,
    where an accepted reason ends in the bare marker and the split form would drop that suffix on
    a later reason-only rewrite.
    """

    segments: list[str] = []
    start = 0
    while (found := reason.find(EVIDENCE_MARKER, start)) >= 0:
        end = reason.find(EVIDENCE_MARKER, found + len(EVIDENCE_MARKER))
        segments.append(reason[found:] if end < 0 else reason[found:end])
        start = found + len(EVIDENCE_MARKER)
    return segments


def with_carried_evidence(reason: str, carry: str | None) -> str:
    """``reason`` with every segment of the stored reason ``carry`` it does not already hold."""

    if not carry:
        return reason
    for segment in carried_evidence_segments(carry):
        if segment not in reason or (segment == EVIDENCE_MARKER and not reason.endswith(segment)):
            reason = f"{reason}{segment}"
    return reason
