"""The index derives each record's state from the tree's history files.

Every case writes its own records and history files into a Git working tree, builds the index of
that tree with the production builder and reads the answer through :meth:`KnowledgeIndex.standing`
-- one row lookup. The review fact and the ``written`` rows are written by the test itself, since
the closeout that writes them in production and the consolidator role are not part of this read:

* a revision a leaf wrote reads **proposed** until that leaf's history file holds a review fact,
  and **reviewed** after;
* a new revision of a reviewed record reads proposed; a reopened leaf's second attempt speaks only
  for what it wrote, so a revision of the first, reviewed attempt keeps its state;
* a converted review fact (an owner closed before reviews were recorded) counts like its own;
* a revision with no owner (no stamp, no ``written`` row) reads proposed;
* **consolidated** both ways: a file handed over by a consolidator wrote the revision, or
  holds a row naming that revision and wrote no newer one;
* a row a consolidator left ``unresolved`` consolidates nothing;
* a retired record has no state;
* the index answers from its own file: the history files are deleted after the build and the
  answer stands.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import apsw
import pytest
from agents_remember.memory.knowledge_index import (
    KnowledgeIndex,
    RecordStanding,
    RecordWriter,
    build_index,
    directory_snapshot,
)
from agents_remember.memory.knowledge_index.projection import text_uuid
from agents_remember.memory.knowledge_index.record_standing import UNKNOWN_WRITER
from agents_remember.models.knowledge_files.converted_reviews import CONVERTED_REVIEWS_PATH
from agents_remember.models.knowledge_files.documents import record_path
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.models.knowledge_files.history_product import record_content_digest
from agents_remember.models.knowledge_files.record_state import UNRESOLVED, RevisionStates
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp
from knowledge_review_fact_test_support import (
    history_document,
    review_fact,
    stamp,
    written,
)

LEAF = "261112-FIX-L1"
LATER_LEAF = "261112-FIX-L2"
CONSOLIDATING_LEAF = "261112-FIX-L3"
RULE = "INV-7K3F9Q"
OTHER_RULE = "INV-8M4G2R"


def _invariant(
    record_id: str,
    revision: int,
    *,
    leaf: str | None = LEAF,
    writer: dict[str, Any] | None = None,
    status: str | None = None,
) -> tuple[str, dict[str, Any]]:
    origin: dict[str, Any] = {"task": "261112-FIX"}
    if leaf is not None:
        origin["leaf"] = leaf
    document: dict[str, Any] = {
        "schema": "ar-invariant/v1",
        "id": record_id,
        "origin": origin,
        "revision": revision,
        "statement": f"{record_id} holds at revision {revision}.",
        "applicability": "Every fixture write.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
    }
    if writer is not None:
        document["writer"] = writer
    if status is not None:
        document["status"] = status
    return record_path("invariant", record_id, record_id.lower()), document


def _no_impact_row(record_id: str, revision: int) -> dict[str, Any]:
    return {
        "id": "ROW-3D5E7F",
        "subject": record_id,
        "disposition": "no_impact",
        "reason": "Read at this revision and left as it was.",
        "items": [],
        "covers": [],
        "revision": revision,
    }


def _index(root: Path, files: dict[str, dict[str, Any]]) -> KnowledgeIndex:
    """Write ``files`` into a fresh Git working tree under ``root`` and open its built index."""

    tree = root / "tree"
    tree.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    for path, document in files.items():
        target = tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    destination = root / "index.sqlite"
    report = build_index(directory_snapshot(tree), destination)
    assert report.state == "complete", report.problems
    return KnowledgeIndex(destination)


def _state(root: Path, files: dict[str, dict[str, Any]], record_id: str = RULE) -> str | None:
    with _index(root, files) as index:
        standing = index.standing(record_id).value
    assert standing is not None
    return standing.state


def _files(*pairs: tuple[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return dict(pairs)


def test_a_revision_reads_proposed_before_closeout_and_reviewed_after(tmp_path: Path) -> None:
    record = _invariant(RULE, 1, writer=stamp(LEAF))
    wrote = (written(RULE, 1, LEAF),)
    open_file = history_document(LEAF, written_rows=wrote)
    closed_file = history_document(LEAF, written_rows=wrote, review=review_fact(LEAF))

    assert _state(tmp_path / "open", _files(record, open_file)) == "proposed"
    assert _state(tmp_path / "closed", _files(record, closed_file)) == "reviewed"
    # A leaf closed out without a recorded pass: closed, no fact, so still proposed.
    unreviewed_path, unreviewed = history_document(LEAF, written_rows=wrote)
    unreviewed = (unreviewed_path, {**unreviewed, "closed": True})
    assert _state(tmp_path / "no-pass", _files(record, unreviewed)) == "proposed"


def test_a_converted_review_fact_makes_its_owners_revisions_reviewed(tmp_path: Path) -> None:
    """An owner closed before reviews were recorded keeps a frozen file without ``review``.

    Its fact stands in the converted review facts beside the history files; the build reads that
    file as such (the index stays complete) and the owner's revision reads reviewed.
    """

    closed = history_document(LEAF, written_rows=(written(RULE, 1, LEAF),))
    closed = (closed[0], {**closed[1], "closed": True})
    converted = (
        CONVERTED_REVIEWS_PATH,
        {
            "schema": "ar-converted-reviews/v1",
            "reviews": [{"owner": LEAF, "fact": review_fact(LEAF)}],
        },
    )
    record = _invariant(RULE, 1, writer=stamp(LEAF))

    assert _state(tmp_path / "without", _files(record, closed)) == "proposed"
    assert _state(tmp_path / "with", _files(record, closed, converted)) == "reviewed"


def test_a_new_revision_of_a_reviewed_record_reads_proposed(tmp_path: Path) -> None:
    reviewed = history_document(
        LEAF, written_rows=(written(RULE, 1, LEAF),), review=review_fact(LEAF)
    )
    later = history_document(LATER_LEAF, written_rows=(written(RULE, 2, LATER_LEAF),))
    files = _files(_invariant(RULE, 2, leaf=LEAF, writer=stamp(LATER_LEAF)), reviewed, later)

    assert _state(tmp_path, files) == "proposed"


@pytest.mark.parametrize("syncing", [LEAF, LATER_LEAF])
def test_a_revision_two_lines_name_reads_through_the_file_with_the_records_stamp(
    tmp_path: Path, syncing: str
) -> None:
    """Two lines' files name one revision when a sync combined their edits under the product.

    The record's own stamp names the file that wrote the revision it holds. Catches a combined
    revision reading reviewed through the other line's closed and reviewed file, whichever of the
    two files is read last.
    """

    other = LATER_LEAF if syncing == LEAF else LEAF
    combined = {**stamp(syncing), "model": "merged-model"}
    syncing_file = history_document(
        syncing, written_rows=({"record": RULE, "revision": 2, "writer": combined},)
    )
    other_file = history_document(
        other, written_rows=(written(RULE, 2, other),), review=review_fact(other)
    )
    files = _files(_invariant(RULE, 2, writer=combined), syncing_file, other_file)

    assert _state(tmp_path, files) == "proposed"


_ORDERS = pytest.mark.parametrize(
    ("first", "second"), [(LEAF, LATER_LEAF), (LATER_LEAF, LEAF)], ids=["one-order", "other-order"]
)


def _two_contents(first: str, second: str, *, handed: str = "curator") -> list[tuple[str, Any]]:
    """Two closed files with facts, each naming its own content of ``RULE`` at revision 2."""

    return [
        history_document(
            owner,
            written_rows=(written(RULE, 2, owner),),
            review=review_fact(owner, handed_over_by=handed),
        )
        for owner in (first, second)
    ]


@_ORDERS
@pytest.mark.parametrize("handed", ["curator", "consolidator"])
def test_two_closed_contents_under_one_number_read_proposed_without_the_records_stamp(
    tmp_path: Path, first: str, second: str, handed: str
) -> None:
    """No fact names the content the record holds: proposed, and its writer is unknown, in either
    order of the files. Catches the last file read taking the revision."""

    files = _files(_invariant(RULE, 2), *_two_contents(first, second, handed=handed))

    with _index(tmp_path, files) as index:
        standing = index.standing(RULE).value
    assert standing is not None
    assert (standing.state, standing.writer) == ("proposed", UNKNOWN_WRITER)


@_ORDERS
@pytest.mark.parametrize(
    ("handed", "state"), [("curator", "reviewed"), ("consolidator", "consolidated")]
)
def test_the_file_whose_row_carries_the_records_stamp_gives_its_state(
    tmp_path: Path, first: str, second: str, handed: str, state: str
) -> None:
    """The record's own stamp names its exact content; that file's fact gives the state, whatever
    the other content's file says, in either order."""

    holder = first
    files = _files(
        _invariant(RULE, 2, writer=stamp(holder)),
        *_two_contents(first, second, handed=handed),
    )

    assert _state(tmp_path, files) == state


