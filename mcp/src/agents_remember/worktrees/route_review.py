"""Task-bound independent route-review evidence for code closeout admission."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.tasks import RouteReviewRecord
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.modules.git import require_git, worktree_candidate_tree
from agents_remember.worktrees.worktree_contract import WorktreeContract


class RouteReviewError(ValueError):
    """The leaf lacks a passing review for its exact current candidate tree."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        super().__init__(detail)


def code_candidate_tree(contract: WorktreeContract) -> str:
    return worktree_candidate_tree(
        contract.code_worktree,
        contract.worktree_group / "reports" / ".route-review-candidate.index",
    )


def code_change_present(contract: WorktreeContract) -> bool:
    """Whether the full current candidate differs from the leaf's accepted base tree."""
    candidate = code_candidate_tree(contract)
    base_tree = require_git(
        contract.code_worktree,
        ["rev-parse", f"{contract.code_base_commit}^{{tree}}"],
    )
    return candidate != base_tree


def build_route_review(
    contract: WorktreeContract,
    task_root: Path,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> RouteReviewRecord:
    """Validate reviewer-authored evidence and stamp the current tree/time in the plane."""
    expected = {"verdict", "verdictRef", "routes"}
    unknown = set(payload) - expected
    if unknown:
        raise RouteReviewError(
            "route-review-invalid",
            "record_route_review accepts only verdict, verdictRef, and routes; "
            f"the plane owns candidateTree and reviewedAt (unknown: {sorted(unknown)})",
        )
    if contract.kind != "leaf":
        raise RouteReviewError("route-review-invalid-altitude", "route review belongs to a leaf")
    try:
        record = RouteReviewRecord.model_validate(
            {
                **payload,
                "candidateTree": code_candidate_tree(contract),
                "reviewedAt": (now or datetime.now(UTC)).replace(microsecond=0).isoformat(),
            }
        )
    except ValidationError as exc:
        raise RouteReviewError("route-review-invalid", str(exc)) from exc
    _require_evidence_files(task_root, record)
    return record


def require_current_route_review(contract: WorktreeContract) -> dict[str, object]:
    """Return current passing evidence or refuse before curator/closeout work begins."""
    if not code_change_present(contract):
        return {"required": False, "status": "not-required-no-code-change"}
    found = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    if found is None:
        raise RouteReviewError(
            "route-review-task-document-missing",
            f"leaf {contract.leaf_id!r} has no task document for route-review evidence",
        )
    _path, document = found
    review = document.routeReview
    if review is None:
        raise RouteReviewError(
            "route-review-required",
            "the current code change has no independent route-review record",
        )
    if review.verdict == "block":
        raise RouteReviewError(
            "route-review-blocked",
            f"independent route review blocks this candidate; see {review.verdictRef}",
        )
    current = code_candidate_tree(contract)
    if review.candidateTree != current:
        raise RouteReviewError(
            "route-review-stale",
            "the code candidate changed after independent route review; rerun route review "
            f"(reviewed {review.candidateTree}, current {current})",
        )
    _require_evidence_files(contract.task_root, review)
    return {
        "required": True,
        "status": "current",
        "candidateTree": current,
        "verdict": review.verdict,
        "verdictRef": review.verdictRef,
        "routeCount": len(review.routes),
    }


def _require_evidence_files(task_root: Path, review: RouteReviewRecord) -> None:
    refs = {review.verdictRef, *(route.evidenceRef for route in review.routes)}
    root = task_root.resolve()
    for ref in sorted(refs):
        supplied = Path(ref)
        if supplied.is_absolute():
            raise RouteReviewError(
                "route-review-evidence-outside-task",
                f"route-review evidence must use a task-relative path: {ref}",
            )
        resolved = (root / supplied).resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise RouteReviewError(
                "route-review-evidence-outside-task",
                f"route-review evidence escapes the task root: {ref}",
            )
        if not resolved.is_file():
            raise RouteReviewError(
                "route-review-evidence-missing",
                f"route-review evidence does not exist: {ref}",
            )
