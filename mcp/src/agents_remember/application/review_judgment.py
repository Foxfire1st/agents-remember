"""The selected subject's judgments and proofs from the review's after memory tree."""

from __future__ import annotations

import apsw
from pydantic import ValidationError

from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_history import owner_rows, raw_subject
from agents_remember.application.review_judgment_proofs import judgment_proofs
from agents_remember.application.review_legacy_comparison import knowledge_unavailable_detail
from agents_remember.memory.knowledge_index import HistoryRow, IndexMismatchError, KnowledgeIndex
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review_judgment import ReviewJudgmentPane, ReviewJudgmentRow
from agents_remember.models.knowledge_files.documents import owner_history_attempt
from agents_remember.models.knowledge_files.history import FamilyRow, InvariantRow, parse_row

NONE_TEXT = "This leaf recorded no judgment about this record"


def review_judgment(
    resolved: ReviewCandidateResolution, selector: KnowledgeReadSeed | None
) -> ReviewJudgmentPane:
    """Read the selected record, never another subject's row or a superseded-only view."""

    addressed = getattr(selector, "invariant_id", None) or getattr(selector, "family_id", None)
    if addressed is None:
        return ReviewJudgmentPane(
            state="unavailable", detail="No knowledge subject was selected for this review"
        )
    legacy = knowledge_unavailable_detail(resolved) if resolved.trees is None else None
    if legacy is not None:
        return ReviewJudgmentPane(state="unavailable", detail=legacy)
    try:
        with KnowledgeIndex(resolved.candidate_database) as index:
            raw = raw_subject(index, addressed)
            if raw is None:
                with KnowledgeIndex(resolved.baseline_database) as before:
                    raw = raw_subject(index, addressed, identity_index=before)
            if raw is None:
                return ReviewJudgmentPane(
                    state="unavailable",
                    detail=f"{addressed!r} names no recorded invariant or family",
                )
            history = _answer(index, resolved.leaf_id, raw)
            return history.model_copy(update={"proofs": judgment_proofs(index, raw, resolved)})
    except (apsw.Error, IndexMismatchError, OSError) as error:
        return ReviewJudgmentPane(
            state="unavailable", detail=f"The after memory history cannot be read: {error}"[:20000]
        )


def _answer(index: KnowledgeIndex, owner: str, subject: str) -> ReviewJudgmentPane:
    rows, problem = owner_rows(index, owner)
    if problem is not None:
        return ReviewJudgmentPane(state="unavailable", detail=problem)
    assert rows is not None
    selected = [row for row in rows if row.subject in (subject, f"membership:{subject}")]
    try:
        displayed = tuple(_row(row) for row in sorted(selected, key=_order))
    except ValidationError as error:
        paths = ", ".join(dict.fromkeys(row.path for row in selected))
        return ReviewJudgmentPane(
            state="unavailable", detail=f"History {paths} cannot be read: {error}"[:20000]
        )
    return ReviewJudgmentPane(
        state="rows" if displayed else "none",
        rows=displayed,
        detail="The leaf's recorded judgments in attempt order." if displayed else NONE_TEXT,
    )


def _order(row: HistoryRow) -> tuple[int, str]:
    return owner_history_attempt(row.path, row.owner) or 1, row.id


def _row(row: HistoryRow) -> ReviewJudgmentRow:
    authored = parse_row(row.document)
    return ReviewJudgmentRow(
        attempt=_order(row)[0],
        row_id=row.id,
        subject=row.subject,
        disposition=row.disposition,
        effect=getattr(authored, "effect", None),
        revision=getattr(authored, "revision", None),
        reason=authored.reason,
        because=getattr(authored, "because", None) or (),
        covered_entries=len(authored.covers) if isinstance(authored, InvariantRow) else None,
        examined_members=len(authored.examined) if isinstance(authored, FamilyRow) else None,
        history_path=row.path,
        closed=row.closed,
    )
