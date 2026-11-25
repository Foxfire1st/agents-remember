"""Where a record is shown, its state and its writer are shown with it.

Every case reads a memory tree that holds both shapes a reader meets until the conversion has run:

* a **converted** rule: it names no status word, carries the writer's stamp of its current revision,
  and the history file of the leaf that wrote it names that revision and holds the review fact; it
  reads ``reviewed``, written by ``curator`` with ``fixture-model``;
* an **unconverted** rule: it still types ``accepted`` (or ``proposed``) and carries no stamp, and
  its leaf's history file has neither ``written`` nor ``review``; it reads ``proposed``, written by
  ``unknown``, and the old word is shown nowhere as its status.

The places read are the reader's record view (``knowledge_read`` view ``record``, whose ``record``
the dashboard's knowledge reader shows), the rows of the tree views, and the intent review's
outline, whose rows are the review tree's family and subject rows.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application import tool_response
from agents_remember.application.knowledge_reader.paths import path_view
from agents_remember.application.knowledge_reader.selection import selection_for_dataset
from agents_remember.application.knowledge_reader.truth import record_list, record_view
from agents_remember.application.published_intent import select_knowledge_dataset
from agents_remember.application.review_outline import read_review_outline
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory.knowledge_index import build_index, directory_snapshot, text_uuid
from agents_remember.memory.knowledge_index.projection import INDEX_REPOSITORY_ID
from agents_remember.models.knowledge_files.documents import record_path
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import TOOL_ANSWER_OVERFLOW_MAX_TOKENS
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from knowledge_index_test_support import (
    DECISION,
    FAMILY,
    REVIEW_INVARIANT,
    SIBLING_INVARIANT,
    commit_all,
    init_repository,
    write_document,
    write_review_tree,
)
from knowledge_review_fact_test_support import history_document, review_fact, stamp, written

WRITING_LEAF = "261112-KNO-L3"
CONVERTED = REVIEW_INVARIANT
UNCONVERTED = SIBLING_INVARIANT
UNKNOWN_WRITER = {"role": "unknown", "harness": "unknown", "model": "unknown"}
WHOLE_WRITER = {"role": "curator", "harness": "fixture-harness", "model": "fixture-model"}


def _converted(memory: Path) -> None:
    """Make REVIEW_INVARIANT a converted rule: revision 2, stamped, written and reviewed."""

    path = next((memory / "knowledge" / "invariants").glob(f"{CONVERTED}-*.json"))
    document = json.loads(path.read_text(encoding="utf-8"))
    document.pop("status")
    document["revision"] = 2
    document["writer"] = stamp(WRITING_LEAF)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    history, fact = history_document(
        WRITING_LEAF,
        written_rows=(written(CONVERTED, 2, WRITING_LEAF),),
        review=review_fact(WRITING_LEAF),
    )
    write_document(memory, history, fact)


def _memory(root: Path) -> Path:
    """The fixture tree, every record of it unconverted, with REVIEW_INVARIANT converted."""

    memory = root / "memory"
    init_repository(memory)
    write_review_tree(memory)
    _converted(memory)
    commit_all(memory)
    return memory


def _read(root: Path, memory: Path, **request: Any) -> dict[str, Any]:
    out = knowledge_read_payload(
        ReadToolRequest(memory_root=str(memory), **request),
        coordination_root=str(root / "coord"),
    )
    TOOL_RESPONSE_MODELS["knowledge_read"].model_validate(out)
    assert out["state"] == "view", out
    return out


def test_the_record_view_answers_one_records_state_and_writer(tmp_path: Path) -> None:
    """The reader's answer for one record names its state and its writer as role, harness, model."""

    memory = _memory(tmp_path)
    record = _read(tmp_path, memory, view="record", record_id=CONVERTED)["record"]
    assert (record["id"], record["revision"]) == (CONVERTED, 2)
    assert record["recordState"] == "reviewed"
    assert record["writer"] == WHOLE_WRITER