@_ORDERS
def test_a_kept_row_says_nothing_where_two_contents_share_the_number(
    tmp_path: Path, first: str, second: str
) -> None:
    """A Consolidator's row that kept "revision 2" cannot say which content it read."""

    wrote = [
        history_document(owner, written_rows=(written(RULE, 2, owner),))
        for owner in (first, second)
    ]
    kept = history_document(
        CONSOLIDATING_LEAF,
        rows=(_no_impact_row(RULE, 2),),
        review=review_fact(CONSOLIDATING_LEAF, handed_over_by="consolidator"),
    )
    files = _files(_invariant(RULE, 2, writer=stamp(first)), *wrote, kept)

    assert _state(tmp_path, files) == "proposed"


@_ORDERS
def test_an_older_number_two_writes_share_counts_under_no_writer(
    tmp_path: Path, first: str, second: str
) -> None:
    """The writers table: an older revision two different writes share has no stamp and is not
    reviewed, in either order; the current revision keeps the record's own stamp."""

    files = _files(
        _invariant(RULE, 3, writer=stamp(CONSOLIDATING_LEAF)),
        *_two_contents(first, second),
        history_document(CONSOLIDATING_LEAF, written_rows=(written(RULE, 3, CONSOLIDATING_LEAF),)),
    )

    with _index(tmp_path, files) as index:
        revisions, _live = index.writer_revisions().value
    by_number = {one.revision: one for one in revisions if one.record == RULE}
    assert (by_number[2].writer, by_number[2].reviewed) == (None, False)
    assert by_number[3].writer == ("curator", "fixture-harness", "fixture-model")


