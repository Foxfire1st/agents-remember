"""The refusal vocabulary of the selective recorded-scope read.

One factory per observable failure point, in one module so the codes, the offending selector and
the advertised next action stay together instead of being spelled out at each call site. The shared
``refusal`` factory and the exception types stay in :mod:`refusals`; this module only names the
failures one bounded snapshot-bound read can produce.

The distinction the codes carry is the one a caller actually acts on, and it is not "did it work":

* **absence** (``selector_absent``, ``registration_absent``) -- the snapshot was read and holds no
  such record. That is a fact about the recorded graph, and it is explicitly *not* a statement that
  the path, identity or family has no obligations and not a finding of "no semantic impact". No
  Markdown, no working tree and no HEAD is consulted to fill the gap.
* **wrong snapshot** (``continuation_binding_mismatch``, ``candidate_snapshot_unpublished``,
  ``stale_precondition``, ``snapshot_unavailable``) -- the bytes are not the ones this request
  named. A page assembled from two revisions is not a page, so none of these returns partial items.
* **budget** (``page_budget_too_small``) -- the selection is valid and one indivisible item does not
  fit. The refusal carries the size that would fit it and leaves the position unchanged, because a
  budget is a presentation choice and must not be able to alter the selection.

Every one of them leaves the database byte-identical: this operation only ever issues ``SELECT``
statements on a connection it opened read-only, so "a refused read persisted nothing" is a property
of the read path rather than a rollback it has to remember to perform.
"""

from __future__ import annotations

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

# The one operation every factory here refuses.
_OPERATION: KnowledgeOperation = "read_knowledge_scope"


def selector_absent_refusal(
    *, record_id: str, kind: str, observed: str = "<absent in the selected snapshot>"
) -> KnowledgeRefusal:
    """Refuse a seed that names an identity or revision the selected snapshot does not hold.

    This is the *unknown record* case, and it is separate from ``registration_absent`` on purpose:
    a caller that named a family identity that does not exist has asked the wrong question, while a
    caller that named a path with no recorded claims has asked a right question whose answer is
    that nothing is recorded there yet.
    """

    return refusal(
        "selector_absent",
        _OPERATION,
        f"the selector names no {kind} in the selected snapshot, so there is no recorded scope to "
        f"select: {record_id}",
        facts=RefusalFacts(record_id=record_id, expected=kind, observed=observed),
        next_action=(
            "Select an identity or revision the snapshot records. This is a fact about this "
            "snapshot, not a statement that the obligation does not exist, and nothing was read "
            "from a working tree or a Markdown document to answer it."
        ),
    )


def registration_absent_refusal(*, path: str) -> KnowledgeRefusal:
    """Refuse a path seed that has no recorded realization claim in the selected snapshot.

    An unregistered path is absence, and the refusal says so with zero recorded counts. It is never
    reported as "no semantic impact": nothing here decided what the path means, only that no
    authored claim cites it in this snapshot.
    """

    return refusal(
        "registration_absent",
        _OPERATION,
        f"no realization claim is recorded at {path!r} in the selected snapshot, so this read "
        "reports absence rather than an empty scope",
        facts=RefusalFacts(record_id=path, expected="a recorded realization claim"),
        next_action=(
            "Author a realization claim for this path, or select a path that is recorded. Absence "
            "is not a semantic verdict: no Markdown fallback and no working-tree lookup is "
            "performed, and no claim was invented to fill the gap."
        ),
    )


def page_budget_too_small_refusal(
    *, minimum_utf8_bytes: int, requested_utf8_bytes: int, item_id: str
) -> KnowledgeRefusal:
    """Refuse a page budget that cannot hold even the next single item.

    The refusal carries the size that would fit it, and the caller's position is unchanged, so
    raising the budget re-reads the same item rather than skipping it. The selection is not
    narrowed and the item is not truncated: a governing statement and its conditions are one
    indivisible item.
    """

    return refusal(
        "page_budget_too_small",
        _OPERATION,
        f"the page budget of {requested_utf8_bytes} bytes cannot hold the next item "
        f"({item_id}), which needs {minimum_utf8_bytes} bytes",
        facts=RefusalFacts(
            record_id=item_id,
            expected=str(requested_utf8_bytes),
            observed=str(minimum_utf8_bytes),
        ),
        next_action=(
            "Re-issue the same request with a byte budget of at least the reported minimum. The "
            "selection, the snapshot and the position are unchanged, so the larger budget returns "
            "this item rather than the one after it."
        ),
    )


def continuation_binding_mismatch_refusal(
    *,
    detail: str,
    expected: str,
    observed: str,
    record_id: str = "<continuation>",
) -> KnowledgeRefusal:
    """Refuse a continuation presented against a snapshot, context, seed or policy it does not bind.

    A cursor is a position in one named snapshot of one selected set. Serving it elsewhere would
    append a page selected from one revision to a page selected from another, which is the exact
    mixing this refusal exists to make impossible.
    """

    return refusal(
        "continuation_binding_mismatch",
        _OPERATION,
        f"the continuation does not bind this read: {detail}",
        facts=RefusalFacts(record_id=record_id, expected=expected, observed=observed),
        next_action=(
            "Discard the continuation and start a new read against the snapshot you want. No "
            "partial page was returned, because a page assembled from two snapshots is not a page "
            "of either."
        ),
    )


def snapshot_unavailable_refusal(*, detail: str, expected: str, observed: str) -> KnowledgeRefusal:
    """Refuse a read whose selected snapshot cannot be obtained at all.

    The mirror of the two absence codes: nothing is known about the recorded graph here because the
    dataset itself is missing or unreadable. It is never answered as an empty scope, because an
    empty scope is a claim about a snapshot that was actually read.
    """

    return refusal(
        "snapshot_unavailable",
        _OPERATION,
        f"the selected snapshot is unavailable: {detail}",
        facts=RefusalFacts(expected=expected, observed=observed),
        next_action=(
            "Select a snapshot that exists and is readable, or produce it through the publication "
            "operation that owns it. No legacy document and no working-tree bytes are substituted "
            "for the missing snapshot."
        ),
    )


def selection_incomplete_refusal(*, item_count: int, bound: int) -> KnowledgeRefusal:
    """Refuse a selection that reached its declared execution bound before it was enumerated.

    It exists so the alternative -- emitting a partial selection with a total that was never
    computed -- is unrepresentable. A caller is told the bound it reached rather than a guessed
    total.
    """

    return refusal(
        "selection_incomplete",
        _OPERATION,
        f"the selected set reached the declared execution bound of {bound} items ({item_count} "
        "counted), so no complete manifest exists for it",
        facts=RefusalFacts(expected=f"at most {bound} items", observed=str(item_count)),
        next_action=(
            "Narrow the seed to a scope inside the bound. No total, no complete-scope claim and no "
            "partial manifest was emitted."
        ),
    )
