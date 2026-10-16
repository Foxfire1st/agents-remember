"""Small task-owned transitions for the fixed-list review protocol."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from agents_remember.kernel._agentic_settings_core import DEFAULT_LOOP_MAX_ROUNDS
from agents_remember.tasks import ReviewApproval, ReviewFinding, ReviewState, TaskDocument
from agents_remember.tasks.route_review import ReviewRound

_CANDIDATE_TREE_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")


class ReviewHistoryError(ValueError):
    """A review-state transition cannot advance the task document."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        super().__init__(detail)


def begin_task_review(
    document: TaskDocument,
    payload: dict[str, Any] | None = None,
    *,
    limit: int | None = None,
) -> TaskDocument:
    """Start the next review round before reviewer work, or resume a pending round.

    ``limit`` is the settings number in force when the round is begun
    (``orchestration.loops.defaults.maxRounds``); callers that do not name one
    take the default. The stored task limit is recomputed here on every begin
    as that number plus the task's approved extra rounds, so a changed settings
    number holds for the next round.
    """

    _require_readable_review(document)
    settings_number = DEFAULT_LOOP_MAX_ROUNDS if limit is None else limit
    lane = review_state_field(payload)
    exception = _parse_approval_exception(
        {key: value for key, value in payload.items() if key != "reviewKind"} if payload else None
    )
    state = getattr(document, lane)
    if state is None or state.round == 0:
        if exception is not None:
            raise ReviewHistoryError(
                "review-admission-invalid",
                "an approval is only usable after the settings review limit is "
                f"reached (count=0, limit={settings_number}); open the first "
                "round with an empty review object",
            )
        return document.model_copy(
            update={lane: ReviewState(round=1, pending=True, limit=settings_number)}
        )
    if state.pending:
        # A replay of an admitted operation owns the same round.  A valid exception
        # supplied on that replay is deliberately ignored and cannot alter state.
        return document
    task_limit = settings_number + state.additionalRounds
    if state.round >= task_limit:
        if exception is None:
            raise ReviewHistoryError(
                "review-budget-exhausted",
                f"review budget exhausted: count={state.round}, limit={task_limit}; "
                "ask the task's architect for a further round (or the developer) "
                "and record it with a begin_review approval naming who gave it "
                "and holding the words",
            )
        approval, allowance = exception
        return document.model_copy(
            update={
                lane: state.model_copy(
                    update={
                        "round": state.round + 1,
                        "pending": True,
                        "limit": settings_number + state.additionalRounds + allowance,
                        "approval": approval,
                        "developerApproval": None,
                        "additionalRounds": state.additionalRounds + allowance,
                    }
                )
            }
        )
    if exception is not None:
        raise ReviewHistoryError(
            "review-admission-invalid",
            "an approval is only usable after the settings review limit is reached "
            f"(count={state.round}, limit={task_limit}); open the round with an "
            "empty review object",
        )
    return document.model_copy(
        update={
            lane: state.model_copy(
                update={"round": state.round + 1, "pending": True, "limit": task_limit}
            )
        }
    )


def record_task_review(
    document: TaskDocument,
    payload: dict[str, Any],
    *,
    settings_limit: int | None = None,
    actual_candidate: str | None = None,
) -> TaskDocument:
    """Record one review result while preserving the sealed first-review definitions.

    ``settings_limit`` is the settings number in force, supplied by the task-doc
    layer (which refuses the call when the settings file cannot be read). It
    backfills the limit of a first record and of a pre-limit record; a stored
    post-R116 limit is never rewritten by a record. ``actual_candidate`` is the
    authority-resolved candidate identity for the task's candidate root,
    supplied by the task-doc layer; ``None`` means unresolvable or unproven and
    admits no same-round record. The binding is persisted on every recorded
    round; the same-round guard compares bindings, never caller strings.
    """

    _require_readable_review(document)
    lane = review_state_field(payload)
    state = getattr(document, lane)
    if state is None:
        state = ReviewState(round=1, pending=True)
    if settings_limit is not None and state.limit == 0:
        state = state.model_copy(update={"limit": settings_limit + state.additionalRounds})
    candidate = _parse_candidate_tree(payload)
    _validate_publication_fields(payload)
    if state.round == 1 and not state.baselineFindings and state.pending:
        updated = _record_baseline(state, payload)
    elif state.pending:
        updated = _record_successor(state, payload)
    else:
        # MIK-R116 rule 5 (L116-F01): a check of a repair to the Worker's
        # record alone, with the candidate unchanged, is no round. The same
        # Reviewer records the repaired record on the round it last recorded;
        # begin_review is not called and the count stays. The remaining findings
        # can only shrink, as in every later result. The guard proves the
        # actual byte-identical candidate through the recorded authority
        # binding: a replayed or forged tree string, an absent prior identity,
        # or an unresolvable current identity refuses with begin_review named,
        # before anything is written.
        _require_actual_unchanged(state, actual_candidate)
        _require_unchanged_candidate(state, candidate)
        updated = _record_successor(state, payload)
    if candidate is not None:
        updated = updated.model_copy(update={"candidateTree": candidate})
    if actual_candidate is not None:
        updated = updated.model_copy(update={"candidateIdentity": actual_candidate})
    updated = _with_recorded_round(state, updated, payload)
    return document.model_copy(update={lane: updated})