def _both_views(root: Path, files: dict[str, dict[str, Any]]) -> tuple[str | None, bool]:
    """The record's state in the record view, and whether the writers table counts its current
    revision reviewed."""

    with _index(root, files) as index:
        standing = index.standing(RULE).value
        revisions, _live = index.writer_revisions().value
    assert standing is not None
    current = max((one for one in revisions if one.record == RULE), key=lambda one: one.revision)
    return standing.state, current.reviewed


_EARLY, _LATE = "261112-FIX-L0", "261112-FIX-L9"
_KEEP = history_document(
    "261112-FIX-L5",
    rows=(_no_impact_row(RULE, 2),),
    review=review_fact("261112-FIX-L5", handed_over_by="consolidator"),
)


@pytest.mark.parametrize("names", [(_EARLY, _LATE), (_LATE, _EARLY)], ids=["one", "other"])
@pytest.mark.parametrize(
    ("record_writer", "expected"),
    [("stamped", ("reviewed", True)), ("unnamed", ("proposed", False))],
)
def test_three_writes_under_one_number_read_only_through_the_records_own(
    tmp_path: Path, names: tuple[str, str], record_writer: str, expected: tuple[str, bool]
) -> None:
    """Two stamped writes and one from before stamps share revision 2, every file closed with a
    fact and a Consolidator file keeping that number: the record reads through the file that
    carries its stamp, or proposed where none does, in either name order and both views."""

    stamped, legacy = names
    files = _files(
        _invariant(RULE, 2, writer=stamp(LEAF if record_writer == "stamped" else None)),
        history_document(LEAF, written_rows=(written(RULE, 2, LEAF),), review=review_fact(LEAF)),
        history_document(
            stamped, written_rows=(written(RULE, 2, stamped),), review=review_fact(stamped)
        ),
        history_document(
            legacy,
            rows=(_judgment(RULE, "changed", revision=2, covers=[], effect="clarify"),),
            review=review_fact(legacy, handed_over_by="consolidator"),
        ),
        _KEEP,
    )
    assert _both_views(tmp_path, files) == expected


@pytest.mark.parametrize("second_closed", [False, True], ids=["second-open", "second-passed"])
def test_a_reopened_leaf_rewriting_one_number_is_no_second_content_of_another_line(
    tmp_path: Path, second_closed: bool
) -> None:
    """One owner's two attempts both name revision 2, the later with the record's stamp: rule 4
    reads it through the later attempt, proposed while open and reviewed once it passes; a
    Consolidator file keeping that number decides nothing."""

    later = stamp(LEAF, model="later-model")
    files = _files(
        _invariant(RULE, 2, writer=later),
        history_document(LEAF, written_rows=(written(RULE, 2, LEAF),), review=review_fact(LEAF)),
        history_document(
            LEAF,
            written_rows=({"record": RULE, "revision": 2, "writer": later},),
            review=review_fact(LEAF) if second_closed else None,
            attempt=2,
        ),
        _KEEP,
    )
    expected = ("reviewed", True) if second_closed else ("proposed", False)
    assert _both_views(tmp_path, files) == expected


