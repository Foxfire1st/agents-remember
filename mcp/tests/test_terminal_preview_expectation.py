"""A cleanup *preview* and the cleanup it previews must agree about the same collection.

D-15 measured the failure this lane pins: ``lifecycle_finalize_task`` with ``dry_run=true`` returned
``state: cleanup-blocked`` on a ``driftSnapshot`` blocker that carried no reason, while the real
finalize on the same contract completed and reclaimed everything. The cause was one missing keyword:
every sibling collection in ``terminal_result_blockers`` propagates ``preview=result.preview`` into
its ``TerminalExpectation``, while the ``driftSnapshot`` branch did not. A preview's drift-snapshot
entry answers ``would_remove`` (it does not answer ``would_delete``), so read with the real call's
pending key it looked like an entry that reclaimed nothing, and ``_blocker`` refused to invent the
missing reason -- correctly, because a blockage an operator cannot read is not a blockage.

These cases drive the real validator over the three shapes the production producer emits
(``kernel/primitives/drift_snapshot._remove_snapshot_file``): a preview that *would* remove, a real
removal, and a real failure that carries its reason. The third is the guard against "fixing" this
by making the collection benign: a genuine drift-snapshot failure must still block, and must still
name the component and the reason.
"""

from __future__ import annotations

from agents_remember.worktrees.modules.terminal_validation import (
    TerminalResult,
    terminal_result_blockers,
)


def _result(
    snapshot: dict[str, object],
    *,
    preview: bool,
) -> TerminalResult:
    """One terminal result whose only non-empty collection is the drift snapshot."""

    return TerminalResult(
        providers={"state": "torn-down"},
        worktrees={},
        branches={},
        directories={},
        drift_snapshots={"code": snapshot},
        preview=preview,
    )


def _preview_snapshot() -> dict[str, object]:
    return {
        "path": "/tmp/coordination/drift/code.json",
        "repository": "code",
        "branch": "ar/leaf",
        "removed": False,
        "would_remove": True,
    }


def _removed_snapshot() -> dict[str, object]:
    return {
        "path": "/tmp/coordination/drift/code.json",
        "repository": "code",
        "branch": "ar/leaf",
        "removed": True,
    }


def _absent_snapshot() -> dict[str, object]:
    return {
        "path": "/tmp/coordination/drift/code.json",
        "repository": "code",
        "branch": "ar/leaf",
        "removed": False,
        "reason": "already-absent",
    }


def _failed_snapshot() -> dict[str, object]:
    return {
        "path": "/tmp/coordination/drift/code.json",
        "repository": "code",
        "branch": "ar/leaf",
        "removed": False,
        "reason": "[Errno 13] Permission denied",
    }


def test_a_preview_over_a_live_drift_snapshot_reports_no_blocker() -> None:
    """The measured L14 shape: a preview that would remove must not read as a blockage.

    Before the fix this call raised ``RuntimeError: terminal result blocker driftSnapshot=code
    carries no reason`` -- the preview was refused by the validator that was supposed to describe
    it, and the operator lost the dry run while the real finalize went through.
    """

    assert terminal_result_blockers(_result(_preview_snapshot(), preview=True)) == []


def test_the_preview_and_the_real_call_agree_over_the_same_collection() -> None:
    """Preview and apply are the same judgement about the same entry, one keyword apart.

    The preview answers ``would_remove`` and the apply answers ``removed``: both are "this entry
    was reclaimed", and neither is a blockage. An entry that was already gone is benign in both.
    """

    assert terminal_result_blockers(_result(_preview_snapshot(), preview=True)) == []
    assert terminal_result_blockers(_result(_removed_snapshot(), preview=False)) == []
    assert terminal_result_blockers(_result(_absent_snapshot(), preview=False)) == []
    assert terminal_result_blockers(_result(_absent_snapshot(), preview=True)) == []


def test_a_real_drift_snapshot_failure_still_blocks_with_its_reason() -> None:
    """The other half of the contract: the fix must not swallow a genuine failure.

    A drift snapshot that was neither removed nor already absent is a real blockage, and it must
    still name the component and the reason. This is what would go red if the expectation were
    "fixed" by declaring the entry benign or by relaxing the reason requirement instead.
    """

    blockers = terminal_result_blockers(_result(_failed_snapshot(), preview=False))
    assert blockers == [{"driftSnapshot": "code", "reason": "[Errno 13] Permission denied"}]