def _require_readable_review(document: TaskDocument) -> None:
    """Launch recovery is read-only authority, never permission to replace damaged evidence."""
    if document.review_read_error:
        raise ReviewHistoryError(
            "review-state-unreadable",
            f"review-state-unreadable: repair the task's unreadable review evidence before begin_review or record_review: {document.review_read_error}",
        )


def review_state_field(payload: dict[str, Any] | None) -> str:
    """Only an explicit memory kind selects the independent memory sequence."""
    if payload is not None and not isinstance(payload, dict):
        raise ReviewHistoryError("review-admission-invalid", "review payload must be an object")
    kind = (payload or {}).get("reviewKind", "code")
    if not isinstance(kind, str) or kind not in {"code", "memory"}:
        raise ReviewHistoryError("review-kind-invalid", "reviewKind must be code or memory")
    return "memoryReviewState" if kind == "memory" else "reviewState"


def role_review_state(document: TaskDocument, role: str) -> ReviewState | None:
    """Return the persisted lane for a counted role; never reinterpret legacy code rows."""
    return {"worker": document.reviewState, "curator": document.memoryReviewState}.get(role)


def _with_recorded_round(
    state: ReviewState, updated: ReviewState, payload: dict[str, Any]
) -> ReviewState:
    if not state.pending:
        prior = next((row for row in state.recordedRounds if row.round == state.round), None)
        if prior and payload.get("reviewKind", prior.kind) != prior.kind:
            raise ReviewHistoryError(
                "review-kind-invalid",
                "A record-only check cannot change the round's reviewKind; use begin_review for new review work.",
            )
        if prior and payload.get("verdictRef") is not None:
            latest = ReviewRound.model_validate(
                {**prior.model_dump(), "verdictRef": payload["verdictRef"]}
            )
            return updated.model_copy(
                update={
                    "recordedRounds": [
                        latest if row.round == prior.round else row for row in state.recordedRounds
                    ]
                }
            )
        return updated
    kind = payload.get("reviewKind", "code")
    row = ReviewRound(
        round=state.round,
        kind=kind,
        findingsRemain=bool(updated.remainingFindingIds),
        verdictRef=payload.get("verdictRef"),
    )
    return updated.model_copy(update={"recordedRounds": [*state.recordedRounds, row]})


def review_count_unavailable(state: ReviewState | None) -> str | None:
    """Diagnose absent/incomplete typed history without inferring historical kinds."""
    if state is None:
        return None
    expected = state.round - int(state.pending)
    rows = state.recordedRounds
    if len(rows) != expected or any(row.round != index for index, row in enumerate(rows, 1)):
        return (
            "reviewState.recordedRounds lacks a complete ordered typed record for every "
            f"completed round (expected {expected}, found {len(rows)}); historical code/memory tries are unavailable."
        )
    return None


def failed_role_tries(document: TaskDocument, role: str) -> int | None:
    """Read failed code/Worker or memory/Curator rounds, never count record checks."""
    if document.review_read_error and role in {"worker", "curator"}:
        return None
    return failed_review_tries(role_review_state(document, role), role)


def failed_review_tries(state: ReviewState | None, role: str) -> int | None:
    """A missing typed historical round makes a counted role's total unknown, not zero."""
    kind = {"worker": "code", "curator": "memory"}.get(role)
    if state is None or kind is None:
        return 0
    if review_count_unavailable(state):
        return None
    return sum(row.kind == kind and row.findingsRemain for row in state.recordedRounds)


def require_pending_review(document: TaskDocument) -> ReviewState:
    """Return the task-owned review state; a document with none is round zero pending."""

    return document.reviewState or ReviewState(round=1, pending=True)