@pytest.mark.parametrize("owner", [_EARLY, _LATE])
@pytest.mark.parametrize(
    ("stamped_beside", "expected"),
    [(False, ("consolidated", True)), (True, ("proposed", False))],
    ids=["alone", "beside-a-stamped-write"],
)
def test_a_write_from_before_stamps_with_no_history_row_is_kept_only_where_alone(
    tmp_path: Path, owner: str, stamped_beside: bool, expected: tuple[str, bool]
) -> None:
    """The record holds a write from before stamps by an owner with no history row at all, and a
    Consolidator file keeps revision 2. Alone, the keep stands; beside another line's stamped
    write of revision 2 the keep cannot say which content it read, in either name order."""

    pairs = [_invariant(RULE, 2, writer=stamp(LEAF)), _KEEP]
    if stamped_beside:
        pairs.append(history_document(owner, written_rows=(written(RULE, 2, owner),)))
    assert _both_views(tmp_path, _files(*pairs)) == expected


def _changed_only(owner: str, *, closed: bool, attempt: int = 1) -> tuple[str, dict[str, Any]]:
    row = _judgment(RULE, "changed", revision=2, covers=[], effect="clarify")
    review = review_fact(owner) if closed else None
    return history_document(owner, rows=(row,), review=review, attempt=attempt)


_PER_FILE_CASES = {
    "same-owner-before-stamps-then-stamped": lambda first, second: (
        _invariant(RULE, 2),
        _changed_only(first, closed=True),
        history_document(
            first,
            written_rows=({"record": RULE, "revision": 2, "writer": stamp(first, model="later")},),
            review=review_fact(first),
            attempt=2,
        ),
    ),
    "same-owner-two-attempts-before-stamps": lambda first, second: (
        _invariant(RULE, 2, writer=stamp(first)),
        _changed_only(first, closed=True),
        _changed_only(first, closed=False, attempt=2),
    ),
    "two-owners-before-stamps-stamp-names-one": lambda first, second: (
        _invariant(RULE, 2, writer=stamp(first)),
        _changed_only(first, closed=True),
        _changed_only(second, closed=True),
    ),
}


@pytest.mark.parametrize("names", [(_EARLY, _LATE), (_LATE, _EARLY)], ids=["one", "other"])
@pytest.mark.parametrize("case", sorted(_PER_FILE_CASES))
def test_writes_from_before_stamps_count_per_file_not_per_owner(
    tmp_path: Path, names: tuple[str, str], case: str
) -> None:
    """Two files each wrote revision 2, at least one before stamps, every file closed with a fact
    or open, and a Consolidator file keeping it: two contents, and the record's stamp is carried by
    no ``written`` row, so proposed in both views, whichever owner or attempt the files are."""

    files = _files(*_PER_FILE_CASES[case](*names), _KEEP)
    assert _both_views(tmp_path, files) == ("proposed", False)


def test_an_older_revision_one_owner_wrote_twice_is_not_counted_reviewed(tmp_path: Path) -> None:
    """The writers table, for a revision that is no longer current: the same per-file count."""

    later = stamp(LATER_LEAF, model="later")
    files = _files(
        _invariant(RULE, 3, writer=later),
        *_PER_FILE_CASES["same-owner-before-stamps-then-stamped"](LEAF, _LATE)[1:],
        _KEEP,
        history_document(
            LATER_LEAF, written_rows=({"record": RULE, "revision": 3, "writer": later},)
        ),
    )
    with _index(tmp_path, files) as index:
        revisions, _live = index.writer_revisions().value
    older = [one for one in revisions if one.record == RULE and one.revision == 2]
    assert older and not any(one.reviewed for one in older)


def test_two_rows_with_one_stamp_are_one_write() -> None:
    """The same write named twice is one content: its stamp is recorded, its file gives the state."""

    row = written(RULE, 2, LEAF)
    states = RevisionStates.of(
        [
            HistoryFile.model_validate(
                history_document(LEAF, written_rows=(row,), review=review_fact(LEAF))[1]
            ),
            HistoryFile.model_validate(history_document(LEAF, written_rows=(row,), attempt=2)[1]),
        ]
    )
    assert states.written_stamp(RULE, 2) == WriterStamp.model_validate(row["writer"])
    assert not states.shared(RULE, 2)
    assert states.state_of(RULE, 2, retired=False, writer=None) == "reviewed"


