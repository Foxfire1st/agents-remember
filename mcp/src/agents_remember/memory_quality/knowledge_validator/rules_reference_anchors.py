"""MIK-R198 rule 5: the publication guard over code and test reference anchors.

Every reference anchor of a converted memory tree must name the blob of its source path **in the
exact paired code tree** and must resolve there to its recorded content identity. Nothing less
publishes: there is no inherited baseline, no stale-content exemption and no count allowance. A
content-current anchor whose blob moved still refuses -- the blob is what binds the claim to the
bytes it was written against (``memory_quality/reference_state.py``, MIK-R03's entry rule).

The rule reuses the product's own traversal and resolution (:mod:`agents_remember.memory_quality.
reference_anchors`) rather than a parallel scanner, and it is registered in the shared validator, so
every commit, landing and quality route runs it: APPLY/closeout, the integration gate, direct and
recorded landing, a sync merge, the writer and the curator's validation command.

It carries the two reporting dispositions MIK-R198 rule 7 requires:

* ``writer_reports`` -- authoring reports the debt instead of refusing, so a leaf can break an anchor
  and repair it before closeout, while no publication route may treat it as a report;
* ``merge_reports`` -- a sync merge reports it, because a merge can create the state from two valid
  sides and it is repaired only after the merge exists; the next publication refuses it.
"""

from __future__ import annotations

from collections.abc import Iterator

from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    merge_report_note,
    merge_reports_at,
    register_rule,
)
from agents_remember.memory_quality.reference_anchors import (
    REFERENCE_ANCHOR_RULE,
    observations_of,
)


def _message(observation_state: str, observation_reason: str, where: str) -> str:
    return (
        f"reference anchor is {observation_state}: {observation_reason}; it must name the paired "
        f"code tree's blob and its recorded content identity ({where}), MIK-R198 rule 5"
    )


def check_reference_anchors(context: ValidationContext) -> Iterator[Finding]:
    """Refuse every reference anchor that does not hold at this validation's paired code tree."""

    code = context.code
    if code is None:
        return
    where = f"measured at {code.label} for {context.candidate.label}"
    reporting = merge_reports_at(context)
    for observation in observations_of(context.parsed.sidecars, code):
        if observation.current:
            continue
        message = _message(observation.state, observation.reason, where)
        if reporting:
            message += f"; {merge_report_note(context)}"
        yield Finding(observation.sidecar, observation.field, message)


register_rule(
    ValidationRule(
        REFERENCE_ANCHOR_RULE,
        "MIK-R198 rule 5",
        "code and test reference anchors hold at the exact paired code tree",
        check_reference_anchors,
        writer_reports=True,
        merge_reports=True,
    )
)
