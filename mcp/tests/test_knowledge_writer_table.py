"""One table says how each writer did, read as the ``writers`` view.

Every case writes its own memory tree -- records with their writer stamps and history files with
``written`` rows and review facts -- commits it, and reads the view through the read tool's builder,
so the answer measured is the one a caller gets:

* two writers' counts: revisions written, reviewed, later changed by another writer (another seat's
  stamp on a later revision), retired (the revision live at retirement);
* a revision no file names (written before stamps) counts under the writer ``unknown``, and so does
  a revision the conversion stamped; a later change where either seat is unknown is counted apart,
  never as a change by another writer;
* the answer stays within the tool's bound at two sizes of tree, holds at most the list bound of
  rows, and names a report whose content lists the revisions behind every number;
* the table is read from the index, with history files neither read nor parsed, and building and
  reading it visits each ``written`` row a bounded number of times at 10, 100 and 1,000 rows;
* writer labels of the admitted 1,024 characters are shortened in the answer and whole in the
  report; rows that still do not fit are left out and counted as remaining.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application import knowledge_writers_view as writers_view_module
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory.knowledge_index import build as index_build
from agents_remember.models.knowledge.base import REFERENCE_MAX_LENGTH
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH, record_path
from agents_remember.models.knowledge_files.ids import CROCKFORD_ALPHABET
from agents_remember.models.knowledge_files.record_state import RevisionStates
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import TOOL_ANSWER_LIST_MAX, bound_for
from knowledge_index_test_support import write_document
from knowledge_review_fact_test_support import history_document, review_fact, stamp, written

TASK = "261112-FIX"
LEAF_A = "261112-FIX-L1"
LEAF_B = "261112-FIX-L2"
LEAF_A_AGAIN = "261112-FIX-L3"
A = {"role": "curator", "model": "model-a"}
B = {"role": "worker", "model": "model-b"}
A_KEY = ("curator", "fixture-harness", "model-a")
B_KEY = ("worker", "fixture-harness", "model-b")
UNKNOWN_KEY = ("unknown", "unknown", "unknown")


def _invariant(
    record_id: str,
    revision: int,
    *,
    writer: dict[str, Any] | None,
    status: str | None = None,
) -> tuple[str, dict[str, Any]]:
    document: dict[str, Any] = {
        "schema": "ar-invariant/v1",
        "id": record_id,
        "origin": {"task": TASK, "leaf": LEAF_A},
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


def _conversion_stamp(leaf: str) -> dict[str, Any]:
    reason = "not recorded before writers were stamped"
    return {
        "by": "conversion",
        "operation": "state-conversion",
        "role": "unknown",
        "harness": "unknown",
        "model": "unknown",
        "effort": "unknown",
        "seat": "unknown",
        "task": TASK,
        "leaf": leaf,
        "at": "2026-11-19T21:00:00+01:00",
        "unknown": dict.fromkeys(("role", "harness", "model", "effort", "seat"), reason),
    }


def _memory(root: Path, files: dict[str, dict[str, Any]]) -> Path:
    """A committed, converted memory tree holding ``files``."""

    memory = root / "memory"
    memory.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(memory)], check=True)
    write_document(memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "1"})
    for path, document in files.items():
        write_document(memory, path, document)
    subprocess.run(["git", "-C", str(memory), "add", "-A"], check=True)
    identity = ("-c", "user.name=t", "-c", "user.email=t@t")
    subprocess.run(["git", "-C", str(memory), *identity, "commit", "-qm", "fixture"], check=True)
    return memory


def _read(root: Path, memory: Path) -> dict[str, Any]:
    return knowledge_read_payload(
        ReadToolRequest(memory_root=str(memory), view="writers"),
        coordination_root=str(root / "coord"),
    )


def _rows(answer: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {(row["role"], row["harness"], row["model"]): row for row in answer["rows"]}


def _report(answer: dict[str, Any]) -> dict[str, Any]:
    return json.loads(Path(answer["report"]).read_text(encoding="utf-8"))


def _counts(row: dict[str, Any]) -> tuple[int, ...]:
    return (
        row["written"],
        row["reviewed"],
        row["laterChangedByAnother"],
        row["laterChangedWriterUnknown"],
        row["retired"],
    )


def _two_writer_tree() -> dict[str, dict[str, Any]]:
    """Writer A (curator, model-a) and writer B (worker, model-b) on six rules.

    * INV-1: A wrote revision 1 in the reviewed leaf, B revision 2 -- A's revision was changed by
      another writer.
    * INV-2: A wrote revision 1 in the reviewed leaf; the record is retired at it.
    * INV-3: A wrote revision 1, and again revision 2 in another leaf from the same seat: no
      change by another writer.
    * INV-4: B wrote revision 3; revisions 1 and 2 are named by no file: written by unknown.
    * INV-5: the conversion stamped revision 2 in the reviewed leaf; revision 1 is named by no file.
    * INV-6: B wrote revision 1; revision 2 carries no stamp at all.
    """

    b2, a3 = stamp(LEAF_B, **B), stamp(LEAF_A_AGAIN, **A)
    records = [
        _invariant("INV-1AAAAA", 2, writer=b2),
        _invariant("INV-2AAAAA", 1, writer=stamp(LEAF_A, **A), status="retired"),
        _invariant("INV-3AAAAA", 2, writer=a3),
        _invariant("INV-4AAAAA", 3, writer=b2),
        _invariant("INV-5AAAAA", 2, writer=_conversion_stamp(LEAF_A)),
        _invariant("INV-6AAAAA", 2, writer=None),
    ]
    histories = [
        history_document(
            LEAF_A,
            written_rows=(
                written("INV-1AAAAA", 1, LEAF_A, **A),
                written("INV-2AAAAA", 1, LEAF_A, **A),
                written("INV-3AAAAA", 1, LEAF_A, **A),
            ),
            review=review_fact(LEAF_A),
        ),
        history_document(
            LEAF_B,
            written_rows=(
                written("INV-1AAAAA", 2, LEAF_B, **B),
                written("INV-4AAAAA", 3, LEAF_B, **B),
                written("INV-6AAAAA", 1, LEAF_B, **B),
            ),
        ),
        history_document(LEAF_A_AGAIN, written_rows=(written("INV-3AAAAA", 2, LEAF_A_AGAIN, **A),)),
    ]
    return dict(records + histories)


def test_two_writers_are_counted_with_reviews_changes_and_retirement(tmp_path: Path) -> None:
    answer = _read(tmp_path, _memory(tmp_path, _two_writer_tree()))

    assert answer["state"] == "view", answer
    rows = _rows(answer)
    #                       written reviewed byAnother writerUnknown retired
    assert _counts(rows[A_KEY]) == (4, 3, 1, 0, 1)
    assert _counts(rows[B_KEY]) == (3, 0, 0, 1, 0)
    assert answer["counts"] == {"writers": 3, "rowsReturned": 3, "revisions": 12, "records": 6}
    # Most revisions written first.
    assert [row["role"] for row in answer["rows"]] == ["unknown", "curator", "worker"]


def test_revisions_without_a_stamp_count_under_unknown(tmp_path: Path) -> None:
    answer = _read(tmp_path, _memory(tmp_path, _two_writer_tree()))

    unknown = _rows(answer)[UNKNOWN_KEY]
    # INV-4@1..2 (named by no file), INV-5@1 (no file) and @2 (the conversion's stamp), INV-6@2.
    # INV-5@2 is reviewed: the conversion's stamp names the reviewed leaf as its owner.
    assert _counts(unknown) == (5, 1, 0, 3, 0)
    behind = {
        (one["role"], one["harness"], one["model"]): one["revisions"]
        for one in _report(answer)["writers"]
    }
    assert sorted(behind[UNKNOWN_KEY]["written"]) == [
        "INV-4AAAAA@1..2",
        "INV-5AAAAA@1",
        "INV-5AAAAA@2",
        "INV-6AAAAA@2",
    ]
    # A later change where a seat is unknown is never counted as a change by another writer.
    assert sorted(behind[UNKNOWN_KEY]["laterChangedWriterUnknown"]) == [
        "INV-4AAAAA@1..2",
        "INV-5AAAAA@1",
    ]
    assert behind[B_KEY]["laterChangedWriterUnknown"] == ["INV-6AAAAA@1"]


def test_the_report_names_the_revisions_behind_every_number(tmp_path: Path) -> None:
    answer = _read(tmp_path, _memory(tmp_path, _two_writer_tree()))

    report = _report(answer)
    assert report["totals"] == {"writers": 3, "revisions": 12, "records": 6}
    behind = {(one["role"], one["harness"], one["model"]): one for one in report["writers"]}
    a = behind[A_KEY]["revisions"]
    assert sorted(a["written"]) == ["INV-1AAAAA@1", "INV-2AAAAA@1", "INV-3AAAAA@1", "INV-3AAAAA@2"]
    assert sorted(a["reviewed"]) == ["INV-1AAAAA@1", "INV-2AAAAA@1", "INV-3AAAAA@1"]
    assert a["laterChangedByAnother"] == ["INV-1AAAAA@1"]
    assert a["retired"] == ["INV-2AAAAA@1"]
    for one in report["writers"]:
        for column, labels in one["revisions"].items():
            spans = [label.split("@")[1] for label in labels]
            sizes = [
                int(s.split("..")[1]) - int(s.split("..")[0]) + 1 if ".." in s else 1 for s in spans
            ]
            assert sum(sizes) == one[column], (one, column)


def _crockford(number: int) -> str:
    return "".join(CROCKFORD_ALPHABET[(number >> (5 * shift)) & 31] for shift in range(5, -1, -1))


def _many_writers_tree(writers: int, records_per_writer: int) -> dict[str, dict[str, Any]]:
    """``writers`` writers, each the writer of revision 2 of its records over a revision 1 by A."""

    files: list[tuple[str, dict[str, Any]]] = []
    for index in range(writers):
        leaf = f"261112-FIX-L{index + 10}"
        model = {"role": "worker", "model": f"model-{index:04d}-with-a-long-provider-name"}
        rows: list[dict[str, Any]] = []
        for position in range(records_per_writer):
            record_id = f"INV-{_crockford(index * records_per_writer + position)}"
            files.append(_invariant(record_id, 2, writer=stamp(leaf, **model)))
            rows.append(written(record_id, 2, leaf, **model))
        files.append(history_document(leaf, written_rows=tuple(rows), review=review_fact(leaf)))
    return dict(files)


def test_the_answer_stays_within_its_bound_at_two_sizes(tmp_path: Path) -> None:
    measured: dict[int, tuple[int, int, int]] = {}
    for writers, per_writer in ((8, 3), (120, 5)):
        root = tmp_path / f"w{writers}"
        answer = _read(root, _memory(root, _many_writers_tree(writers, per_writer)))

        assert answer["state"] == "view", answer
        assert "cut" not in answer
        tokens = count_response_tokens(answer)
        assert tokens <= bound_for("knowledge_read")
        assert len(answer["rows"]) == min(writers + 1, TOOL_ANSWER_LIST_MAX)
        assert answer["counts"]["writers"] == writers + 1
        assert answer["readOn"]["writersRemaining"] == writers + 1 - len(answer["rows"])
        revisions = writers * per_writer * 2
        assert answer["counts"]["revisions"] == revisions
        report = _report(answer)
        assert len(report["writers"]) == writers + 1
        assert sum(one["written"] for one in report["writers"]) == revisions
        measured[writers] = (tokens, Path(answer["report"]).stat().st_size, revisions)
    small, large = measured[8], measured[120]
    # The answer is bounded by the list cap, not by the tree: fifteen times the writers, the answer
    # grows by the rows the cap still admits and no further.
    assert large[0] <= small[0] * (TOOL_ANSWER_LIST_MAX / 9 + 1)
    # The report grows linearly with the revisions it lists.
    assert large[1] / large[2] <= 2 * small[1] / small[2]


def test_the_table_is_read_from_the_index_with_history_files_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the index of a tree exists, the table needs no history file: reading or parsing one
    fails here, and the answer is the same as the one read before."""

    memory = _memory(tmp_path, _two_writer_tree())
    first = _read(tmp_path, memory)
    assert first["state"] == "view", first

    def refused(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("history files are not readable in this test")

    real_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if "/knowledge/history/" in path.as_posix():
            refused()
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(index_build, "history_document", refused)
    monkeypatch.setattr(RevisionStates, "of", classmethod(refused))
    again = _read(tmp_path, memory)

    assert again["state"] == "view", again
    assert again["rows"] == first["rows"]
    assert again["counts"] == first["counts"]


class _CountedRows(tuple[Any, ...]):
    """A ``written`` list that counts every row anyone visits."""

    visits = 0

    def __iter__(self) -> Iterator[Any]:
        for row in super().__iter__():
            _CountedRows.visits += 1
            yield row


def test_stamp_lookups_stay_linear_in_the_written_rows_of_a_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Index build and table read together visit each ``written`` row a bounded number of times
    at 10, 100 and 1,000 rows per history; one lookup scan per revision would grow with the square."""

    real_history = index_build.history_document

    def counted(path: str, document: Any) -> Any:
        history = real_history(path, document)
        if history.written is not None:
            object.__setattr__(history, "written", _CountedRows(history.written))
        return history

    monkeypatch.setattr(index_build, "history_document", counted)
    per_row: dict[int, float] = {}
    for size in (10, 100, 1000):
        root = tmp_path / f"n{size}"
        rows = tuple(written(f"INV-{_crockford(i)}", 1, LEAF_A, **A) for i in range(size))
        memory = _memory(root, dict([history_document(LEAF_A, written_rows=rows)]))
        _CountedRows.visits = 0
        answer = _read(root, memory)
        assert answer["state"] == "view", answer
        assert answer["counts"]["revisions"] == size
        per_row[size] = _CountedRows.visits / size
    assert per_row[1000] <= per_row[10] + 1, per_row


def _long_label(index: int, field: str) -> str:
    """A valid 1,024-character label that tokenizes poorly (hex digests), distinct per writer."""

    seed = hashlib.sha256(f"{field}-{index}".encode()).hexdigest()
    return (seed * (REFERENCE_MAX_LENGTH // len(seed) + 1))[:REFERENCE_MAX_LENGTH]


def _long_label_tree() -> tuple[dict[str, dict[str, Any]], set[tuple[str, str, str]]]:
    """Twenty writers, each writing one record, whose role, harness and model hold 1,024 chars."""

    files: list[tuple[str, dict[str, Any]]] = []
    labels: set[tuple[str, str, str]] = set()
    for index in range(20):
        leaf = f"261112-FIX-L{index + 10}"
        role, harness, model = (_long_label(index, one) for one in ("role", "harness", "model"))
        writer = {
            **stamp(leaf, role=role, model=model),
            "harness": harness,
            "seat": f"seat-{index}",
        }
        files.append(_invariant(f"INV-{_crockford(index)}", 1, writer=writer))
        labels.add((role, harness, model))
    return dict(files), labels


def test_writers_with_labels_of_the_admitted_length_stay_within_the_bound(
    tmp_path: Path,
) -> None:
    """Twenty writers whose role, harness and model each hold 1,024 characters: the public answer
    keeps rows, counts and the report's path inside its bound; the report keeps every label whole."""

    files, labels = _long_label_tree()
    answer = _read(tmp_path, _memory(tmp_path, files))

    assert answer["state"] == "view", answer
    assert not answer.get("overflow"), answer
    assert count_response_tokens(answer) <= bound_for("knowledge_read")
    counts = answer["counts"]
    assert counts["writers"] == 20 and counts["revisions"] == 20 and counts["records"] == 20
    assert answer["rows"], answer
    assert counts["rowsReturned"] == len(answer["rows"])
    assert answer["readOn"]["writersRemaining"] == 20 - len(answer["rows"])
    report = _report(answer)
    whole = {(one["role"], one["harness"], one["model"]) for one in report["writers"]}
    assert whole == labels
    for row in answer["rows"]:
        assert len(row["role"]) < REFERENCE_MAX_LENGTH
        match = [key for key in whole if key[0].startswith(row["role"].rstrip("…"))]
        assert len(match) == 1, row
        assert match[0][2].startswith(row["model"].rstrip("…"))
    assert answer["readOn"]["labelsShortened"] == len(answer["rows"])


def test_rows_that_do_not_fit_are_left_out_and_counted_as_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With labels kept whole, the same twenty writers do not fit: the answer leaves out the last
    rows and counts them as remaining, inside its bound, with the totals and the report."""

    monkeypatch.setattr(writers_view_module, "ANSWER_LABEL_MAX_CHARS", 4 * REFERENCE_MAX_LENGTH)
    files, labels = _long_label_tree()
    answer = _read(tmp_path, _memory(tmp_path, files))

    assert answer["state"] == "view", answer
    assert not answer.get("overflow"), answer
    assert count_response_tokens(answer) <= bound_for("knowledge_read")
    assert 0 < len(answer["rows"]) < 20, answer["counts"]
    assert answer["counts"]["writers"] == 20
    assert answer["counts"]["rowsReturned"] == len(answer["rows"])
    assert answer["readOn"]["writersRemaining"] == 20 - len(answer["rows"])
    assert answer["readOn"]["labelsShortened"] == 0
    assert {(row["role"], row["harness"], row["model"]) for row in answer["rows"]} <= labels
    assert len(_report(answer)["writers"]) == 20
