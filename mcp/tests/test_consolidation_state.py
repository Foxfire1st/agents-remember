"""The states a consolidation pass leaves, its closeout sentence, and its columns in the writer table.

Every case writes its own records and history files, as the closeout and the knowledge writer would
leave them, and reads the result through the index the product reads (``KnowledgeIndex.standing``
and the ``writers`` view):

* after a closeout with a recorded pass, a revision the Consolidator wrote, a revision it read and
  left standing, and a rule whose card entries alone changed read consolidated; a rule with an
  ``unresolved`` row keeps its state, also where its family's row is ``stands``; a family the list
  did not name, reached only through a corrected member, is not consolidated;
* without a pass the revisions it only read keep their state, the ones it wrote read proposed, and
  the closeout's answer says both sentences;
* the Consolidator's columns count, for the writer of each revision read, what the pass did with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import record_path
from agents_remember.worktrees.knowledge_gate import HistoryClosing
from agents_remember.worktrees.modules.closeout_external import _review_note
from agents_remember.worktrees.review_fact_source import (
    NO_PASS_SENTENCE,
    NOTHING_CONSOLIDATED_SENTENCE,
)
from agents_remember.worktrees.review_note import closed_history_review_note
from knowledge_review_fact_test_support import (
    consolidating,
    history_document,
    review_fact,
    stamp,
    written,
)
from test_knowledge_record_state import _index, _invariant
from test_knowledge_writer_table import A_KEY, B_KEY, A, B, _memory, _read, _report, _rows

LEAF = "261112-FIX-L1"
PASS_LEAF = "261112-FIX-L3"
WROTE, STANDS, CARD, CARD_DELETED, UNSURE, UNLISTED_RULE = (f"INV-{c * 6}" for c in "ABCDEF")
FAMILY, UNLISTED_FAMILY = "FAM-AAAAAA", "FAM-BBBBBB"
CONSOLIDATOR = {"role": "consolidator"}


def _family(record_id: str, members: list[str]) -> tuple[str, dict[str, Any]]:
    document = {
        "schema": "ar-family/v1",
        "id": record_id,
        "origin": {"task": "261112-FIX", "leaf": LEAF},
        "revision": 1,
        "writer": stamp(LEAF),
        "title": record_id,
        "guarantee": "They hold together.",
        "members": members,
        "routes": ["pkg"],
        "admission": "legacy-unassessed",
    }
    return record_path("family", record_id, record_id.lower()), document


def _row(subject: str, disposition: str, number: int, **fields: Any) -> dict[str, Any]:
    return {
        "id": f"ROW-{number:06d}",
        "subject": subject,
        "disposition": disposition,
        "reason": "Read against the code.",
        "items": [],
        "revision": 1,
        **fields,
    }


def _covered_new_entry() -> dict[str, Any]:
    return {
        "id": "RLZ-0QYX992Q",
        "before": "absent",
        "after": {
            "path": "pkg/review.py",
            "locator": {"kind": "symbol", "name": "fixture"},
            "blob": "1" * 40,
            "content": "sha256:" + "9c" * 32,
        },
    }


def _covered_deleted_entry() -> dict[str, Any]:
    return {
        "id": "PRF-0QYX992Q",
        "before": {
            "path": "tests/test_review.py",
            "locator": {"kind": "symbol", "name": "test_bogus"},
            "blob": "2" * 40,
            "content": "sha256:" + "8b" * 32,
        },
        "after": "absent",
    }


TESTED = {"testsRun": [{"test": "tests/test_review.py::test_listed", "result": "passed"}]}
PASS_ROWS = (
    _row(WROTE, "changed", 1, revision=2, covers=[], effect="clarify", **TESTED),
    _row(STANDS, "stands", 2, covers=[], **TESTED),
    _row(CARD, "extended", 3, covers=[_covered_new_entry()], noProofRecorded=True),
    _row(CARD_DELETED, "deleted", 7, covers=[_covered_deleted_entry()], **TESTED),
    _row(UNSURE, "unresolved", 4, covers=[], **TESTED),
    _row(FAMILY, "stands", 5, examined=[{"id": one, "revision": 1} for one in (STANDS, UNSURE)]),
    _row(UNLISTED_FAMILY, "no_impact", 6, examined=[{"id": WROTE, "revision": 2}]),
)
SUBJECTS = (WROTE, STANDS, CARD, CARD_DELETED, UNSURE, FAMILY)


def _tree(*, passed: bool) -> dict[str, dict[str, Any]]:
    reviewed = history_document(
        LEAF,
        written_rows=tuple(
            written(one, 1, LEAF)
            for one in (WROTE, STANDS, CARD, CARD_DELETED, UNSURE, FAMILY, UNLISTED_FAMILY)
        ),
        review=review_fact(LEAF),
    )
    path, pass_file = consolidating(
        history_document(
            PASS_LEAF,
            written_rows=(written(WROTE, 2, PASS_LEAF, **CONSOLIDATOR),),
            rows=PASS_ROWS,
            review=review_fact(PASS_LEAF, handed_over_by="consolidator"),
        ),
        *SUBJECTS,
    )
    if not passed:  # closed by its closeout without a review fact
        pass_file = {key: value for key, value in pass_file.items() if key != "review"}
    records = [
        _invariant(WROTE, 2, writer=stamp(PASS_LEAF, **CONSOLIDATOR)),
        *(_invariant(one, 1, writer=stamp(LEAF)) for one in (STANDS, CARD, CARD_DELETED, UNSURE)),
        _family(FAMILY, [STANDS, UNSURE]),
        _family(UNLISTED_FAMILY, [WROTE]),
    ]
    return dict([*records, reviewed, (path, pass_file)])


def _states(root: Path, files: dict[str, dict[str, Any]]) -> dict[str, str | None]:
    with _index(root, files) as index:
        standings = {one: index.standing(one).value for one in (*SUBJECTS, UNLISTED_FAMILY)}
    return {
        one: None if standing is None else standing.state for one, standing in standings.items()
    }


def test_after_a_passed_closeout_written_standing_and_card_only_revisions_read_consolidated(
    tmp_path: Path,
) -> None:
    assert _states(tmp_path, _tree(passed=True)) == {
        WROTE: "consolidated",  # the Consolidator wrote it
        STANDS: "consolidated",  # read and left standing
        CARD: "consolidated",  # its card entries alone changed (extended)
        CARD_DELETED: "consolidated",  # its card entries alone changed (non-retiring deleted)
        UNSURE: "reviewed",  # unresolved: it keeps the state it had
        FAMILY: "consolidated",  # its row stands, beside its unresolved member
        UNLISTED_FAMILY: "reviewed",  # reached by a corrected member, not on the list
    }


def test_without_a_pass_read_revisions_keep_their_state_and_the_answer_says_both_sentences(
    tmp_path: Path,
) -> None:
    states = _states(tmp_path, _tree(passed=False))
    assert states[WROTE] == "proposed"
    assert {
        one: states[one] for one in (STANDS, CARD, CARD_DELETED, UNSURE, FAMILY)
    } == dict.fromkeys((STANDS, CARD, CARD_DELETED, UNSURE, FAMILY), "reviewed")
    unpassed = _tree(passed=False)[f"knowledge/history/{PASS_LEAF}.json"]
    closed = canonical_text({**unpassed, "closed": True}).encode("utf-8")
    both = f"{NO_PASS_SENTENCE} {NOTHING_CONSOLIDATED_SENTENCE}"
    assert closed_history_review_note(closed) == both
    previous = canonical_text({**unpassed, "closed": False}).encode("utf-8")
    assert _review_note(HistoryClosing(tmp_path / "h.json", previous, closed), None) == both
    # An ordinary task's file gives the first sentence alone; a passed one gives none.
    ordinary = {key: value for key, value in unpassed.items() if key != "consolidationSubjects"}
    assert closed_history_review_note(canonical_text({**ordinary, "closed": True})) == (
        NO_PASS_SENTENCE
    )
    passed = _tree(passed=True)[f"knowledge/history/{PASS_LEAF}.json"]
    assert closed_history_review_note(canonical_text({**passed, "closed": True})) == ""


def test_the_consolidator_columns_count_per_writer_of_the_revision_read(tmp_path: Path) -> None:
    """A wrote five rules, B two; one pass kept, corrected, merged, retired and left unresolved."""

    kept, corrected, merged, retired, unresolved = (f"INV-{c}AAAAA" for c in "12345")
    successor, b_kept = "INV-6AAAAA", "INV-7AAAAA"
    consolidator = stamp(PASS_LEAF, **CONSOLIDATOR)
    records = [
        _invariant(kept, 1, writer=stamp(LEAF, **A)),
        _invariant(corrected, 2, writer=consolidator),
        _invariant(merged, 1, writer=stamp(LEAF, **A), status="retired"),
        _invariant(retired, 1, writer=stamp(LEAF, **B), status="retired"),
        _invariant(unresolved, 1, writer=stamp(LEAF, **A)),
        _invariant(b_kept, 1, writer=stamp(LEAF, **B)),
    ]
    successor_path, successor_document = _invariant(successor, 1, writer=consolidator)
    successor_document["supersedes"] = [merged]
    rows = (
        _row(kept, "stands", 1, covers=[]),
        _row(corrected, "changed", 2, revision=2, covers=[], effect="clarify"),
        _row(merged, "deleted", 3, covers=[], effect="retire"),
        _row(retired, "deleted", 4, covers=[], effect="retire"),
        _row(unresolved, "unresolved", 5, covers=[]),
        _row(b_kept, "no_impact", 6, covers=[]),
    )
    histories = [
        history_document(
            LEAF,
            written_rows=(
                *(written(one, 1, LEAF, **A) for one in (kept, corrected, merged, unresolved)),
                *(written(one, 1, LEAF, **B) for one in (retired, b_kept)),
            ),
            review=review_fact(LEAF),
        ),
        consolidating(
            history_document(
                PASS_LEAF,
                written_rows=(
                    written(corrected, 2, PASS_LEAF, **CONSOLIDATOR),
                    written(successor, 1, PASS_LEAF, **CONSOLIDATOR),
                ),
                rows=rows,
                review=review_fact(PASS_LEAF, handed_over_by="consolidator"),
            ),
            kept,
            corrected,
            merged,
            retired,
            unresolved,
            b_kept,
        ),
    ]
    files = dict([*records, (successor_path, successor_document), *histories])
    answer = _read(tmp_path, _memory(tmp_path, files))
    rows_by_writer = _rows(answer)
    columns = (
        "consolidationKept",
        "consolidationCorrected",
        "consolidationMerged",
        "consolidationRetired",
        "consolidationUnresolved",
    )
    assert tuple(rows_by_writer[A_KEY][one] for one in columns) == (1, 1, 1, 0, 1)
    assert tuple(rows_by_writer[B_KEY][one] for one in columns) == (1, 0, 0, 1, 0)
    behind = {
        tuple(writer[key] for key in ("role", "harness", "model")): writer["revisions"]
        for writer in _report(answer)["writers"]
    }
    assert behind[A_KEY]["consolidationCorrected"] == [f"{corrected}@1"]
    assert behind[A_KEY]["consolidationMerged"] == [f"{merged}@1"]
    assert behind[B_KEY]["consolidationRetired"] == [f"{retired}@1"]
    assert set(columns) <= set(_report(answer)["columns"])
