"""Friendly-label edits on the two identity rows, and the concurrency guard they carry.

A display label is the one field of an identity row that may legitimately change. Everything that
makes the identity an identity -- its ID, its namespace, and for a revision its sealed payload -- is
immutable, so a label edit is not a rewrite of the subject: it is a change to how a reader displays
it. Because it is the one mutable field, it is also the one place a stored row can differ from the
copy a caller read, which is why every label edit names the exact row it expects.

Both identity rows are shaped the same way (identity columns, a label, an authorship envelope), so
the guard, the update and the result shape live here once for both. Each edit has two entry points:
the operation, which owns the candidate lock and the transaction, and the in-transaction step, which
raises a typed refusal for the batch path to roll back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.memory.knowledge import families
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    SqliteFailureContext,
    scope_refusal,
    stale_expected_row_refusal,
    unknown_family_refusal,
    unknown_invariant_refusal,
)
from agents_remember.models.knowledge.result import (
    KnowledgeRefusal,
    SetFamilyLabelRequest,
    SetFamilyLabelResult,
    SetInvariantLabelRequest,
    SetInvariantLabelResult,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore


def set_invariant_label(
    store: OpenedKnowledgeStore, request: SetInvariantLabelRequest
) -> SetInvariantLabelResult:
    """Change one invariant's display label, holding the candidate lock and one transaction."""

    denied = scope_refusal("set_invariant_label", store.repository_id, request.repository_id)
    if denied is not None:
        return _invariant_label_refusal(request, denied)
    with store.exclusive_candidate_lock("set_invariant_label") as lock_refusal:
        if lock_refusal is not None:
            return _invariant_label_refusal(request, lock_refusal)
        return store.within_immediate(
            lambda: _apply_invariant_label(store, request),
            on_refusal=lambda refused: _invariant_label_refusal(request, refused),
            failure=SqliteFailureContext(
                operation="set_invariant_label",
                table="invariant",
                record_id=request.invariant_id,
            ),
        )


def apply_invariant_label(
    store: OpenedKnowledgeStore,
    *,
    invariant_id: str,
    display_label: str,
    expected_row_digest: str,
) -> bool:
    """Write one invariant label inside the caller's open transaction, or raise its refusal.

    Returns whether a statement ran. A requested label that is already the stored one writes
    nothing and answers ``False``, so a caller assembling a receipt can report what it touched
    instead of asserting a write that never happened.

    The stored row digest is the same value ``get_invariant`` exposes, so the caller's expectation
    is carried straight from a read instead of being recomputed by a second rule.
    """

    existing = store.get_invariant(invariant_id)
    if existing is None:
        raise KnowledgeRefused(unknown_invariant_refusal(invariant_id))
    label = display_label.strip()
    if existing.row_digest != expected_row_digest:
        raise KnowledgeRefused(
            stale_expected_row_refusal(
                operation="set_invariant_label",
                table="invariant",
                record_id=invariant_id,
                expected=expected_row_digest,
                observed=existing.row_digest,
            )
        )
    if existing.display_label == label:
        return False
    store.write(
        "UPDATE invariant SET display_label = ? WHERE repository_id = ? AND invariant_id = ?",
        (label, store.repository_id, invariant_id),
    )
    return True


def set_family_label(
    store: OpenedKnowledgeStore, request: SetFamilyLabelRequest
) -> SetFamilyLabelResult:
    """Change one family's display label, holding the candidate lock and one transaction."""

    denied = scope_refusal("set_family_label", store.repository_id, request.repository_id)
    if denied is not None:
        return _family_label_refusal(request, denied)
    with store.exclusive_candidate_lock("set_family_label") as lock_refusal:
        if lock_refusal is not None:
            return _family_label_refusal(request, lock_refusal)
        return store.within_immediate(
            lambda: _apply_family_label(store, request),
            on_refusal=lambda refused: _family_label_refusal(request, refused),
            failure=SqliteFailureContext(
                operation="set_family_label",
                table="family",
                record_id=request.family_id,
            ),
        )


def apply_family_label(
    store: OpenedKnowledgeStore,
    *,
    family_id: str,
    display_label: str,
    expected_row_digest: str,
) -> bool:
    """Write one family label inside the caller's open transaction, or raise its refusal.

    Returns whether a statement ran, on the same rule as the invariant twin.
    """

    existing = families.get_family(store, family_id)
    if existing is None:
        raise KnowledgeRefused(unknown_family_refusal(family_id))
    label = display_label.strip()
    if existing.row_digest != expected_row_digest:
        raise KnowledgeRefused(
            stale_expected_row_refusal(
                operation="set_family_label",
                table="family",
                record_id=family_id,
                expected=expected_row_digest,
                observed=existing.row_digest,
            )
        )
    if existing.display_label == label:
        return False
    store.write(
        "UPDATE family SET display_label = ? WHERE repository_id = ? AND family_id = ?",
        (label, store.repository_id, family_id),
    )
    return True


def _apply_invariant_label(
    store: OpenedKnowledgeStore, request: SetInvariantLabelRequest
) -> SetInvariantLabelResult:
    """Apply one invariant label change inside the caller's open transaction."""

    apply_invariant_label(
        store,
        invariant_id=request.invariant_id,
        display_label=request.display_label,
        expected_row_digest=request.expected_row_digest,
    )
    return SetInvariantLabelResult(
        state="labeled",
        repository_id=request.repository_id,
        invariant_id=request.invariant_id,
        display_label=request.display_label.strip(),
    )


def _apply_family_label(
    store: OpenedKnowledgeStore, request: SetFamilyLabelRequest
) -> SetFamilyLabelResult:
    """Apply one family label change inside the caller's open transaction."""

    apply_family_label(
        store,
        family_id=request.family_id,
        display_label=request.display_label,
        expected_row_digest=request.expected_row_digest,
    )
    return SetFamilyLabelResult(
        state="labeled",
        repository_id=request.repository_id,
        family_id=request.family_id,
        display_label=request.display_label.strip(),
    )


def _invariant_label_refusal(
    request: SetInvariantLabelRequest, refused: KnowledgeRefusal
) -> SetInvariantLabelResult:
    return SetInvariantLabelResult(
        state="refused",
        repository_id=request.repository_id,
        invariant_id=request.invariant_id,
        refusal=refused,
    )


def _family_label_refusal(
    request: SetFamilyLabelRequest, refused: KnowledgeRefusal
) -> SetFamilyLabelResult:
    return SetFamilyLabelResult(
        state="refused",
        repository_id=request.repository_id,
        family_id=request.family_id,
        refusal=refused,
    )


__all__ = [
    "apply_family_label",
    "apply_invariant_label",
    "set_family_label",
    "set_invariant_label",
]