def test_the_record_view_reads_an_unconverted_record_as_proposed_by_unknown(
    tmp_path: Path,
) -> None:
    """A record that still types ``accepted`` reads proposed, written by ``unknown``, no old word."""

    memory = _memory(tmp_path)
    record = _read(tmp_path, memory, view="record", record_id=UNCONVERTED)["record"]
    assert record["recordState"] == "proposed"
    assert record["writer"] == UNKNOWN_WRITER
    assert record["status"] == ""
    assert "accepted" not in json.dumps(record)


@pytest.mark.parametrize("word", ["proposed", "accepted"])
def test_an_old_status_word_is_not_shown_as_the_records_status(tmp_path: Path, word: str) -> None:
    memory = tmp_path / "memory"
    init_repository(memory)
    write_review_tree(memory)
    path = next((memory / "knowledge" / "invariants").glob(f"{UNCONVERTED}-*.json"))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["status"] = word
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    commit_all(memory)
    record = _read(tmp_path, memory, view="record", record_id=UNCONVERTED)["record"]
    assert (record["status"], record["recordState"], record["writer"]) == (
        "",
        "proposed",
        UNKNOWN_WRITER,
    )


def test_tree_view_rows_carry_record_state_and_a_compact_writer(tmp_path: Path) -> None:
    """The family view's record rows carry ``recordState`` and ``writer`` as ``role/model``.

    The row's ``state`` stays its currentness; a record no stamp names reads ``unknown/unknown``,
    and a realization, which is no record, carries neither.
    """

    memory = _memory(tmp_path)
    out = _read(
        tmp_path,
        memory,
        view="family",
        family_revision_id=text_uuid("revision", f"{FAMILY}@1"),
        limit=10,
    )
    rows: dict[str, dict[str, Any]] = {}
    for row in out["payload"]["rows"]:
        rows.setdefault(row["id"], row)
    converted, unconverted, family = rows[CONVERTED], rows[UNCONVERTED], rows[FAMILY]
    assert (converted["recordState"], converted["writer"]) == ("reviewed", "curator/fixture-model")
    assert converted["state"] in {"current", "stale", "unverifiable"}
    assert (unconverted["recordState"], unconverted["writer"]) == ("proposed", "unknown/unknown")
    assert (family["recordState"], family["writer"]) == ("proposed", "unknown/unknown")
    realizations = [row for row in out["payload"]["rows"] if row["kind"] == "realization"]
    assert realizations
    assert all("recordState" not in row and "writer" not in row for row in realizations)


# --------------------------------------------------------------------------------------------------
# The intent review's outline: the rows of the review tree, from the candidate tree's index
# --------------------------------------------------------------------------------------------------

RULE = "INV-7K3F9Q"
OLD_RULE = "INV-8M4G2R"
GONE_RULE = "INV-9N5H3S"
RULE_FAMILY = "FAM-STATEW"


def _rule(record_id: str, revision: int, **extra: Any) -> tuple[str, dict[str, Any]]:
    document = {
        "schema": "ar-invariant/v1",
        "id": record_id,
        "origin": {"task": "261112-KNO", "leaf": WRITING_LEAF},
        "revision": revision,
        "statement": f"{record_id} holds at revision {revision}.",
        "applicability": "Every fixture write.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
        **extra,
    }
    return record_path("invariant", record_id, record_id.lower()), document


def _rule_family(members: list[str]) -> tuple[str, dict[str, Any]]:
    document = {
        "schema": "ar-family/v1",
        "id": RULE_FAMILY,
        "origin": {"task": "261112-KNO", "leaf": WRITING_LEAF},
        "revision": 1,
        "status": "proposed",
        "title": "State and writer fixture",
        "guarantee": "Every member is shown with its state and writer.",
        "members": members,
        "routes": ["src"],
        "admission": "legacy-unassessed",
    }
    return record_path("family", RULE_FAMILY, "state-and-writer"), document


def _built(root: Path, name: str, files: list[tuple[str, dict[str, Any]]]) -> Path:
    tree = root / name
    tree.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    for path, document in files:
        write_document(tree, path, document)
    destination = root / f"{name}.sqlite"
    report = build_index(directory_snapshot(tree), destination)
    assert report.state == "complete", report.problems
    return destination