@pytest.mark.parametrize(
    ("owners", "reviewed", "expected"),
    [
        ((LEAF, "261112-FIX-L0"), LEAF, "reviewed"),
        ((LEAF, "261112-FIX-L0"), "261112-FIX-L0", "proposed"),
        ((LEAF, LATER_LEAF), LEAF, "reviewed"),
        ((LEAF, LATER_LEAF), LATER_LEAF, "proposed"),
        (("261112-FIX-L0", LATER_LEAF), "261112-FIX-L0", "proposed"),
        (("261112-FIX-L0", LATER_LEAF), LATER_LEAF, "proposed"),
    ],
    ids=[
        "other-sorts-before-own-reviewed",
        "other-sorts-before-other-reviewed",
        "other-sorts-after-own-reviewed",
        "other-sorts-after-other-reviewed",
        "no-file-of-the-owner-first-reviewed",
        "no-file-of-the-owner-last-reviewed",
    ],
)
def test_one_stamp_in_files_of_several_owners_reads_through_the_owner_it_names(
    owners: tuple[str, str], reviewed: str, expected: str
) -> None:
    """Files of two owners carry one stamp of ``LEAF``'s write; one of them is closed reviewed.

    Catches the file whose owner name sorts first giving the state: only ``LEAF``'s own file can,
    and without it no file does, whichever way the names sort.
    """

    row = written(RULE, 2, LEAF)
    states = RevisionStates.of(
        HistoryFile.model_validate(
            history_document(
                owner, written_rows=(row,), review=review_fact(owner) if owner == reviewed else None
            )[1]
        )
        for owner in owners
    )
    stamp_of_row = WriterStamp.model_validate(row["writer"])
    assert not states.shared(RULE, 2)
    for writer in (stamp_of_row, None):
        assert states.state_of(RULE, 2, retired=False, writer=writer) == expected


def test_a_reopened_leaf_speaks_only_for_the_revisions_each_attempt_wrote(tmp_path: Path) -> None:
    first = history_document(LEAF, written_rows=(written(RULE, 1, LEAF),), review=review_fact(LEAF))
    second = history_document(LEAF, written_rows=(written(OTHER_RULE, 1, LEAF),), attempt=2)
    files = _files(
        _invariant(RULE, 1, writer=stamp(LEAF)),
        _invariant(OTHER_RULE, 1, writer=stamp(LEAF)),
        first,
        second,
    )

    with _index(tmp_path, files) as index:
        left_alone = index.standing(RULE).value
        rewritten = index.standing(OTHER_RULE).value
    assert left_alone is not None and left_alone.state == "reviewed"
    assert rewritten is not None and rewritten.state == "proposed"


def test_a_revision_with_no_owner_reads_proposed(tmp_path: Path) -> None:
    """No stamp and no ``written`` row: no file speaks for it, even beside a reviewed leaf."""

    reviewed = history_document(
        LEAF, written_rows=(written(OTHER_RULE, 1, LEAF),), review=review_fact(LEAF)
    )
    files = _files(
        _invariant(RULE, 1, leaf=None),
        _invariant(OTHER_RULE, 1, writer=stamp(LEAF)),
        reviewed,
    )

    with _index(tmp_path, files) as index:
        standing = index.standing(RULE).value
    assert standing == RecordStanding(id=RULE, revision=1, state="proposed", writer=UNKNOWN_WRITER)


def test_a_revision_a_consolidator_wrote_reads_consolidated(tmp_path: Path) -> None:
    consolidated = history_document(
        CONSOLIDATING_LEAF,
        written_rows=(written(RULE, 2, CONSOLIDATING_LEAF, role="consolidator"),),
        review=review_fact(CONSOLIDATING_LEAF, handed_over_by="consolidator"),
    )
    record = _invariant(RULE, 2, writer=stamp(CONSOLIDATING_LEAF, role="consolidator"))

    assert _state(tmp_path, _files(record, consolidated)) == "consolidated"


def test_a_revision_a_consolidator_read_and_kept_reads_consolidated(tmp_path: Path) -> None:
    wrote = history_document(LEAF, written_rows=(written(RULE, 1, LEAF),), review=review_fact(LEAF))
    kept = history_document(
        CONSOLIDATING_LEAF,
        rows=(_no_impact_row(RULE, 1),),
        review=review_fact(CONSOLIDATING_LEAF, handed_over_by="consolidator"),
    )
    record = _invariant(RULE, 1, writer=stamp(LEAF))

    assert _state(tmp_path / "kept", _files(record, wrote, kept)) == "consolidated"
    # The same row in a file a curator handed over is no consolidation: the revision is reviewed.
    curated = history_document(
        CONSOLIDATING_LEAF, rows=(_no_impact_row(RULE, 1),), review=review_fact(CONSOLIDATING_LEAF)
    )
    assert _state(tmp_path / "curated", _files(record, wrote, curated)) == "reviewed"


