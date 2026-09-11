"""Small task-owned transitions for the fixed-list review protocol."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agents_remember.kernel._agentic_settings_core import MAX_REVIEW_ROUNDS
from agents_remember.tasks import ReviewFinding, ReviewState, TaskDocument


class ReviewHistoryError(ValueError):
    """A review-state transition cannot advance the task document."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        super().__init__(detail)


def begin_task_review(
    document: TaskDocument,
    payload: dict[str, Any] | None = None,
) -> TaskDocument:
    """Start the next review round before reviewer work, or resume a pending round."""

    exception = _parse_developer_exception(payload)
    state = document.reviewState
    if state is None or state.round == 0:
        if exception is not None:
            raise ReviewHistoryError(
                "review-admission-invalid",
                "developer approval is only usable after the ordinary three-round limit",
            )
        return document.model_copy(update={"reviewState": ReviewState(round=1, pending=True)})
    if state.pending:
        # A replay of an admitted operation owns the same round.  A valid exception
        # supplied on that replay is deliberately ignored and cannot alter state.
        return document
    limit = MAX_REVIEW_ROUNDS + state.additionalRounds
    if state.round >= limit:
        if exception is None:
            raise ReviewHistoryError(
                "review-budget-exhausted",
                f"review budget exhausted: count={state.round}, limit={limit}; "
                "ask the developer for an explicit additional review-round authorization",
            )
        answer, allowance = exception
        return document.model_copy(
            update={
                "reviewState": state.model_copy(
                    update={
                        "round": state.round + 1,
                        "pending": True,
                        "developerApproval": answer,
                        "additionalRounds": state.additionalRounds + allowance,
                    }
                )
            }
        )
    if exception is not None:
        raise ReviewHistoryError(
            "review-admission-invalid",
            "developer approval is only usable after the ordinary review limit is reached",
        )
    return document.model_copy(
        update={"reviewState": state.model_copy(update={"round": state.round + 1, "pending": True})}
    )


def record_task_review(
    document: TaskDocument,
    payload: dict[str, Any],
) -> TaskDocument:
    """Record one review result while preserving the sealed first-review definitions."""

    state = document.reviewState
    if state is None:
        state = ReviewState(round=1, pending=True)
    _validate_publication_fields(payload)
    if state.round == 1 and not state.baselineFindings:
        updated = _record_baseline(state, payload)
    else:
        updated = _record_successor(state, payload)
    return document.model_copy(update={"reviewState": updated})


def require_pending_review(document: TaskDocument) -> ReviewState:
    """Return the task-owned review state; a document with none is round zero pending."""

    return document.reviewState or ReviewState(round=1, pending=True)


def _validate_publication_fields(payload: dict[str, Any]) -> None:
    allowed = {"verdict", "findings", "remainingFindingIds", "verdictRef"}
    unknown = set(payload).difference(allowed)
    if unknown:
        raise ReviewHistoryError(
            "review-publication-invalid",
            f"record_review received unsupported fields: {sorted(unknown)}",
        )
    verdict = payload.get("verdict")
    if verdict not in {"pass", "pass-with-notes", "block"}:
        raise ReviewHistoryError(
            "review-publication-invalid",
            "record_review requires verdict pass, pass-with-notes, or block",
        )


def _parse_developer_exception(
    payload: dict[str, Any] | None,
) -> tuple[str, int] | None:
    if payload is None or not payload:
        return None
    if not isinstance(payload, dict):
        raise ReviewHistoryError(
            "review-admission-invalid",
            "begin_review payload must be an object containing developerApproval and "
            "additionalRounds",
        )
    allowed = {"developerApproval", "additionalRounds"}
    unknown = set(payload).difference(allowed)
    if unknown:
        raise ReviewHistoryError(
            "review-admission-invalid",
            f"begin_review received unsupported fields: {sorted(unknown)}",
        )
    missing = allowed.difference(payload)
    if missing:
        raise ReviewHistoryError(
            "review-admission-invalid",
            "developer exception requires nonblank developerApproval and positive "
            f"additionalRounds; missing {sorted(missing)}",
        )
    answer = payload["developerApproval"]
    if not isinstance(answer, str) or not answer.strip():
        raise ReviewHistoryError(
            "review-admission-invalid",
            "developerApproval must be a nonblank recorded developer answer",
        )
    allowance = payload["additionalRounds"]
    if isinstance(allowance, bool) or not isinstance(allowance, int) or allowance <= 0:
        raise ReviewHistoryError(
            "review-admission-invalid",
            "additionalRounds must be a positive integer",
        )
    return answer.strip(), allowance


def _record_baseline(state: ReviewState, payload: dict[str, Any]) -> ReviewState:
    if "remainingFindingIds" in payload:
        raise ReviewHistoryError(
            "review-remaining-invalid",
            "the first result derives remainingFindingIds from its sealed findings",
        )
    raw = payload.get("findings", [])
    if not isinstance(raw, list):
        raise ReviewHistoryError("review-findings-invalid", "findings must be a list")
    try:
        findings = [ReviewFinding.model_validate(item) for item in raw]
    except (TypeError, ValidationError) as exc:
        raise ReviewHistoryError("review-findings-invalid", str(exc)) from exc
    ids = [item.findingId for item in findings]
    _require_unique_ids(ids, "review baseline finding IDs")
    if ids and payload.get("verdict") != "block":
        raise ReviewHistoryError(
            "review-unresolved-findings",
            "a first result with findings must remain blocking",
        )
    return state.model_copy(
        update={"pending": False, "baselineFindings": findings, "remainingFindingIds": ids}
    )


def _record_successor(state: ReviewState, payload: dict[str, Any]) -> ReviewState:
    if payload.get("findings"):
        raise ReviewHistoryError(
            "review-successor-new-findings",
            "successor rounds may verify the sealed list only; new findings are forbidden",
        )
    raw = payload.get("remainingFindingIds")
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ReviewHistoryError(
            "review-remaining-invalid",
            "successor results require remainingFindingIds as a string list",
        )
    remaining = [item.strip() for item in raw]
    _require_unique_ids(remaining, "review remaining finding IDs")
    prior = set(state.remainingFindingIds)
    baseline = {item.findingId for item in state.baselineFindings}
    if not set(remaining).issubset(prior):
        raise ReviewHistoryError(
            "review-remaining-invalid",
            "successor remaining IDs must be a subset of the preceding remaining set",
        )
    if not set(remaining).issubset(baseline):
        raise ReviewHistoryError(
            "review-remaining-invalid",
            "successor remaining IDs must belong to the sealed baseline",
        )
    if remaining and payload.get("verdict") != "block":
        raise ReviewHistoryError(
            "review-unresolved-findings",
            "a passing successor cannot leave findings unresolved",
        )
    return state.model_copy(update={"pending": False, "remainingFindingIds": remaining})


def _require_unique_ids(values: list[str], label: str) -> None:
    if any(not value.strip() for value in values):
        raise ReviewHistoryError("review-remaining-invalid", f"{label} cannot contain blank IDs")
    if len(values) != len(set(values)):
        raise ReviewHistoryError("review-remaining-invalid", f"{label} must be unique")


__all__ = [
    "ReviewHistoryError",
    "begin_task_review",
    "record_task_review",
    "require_pending_review",
]