def test_the_review_outline_rows_carry_the_candidate_sides_state_and_writer(
    tmp_path: Path,
) -> None:
    """Family and subject rows carry state and writer (role, model) of the candidate tree K_C.

    The base tree holds every rule unconverted. In the candidate, RULE is rewritten, stamped and
    reviewed; OLD_RULE stays unconverted; GONE_RULE is only in the base, so it has no state there.
    """

    base = _built(
        tmp_path,
        "base",
        [
            _rule(RULE, 1, status="accepted"),
            _rule(OLD_RULE, 1, status="accepted"),
            _rule(GONE_RULE, 1, status="proposed"),
            _rule_family([RULE, OLD_RULE, GONE_RULE]),
        ],
    )
    candidate = _built(
        tmp_path,
        "candidate",
        [
            _rule(RULE, 2, writer=stamp(WRITING_LEAF, role="worker", model="model-b")),
            _rule(OLD_RULE, 1, status="accepted"),
            _rule_family([RULE, OLD_RULE]),
            history_document(
                WRITING_LEAF,
                written_rows=(written(RULE, 2, WRITING_LEAF, role="worker", model="model-b"),),
                review=review_fact(WRITING_LEAF),
            ),
        ],
    )
    resolved = SimpleNamespace(
        trees=object(),
        baseline_database=base,
        candidate_database=candidate,
        leaf_id=WRITING_LEAF,
    )
    outline = read_review_outline(resolved)  # type: ignore[arg-type]
    assert outline.state == "outlined", outline.detail
    rows = {row.id: row for row in outline.invariants}
    writer = rows[RULE].writer
    assert rows[RULE].record_state == "reviewed"
    assert writer is not None and writer.model_dump() == {"role": "worker", "model": "model-b"}
    unknown = {"role": "unknown", "model": "unknown"}
    old_writer = rows[OLD_RULE].writer
    assert rows[OLD_RULE].record_state == "proposed"
    assert old_writer is not None and old_writer.model_dump() == unknown
    assert (rows[GONE_RULE].record_state, rows[GONE_RULE].writer) == (None, None)
    (family,) = outline.families
    assert family.record_state == "proposed"
    assert family.writer is not None and family.writer.model_dump() == unknown
    assert "accepted" not in outline.model_dump_json()


# --------------------------------------------------------------------------------------------------
# The intent outline admits every writer a stamp admits
# --------------------------------------------------------------------------------------------------


def test_the_review_outline_keeps_a_writer_longer_than_a_short_label(tmp_path: Path) -> None:
    """A stamp admits a role and a model of up to 1,024 characters; the outline keeps them whole."""

    role, model = "r" * 513, "m" * 1024
    tree = [
        _rule(RULE, 1, writer=stamp(None, role=role, model=model)),
        _rule(OLD_RULE, 1, status="accepted"),
        _rule_family([RULE, OLD_RULE]),
    ]
    base = _built(tmp_path, "base", tree)
    candidate = _built(tmp_path, "candidate", tree)
    resolved = SimpleNamespace(
        trees=object(), baseline_database=base, candidate_database=candidate, leaf_id=WRITING_LEAF
    )
    outline = read_review_outline(resolved)  # type: ignore[arg-type]
    assert outline.state == "outlined", outline.detail
    rows = {row.id: row for row in outline.invariants}
    writer = rows[RULE].writer
    assert writer is not None and (writer.role, writer.model) == (role, model)
    assert rows[OLD_RULE].record_state == "proposed"
    assert [family.id for family in outline.families] == [RULE_FAMILY]


# --------------------------------------------------------------------------------------------------
# A kind's facet shows no legacy typed state; its own lifecycle words stay
# --------------------------------------------------------------------------------------------------

ASSUMPTION = "ASM-STATE1"


def _assumption_memory(root: Path, status: str) -> Path:
    memory = root / "memory"
    init_repository(memory)
    write_review_tree(memory)
    write_document(
        memory,
        record_path("assumption", ASSUMPTION, "review-reads-trees"),
        {
            "schema": "ar-assumption/v1",
            "id": ASSUMPTION,
            "origin": {"task": "261112-KNO", "leaf": WRITING_LEAF},
            "revision": 1,
            "status": status,
            "proposition": "A review reads Git trees, never a working tree.",
            "basis": "Observed.",
            "links": [{"target": REVIEW_INVARIANT, "relation": "underlies"}],
        },
    )
    commit_all(memory)
    return memory