def test_two_writers_of_identical_content_do_not_make_a_keep_ambiguous(tmp_path: Path) -> None:
    record = _invariant(RULE, 2, writer=stamp(LEAF))
    digest = record_content_digest(record[1])
    histories = [
        history_document(
            owner, written_rows=({**written(RULE, 2, owner), "contentDigest": digest},)
        )
        for owner in (LEAF, LATER_LEAF)
    ]
    assert _both_views(tmp_path, _files(record, *histories, _KEEP)) == ("consolidated", True)


def test_a_consolidator_row_about_an_older_revision_keeps_no_newer_one(tmp_path: Path) -> None:
    """A row names the revision it read; the record's newer revision is not consolidated by it."""

    kept = history_document(
        CONSOLIDATING_LEAF,
        rows=(_no_impact_row(RULE, 1),),
        review=review_fact(CONSOLIDATING_LEAF, handed_over_by="consolidator"),
    )
    later = history_document(LATER_LEAF, written_rows=(written(RULE, 2, LATER_LEAF),))
    files = _files(_invariant(RULE, 2, writer=stamp(LATER_LEAF)), kept, later)

    assert _state(tmp_path, files) == "proposed"


def test_a_retired_record_shows_no_state(tmp_path: Path) -> None:
    reviewed = history_document(
        LEAF, written_rows=(written(RULE, 1, LEAF),), review=review_fact(LEAF)
    )
    files = _files(_invariant(RULE, 1, writer=stamp(LEAF), status="retired"), reviewed)

    assert _state(tmp_path, files) is None


def test_the_index_answers_state_and_writer_without_the_history_files(tmp_path: Path) -> None:
    """The history files are removed from the tree after the build; the stored answer stands.

    The recorded digest binds the history's writer to the unstamped record's exact content. The
    projected revision row carries the same derived state the reused read code shows.
    """

    record = _invariant(RULE, 1)
    row = {
        **written(RULE, 1, LEAF, model="model-a"),
        "contentDigest": record_content_digest(record[1]),
    }
    path, document = history_document(LEAF, written_rows=(row,), review=review_fact(LEAF))
    index = _index(tmp_path, _files(record, (path, document)))
    (tmp_path / "tree" / path).unlink()
    assert not list((tmp_path / "tree" / "knowledge" / "history").glob("*.json"))
    with index:
        standing = index.standing(RULE).value
    reader = apsw.Connection(str(index.database_path), flags=apsw.SQLITE_OPEN_READONLY)
    try:
        projected = reader.execute(
            "SELECT state FROM invariant_revision WHERE revision_id = ?",
            (text_uuid("revision", f"{RULE}@1"),),
        ).fetchall()
    finally:
        reader.close()
    assert standing == RecordStanding(
        id=RULE,
        revision=1,
        state="reviewed",
        writer=RecordWriter(role="curator", harness="fixture-harness", model="model-a"),
    )
    assert projected == [("reviewed",)]


def test_an_unknown_record_has_no_standing(tmp_path: Path) -> None:
    with _index(tmp_path, _files(_invariant(RULE, 1))) as index:
        assert index.standing(OTHER_RULE).value is None


@pytest.mark.parametrize("word", ["proposed", "accepted"])
def test_an_old_stored_state_word_is_no_status_and_no_state(tmp_path: Path, word: str) -> None:
    """A record of an unconverted tree still says ``proposed`` or ``accepted``; neither counts."""

    with _index(tmp_path, _files(_invariant(RULE, 1, status=word))) as index:
        record = index.record(RULE).value
        standing = index.standing(RULE).value
    assert record is not None and record.status == ""
    assert standing is not None and standing.state == "proposed"


def test_an_unconverted_and_a_converted_record_are_read_side_by_side(tmp_path: Path) -> None:
    """Both shapes in one tree: the candidate meets unconverted memory until the conversion runs.

    The unconverted record types ``accepted``, has no stamp, and its leaf's closed history file
    has neither ``written`` nor ``review``: it reads proposed, written by ``unknown``. The new-shape record
    beside it reads the state and writer its own history file gives it.
    """

    old_path, old_history = history_document(LATER_LEAF)
    files = _files(
        _invariant(OTHER_RULE, 3, leaf=LATER_LEAF, status="accepted"),
        (old_path, {**old_history, "closed": True}),
        _invariant(RULE, 1, writer=stamp(LEAF)),
        history_document(LEAF, written_rows=(written(RULE, 1, LEAF),), review=review_fact(LEAF)),
    )
    with _index(tmp_path, files) as index:
        old = index.standing(OTHER_RULE).value
        new = index.standing(RULE).value
    assert old is not None and (old.state, old.writer) == ("proposed", UNKNOWN_WRITER)
    assert new is not None and new.state == "reviewed" and new.writer is not None