def _require_actual_unchanged(state: ReviewState, actual: str | None) -> None:
    """Refuse a record-only result that cannot prove the actual candidate unchanged.

    Proof is the authority-resolved binding the round was recorded on: it must
    be present, the current identity must resolve, and the two must be equal.
    Anything else — a changed candidate, an absent prior binding, an
    unresolvable current identity — refuses before anything is written.
    """
    recorded = state.candidateIdentity
    if recorded is not None and actual is not None and actual == recorded:
        return
    raise ReviewHistoryError(
        "review-candidate-changed",
        "the candidate cannot be proven unchanged since this round was recorded; "
        "open the next round with begin_review before recording a result, and "
        "record the new candidate there",
    )


def _require_unchanged_candidate(state: ReviewState, candidate: str | None) -> None:
    """Refuse a record-only result that names a different checked candidate."""
    recorded = state.candidateTree
    if recorded is None or candidate == recorded:
        return
    raise ReviewHistoryError(
        "review-candidate-changed",
        "the result names a different checked candidate than this round was "
        "recorded on; open the next round with begin_review before recording "
        "a result on the new candidate",
    )


def _parse_candidate_tree(payload: dict[str, Any]) -> str | None:
    """Read the optional candidate identity a result was checked against."""
    if "candidateTree" not in payload:
        return None
    candidate = payload["candidateTree"]
    if not isinstance(candidate, str) or not _CANDIDATE_TREE_PATTERN.fullmatch(candidate.strip()):
        raise ReviewHistoryError(
            "review-candidate-invalid",
            "candidateTree must be the lowercase hex Git tree of the checked "
            "candidate (40 to 64 characters)",
        )
    return candidate.strip()


def _validate_publication_fields(payload: dict[str, Any]) -> None:
    allowed = {
        "verdict",
        "findings",
        "remainingFindingIds",
        "verdictRef",
        "candidateTree",
        "reviewKind",
    }
    if payload.get("reviewKind", "code") not in {"code", "memory"}:
        raise ReviewHistoryError("review-kind-invalid", "reviewKind must be code or memory")
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


def _parse_approval_exception(
    payload: dict[str, Any] | None,
) -> tuple[ReviewApproval, int] | None:
    """Read a beyond-limit approval: the architect's or the developer's.

    Accepts the ``approval`` shape (``by`` architect/developer plus ``text``) and
    the earlier ``developerApproval`` words, each with ``additionalRounds``. A
    legacy approval is recorded as ``approval`` with ``by`` developer.
    """
    if payload is None or not payload:
        return None
    if not isinstance(payload, dict):
        raise ReviewHistoryError(
            "review-admission-invalid",
            "begin_review payload must be an object containing an approval "
            "(approval or developerApproval) and additionalRounds",
        )
    allowed = {"approval", "developerApproval", "additionalRounds"}
    unknown = set(payload).difference(allowed)
    if unknown:
        raise ReviewHistoryError(
            "review-admission-invalid",
            f"begin_review received unsupported fields: {sorted(unknown)}",
        )
    if "approval" in payload and "developerApproval" in payload:
        raise ReviewHistoryError(
            "review-admission-invalid",
            "begin_review takes a single approval: approval or developerApproval, not both",
        )
    if "additionalRounds" not in payload:
        raise ReviewHistoryError(
            "review-admission-invalid",
            "a beyond-limit approval requires nonblank approval words and positive "
            "additionalRounds",
        )
    if "approval" in payload:
        approval = _parse_approval_shape(payload["approval"])
    else:
        answer = payload.get("developerApproval")
        if not isinstance(answer, str) or not answer.strip():
            raise ReviewHistoryError(
                "review-admission-invalid",
                "developerApproval must be a nonblank recorded developer answer",
            )
        approval = ReviewApproval(by="developer", text=answer.strip())
    allowance = payload["additionalRounds"]
    if isinstance(allowance, bool) or not isinstance(allowance, int) or allowance <= 0:
        raise ReviewHistoryError(
            "review-admission-invalid",
            "additionalRounds must be a positive integer",
        )
    return approval, allowance


def _parse_approval_shape(raw: Any) -> ReviewApproval:
    if not isinstance(raw, dict):
        raise ReviewHistoryError(
            "review-admission-invalid",
            "approval must be an object with by (architect or developer) and text",
        )
    unknown = set(raw).difference({"by", "text"})
    if unknown:
        raise ReviewHistoryError(
            "review-admission-invalid",
            f"approval received unsupported fields: {sorted(unknown)}",
        )
    if raw.get("by") not in {"architect", "developer"}:
        raise ReviewHistoryError(
            "review-admission-invalid",
            "approval.by must be architect or developer",
        )
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ReviewHistoryError(
            "review-admission-invalid",
            "approval.text must be the nonblank recorded words of the approval",
        )
    return ReviewApproval(by=raw["by"], text=text.strip())


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