@pytest.mark.parametrize("word", ["accepted", "proposed"])
def test_a_legacy_assumption_shows_no_typed_state_in_its_facet(tmp_path: Path, word: str) -> None:
    memory = _assumption_memory(tmp_path, word)
    out = _read(tmp_path, memory, view="record", record_id=ASSUMPTION)
    assert out["record"]["recordState"] == "proposed"
    assert "status" not in out["facet"]
    assert out["facet"]["proposition"] == "A review reads Git trees, never a working tree."
    assert out["record"]["status"] == ""
    assert word not in json.dumps(out["facet"])


def test_a_retired_assumption_and_an_active_decision_keep_their_lifecycle_words(
    tmp_path: Path,
) -> None:
    memory = _assumption_memory(tmp_path, "retired")
    assert _read(tmp_path, memory, view="record", record_id=ASSUMPTION)["facet"]["status"] == (
        "retired"
    )
    decision = _read(tmp_path, memory, view="record", record_id=DECISION)["decision"]
    assert (decision["storedStatus"], decision["derivedStatus"]) == ("active", "active")


# --------------------------------------------------------------------------------------------------
# The record answer's overflow short form, and the reader's path view and record lists
# --------------------------------------------------------------------------------------------------


def test_the_record_answers_short_form_keeps_state_and_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An answer over its bound is delivered as the short form; state and writer still ride it."""

    memory = _memory(tmp_path)
    monkeypatch.setattr(tool_response, "bound_for", lambda *_args, **_kwargs: 200)
    for record_id, writer in (
        (CONVERTED, "curator/fixture-model"),
        (UNCONVERTED, "unknown/unknown"),
    ):
        out = knowledge_read_payload(
            ReadToolRequest(memory_root=str(memory), view="record", record_id=record_id),
            coordination_root=str(tmp_path / "coord"),
        )
        assert out.get("overflow") is True, out
        assert "record" not in out
        expected = "reviewed" if record_id == CONVERTED else "proposed"
        assert (out.get("recordState"), out.get("writer")) == (expected, writer), out
        assert count_response_tokens(out) <= TOOL_ANSWER_OVERFLOW_MAX_TOKENS


def _selection(root: Path, memory: Path) -> Any:
    selected = select_knowledge_dataset(memory, coordination_root=root / "coord")
    assert selected.memory_tree is not None
    return selection_for_dataset(
        repository_id=INDEX_REPOSITORY_ID,
        memory_root=memory,
        database_path=selected.database_path,
        tree_key=selected.memory_tree.tree_key,
    )


def _summaries(node: Any, found: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict]]:
    """Every record summary of an answer by record id (a dict with an id, a kind and a title)."""

    if isinstance(node, dict):
        if isinstance(node.get("id"), str) and "kind" in node and "title" in node:
            found.setdefault(node["id"], []).append(node)
        for inner in node.values():
            _summaries(inner, found)
    elif isinstance(node, list):
        for inner in node:
            _summaries(inner, found)
    return found


def test_the_readers_path_view_and_record_list_name_state_and_writer(tmp_path: Path) -> None:
    """Every record row of the path view, the record list and a family's members names both."""

    memory = _memory(tmp_path)
    selection = _selection(tmp_path, memory)
    try:
        answers = {
            "path": path_view(selection, "dashboard/src/data/review.ts"),
            "records": record_list(selection),
            "family": (record_view(selection, FAMILY) or {}).get("family"),
        }
    finally:
        selection.close()
    for name, answer in answers.items():
        rows = _summaries(answer, {})
        assert CONVERTED in rows and UNCONVERTED in rows, (name, sorted(rows))
        for row in rows[CONVERTED]:
            assert (row["recordState"], row["writer"]) == ("reviewed", WHOLE_WRITER), name
        for row in rows[UNCONVERTED]:
            assert (row["recordState"], row["writer"]) == ("proposed", UNKNOWN_WRITER), name
