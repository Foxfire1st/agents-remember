"""Product-written decision-log records (MIK-R126 rule 6 under the lifecycle-records ruling).

An entry the product writes itself is a record of an act, not a decision: the
projection marks it from the writers' own constants, the page matches no text, and the
stored entry keeps its shape. These cases pin the predicate and the finalizer's entry.
"""

from __future__ import annotations

from agents_remember.tasks.document import (
    FINALIZE_TASK_LIFECYCLE_DECISION,
    REMOVED_STEP_DECISION_PREFIX,
    SKIP_STEP_DECISION_PREFIX,
    is_product_record_decision,
)
from agents_remember.worktrees.modules.finalize_task_documents import _finalized_decisions


def test_writer_constants_are_marked_and_authored_text_is_not() -> None:
    assert is_product_record_decision(FINALIZE_TASK_LIFECYCLE_DECISION) is True
    assert is_product_record_decision(f"{REMOVED_STEP_DECISION_PREFIX}S1.") is True
    assert is_product_record_decision(f"{SKIP_STEP_DECISION_PREFIX}S2.") is True
    assert is_product_record_decision("Start L126 now under D183.") is False
    assert is_product_record_decision("Finalize task lifecycle") is False
    assert is_product_record_decision("Removed steppe.") is False
    assert is_product_record_decision("") is False


def test_finalizer_entry_keeps_its_shape_and_is_marked() -> None:
    [first, *_] = _finalized_decisions({"decisions": []})
    assert set(first) == {"at", "decision", "rationale"}
    assert first["decision"] == "Finalize task lifecycle."
    assert is_product_record_decision(first["decision"]) is True