def test_an_unresolved_consolidator_row_keeps_no_revision() -> None:
    """A row a consolidator could not judge consolidates nothing; the revision keeps its state.

    No row kind admits the ``unresolved`` disposition until the consolidator role brings it, so
    the row is placed on a parsed file by copy and read through the model the index uses.
    """

    _path, wrote = history_document(
        LEAF, written_rows=(written(RULE, 1, LEAF),), review=review_fact(LEAF)
    )
    _path, kept = history_document(
        CONSOLIDATING_LEAF,
        rows=(_no_impact_row(RULE, 1),),
        review=review_fact(CONSOLIDATING_LEAF, handed_over_by="consolidator"),
    )
    keeping = HistoryFile.model_validate(kept)
    unresolved = keeping.model_copy(
        update={"rows": (keeping.rows[0].model_copy(update={"disposition": UNRESOLVED}),)}
    )
    writer = WriterStamp.model_validate(stamp(LEAF))
    files = (HistoryFile.model_validate(wrote),)

    def state_with(history: HistoryFile) -> str | None:
        return RevisionStates.of((*files, history)).state_of(RULE, 1, retired=False, writer=writer)

    assert state_with(keeping) == "consolidated"
    assert state_with(unresolved) == "reviewed"


# --------------------------------------------------------------------------------------------------
# Only a closed file's fact counts; which consolidator rows keep a revision; legacy attribution;
# constant-time stamp lookups
# --------------------------------------------------------------------------------------------------

FAMILY = "FAM-2M8HXR"
DECISION = "DEC-7K3F9Q"


