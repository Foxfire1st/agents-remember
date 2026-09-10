"""A certification-admission refusal keeps its route-review status and remedy.

The closeout *transport* no longer refuses on a route-review record: the gate was removed,
so `worktree_closeout_preview` and `worktree_closeout_apply` close out on the trifecta,
the commit messages and ancestry alone (pinned by
`test_atomic_master_review_public.py::test_closeout_never_gates_on_a_route_review_record_at_either_altitude`).
What remains here is the payload shaping for a refusal that some other caller raises.
"""

from __future__ import annotations

from typing import cast

from agents_remember.application.lifecycle.certification_refusal import (
    certification_admission_refusal,
)
from agents_remember.errors import CertificationContractError


def test_certification_admission_refusal_keeps_route_review_status() -> None:
    error = CertificationContractError(
        "selected certification route-review authority refused",
        (
            {
                "code": "route-review-required",
                "path": "routeReview",
                "detail": "the current code change has no independent route-review record",
            },
        ),
    )

    payload = certification_admission_refusal("worktree_closeout_apply", error)

    assert payload["status"] == "route-review-required"
    assert payload["detail"] == "the current code change has no independent route-review record"
    assert payload["nextAction"] == "record_route_review"
    next_step = cast("dict[str, object]", payload["nextStep"])
    assert next_step["nextOperation"] == "record_route_review"
    assert "nextTool" not in next_step