def _opened(file: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """The same history file with ``closed: false``: its review fact is written but not closed out."""

    path, document = file
    return path, {**document, "closed": False}


def test_a_fact_on_an_open_history_makes_nothing_reviewed_or_consolidated(tmp_path: Path) -> None:
    """Closeout writes the fact and ``closed`` together; a fact on an open file is no closeout."""

    wrote = (written(RULE, 1, LEAF),)
    record = _invariant(RULE, 1, writer=stamp(LEAF))
    reviewed = history_document(LEAF, written_rows=wrote, review=review_fact(LEAF))
    assert _state(tmp_path / "open", _files(record, _opened(reviewed))) == "proposed"
    assert _state(tmp_path / "closed", _files(record, reviewed)) == "reviewed"

    consolidator_fact = review_fact(CONSOLIDATING_LEAF, handed_over_by="consolidator")
    rewritten = _invariant(RULE, 2, writer=stamp(CONSOLIDATING_LEAF, role="consolidator"))
    wrote_it = history_document(
        CONSOLIDATING_LEAF,
        written_rows=(written(RULE, 2, CONSOLIDATING_LEAF, role="consolidator"),),
        review=consolidator_fact,
    )
    assert _state(tmp_path / "wrote-open", _files(rewritten, _opened(wrote_it))) == "proposed"
    assert _state(tmp_path / "wrote-closed", _files(rewritten, wrote_it)) == "consolidated"

    kept = history_document(
        CONSOLIDATING_LEAF, rows=(_no_impact_row(RULE, 1),), review=consolidator_fact
    )
    assert _state(tmp_path / "kept-open", _files(record, reviewed, _opened(kept))) == "reviewed"
    assert _state(tmp_path / "kept-closed", _files(record, reviewed, kept)) == "consolidated"


def _covered(entry: str, *, added: bool = False) -> dict[str, Any]:
    def anchor(content: str, blob: str) -> dict[str, Any]:
        return {
            "path": "mcp/src/fixture.py",
            "locator": {"kind": "symbol", "name": "fixture"},
            "blob": blob * 40,
            "content": "sha256:" + (content * 64)[:64],
        }

    return {
        "id": entry,
        "before": "absent" if added else anchor("1a", "0"),
        "after": anchor("9c", "1"),
    }


def _judgment(subject: str, disposition: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "ROW-3D5E7F",
        "subject": subject,
        "disposition": disposition,
        "reason": "Read at this revision.",
        "items": [],
        "revision": 1,
    }
    return {**row, **fields}


_KEEPING_ROWS = {
    "rule no_impact": _judgment(RULE, "no_impact", covers=[]),
    "rule moved": _judgment(RULE, "moved", covers=[_covered("RLZ-0QYX992Q")]),
    "rule extended": _judgment(RULE, "extended", covers=[_covered("RLZ-0QYX992Q", added=True)]),
    "family no_impact": _judgment(FAMILY, "no_impact", examined=[]),
    "family rerouted": _judgment(FAMILY, "rerouted", examined=[]),
    "family assigned": _judgment(FAMILY, "assigned", examined=[]),
}
_NON_KEEPING_ROWS = {
    "rule changed": _judgment(RULE, "changed", covers=[], effect="strengthen"),
    "rule deleted": _judgment(RULE, "deleted", covers=[], effect="retire"),
    "family changed": _judgment(FAMILY, "changed", examined=[], effect="strengthen"),
    "family retired": _judgment(FAMILY, "retired", examined=[]),
    "decision no_impact": _judgment(DECISION, "no_impact"),
    "decision changed": _judgment(DECISION, "changed", effect="clarify"),
    "decision retired": _judgment(DECISION, "retired", effect="retire"),
}


def _state_beside_a_consolidator_row(row: dict[str, Any]) -> str | None:
    record = row["subject"]
    _path, wrote = history_document(
        LEAF, written_rows=(written(record, 1, LEAF),), review=review_fact(LEAF)
    )
    _path, consolidating = history_document(
        CONSOLIDATING_LEAF,
        rows=(row,),
        review=review_fact(CONSOLIDATING_LEAF, handed_over_by="consolidator"),
    )
    histories = (HistoryFile.model_validate(wrote), HistoryFile.model_validate(consolidating))
    writer = WriterStamp.model_validate(stamp(LEAF))
    return RevisionStates.of(histories).state_of(record, 1, retired=False, writer=writer)


@pytest.mark.parametrize("row", _KEEPING_ROWS.values(), ids=_KEEPING_ROWS.keys())
def test_a_rule_or_family_row_that_kept_the_revision_consolidates_it(row: dict[str, Any]) -> None:
    assert _state_beside_a_consolidator_row(row) == "consolidated"


@pytest.mark.parametrize("row", _NON_KEEPING_ROWS.values(), ids=_NON_KEEPING_ROWS.keys())
def test_a_row_of_another_kind_or_a_changing_disposition_consolidates_nothing(
    row: dict[str, Any],
) -> None:
    """A decision row, or a rule or family row that changed or retired it, keeps no revision."""

    assert _state_beside_a_consolidator_row(row) == "reviewed"


def test_a_later_read_only_row_leaves_a_first_revision_with_the_attempt_that_wrote_it(
    tmp_path: Path,
) -> None:
    """A revision written before ``written`` lists existed is attributed through rows that wrote it.

    The first attempt created the rule (no row: a created record has none) and was closed with a
    pass; the reopened second attempt only read it. Its ``no_impact`` row wrote nothing, so the
    revision stays the first attempt's and reads reviewed. A ``changed`` row of the second attempt
    does attribute the revision it wrote to that attempt, which is still open.
    """

    first = history_document(LEAF, review=review_fact(LEAF))
    read_only = history_document(LEAF, rows=(_no_impact_row(RULE, 1),), attempt=2)
    files = _files(_invariant(RULE, 1, writer=stamp(LEAF)), first, read_only)
    assert _state(tmp_path / "read", files) == "reviewed"

    changing = history_document(
        LEAF, rows=(_judgment(RULE, "changed", revision=2, covers=[], effect="clarify"),), attempt=2
    )
    files = _files(_invariant(RULE, 2, writer=stamp(LEAF)), first, changing)
    assert _state(tmp_path / "changed", files) == "proposed"


class _CountedRevisions(tuple):  # type: ignore[type-arg]
    """A ``written`` list that counts how often it is scanned."""

    scans = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        type(self).scans += 1
        return super().__iter__()


@pytest.mark.parametrize("size", [10, 200])
def test_stamp_lookups_do_not_rescan_the_written_list(size: int) -> None:
    """Each named revision is found by one lookup: the scans stay the same at every list size."""

    rows = tuple(written(f"INV-{index:06d}", 1, LEAF) for index in range(size))
    _path, document = history_document(LEAF, written_rows=rows, review=review_fact(LEAF))
    parsed = HistoryFile.model_validate(document)
    history = parsed.model_copy(update={"written": _CountedRevisions(parsed.written or ())})
    _CountedRevisions.scans = 0
    states = RevisionStates.of((history,))
    for index in range(size):
        assert states.written_stamp(f"INV-{index:06d}", 1) is not None
        assert history.written_revision(f"INV-{index:06d}", 1) is not None
    assert history.written_revision("INV-ABSENT", 1) is None
    assert _CountedRevisions.scans <= 3
