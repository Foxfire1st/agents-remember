"""MIK-R118 and MIK-R124: the two record kinds, their writer rules and the staleness derivation.

The cases protect the operations a reviewer grades: a weakness of each kind is written with its
file anchor and refused without a file, with an unknown kind or with an overlong sentence; a
dependent is written with each kind of link and refused without a link, for a path that is not a
tracked file, and as a second record for one document; and a document's state is derived -- an
anchor or blob that moved makes it stale, an unchanged value keeps it current, and no byte of the
record changes when it does.

Later cases add what the two packets owe a reader and a landing: the card read's one-line counts,
the two worklist kinds and their currency, and the count a landing answers with over real trees.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest
from agents_remember.application.knowledge_currentness.observe import CodeTree, open_code_tree
from agents_remember.application.knowledge_gate import landing as landing_module
from agents_remember.application.knowledge_gate.adapter import KnowledgeGate
from agents_remember.application.knowledge_gate.landing import (
    DocumentStaleness,
    document_staleness,
)
from agents_remember.application.knowledge_gate.predicates import (
    GateContext,
    changed_document_open,
    weakness_anchor_open,
)
from agents_remember.application.knowledge_reader.marks import (
    _moved_link,
    _moving_commit,
    _Observe,
    dependent_counts,
    dependent_movement,
    open_weakness_counts,
)
from agents_remember.application.knowledge_worklist import ITEM_KINDS
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_worklist.compute import WorklistInputs, compute_worklist
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.marks import mark_items
from agents_remember.application.knowledge_worklist.onboarding_trace import (
    TraceSideRequest,
    onboarding_trace_sides,
)
from agents_remember.application.knowledge_writer.authoring import code_state_of
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.writer import WriteRequest, write_knowledge
from agents_remember.application.landing_answer_shapes import project_landing_answer
from agents_remember.application.read_files import _add_mark_lines, _BatchScope
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.server import create_server
from agents_remember.mcp.tools import knowledge as _knowledge
from agents_remember.mcp.tools.knowledge import ReadToolRequest
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory.knowledge_index import directory_snapshot
from agents_remember.memory.knowledge_index.build import IndexedRecord, ParsedTree
from agents_remember.memory_quality.knowledge_validator.commit_route import GitKnowledgeValidation
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.registry import ValidationContext
from agents_remember.memory_quality.knowledge_validator.rules_history import check_history_rows
from agents_remember.models.direct_landing import DirectLandingResponse
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    dependent_link_index,
    record_path,
)
from agents_remember.models.knowledge_files.history import RecordRow
from agents_remember.models.knowledge_files.records import (
    WEAKNESS_KINDS,
    DependentAnchorLink,
    DependentFileLink,
    DependentRecord,
    WeaknessRecord,
)
from agents_remember.models.knowledge_files.shapes import ONE_SENTENCE_MAX
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.knowledge_responses import KnowledgeWriteResponse
from agents_remember.models.tools.tool_answer import TOOL_ANSWER_LIST_MAX, short_form
from agents_remember.worktrees.knowledge_gate import landing_document_answer, landing_gate_refusal
from agents_remember.worktrees.modules.onboarding import (
    OnboardingTraceInputs,
    onboarding_refresh_plan_for_context,
    onboarding_trace_gate_for_context,
)
from agents_remember.worktrees.services import (
    LandingGateRequest,
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from knowledge_index_test_support import write_document
from knowledge_validator_test_support import encode, tree
from knowledge_writer_test_support import BASE_FAMILY, BASE_INVARIANT, build_world, commit_all, git
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

ANCHOR = {
    "path": "cli/tool.py",
    "locator": {"kind": "symbol", "name": "_receipts"},
    "blob": "0" * 40,
    "content": "sha256:" + "1" * 64,
}
REVISION = {
    "task": "260928-MIK-L118",
    "leaf": None,
    "handoffEntry": "weakness-1",
}


def _weakness(**overrides):
    document = {
        "schema": "ar-weakness/v1",
        "id": "WKN-7K3F9Q",
        "revision": 1,
        "kind": "time",
        "statement": "The reader walks every receipt file on each message, which costs a message's time.",
        "anchor": dict(ANCHOR),
        "seen": {"task": "260928-MIK-L118", "role": "worker"},
        "state": "open",
        "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L118"},
    }
    document.update(overrides)
    return document


def _dependent(**overrides):
    document = {
        "schema": "ar-dependent/v1",
        "id": "DPD-7K3F9Q",
        "revision": 1,
        "document": "docs/reference/settings-json.md",
        "stands_on": [{"kind": "file", "path": "contract.json", "blob": "a" * 40}],
        "confirmed": {
            "task": "260928-MIK-L118",
            "role": "curator",
            "statement": "The document still describes the code it stands on.",
            "commit": "b" * 40,
        },
        "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L118"},
    }
    document.update(overrides)
    return document


def test_every_weakness_kind_is_a_record_and_the_list_is_one_place():
    assert set(WEAKNESS_KINDS) == {"context", "compute", "storage", "time", "fault", "debt"}
    for kind in WEAKNESS_KINDS:
        assert WeaknessRecord.model_validate(_weakness(kind=kind)).kind == kind


def test_a_weakness_is_refused_without_a_file_with_an_unknown_kind_and_over_the_bound():
    without_file = _weakness()
    without_file["anchor"] = {key: value for key, value in ANCHOR.items() if key != "path"}
    with pytest.raises(ValidationError, match=r"anchor\.path"):
        WeaknessRecord.model_validate(without_file)
    with pytest.raises(ValidationError, match="kind"):
        WeaknessRecord.model_validate(_weakness(kind="smell"))
    with pytest.raises(ValidationError, match="statement"):
        WeaknessRecord.model_validate(_weakness(statement="x" * (ONE_SENTENCE_MAX + 1)))


def test_a_weakness_is_closed_with_the_task_and_commit_and_an_open_one_carries_none():
    closed = _weakness(state="closed", closed={"task": "260928-MIK-L200", "commit": "c" * 40})
    closed_record = WeaknessRecord.model_validate(closed)
    assert closed_record.closed is not None
    assert closed_record.closed.task == "260928-MIK-L200"
    with pytest.raises(ValidationError, match="closed"):
        WeaknessRecord.model_validate(_weakness(state="closed"))
    with pytest.raises(ValidationError, match="closed"):
        WeaknessRecord.model_validate(
            _weakness(closed={"task": "260928-MIK-L200", "commit": "c" * 40})
        )


def test_a_weakness_carries_no_priority_and_no_second_sentence_field():
    with pytest.raises(ValidationError):
        WeaknessRecord.model_validate(_weakness(priority="high"))
    with pytest.raises(ValidationError):
        WeaknessRecord.model_validate(_weakness(proposal="Fix it later."))


def test_a_dependent_is_written_with_each_kind_of_link():
    record = DependentRecord.model_validate(
        _dependent(
            stands_on=[
                {"kind": "anchor", "heading": "The pin", "anchor": dict(ANCHOR)},
                {"kind": "file", "path": "contract.json", "blob": "a" * 40},
                {"kind": "record", "id": "INV-7K3F9Q", "revision": 3},
            ]
        )
    )
    assert [link.kind for link in record.stands_on] == ["anchor", "file", "record"]


def test_a_dependent_is_refused_without_a_link_or_as_a_second_record_for_one_document():
    with pytest.raises(ValidationError, match="stands_on"):
        DependentRecord.model_validate(_dependent(stands_on=[]))
    with pytest.raises(ValidationError, match="stands_on"):
        DependentRecord.model_validate(
            _dependent(
                stands_on=[
                    {"kind": "file", "path": "contract.json", "blob": "a" * 40},
                    {"kind": "file", "path": "contract.json", "blob": "a" * 40},
                ]
            )
        )
    document = _dependent()
    document["document"] = "docs/a.md"
    document["id"] = "DPD-8M4G0R"
    different = DependentRecord.model_validate(document)
    first = DependentRecord.model_validate(_dependent())
    # One record per document is the writer's rule (there is no tree here): the two records are
    # refused together by the writer's own scan, and both models parse on their own.
    assert different.document != first.document
    with pytest.raises(ValidationError):
        DependentRecord.model_validate(_dependent(document="../outside.md"))


def _code(files, problem=None):
    return SimpleNamespace(files=files, problem=problem)


def _file_blobs(record) -> dict[str, str]:
    """The path→blob map of a dependent's whole-file links, for a code tree built in a case."""

    return {
        link.path: link.blob for link in record.stands_on if isinstance(link, DependentFileLink)
    }


def test_a_file_link_moves_when_its_blob_differs_and_stands_when_it_is_the_same():
    record = DependentRecord.model_validate(_dependent())
    revisions = {}
    state, moved = dependent_movement(
        record, revision_of=revisions.get, code=_code({"contract.json": "a" * 40}), tree=None
    )
    assert (state, moved) == ("current", [])
    state, moved = dependent_movement(
        record, revision_of=revisions.get, code=_code({"contract.json": "d" * 40}), tree=None
    )
    assert state == "stale"
    # The row names the values and, since no code tree was resolved here, why no commit can be
    # named for the movement (F15): a stale row never leaves the movement unattributed or guessed.
    assert moved == [
        {
            "position": 1,
            "kind": "file",
            "path": "contract.json",
            "state": "stale",
            "recordedBlob": "a" * 40,
            "observedBlob": "d" * 40,
            "movingCommitUnknown": "no code tree was resolved for this read",
        }
    ]
    state, moved = dependent_movement(record, revision_of=revisions.get, code=_code({}), tree=None)
    assert state == "stale" and moved[0]["reason"] == "the file is gone"


def test_a_record_link_moves_when_the_stored_revision_differs():
    record = DependentRecord.model_validate(
        _dependent(stands_on=[{"kind": "record", "id": "INV-7K3F9Q", "revision": 3}])
    )
    state, _moved = dependent_movement(
        record, revision_of={"INV-7K3F9Q": 3}.get, code=_code({}), tree=None
    )
    assert state == "current"
    state, moved = dependent_movement(
        record, revision_of={"INV-7K3F9Q": 4}.get, code=_code({}), tree=None
    )
    assert state == "stale"
    assert moved[0]["recordedRevision"] == 3 and moved[0]["revision"] == 4


def test_the_derivation_writes_nothing_into_the_record():
    record = DependentRecord.model_validate(
        _dependent(stands_on=[{"kind": "file", "path": "contract.json", "blob": "a" * 40}])
    )
    before = record.to_document()
    dependent_movement(
        record, revision_of={}.get, code=_code({"contract.json": "e" * 40}), tree=None
    )
    assert record.to_document() == before
    assert "state" not in before


def test_the_landing_answer_names_the_count_and_is_absent_when_no_document_moved():
    assert DocumentStaleness().answer() is None
    answer = DocumentStaleness(documents=("docs/reference/settings-json.md",)).answer()
    assert answer is not None
    assert answer["count"] == 1
    assert answer["read"] == {
        "view": "dependent",
        "staleOnly": True,
        "memoryRoot": "the landing's memory tree",
    }
    uncomputable = DocumentStaleness(uncomputable="the tree cannot be read").answer()
    assert uncomputable == {"state": "uncomputable", "detail": "the tree cannot be read"}
    refused = DocumentStaleness(documents=("docs/a.md", "docs/b.md")).refusal_message()
    assert "2 document(s)" in refused and "view=dependent" in refused


def _side(records):
    parsed = ParsedTree()
    for record in records:
        parsed.records[record.id] = IndexedRecord(
            path=f"knowledge/{record.record_kind}s/{record.id}-x.json", record=record
        )
    return KnowledgeSide(label="K_B", key="tree", converted=True, parsed=parsed, files={})


def test_a_changed_file_raises_the_weakness_row_and_a_changed_document_raises_its_item():
    weakness = WeaknessRecord.model_validate(_weakness())
    dependent = DependentRecord.model_validate(_dependent())
    items = mark_items(
        _side([weakness, dependent]), {"cli/tool.py", "docs/reference/settings-json.md"}
    )
    assert [(one["kind"], one["subject"]) for one in items] == [
        ("weakness_anchor", weakness.id),
        ("changed_document", dependent.id),
    ]
    assert items[0]["facts"]["question"] == "does it still stand"
    assert items[0]["id"].startswith("sha256:")
    # A file this task did not change raises nothing for either kind.
    assert mark_items(_side([weakness, dependent]), set()) == []


# --- what a card's read names, and what refuses nothing ---------------------------------------


def _selection(records):
    """A reader selection good enough for the two count functions: the index and its records."""

    by_kind: dict[str, list] = {"weakness": [], "dependent": []}
    for record in records:
        kind = "weakness" if isinstance(record, WeaknessRecord) else "dependent"
        by_kind[kind].append(SimpleNamespace(document=record.to_document()))
    index = SimpleNamespace(records_of_kind=lambda kind: SimpleNamespace(value=by_kind[kind]))
    return cast(Any, SimpleNamespace(index=index))


def test_a_card_read_names_one_count_per_kind_and_no_sentence():
    weakness = WeaknessRecord.model_validate(_weakness())
    closed = WeaknessRecord.model_validate(
        _weakness(id="WKN-8M4G0R", state="closed", closed={"task": "L200", "commit": "c" * 40})
    )
    dependent = DependentRecord.model_validate(_dependent())
    selection = _selection([weakness, closed, dependent])
    counts = open_weakness_counts(selection)
    # A closed weakness is not counted, and a file with none is absent rather than zero.
    assert counts == {"cli/tool.py": 1}
    standing = dependent_counts(selection)
    assert standing == {"contract.json": 1}
    # The line is a number: no statement, measurement or document text travels with it.
    assert all(isinstance(value, int) for value in (*counts.values(), *standing.values()))


def _row(subject: str, disposition: str, *, revision: int = 1) -> RecordRow:
    fields = {
        "id": "ROW-7K3F9Q",
        "subject": subject,
        "disposition": disposition,
        "reason": "the row's one sentence",
        "items": (subject,),
        "revision": revision,
    }
    effect = {"changed": "clarify", "retired": "retire"}.get(disposition)
    return RecordRow(**({**fields, "effect": effect} if effect else fields))


def _gate_context(row: RecordRow, candidate_records=()):
    """The predicate's view: the leaf's history row about one subject, and K_C's records."""

    history = SimpleNamespace(row_about=lambda subject: row if subject == row.subject else None)
    parsed = _side(list(candidate_records)).parsed
    candidate = SimpleNamespace(
        revision=lambda subject: (
            parsed.records[subject].record.revision if subject in parsed.records else None
        ),
        parsed=parsed,
    )
    return cast(Any, SimpleNamespace(history=history, candidate=candidate, owner="260928-MIK-L118"))


def test_a_standing_weakness_refuses_nothing_and_answers_its_row():
    standing = _row("WKN-7K3F9Q", "no_impact")
    assert weakness_anchor_open({"subject": "WKN-7K3F9Q"}, _gate_context(standing)) is None
    # A changed row answers it as well: the Curator moved the anchor or closed the weakness.
    assert (
        weakness_anchor_open(
            {"subject": "WKN-7K3F9Q"}, _gate_context(_row("WKN-7K3F9Q", "changed"))
        )
        is None
    )
    # An item without its row is the only thing this rule names.
    silent = _gate_context(standing)
    assert weakness_anchor_open({"subject": "WKN-8M4G0R"}, silent) is not None


def test_a_no_impact_row_does_not_answer_a_changed_document_but_a_changed_one_does():
    document = "DPD-7K3F9Q"
    dependent = DependentRecord.model_validate(_dependent())
    item = {"subject": document, "facts": {"document": dependent.document, "confirmedTask": "L99"}}
    refusal = changed_document_open(item, _gate_context(_row(document, "no_impact"), [dependent]))
    assert refusal is not None and "no_impact" in refusal
    answering = _gate_context(_row(document, "changed"), [dependent])
    answering.code = _code(_file_blobs(dependent))
    assert changed_document_open(item, answering) is None


def test_a_changed_row_cannot_stand_in_for_this_task_s_confirmation():
    """F07: the row must belong to a record this task confirmed, for the document it changed."""

    dependent = DependentRecord.model_validate(_dependent())
    previous = "260928-MIK-L99"
    item = {
        "subject": dependent.id,
        "facts": {"document": dependent.document, "confirmedTask": previous},
    }
    changed = _row(dependent.id, "changed")
    # The record still carries the previous task's confirmation: the label answers nothing.
    stale = DependentRecord.model_validate(
        _dependent(confirmed={"task": previous, "role": "curator", "commit": "b" * 40})
    )
    stale_context = _gate_context(changed, [stale])
    stale_context.code = _code(_file_blobs(stale))
    refusal = changed_document_open(item, stale_context)
    assert refusal is not None and "still confirmed by" in refusal
    # A record this task confirmed answers it -- this task is the context's owner.
    fresh = DependentRecord.model_validate(
        _dependent(confirmed={"task": "260928-MIK-L118", "role": "curator", "commit": "b" * 40})
    )
    fresh_context = _gate_context(changed, [fresh])
    fresh_context.code = _code(_file_blobs(fresh))
    assert changed_document_open(item, fresh_context) is None
    # A record naming another document answers nothing either.
    moved = DependentRecord.model_validate(
        _dependent(
            document="docs/reference/other.md",
            confirmed={"task": "L118-successor", "role": "curator", "commit": "b" * 40},
        )
    )
    refusal = changed_document_open(item, _gate_context(changed, [moved]))
    assert refusal is not None and "not the changed document" in refusal
    # A row whose revision is not K_C's is refused before any of that is believed.
    wrong = _row(dependent.id, "changed", revision=2)
    refusal = changed_document_open(item, _gate_context(wrong, [fresh]))
    assert refusal is not None and "records revision" in refusal


def test_a_removed_dependent_is_answered_by_its_retired_row():
    """F05: a deleted document's record removal is the answer, not a changed row about nothing."""

    item = {"subject": "DPD-7K3F9Q", "facts": {"document": "docs/gone.md", "confirmedTask": "L99"}}
    changed = _row("DPD-7K3F9Q", "changed")
    refusal = changed_document_open(item, _gate_context(changed, []))
    assert refusal is not None and "K_C holds no record" in refusal
    assert changed_document_open(item, _gate_context(_row("DPD-7K3F9Q", "retired"), [])) is None


def test_the_two_kinds_are_registered_where_the_worklist_reads_them():
    assert {"weakness_anchor", "changed_document"} <= set(ITEM_KINDS)
    assert ITEM_KINDS["weakness_anchor"].owner == "MIK-R118"
    assert ITEM_KINDS["changed_document"].owner == "MIK-R124"


# --- the count a landing answers over real trees ---------------------------------------------


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=repository, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    return root


def _commit(root: Path, message: str) -> str:
    marker_path = root / LAYOUT_MARKER_PATH
    if marker_path.exists():
        marker = json.loads(marker_path.read_bytes())
        links = dependent_link_index(
            (
                path.relative_to(root).as_posix(),
                DependentRecord.model_validate_json(path.read_bytes()),
            )
            for path in root.glob("knowledge/dependents/*.json")
        )
        if links is None:
            marker.pop("dependent_links", None)
        else:
            marker["dependent_links"] = links
        marker_path.write_text(canonical_text(marker), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _blob(root: Path, relative: str) -> str:
    return _git(root, "hash-object", relative)


def _landing_world(root: Path) -> tuple[Path, Path, str, str, str, str]:
    """A memory tree holding one dependent, and a code tree it stands on.

    Returns the memory repository, the code repository, the memory base and tip, and the code base
    and tip. The memory's tip commit changes nothing the dependent records; the code's tip commit
    changes the file the dependent stands on.
    """

    code = _repository(root / "code")
    (code / "cli").mkdir(parents=True, exist_ok=True)
    (code / "cli" / "tool.py").write_text("one\n", encoding="utf-8")
    code_base = _commit(code, "one")

    memory = _repository(root / "memory")
    write_document(
        memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    write_document(
        memory,
        record_path("dependent", "DPD-7K3F9Q", "settings-json"),
        _dependent(
            document="docs/reference/settings-json.md",
            stands_on=[{"kind": "file", "path": "cli/tool.py", "blob": _blob(code, "cli/tool.py")}],
            confirmed={"task": "260928-MIK-L118", "role": "curator", "commit": code_base},
        ),
    )
    memory_base = _commit(memory, "the dependent at the base")
    (memory / "memory.md").write_text("the ledger\n", encoding="utf-8")
    memory_tip = _commit(memory, "an unrelated memory change")

    (code / "cli" / "tool.py").write_text("two\n", encoding="utf-8")
    code_tip = _commit(code, "two")
    return memory, code, memory_base, memory_tip, code_base, code_tip


def _request(world: tuple) -> LandingGateRequest:
    memory, code, memory_base, memory_tip, code_base, code_tip = world
    return LandingGateRequest(
        memory_repository=memory,
        memory_commit=memory_tip,
        memory_bases=(memory_base,),
        code_repository=code,
        code_commit=code_tip,
        code_base=code_base,
    )


def test_the_landing_names_the_documents_it_made_stale_and_only_those(tmp_path):
    world = _landing_world(tmp_path)
    staleness = document_staleness(_request(world))
    assert staleness.uncomputable is None
    assert staleness.documents == ("docs/reference/settings-json.md",)
    answer = staleness.answer()
    assert answer is not None and answer["count"] == 1
    read = cast(dict[str, Any], answer["read"])
    assert read["view"] == "dependent"
    assert read["staleOnly"] is True

    # A landing that changes nothing the document stands on makes nothing stale: no field at all.
    memory, code, memory_base, memory_tip, code_base, _tip = world
    quiet = document_staleness(
        LandingGateRequest(
            memory_repository=memory,
            memory_commit=memory_tip,
            memory_bases=(memory_base,),
            code_repository=code,
            code_commit=code_base,
            code_base=code_base,
        )
    )
    assert quiet.documents == () and quiet.answer() is None


def test_a_document_already_stale_at_the_base_is_named_and_refuses_nothing(tmp_path):
    code = _repository(tmp_path / "code")
    (code / "cli").mkdir(parents=True, exist_ok=True)
    (code / "cli" / "tool.py").write_text("one\n", encoding="utf-8")
    code_base = _commit(code, "one")

    memory = _repository(tmp_path / "memory")
    write_document(
        memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    write_document(
        memory,
        record_path("dependent", "DPD-7K3F9Q", "settings-json"),
        _dependent(
            stands_on=[{"kind": "file", "path": "cli/tool.py", "blob": "0" * 40}],
            confirmed={"task": "260928-MIK-L118", "role": "curator", "commit": code_base},
        ),
    )
    memory_base = _commit(memory, "the dependent at the base")
    memory_tip = memory_base
    (code / "cli" / "tool.py").write_text("two\n", encoding="utf-8")
    code_tip = _commit(code, "two")

    staleness = document_staleness(
        _request((memory, code, memory_base, memory_tip, code_base, code_tip))
    )
    assert staleness.documents == ()
    assert staleness.already_stale == ("docs/reference/settings-json.md",)
    # An ordinary task's landing says nothing; a master's end names it and still refuses nothing.
    assert staleness.answer() is None
    master = staleness.answer(name_already_stale=True)
    assert master is not None and master["count"] == 0
    assert master["alreadyStale"] == ["docs/reference/settings-json.md"]
    assert "already stale at the base" in staleness.refusal_message()


def test_a_state_that_cannot_be_computed_is_named_in_place_of_the_count(tmp_path):
    world = _landing_world(tmp_path)
    memory, code, _memory_base, _memory_tip, code_base, code_tip = world
    request = LandingGateRequest(
        memory_repository=memory,
        memory_commit="f" * 40,
        memory_bases=(code_base,),
        code_repository=code,
        code_commit=code_tip,
        code_base=code_base,
    )
    staleness = document_staleness(request)
    assert staleness.uncomputable is not None
    assert staleness.answer() == {"state": "uncomputable", "detail": staleness.uncomputable}


def _gate_repository(root: Path, *, weakness_at_base: bool) -> tuple:
    """A converted memory pair and its code: one leaf, one weakness, no work.

    The leaf's history file is closed and holds no row, so the gate has nothing else to judge. The
    weakness sits in the base or only in the tip, which is the difference between an inherited
    weakness and one this leaf records itself.
    """

    code = _repository(root / "code")
    (code / "cli").mkdir(parents=True, exist_ok=True)
    (code / "cli" / "tool.py").write_text("one\n", encoding="utf-8")
    code_commit = _commit(code, "one")

    memory = _repository(root / "memory")
    write_document(
        memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    write_document(
        memory,
        "knowledge/history/260928-MIK-L118.json",
        {"schema": "ar-history/v1", "leaf": "260928-MIK-L118", "closed": True, "rows": []},
    )
    weakness = _weakness(
        anchor={
            "path": "cli/tool.py",
            "locator": {"kind": "symbol", "name": "tool"},
            "blob": _blob(code, "cli/tool.py"),
            "content": "sha256:" + "1" * 64,
        }
    )
    path = record_path("weakness", weakness["id"], "receipts")
    if weakness_at_base:
        write_document(memory, path, weakness)
    base = _commit(memory, "the base")
    if not weakness_at_base:
        write_document(memory, path, weakness)
    (memory / "memory.md").write_text("the ledger\n", encoding="utf-8")
    tip = _commit(memory, "the tip")
    return memory, code, base, tip, code_commit


@pytest.fixture
def _bound_services():
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=cast(Any, None),
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


@pytest.mark.parametrize("weakness_at_base", [True, False])
def test_the_validator_and_the_gate_pass_with_an_open_weakness(
    _bound_services, tmp_path, weakness_at_base
):
    """MIK-R118 rule 4: an inherited and a newly recorded weakness both refuse nothing."""

    memory, code, base, tip, code_commit = _gate_repository(
        tmp_path, weakness_at_base=weakness_at_base
    )
    request = LandingGateRequest(
        memory_repository=memory,
        memory_commit=tip,
        memory_bases=(base,),
        code_repository=code,
        code_commit=code_commit,
        code_base=code_commit,
        leaf_owner="260928-MIK-L118",
    )
    assert landing_gate_refusal(request) is None


def test_the_card_row_carries_a_count_only_when_there_is_one():
    """MIK-R118 rule 5 and MIK-R124 rule 7: one short line, absent at zero, never the text."""

    def scope(**overrides):
        fields = {
            "memory_state": "converted",
            "dataset": SimpleNamespace(),
            "code_tree": None,
            "working_folder": None,
            "object_directory": None,
        }
        return _BatchScope(**{**fields, **overrides})

    counted: dict = {"path": "cli/tool.py", "exists": True}
    _add_mark_lines(counted, "cli/tool.py", scope(open_weaknesses={"cli/tool.py": 2}))
    assert counted["openWeaknesses"] == 2 and "dependentDocuments" not in counted
    assert set(counted) == {"path", "exists", "openWeaknesses"}

    both: dict = {"path": "cli/tool.py", "exists": True}
    _add_mark_lines(
        both,
        "cli/tool.py",
        scope(dependent_documents={"cli/tool.py": 1}, open_weaknesses={"cli/tool.py": 3}),
    )
    assert both["openWeaknesses"] == 3 and both["dependentDocuments"] == 1

    # No record on the file: no line at all, and no field standing in for one.
    quiet: dict = {"path": "cli/tool.py", "exists": True}
    _add_mark_lines(quiet, "cli/tool.py", scope())
    assert quiet == {"path": "cli/tool.py", "exists": True}

    # A count that could not be taken is said, and is never reported as none.
    failed: dict = {"path": "cli/tool.py", "exists": True}
    _add_mark_lines(failed, "cli/tool.py", scope(counts_problem="the tree could not be read"))
    assert failed["statesProblem"] == "the tree could not be read"


def _contract(memory: Path, code: Path, memory_base: str, code_base: str, kind: str = "leaf"):
    """The six cells the landing answer reads, over the real trees a case built."""

    return cast(
        Any,
        SimpleNamespace(
            kind=kind,
            memory_mode="external",
            memory_repo_path=memory,
            memory_base_commit=memory_base,
            code_repo_path=code,
            code_base_commit=code_base,
            sync_log=(),
        ),
    )


def test_the_landing_answer_carries_the_count_and_is_absent_when_nothing_moved(tmp_path):
    """MIK-R124 rule 4: the landing tool's own answer field, not only the computation."""

    memory, code, memory_base, memory_tip, code_base, code_tip = _landing_world(tmp_path)
    contract = _contract(memory, code, memory_base, code_base)
    answered = landing_document_answer(contract, code_commit=code_tip, memory_commit=memory_tip)
    assert answered is not None and answered["count"] == 1
    quiet = landing_document_answer(contract, code_commit=code_base, memory_commit=memory_tip)
    assert quiet is None


def _stale_world(root: Path, *, recorded_blob: str | None = None) -> tuple:
    """A memory pair whose one dependent stands on a file the code's tip changed."""

    code = _repository(root / "code")
    (code / "cli").mkdir(parents=True, exist_ok=True)
    (code / "cli" / "tool.py").write_text("one\n", encoding="utf-8")
    code_base = _commit(code, "one")
    blob = _blob(code, "cli/tool.py") if recorded_blob is None else recorded_blob

    memory = _repository(root / "memory")
    write_document(
        memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    write_document(
        memory,
        record_path("dependent", "DPD-7K3F9Q", "settings-json"),
        _dependent(
            stands_on=[{"kind": "file", "path": "cli/tool.py", "blob": blob}],
            confirmed={"task": "260928-MIK-L118", "role": "curator", "commit": code_base},
        ),
    )
    memory_base = _commit(memory, "the dependent at the base")
    (memory / "memory.md").write_text("the ledger\n", encoding="utf-8")
    memory_tip = _commit(memory, "the tip")

    (code / "cli" / "tool.py").write_text("two\n", encoding="utf-8")
    code_tip = _commit(code, "two")
    return memory, code, memory_base, memory_tip, code_base, code_tip


def test_the_masters_end_refuses_and_passes_after_the_confirmation(_bound_services, tmp_path):
    """MIK-R124 rule 8: refused with what its own task made stale, then passing once confirmed."""

    memory, code, memory_base, memory_tip, code_base, code_tip = _stale_world(tmp_path)
    refused = landing_gate_refusal(
        LandingGateRequest(
            memory_repository=memory,
            memory_commit=memory_tip,
            memory_bases=(memory_base,),
            code_repository=code,
            code_commit=code_tip,
            code_base=code_base,
            series_contract="/tmp/series-contract.md",
            # This is the landing that finishes the master: only it refuses document debt (F10).
            master_end=True,
        )
    )
    assert refused is not None
    assert "1 document(s) are stale" in refused
    assert "view=dependent" in refused and "staleOnly=true" in refused
    assert "The remedy is a task of this master" in refused

    # The confirmation (MIK-R124 rule 5): the dependent records what stands at the tip. The memory
    # tree the landing reads is the candidate's, so the confirmed record is its own commit.
    write_document(
        memory,
        record_path("dependent", "DPD-7K3F9Q", "settings-json"),
        _dependent(
            stands_on=[{"kind": "file", "path": "cli/tool.py", "blob": _blob(code, "cli/tool.py")}],
            confirmed={"task": "260928-MIK-L118", "role": "curator", "commit": code_tip},
        ),
    )
    confirmed = _commit(memory, "the confirmation")
    assert (
        landing_gate_refusal(
            LandingGateRequest(
                memory_repository=memory,
                memory_commit=confirmed,
                memory_bases=(memory_tip,),
                code_repository=code,
                code_commit=code_tip,
                code_base=code_base,
                series_contract="/tmp/series-contract.md",
                master_end=True,
            )
        )
        is None
    )


def test_the_masters_end_names_a_document_already_stale_at_its_base_and_refuses_nothing(
    _bound_services, tmp_path
):
    memory, code, memory_base, memory_tip, code_base, code_tip = _stale_world(
        tmp_path, recorded_blob="0" * 40
    )
    request = LandingGateRequest(
        memory_repository=memory,
        memory_commit=memory_tip,
        memory_bases=(memory_base,),
        code_repository=code,
        code_commit=code_tip,
        code_base=code_base,
        series_contract="/tmp/series-contract.md",
        master_end=True,
    )
    assert landing_gate_refusal(request) is None
    staleness = document_staleness(request)
    assert staleness.documents == ()
    assert staleness.already_stale == ("docs/reference/settings-json.md",)


def test_a_changed_document_without_a_record_raises_no_item_of_this_kind():
    """MIK-R124 rule 6: nothing changes for a document that has no dependent."""

    dependent = DependentRecord.model_validate(_dependent())
    assert mark_items(_side([dependent]), {"docs/other.md", "cli/tool.py"}) == []


def test_only_the_documents_linked_at_a_changed_path_are_read(tmp_path):
    """MIK-R124 rule 3's last clause: one landing reads the dependents the change can move."""

    code = _repository(tmp_path / "code")
    (code / "cli").mkdir(parents=True, exist_ok=True)
    (code / "cli" / "tool.py").write_text("one\n", encoding="utf-8")
    (code / "cli" / "other.py").write_text("one\n", encoding="utf-8")
    code_base = _commit(code, "one")
    changed_blob = _blob(code, "cli/tool.py")
    still_blob = _blob(code, "cli/other.py")

    memory = _repository(tmp_path / "memory")
    write_document(
        memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    for name, document, path, blob in (
        ("DPD-7K3F9Q", "docs/a.md", "cli/tool.py", changed_blob),
        ("DPD-8M4G0R", "docs/b.md", "cli/other.py", still_blob),
    ):
        write_document(
            memory,
            record_path("dependent", name, "doc"),
            _dependent(
                id=name,
                document=document,
                stands_on=[{"kind": "file", "path": path, "blob": blob}],
                confirmed={"task": "260928-MIK-L118", "role": "curator", "commit": code_base},
            ),
        )
    memory_base = _commit(memory, "the base")
    (memory / "memory.md").write_text("the ledger\n", encoding="utf-8")
    memory_tip = _commit(memory, "the tip")

    (code / "cli" / "tool.py").write_text("two\n", encoding="utf-8")
    code_tip = _commit(code, "two")

    staleness = document_staleness(
        _request((memory, code, memory_base, memory_tip, code_base, code_tip))
    )
    # One of the two documents is read; the other stands on a file this range did not change.
    assert staleness.examined == ("docs/a.md",)
    assert staleness.documents == ("docs/a.md",)


# --- the writer end to end, over a converted memory repository -------------------------------

WRITER_TASK = "260928-MIK"
WRITER_LEAF = "260928-MIK-L99"
WRITER_DOCUMENT = "docs/reference/settings-json.md"


def _writer_world(tmp_path):
    """The writer's converted world, plus the document a dependent describes."""

    world = build_world(tmp_path)
    (world.code / "docs/reference").mkdir(parents=True, exist_ok=True)
    (world.code / WRITER_DOCUMENT).write_text("# Settings\n", encoding="utf-8")
    commit_all(world.code, "the document a dependent describes")
    return world


def _write(world, records, *, commit: bool = False, rows=None, leaf: str = WRITER_LEAF):
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task=WRITER_TASK, kind="leaf", id=leaf),
            handoff_path="notes/handoff.json",
            document={"records": records, "history": list(rows or [])},
            commit=commit,
            authorization="test",
            worklist={"items": []},
        )
    )


def _weakness_record(
    *, key: str = "weakness-1", slug: str = "receipts", record_id=None, remove=False, **fields
):
    document = {
        "key": key,
        "kind": "weakness",
        "slug": slug,
        "fields": {
            "kind": "time",
            "statement": "The reader walks every receipt file on each message, which costs time.",
            "anchor": {
                "path": "pkg/landing.py",
                "locator": {"kind": "symbol", "value": "land_pair"},
            },
            "seen": {"task": WRITER_LEAF, "role": "worker"},
            "state": "open",
        },
    }
    document["fields"].update(fields)
    if record_id is not None:
        document["id"] = record_id
    if remove:
        document["remove"] = True
    return document


def _dependent_record(
    *, key: str = "dependent-1", slug: str = "settings-json", record_id=None, remove=False, **fields
):
    document = {
        "key": key,
        "kind": "dependent",
        "slug": slug,
        "fields": {
            "document": WRITER_DOCUMENT,
            "stands_on": [{"kind": "file", "path": "pkg/landing.py"}],
            "confirmed": {
                "task": WRITER_LEAF,
                "role": "curator",
                "statement": "The document still describes the code it stands on.",
            },
        },
    }
    document["fields"].update(fields)
    if record_id is not None:
        document["id"] = record_id
    if remove:
        document["remove"] = True
    return document


def test_the_writer_records_both_kinds_with_each_value_read_from_the_candidate(tmp_path):
    """MIK-R118 rule 2, MIK-R124 rules 2 and 5: the writer fills the anchor, the blob and the commit."""

    world = _writer_world(tmp_path)
    report = _write(world, [_weakness_record(), _dependent_record()], commit=True)
    assert report.state == "written", report.problems

    weakness = json.loads(next(world.memory.glob("knowledge/weaknesses/*.json")).read_text())
    assert weakness["anchor"]["path"] == "pkg/landing.py"
    assert weakness["anchor"]["blob"] == git(world.code, "hash-object", "pkg/landing.py")
    assert weakness["anchor"]["content"].startswith("sha256:")

    dependent = json.loads(next(world.memory.glob("knowledge/dependents/*.json")).read_text())
    assert dependent["document"] == WRITER_DOCUMENT
    # Nothing was typed: the blob is the candidate's, and so is the confirmation's commit.
    assert dependent["stands_on"][0]["blob"] == git(world.code, "hash-object", "pkg/landing.py")
    assert dependent["confirmed"]["commit"] == git(world.code, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("records", "where"),
    [
        (
            [_weakness_record(anchor={"locator": {"kind": "symbol", "value": "land_pair"}})],
            "anchor.path",
        ),
        ([_weakness_record(kind="smell")], "kind"),
        ([_weakness_record(statement="x" * (ONE_SENTENCE_MAX + 1))], "statement"),
    ],
)
def test_the_writer_refuses_a_weakness_by_its_field(tmp_path, records, where):
    """MIK-R118 rule 1 and its failure behaviour: the writer refuses, naming the field."""

    report = _write(_writer_world(tmp_path), records)
    assert report.state == "refused"
    assert any(where in f"{problem.where} {problem.message}" for problem in report.problems), (
        report.problems
    )


@pytest.mark.parametrize(
    ("records", "where"),
    [
        ([_dependent_record(document="docs/no-such-document.md")], "record dependent-1.document"),
        ([_dependent_record(stands_on=[])], "stands_on"),
        (
            [
                _dependent_record(
                    stands_on=[
                        {
                            "kind": "anchor",
                            "anchor": {
                                "path": "pkg/landing.py",
                                "locator": {"kind": "symbol", "value": "no_such_symbol"},
                            },
                        }
                    ]
                )
            ],
            "stands_on[1]",
        ),
    ],
)
def test_the_writer_refuses_a_dependent_by_its_field(tmp_path, records, where):
    """MIK-R124 rules 1 and 2 and their failure behaviour."""

    report = _write(_writer_world(tmp_path), records)
    assert report.state == "refused"
    assert any(where in problem.where for problem in report.problems), report.problems


def test_the_writer_refuses_a_second_record_for_one_document(tmp_path):
    """MIK-R124 rule 1: a document has at most one record."""

    world = _writer_world(tmp_path)
    assert _write(world, [_dependent_record()], commit=True).state == "written"
    second = _dependent_record(key="dependent-2", slug="again")
    report = _write(world, [second])
    assert report.state == "refused"
    assert any("at most one dependent record" in problem.message for problem in report.problems)


def test_a_record_link_names_the_record_and_not_the_file_it_lives_in(tmp_path):
    """MIK-R124 rule 2: the writer stores a real record ID, so strict validation accepts it.

    Fails before the repair: the writer wrote the record's stored path as the link ID, and both
    invariant and family links then refused ``stands_on.0.record.id``.
    """

    for record_id in (BASE_INVARIANT, BASE_FAMILY):
        world = _writer_world(tmp_path / record_id)
        report = _write(
            world,
            [
                _dependent_record(stands_on=[{"kind": "record", "id": record_id}]),
                # The family follows the dependent in the input. Its final revision, rather
                # than its old revision, must be captured by the dependent in this same write.
                {
                    "key": "family",
                    "kind": "family",
                    "id": BASE_FAMILY,
                    "fields": {"guarantee": "Every landing names the exact code and memory pair."},
                },
            ],
            commit=True,
        )
        assert report.state == "written", report.problems
        stored = _stored_document(world.memory, "dependent")
        link = stored["stands_on"][0]
        named = next(world.memory.glob(f"knowledge/*/{record_id}-*.json"))
        assert link == {
            "kind": "record",
            "id": record_id,
            "revision": json.loads(named.read_bytes())["revision"],
        }
        assert link["revision"] == (2 if record_id == BASE_FAMILY else 1)
        assert DependentRecord.model_validate(stored).stands_on[0]


# --- what a deletion, a closure and a second confirmation owe (A4 F04, F05, F06, F20) --------


def _stored_document(memory: Path, kind: str) -> dict[str, Any]:
    folder = {"weakness": "weaknesses", "dependent": "dependents"}[kind]
    document = json.loads(next(memory.glob(f"knowledge/{folder}/*.json")).read_text())
    return cast(dict[str, Any], document)


@pytest.mark.parametrize("removed", ["file", "symbol"])
def test_a_weakness_closes_where_it_stood_after_its_file_was_deleted(tmp_path, removed):
    """F04: the closure keeps the recorded place, observer and closing task; it is not refused."""

    world = _writer_world(tmp_path)
    assert _write(world, [_weakness_record()], commit=True).state == "written"
    stored = _stored_document(world.memory, "weakness")
    path = world.code / "pkg/landing.py"
    if removed == "file":
        path.unlink()
    else:
        path.write_text("SETTING = 1\n", encoding="utf-8")
    closing = commit_all(world.code, "the cause is gone")

    report = _write(
        world,
        [
            _weakness_record(
                record_id=stored["id"],
                state="closed",
                closed={"task": WRITER_LEAF, "commit": closing, "reason": "the pair is gone."},
            )
        ],
        commit=True,
    )
    assert report.state != "refused", report.problems
    closed = _stored_document(world.memory, "weakness")
    assert closed["state"] == "closed"
    assert closed["closed"]["commit"] == closing
    # The place, the blob and the content are the ones the weakness was recorded at.
    assert closed["anchor"] == stored["anchor"]
    assert closed["seen"] == stored["seen"]


def test_a_new_weakness_still_names_a_place_that_resolves_at_the_candidate(tmp_path):
    """F04's other half: only a closure keeps a place C cannot resolve."""

    world = _writer_world(tmp_path)
    report = _write(
        world,
        [_weakness_record(anchor={"path": "pkg/gone.py", "locator": {"kind": "file"}})],
    )
    assert report.state == "refused"
    assert any("pkg/gone.py" in problem.message for problem in report.problems)


def test_a_deleted_document_takes_its_dependent_record_and_no_other_kind(tmp_path):
    """F05: the supported removal, and the kinds that are never removed."""

    world = _writer_world(tmp_path)
    assert _write(world, [_dependent_record()], commit=True).state == "written"
    stored = _stored_document(world.memory, "dependent")
    (world.code / WRITER_DOCUMENT).unlink()
    commit_all(world.code, "the document is gone")

    report = _write(
        world,
        [_dependent_record(record_id=stored["id"], remove=True)],
    )
    assert report.state != "refused", report.problems
    assert [(outcome.id, outcome.action) for outcome in report.records] == [
        (stored["id"], "removed")
    ]

    assert _write(world, [_weakness_record()], commit=True).state == "written"
    weakness = _stored_document(world.memory, "weakness")
    refused = _write(
        world,
        [_weakness_record(record_id=weakness["id"], remove=True)],
    )
    assert refused.state == "refused"
    assert any(
        "only a document that was deleted" in problem.message for problem in refused.problems
    )


def test_a_second_task_confirming_an_unchanged_document_names_itself_and_its_judgment(tmp_path):
    """F06: no inherited attribution, and an unchanged document takes this task's sentence."""

    world = _writer_world(tmp_path)
    assert _write(world, [_dependent_record()], commit=True).state == "written"
    stored = _stored_document(world.memory, "dependent")
    assert stored["confirmed"]["task"] == WRITER_LEAF

    inherited = _write(
        world,
        [_dependent_record(record_id=stored["id"], confirmed={"task": "L100", "role": "curator"})],
    )
    assert inherited.state == "refused"
    assert any("confirmed.statement" in problem.where for problem in inherited.problems), (
        inherited.problems
    )

    silent = _write(
        world, [{"key": "dependent-1", "kind": "dependent", "id": stored["id"], "fields": {}}]
    )
    assert silent.state == "refused"
    assert any("confirmed" in problem.where for problem in silent.problems)

    fresh = _write(
        world,
        [
            _dependent_record(
                record_id=stored["id"],
                confirmed={
                    "task": "L100",
                    "role": "curator",
                    "statement": "The document still describes the code it stands on.",
                },
            )
        ],
        commit=True,
        leaf="L100",
    )
    assert fresh.state != "refused", fresh.problems
    reconfirmed = _stored_document(world.memory, "dependent")
    assert reconfirmed["confirmed"]["task"] == "L100"
    assert reconfirmed["confirmed"]["statement"].startswith("The document still")


@pytest.mark.parametrize("mode", ["selected", "dirty", "bare"])
def test_a_confirmation_names_the_code_state_its_values_were_read_from(tmp_path, mode):
    """F20: persisted writer values follow selected A, not root B, including bare and dirty C."""

    world = _writer_world(tmp_path)
    first = git(world.code, "rev-parse", "HEAD")
    first_blob = _blob(world.code, "pkg/landing.py")
    (world.code / "pkg/landing.py").write_text("def land_pair():\n    return 2\n")
    second = _commit(world.code, "B changes the linked file")
    if mode == "dirty":
        (world.code / "pkg/landing.py").write_text("def land_pair():\n    return 3\n")
        selected = CodeSnapshot.capture(world.code)
        expected = (second, selected.tree, True)
    else:
        root = world.code
        if mode == "bare":
            root = tmp_path / "bare.git"
            git(world.code, "clone", "--bare", str(world.code), str(root))
        selected = CodeSnapshot.at_commit(root, first)
        expected = (first, git(world.code, "rev-parse", f"{first}^{{tree}}"), False)
    assert code_state_of(selected) == expected
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task=WRITER_TASK, kind="leaf", id=WRITER_LEAF),
            handoff_path="notes/handoff.json",
            document={"records": [_dependent_record()], "history": []},
            commit=True,
            authorization="test",
            worklist={"items": []},
        ),
        code=selected,
    )
    assert report.state == "written", report.problems
    stored = _stored_document(world.memory, "dependent")
    confirmed = stored["confirmed"]
    assert (confirmed["commit"], confirmed["codeTree"], confirmed["uncommitted"]) == expected
    assert stored["stands_on"][0]["blob"] == selected.blobs["pkg/landing.py"]
    if mode != "dirty":
        assert stored["stands_on"][0]["blob"] == first_blob


# --- acquisition, disclosure and the master's end (A4 F08, F09, F10, F11, F13, F14, F15) ------


def _tree_selected():
    """A converted selection whose memory tree is readable, for the bounded-read case."""

    tree = SimpleNamespace(tree_key="tree-key", index_state="complete")
    return cast(Any, SimpleNamespace(memory_tree=tree, database_path=None))


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _doc_id(position: int) -> str:
    """A record ID the real filename shape admits: six Crockford characters."""

    digits = []
    value = position
    for _ in range(6):
        digits.append(_CROCKFORD[value % 32])
        value //= 32
    return "DPD-" + "".join(reversed(digits))


def _many_documents_world(root: Path, count: int) -> tuple:
    """A memory pair of ``count`` dependents, exactly one of which stands on the changed file."""

    code = _repository(root / "code")
    (code / "cli").mkdir(parents=True, exist_ok=True)
    (code / "cli" / "tool.py").write_text("one\n", encoding="utf-8")
    (code / "cli" / "other.py").write_text("still\n", encoding="utf-8")
    code_base = _commit(code, "one")
    stale_blob = _blob(code, "cli/tool.py")
    other_blob = _blob(code, "cli/other.py")

    memory = _repository(root / "memory")
    write_document(
        memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    for position in range(count):
        document = "docs/reference/tool.md" if position == 0 else f"docs/reference/d{position}.md"
        write_document(
            memory,
            record_path("dependent", _doc_id(position), "doc"),
            _dependent(
                id=_doc_id(position),
                document=document,
                stands_on=[
                    {
                        "kind": "file",
                        "path": "cli/tool.py" if position == 0 else "cli/other.py",
                        "blob": stale_blob,
                    }
                ],
            ),
        )
    for position in range(1, count):
        path = memory / record_path("dependent", _doc_id(position), "doc")
        stored = json.loads(path.read_text())
        stored["stands_on"][0] = {"kind": "file", "path": "cli/other.py", "blob": other_blob}
        path.write_text(json.dumps(stored), encoding="utf-8")
    memory_base = _commit(memory, "the dependents")
    (memory / "memory.md").write_text("the ledger\n", encoding="utf-8")
    memory_tip = _commit(memory, "the tip")

    (code / "cli" / "tool.py").write_text("two\n", encoding="utf-8")
    code_tip = _commit(code, "two")
    return memory, code, memory_base, memory_tip, code_base, code_tip


def test_a_landing_reads_only_the_dependents_the_change_can_move(tmp_path, monkeypatch):
    """F08: acquisition is metadata first; the population's bodies are not read."""

    original = landing_module.read_git_blobs_bytes
    acquired: list[str] = []

    def measured(repository, ids):
        result = original(repository, ids)
        acquired.extend(blob for blob, data in result.items() if b'"ar-dependent/v1"' in data)
        return result

    monkeypatch.setattr(landing_module, "read_git_blobs_bytes", measured)
    for count in (20, 200):
        root = tmp_path / f"w{count}"
        memory, code, memory_base, memory_tip, code_base, code_tip = _many_documents_world(
            root, count
        )
        second_path = memory / record_path("dependent", _doc_id(1), "doc")
        second = json.loads(second_path.read_bytes())
        second["stands_on"] = [
            {
                "kind": "file",
                "path": "cli/tool.py",
                "blob": _git(code, "rev-parse", f"{code_base}:cli/tool.py"),
            }
        ]
        second_path.write_text(canonical_text(second), encoding="utf-8")
        memory_base = memory_tip = _commit(memory, "two documents follow the changed file")
        acquired.clear()
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                document_staleness,
                _request((memory, code, memory_base, memory_tip, code_base, code_tip)),
            )
            staleness = pending.result(timeout=HANG_GUARD_SECONDS)
        elapsed = time.perf_counter() - started
        assert staleness.examined == ("docs/reference/d1.md", "docs/reference/tool.md")
        assert staleness.documents == staleness.examined
        # Include acquisition, not only observation: two record bodies per side and no others.
        assert staleness.dependents_read == len(acquired) == 4, (count, acquired)
        assert len(set(acquired)) == 2
        print(
            {
                "population": count,
                "dependentBodiesRead": len(acquired),
                "distinctDependents": len(set(acquired)),
                "elapsedSeconds": elapsed,
            }
        )


def test_a_new_record_made_stale_by_a_net_zero_change_is_caught_at_the_masters_end(
    _bound_services, tmp_path
):
    """F11 and F10: the master's end is a full pass; a checkpoint reports and refuses nothing.

    The master's own net code diff is empty: a leaf changed the file and a later one put it back.
    The document a leaf re-recorded at the intermediate state is stale at the tip and current at the
    base, and only the full pass can see it.
    """

    code = _repository(tmp_path / "code")
    (code / "cli").mkdir(parents=True, exist_ok=True)
    (code / "cli" / "tool.py").write_text("one\n", encoding="utf-8")
    code_base = _commit(code, "one")
    base_blob = _blob(code, "cli/tool.py")
    (code / "cli" / "tool.py").write_text("two\n", encoding="utf-8")
    _commit(code, "two")
    middle_blob = _blob(code, "cli/tool.py")

    memory = _repository(tmp_path / "memory")
    write_document(
        memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    write_document(
        memory,
        record_path("dependent", "DPD-7K3F9Q", "doc"),
        _dependent(
            stands_on=[{"kind": "file", "path": "cli/tool.py", "blob": base_blob}],
        ),
    )
    memory_base = _commit(memory, "the document at the base")
    write_document(
        memory,
        record_path("dependent", "DPD-7K3F9Q", "doc"),
        _dependent(
            revision=2,
            stands_on=[{"kind": "file", "path": "cli/tool.py", "blob": middle_blob}],
        ),
    )
    memory_tip = _commit(memory, "the document a leaf re-recorded")

    (code / "cli" / "tool.py").write_text("one\n", encoding="utf-8")
    code_tip = _commit(code, "one again")

    request = LandingGateRequest(
        memory_repository=memory,
        memory_commit=memory_tip,
        memory_bases=(memory_base,),
        code_repository=code,
        code_commit=code_tip,
        code_base=code_base,
    )
    # The range is empty, so the change-selective read sees nothing to move.
    assert document_staleness(request).documents == ()
    assert document_staleness(request, full=True).documents == ("docs/reference/settings-json.md",)
    # A checkpoint (master_end False) reports that debt and refuses nothing; the master's end refuses.
    assert landing_gate_refusal(request) is None
    refused = landing_gate_refusal(replace(request, master_end=True, series_contract="/tmp/x.md"))
    assert refused is not None and "1 document(s) are stale" in refused


def test_the_answer_projection_keeps_the_document_field_on_every_landing_route():
    """F13: the field the raw result computes survives the shared answer projection."""

    for tool in (
        "worktree_integrate",
        "worktree_checkpoint_landing",
        "worktree_record_landing",
        "direct_landing",
    ):
        whole = {"ok": True, "state": "integrated", "staleDocuments": {"count": 1}}
        answered = project_landing_answer(tool, whole, {})
        assert answered is not None and answered.get("staleDocuments") == {"count": 1}, tool
    direct = DirectLandingResponse.model_validate(
        {"ok": True, "state": "landed", "staleDocuments": {"count": 200}}
    ).model_dump(exclude_none=True)
    assert direct["staleDocuments"]["count"] == 200
    for notice in ({"count": 200}, {"state": "uncomputable", "detail": "unreadable"}):
        whole = {"ok": True, "staleDocuments": notice, "summary": "long " * 3000}
        short, _, _ = short_form(
            whole,
            answer_tokens=count_response_tokens(whole),
            log="/sandbox/landing.json",
            count=count_response_tokens,
        )
        assert short["staleDocuments"] == {
            **{key: notice[key] for key in ("count", "state") if key in notice},
            "read": {"view": "dependent", "staleOnly": True},
        }
        assert count_response_tokens(short) <= 500


def test_a_moves_moving_commit_is_named_and_an_uncommitted_one_says_so(tmp_path):
    """F15: the stale row names the commit that moved the link, or why none can be named."""

    code = _repository(tmp_path / "code")
    (code / "a.py").write_text("one\n", encoding="utf-8")
    _commit(code, "one")
    (code / "a.py").write_text("two\n", encoding="utf-8")
    moved = _commit(code, "two")
    opened = open_code_tree(CodeTree(code, git(code, "rev-parse", f"{moved}^{{tree}}")))
    named = _moving_commit(opened, "a.py")
    assert named == {"movedBy": moved, "movingCommit": moved}

    (code / "a.py").write_text("three\n", encoding="utf-8")
    captured = CodeSnapshot.capture(code)
    uncommitted = open_code_tree(CodeTree(code, captured.tree))
    assert uncommitted.problem is None
    claim = _moving_commit(uncommitted, "a.py")
    assert "movingCommitUnknown" in claim and "uncommitted" in claim["movingCommitUnknown"]


def test_a_bounded_marks_read_delivers_what_it_counts_and_pages_on():
    """F14: truthful delivered/remaining counts and a working continuation past the bound."""

    rows = [{"id": f"WKN-{position:05d}"} for position in range(200)]
    request = ReadToolRequest(memory_root="/none", view="weakness", limit=200)
    selection = SimpleNamespace(close=lambda: None)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_knowledge, "selection_for_dataset", lambda **_kwargs: selection)
        patch.setattr(_knowledge, "_marks_rows", lambda _request, _selection: list(rows))
        patch.setattr(
            _knowledge,
            "_observed_source_or_none",
            lambda _request, _workspace: None,
        )
        answered = _knowledge._marks_view(request, _tree_selected(), "repo")
        delivered = answered["counts"]
        assert delivered["rowsReturned"] == TOOL_ANSWER_LIST_MAX
        assert delivered["total"] == 200
        assert delivered["rowsRemaining"] == 200 - TOOL_ANSWER_LIST_MAX
        assert len(answered["rows"]) == TOOL_ANSWER_LIST_MAX
        # The continuation reaches the rows the transport's bound would otherwise hide.
        next_request = ReadToolRequest(
            memory_root="/none",
            view="weakness",
            limit=200,
            continuation=answered["readOn"]["continuation"],
        )
        second = _knowledge._marks_view(next_request, _tree_selected(), "repo")
        assert second["counts"]["offset"] == TOOL_ANSWER_LIST_MAX
        assert second["counts"]["rowsReturned"] == TOOL_ANSWER_LIST_MAX
        assert second["counts"]["rowsRemaining"] == 200 - 2 * TOOL_ANSWER_LIST_MAX
        assert second["rows"][0]["id"] == f"WKN-{TOOL_ANSWER_LIST_MAX:05d}"


def test_an_unobservable_document_is_disclosed_and_refuses_the_masters_end(
    _bound_services, tmp_path
):
    """F09: an unverifiable observation is reported, never folded away as a clean document."""

    code = _repository(tmp_path / "code")
    (code / "notes.txt").write_text("one\n", encoding="utf-8")
    code_base = _commit(code, "one")

    memory = _repository(tmp_path / "memory")
    write_document(
        memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    write_document(
        memory,
        record_path("dependent", "DPD-7K3F9Q", "doc"),
        _dependent(
            stands_on=[
                {
                    "kind": "anchor",
                    "anchor": {
                        "path": "notes.txt",
                        "blob": "a" * 40,
                        "content": "sha256:" + "b" * 64,
                        "locator": {"kind": "symbol", "name": "nothing_binds_this"},
                    },
                }
            ],
        ),
    )
    memory_base = _commit(memory, "the document at the base")
    (memory / "memory.md").write_text("the ledger\n", encoding="utf-8")
    memory_tip = _commit(memory, "the tip")
    (code / "notes.txt").write_text("two\n", encoding="utf-8")
    code_tip = _commit(code, "two")

    request = LandingGateRequest(
        memory_repository=memory,
        memory_commit=memory_tip,
        memory_bases=(memory_base,),
        code_repository=code,
        code_commit=code_tip,
        code_base=code_base,
        master_end=True,
        series_contract="/tmp/series-contract.md",
    )
    staleness = document_staleness(request)
    assert staleness.unverifiable, staleness
    answer = staleness.answer()
    assert answer is not None and answer["state"] == "unverifiable"
    refused = landing_gate_refusal(request)
    assert refused is not None and "cannot all be observed" in refused


def test_the_writer_records_a_history_row_about_a_weakness_and_a_dependent(tmp_path):
    """F02: the rows the new predicates read have a supported producer, not only a model."""

    world = _writer_world(tmp_path)
    # An earlier leaf records the weakness: a record this leaf first authors needs no row.
    assert _write(world, [_weakness_record()], commit=True, leaf="260928-MIK-L98").state == (
        "written"
    )
    weakness = _stored_document(world.memory, "weakness")
    report = _write(
        world,
        [
            _weakness_record(
                record_id=weakness["id"],
                state="closed",
                closed={"task": WRITER_LEAF, "commit": "c" * 40},
            )
        ],
        rows=[
            {
                "subject": weakness["id"],
                "disposition": "changed",
                "effect": "clarify",
                "reason": "the weakness is closed where it stood.",
            }
        ],
        commit=True,
    )
    assert report.state == "written", report.problems
    history = json.loads(next(world.memory.glob("knowledge/history/*.json")).read_text())
    subjects = [row["subject"] for row in history["rows"]]
    assert weakness["id"] in subjects

    assert _write(world, [_dependent_record()], commit=True, leaf="260928-MIK-L98").state == (
        "written"
    )
    dependent = _stored_document(world.memory, "dependent")
    confirmed = _write(
        world,
        [
            _dependent_record(
                record_id=dependent["id"],
                confirmed={
                    "task": WRITER_LEAF,
                    "role": "curator",
                    "statement": "The document still describes the code it stands on.",
                },
            )
        ],
        rows=[
            {
                "subject": dependent["id"],
                "disposition": "changed",
                "effect": "clarify",
                "reason": "the document is confirmed by this task.",
            }
        ],
    )
    assert confirmed.state != "refused", confirmed.problems


def _registered_server(world):
    """The mounted tool server over this world, reached the way an agent reaches it."""

    root = world.root
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "repo").symlink_to(world.code, target_is_directory=True)
    settings = root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coord").as_posix(),
                "workspaceRoot": workspace.as_posix(),
                "repositories": {"repo": {"path": world.code.as_posix()}},
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    return create_server(
        McpRuntimeConfig(
            config_path=settings,
            coordination_root=root / "coord",
            workspace_root=workspace,
            transcript_root=root / "coord" / "logs" / "mcp",
            repositories={"repo": RepositoryScope(repo_id="repo", path=world.code)},
        )
    )


async def _registered_call(server, name: str, arguments: dict[str, Any]):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await client.call_tool(name, arguments)


def _call_message(result) -> str:
    return "\n".join(
        text for content in result.content if (text := getattr(content, "text", None)) is not None
    )


def test_the_registered_writer_answers_a_planned_write_without_a_model_refusal(tmp_path):
    """MC-01: the public writer's success answer must validate as its own response model.

    The answer this tool builds carries `reportError` beside `reportPath` for every report it
    reaches -- `None` when the report was written. A response model that does not declare the
    field turns every planned and every written answer into a validation failure, so the caller
    never sees the outcome it asked for. The case goes through the *registered* tool, because
    the application writer answers fine and only the protocol envelope refuses.
    """

    world = _writer_world(tmp_path)
    # The leaf route reads its persisted worklist before it plans; an empty one admits the call.
    (world.task_root / "knowledge-worklist.json").write_text(
        json.dumps({"items": []}), encoding="utf-8"
    )
    server = _registered_server(world)
    result = anyio.run(
        _registered_call,
        server,
        "knowledge_write",
        {
            "contractPath": world.contract.as_posix(),
            "handoffName": "mc01.json",
            "authorizationRef": "MC-01: the public writer's planned answer",
            "handoff": {"records": [], "history": []},
        },
    )
    assert not result.isError, _call_message(result)
    answer = result.structuredContent
    assert answer is not None
    assert answer["ok"] is True, answer
    assert answer["state"] == "planned", answer
    # The report was written, so nothing names a reason it was not. A `None` field is left out of
    # the wire form, so the caller reads its absence as "the report is there" -- which the path
    # below proves. A report that could not be written would carry the reason here instead.
    assert answer.get("reportError") is None, answer
    assert answer["reportPath"], answer
    # And the model itself declares the field the tool's answer carries, which is the defect: a
    # response model that does not know `reportError` refuses every answer that has it.
    assert (
        KnowledgeWriteResponse.model_validate(
            {
                "ok": True,
                "operation": "knowledge_write",
                "state": "planned",
                "reportPath": answer["reportPath"],
                "reportError": None,
            }
        ).reportError
        is None
    )


def test_a_handoff_alias_and_a_literal_id_name_the_same_row_kind(tmp_path):
    """F02: an alias that resolves to a weakness or a dependent gets the row a literal ID gets."""

    world = _writer_world(tmp_path)
    assert _write(world, [_weakness_record(), _dependent_record()], commit=True).state == "written"
    for kind in ("weakness", "dependent"):
        stored = _stored_document(world.memory, kind)
        key = f"updated-{kind}"
        fields = (
            {"statement": "The reader still walks receipts on every message."}
            if kind == "weakness"
            else {
                "confirmed": {
                    "task": "260928-MIK-L100",
                    "role": "curator",
                    "statement": "The document still describes its unchanged code.",
                }
            }
        )
        record = {"key": key, "kind": kind, "id": stored["id"], "fields": fields}
        row = {
            "subject": f"handoff:{key}",
            "disposition": "changed",
            "effect": "clarify",
            "reason": "The record was reviewed at the current candidate.",
        }
        alias = _write(world, [record], rows=[row], leaf="260928-MIK-L100")
        literal = _write(
            world, [record], rows=[{**row, "subject": stored["id"]}], leaf="260928-MIK-L100"
        )
        assert literal.state != "refused", literal.problems
        # The alias is the same subject by another spelling: it must reach the same row kind.
        assert alias.state != "refused", alias.problems
        assert [p.message for p in alias.problems] == []


def test_a_retired_row_answers_a_document_this_write_removes(tmp_path):
    """F05: the row that answers a removal is admitted although the record is gone."""

    world = _writer_world(tmp_path)
    assert _write(world, [_dependent_record()], commit=True).state == "written"
    dependent = _stored_document(world.memory, "dependent")
    commit_all(world.memory, "base includes the dependent")
    row = {
        "subject": dependent["id"],
        "disposition": "retired",
        "effect": "retire",
        "reason": "The document this record described was removed.",
    }
    row_control = _write(world, [], rows=[row], leaf="260928-MIK-L100")
    assert row_control.state != "refused", row_control.problems
    (world.code / WRITER_DOCUMENT).unlink()
    commit_all(world.code, "remove the document")
    base = git(world.memory, "rev-parse", "HEAD")
    result = _write(
        world,
        [_dependent_record(record_id=dependent["id"], remove=True)],
        rows=[row],
        leaf="260928-MIK-L100",
        commit=True,
    )
    assert not (world.code / WRITER_DOCUMENT).exists()
    assert result.state != "refused", result.problems
    # The writer's own answer is not publication proof: it writes files and commits nothing, so a
    # publication is formed by staging what it wrote. That tree must pass the validator, and its
    # retired row must not read as a dangling subject (F05).
    git(world.memory, "add", "-A")
    candidate_tree = git(world.memory, "write-tree")
    validation = GitKnowledgeValidation(base_converter=GitBaseConverter())
    refusal = validation.refusal(
        memory_repository=world.memory,
        candidate_tree=candidate_tree,
        bases=[base],
        code_repository=world.code,
        code_commit=git(world.code, "rev-parse", "HEAD"),
    )
    assert refusal is None, refusal


def test_a_confirmation_always_carries_this_tasks_judgment(tmp_path):
    """F06: a dependent's every write is a confirmation, so the sentence is never optional."""

    world = _writer_world(tmp_path)
    assert _write(world, [_dependent_record()], commit=True).state == "written"
    before = _stored_document(world.memory, "dependent")
    document_before = (world.code / WRITER_DOCUMENT).read_bytes()
    commit_all(world.memory, "base dependent")
    with (world.code / "pkg/landing.py").open("a") as output:
        output.write("\n# linked file changed while the document did not\n")
    commit_all(world.code, "change linked code only")
    owner = "260928-MIK-L100"
    row = {
        "subject": before["id"],
        "disposition": "changed",
        "effect": "clarify",
        "reason": "This documentation task confirms the existing document.",
    }
    # Without its sentence the confirmation is not a judgment: refused, and nothing is written.
    without = _write(
        world,
        [_dependent_record(record_id=before["id"], confirmed={"task": owner, "role": "curator"})],
        rows=[row],
        leaf=owner,
    )
    assert without.state == "refused", without.problems
    assert any("statement" in problem.where for problem in without.problems), without.problems
    assert (world.code / WRITER_DOCUMENT).read_bytes() == document_before
    with_judgment = _write(
        world,
        [
            _dependent_record(
                record_id=before["id"],
                confirmed={
                    "task": owner,
                    "role": "curator",
                    "statement": "The document still describes the code it stands on.",
                },
            )
        ],
        rows=[row],
        leaf=owner,
        commit=True,
    )
    assert with_judgment.state != "refused", with_judgment.problems
    after = _stored_document(world.memory, "dependent")
    assert after["stands_on"] != before["stands_on"]
    assert after["confirmed"]["statement"]


def test_a_changed_document_needs_this_tasks_current_confirmation():
    """F07: a third party's confirmation, or a stale one, does not answer the item."""

    off_owner = DependentRecord.model_validate(
        _dependent(confirmed={"task": "260928-MIK-L777", "role": "curator", "commit": "b" * 40})
    )
    item = {
        "subject": off_owner.id,
        "facts": {
            "document": off_owner.document,
            "confirmedTask": "260928-MIK-L99",
            "confirmedCommit": "b" * 40,
        },
    }
    context = _gate_context(_row(off_owner.id, "changed"), [off_owner])
    context.code = _code({"contract.json": "c" * 40})
    reason = changed_document_open(item, context)
    assert reason is not None and "260928-MIK-L777" in reason, reason
    # A stale link does not answer it either: the confirmation must name the code at C.
    stale = DependentRecord.model_validate(
        _dependent(confirmed={"task": "260928-MIK-L118", "role": "curator", "commit": "b" * 40})
    )
    stale_context = _gate_context(_row(stale.id, "changed"), [stale])
    stale_context.code = _code({"contract.json": "c" * 40})
    stale_reason = changed_document_open(item, stale_context)
    assert stale_reason is not None, stale_reason
    # Nor does a confirmation whose link names a file C does not hold.
    missing = DependentRecord.model_validate(
        _dependent(confirmed={"task": "260928-MIK-L118", "role": "curator", "commit": "b" * 40})
    )
    missing_context = _gate_context(_row(missing.id, "changed"), [missing])
    missing_context.code = _code({})
    missing_reason = changed_document_open(item, missing_context)
    assert missing_reason is not None and "does not hold" in missing_reason, missing_reason


_RETIRED_ABSENT_KINDS = (
    "decision",
    "incident",
    "assumption",
    "limitation",
    "failure_mode",
    "scenario",
    "diagnostic",
    "term",
    "weakness",
)


@pytest.mark.parametrize("kind", (*_RETIRED_ABSENT_KINDS, "dependent"))
def test_only_a_removed_dependent_may_be_named_by_a_retired_row(kind):
    """F05: the R09 exception is the deleted document's dependent, not a licence for any subject.

    A retired row is what makes a removal readable, so the R09 subject check must let the row of a
    *removed* dependent name a record the tree no longer holds -- and must refuse exactly the same
    shape for every other record kind, which is a ghost subject (F05, narrowed).
    """

    prefix = {
        "decision": "DEC",
        "incident": "INC",
        "assumption": "ASM",
        "limitation": "LIM",
        "failure_mode": "FLM",
        "scenario": "SCN",
        "diagnostic": "DGN",
        "term": "TRM",
        "weakness": "WKN",
        "dependent": "DPD",
    }[kind]
    subject = f"{prefix}-AAAAAA"
    files = {
        "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        "knowledge/history/leaf.json": encode(
            {
                "schema": "ar-history/v1",
                "leaf": "leaf",
                "closed": False,
                "rows": [
                    {
                        "id": "ROW-AAAAAA",
                        "subject": subject,
                        "disposition": "retired",
                        "reason": "The record it named is gone from this tree.",
                        "items": [],
                        "revision": 1,
                        "effect": "retire",
                    }
                ],
            }
        ),
    }
    candidate = tree(files)
    context = ValidationContext(candidate=candidate, parsed=parse_tree(candidate))
    findings = [
        finding for finding in check_history_rows(context) if finding.field.endswith(".subject")
    ]
    if kind == "dependent":
        assert findings == [], findings
    else:
        assert any(subject in finding.message for finding in findings), (kind, findings)


def test_the_validator_still_refuses_a_retired_row_about_an_absent_weakness(tmp_path):
    """F05, narrowed: the same shape the dependent may use refuses for a weakness at the Git level."""

    world = _writer_world(tmp_path)
    assert _write(world, [_weakness_record()], commit=True).state == "written"
    weakness = _stored_document(world.memory, "weakness")
    base = git(world.memory, "rev-parse", "HEAD")
    # The record goes, and the row that names it stays: a ghost subject, by hand.
    for path in world.memory.glob("knowledge/weaknesses/*"):
        path.unlink()
    history = next(iter(world.memory.glob("knowledge/history/*.json")), None)
    if history is None:
        history = world.memory / "knowledge/history/leaf.json"
        history.parent.mkdir(parents=True, exist_ok=True)
        document = {"schema": "ar-history/v1", "leaf": "leaf", "closed": False, "rows": []}
    else:
        document = json.loads(history.read_text())
    document["rows"] = [
        {
            "id": "ROW-AAAAAA",
            "subject": weakness["id"],
            "disposition": "retired",
            "reason": "The weakness is gone, and this row claims it retired.",
            "items": [],
            "revision": 1,
            "effect": "retire",
        }
    ]
    history.write_text(canonical_text(document), encoding="utf-8")
    git(world.memory, "add", "-A")
    candidate_tree = git(world.memory, "write-tree")
    refusal = GitKnowledgeValidation(base_converter=GitBaseConverter()).refusal(
        memory_repository=world.memory,
        candidate_tree=candidate_tree,
        bases=[base],
        code_repository=world.code,
        code_commit=git(world.code, "rev-parse", "HEAD"),
    )
    assert refusal is not None, refusal
    assert "R09-history-rows" in refusal and weakness["id"] in refusal, refusal


def test_a_deleted_linked_file_names_the_commit_that_deleted_it(tmp_path):
    """F15: the deletion commit is the movement, not "uncommitted or another line"."""

    code = _repository(tmp_path / "code")
    (code / "gone.py").write_text("one\n", encoding="utf-8")
    _commit(code, "one")
    (code / "gone.py").unlink()
    deleted = _commit(code, "delete it")
    opened = open_code_tree(CodeTree(code, git(code, "rev-parse", f"{deleted}^{{tree}}")))
    assert "gone.py" not in opened.files
    named = _moving_commit(opened, "gone.py")
    assert named.get("movingCommit") == deleted, named


def test_a_record_link_at_a_stale_revision_does_not_answer_a_changed_document():
    """F07: a stand-on record link is current only at the revision the candidate holds."""

    linked = DependentRecord.model_validate(
        _dependent(id="DPD-AAAAAA", revision=2, document="docs/reference/other.md")
    )
    stale = DependentRecord.model_validate(
        _dependent(
            stands_on=[{"kind": "record", "id": "DPD-AAAAAA", "revision": 1}],
            confirmed={
                "task": "260928-MIK-L118",
                "role": "curator",
                "statement": "The document still describes the record it stands on.",
                "commit": "b" * 40,
            },
        )
    )
    item = {
        "subject": stale.id,
        "facts": {"document": stale.document, "confirmedTask": "260928-MIK-L99"},
    }
    context = _gate_context(_row(stale.id, "changed"), [stale, linked])
    context.code = _code({})
    reason = changed_document_open(item, context)
    assert reason is not None and "revision 1" in reason, reason
    # At the revision the candidate holds, the same confirmation answers the item.
    current = DependentRecord.model_validate(
        _dependent(
            stands_on=[{"kind": "record", "id": "DPD-AAAAAA", "revision": 2}],
            confirmed={
                "task": "260928-MIK-L118",
                "role": "curator",
                "statement": "The document still describes the record it stands on.",
                "commit": "b" * 40,
            },
        )
    )
    current_context = _gate_context(_row(current.id, "changed"), [current, linked])
    current_context.code = _code({})
    assert changed_document_open(item, current_context) is None


# ------------------------------------------------------------------------------------------------
# R3 probes: the anchor seam, rebuilt from the Reviewer's independent measurements
# ------------------------------------------------------------------------------------------------

_SYMBOL_MODULE = """\
def words(text):
    return len(text.split())


SETTING = 1
"""

_SYMBOL_CHANGED = """\
def words(text):
    return len([one for one in text.split() if one])


SETTING = 1
"""

_SYMBOL_UNRELATED = """\
def words(text):
    return len([one for one in text.split() if one])


SETTING = 2
"""


def _symbol_record(tmp_path):
    """A code repo whose symbol is recorded by a real symbol anchor, as a writer records it."""

    code = _repository(tmp_path / "code")
    (code / "textkit").mkdir(parents=True, exist_ok=True)
    (code / "textkit/count.py").write_text(textwrap.dedent(_SYMBOL_MODULE), encoding="utf-8")
    _commit(code, "A records the symbol")
    snapshot = CodeSnapshot.capture(code)
    resolved = snapshot.resolve("textkit/count.py", {"kind": "symbol", "name": "words"})
    anchor = {**resolved.to_document(), "path": "textkit/count.py"}
    record = DependentRecord.model_validate(
        _dependent(
            stands_on=[{"kind": "anchor", "anchor": anchor}],
            confirmed={
                "task": "260928-MIK-L118",
                "role": "curator",
                "statement": "The document describes what the symbol does.",
                "commit": "b" * 40,
            },
        )
    )
    return code, record


def _rewrite_symbol(code: Path, body: str, message: str) -> str:
    (code / "textkit/count.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return _commit(code, message)


def test_a_changed_symbol_anchor_does_not_answer_a_changed_document(tmp_path):
    """F07: an anchor link whose symbol moved is not a confirmation of the code at C."""

    code, record = _symbol_record(tmp_path)
    item = {
        "subject": record.id,
        "facts": {"document": record.document, "confirmedTask": "260928-MIK-L99"},
    }
    at_a = _gate_context(_row(record.id, "changed"), [record])
    at_a.code = open_code_tree(CodeTree(code, CodeSnapshot.capture(code).tree))
    assert changed_document_open(item, at_a) is None

    _rewrite_symbol(code, _SYMBOL_CHANGED, "B changes the symbol")
    at_b = _gate_context(_row(record.id, "changed"), [record])
    at_b.code = open_code_tree(CodeTree(code, CodeSnapshot.capture(code).tree))
    reason = changed_document_open(item, at_b)
    assert reason is not None and "moved" in reason, reason

    control = _gate_context(_row(record.id, "changed"), [record])
    control.code = at_b.code
    control.owner = "260928-MIK-L777"
    refusal = changed_document_open(item, control)
    assert refusal is not None and "no other task's confirmation answers it" in refusal, refusal


def test_an_anchor_names_the_commit_that_changed_its_symbol(tmp_path):
    """F15: a later commit editing an unrelated constant is not the anchor's mover."""

    code, record = _symbol_record(tmp_path)
    moved = _rewrite_symbol(code, _SYMBOL_CHANGED, "B changes the symbol")
    _rewrite_symbol(code, _SYMBOL_UNRELATED, "C edits an unrelated constant")

    opened = open_code_tree(CodeTree(code, CodeSnapshot.capture(code).tree))
    assert opened.problem is None
    link = DependentAnchorLink.model_validate(record.stands_on[0].to_document())
    observe = _Observe(revision_of=lambda _id: 1, code=opened, tree=opened.tree, cache={})
    movement = _moved_link(0, link, observe)
    assert movement is not None and movement.get("state") == "stale", movement
    assert movement.get("movingCommit") == moved, movement


def test_selected_dependent_acquisition_failures_are_never_absence(
    _bound_services, tmp_path, monkeypatch
):
    """F09: failed body acquisition and malformed selected bodies disclose and refuse at end."""

    world = _landing_world(tmp_path)
    memory, _, memory_base, memory_tip, _, _ = world
    path = record_path("dependent", "DPD-7K3F9Q", "settings-json")
    blob = _git(memory, "rev-parse", f"{memory_tip}:{path}")
    read = landing_module.read_git_blobs_bytes

    def missing(repository, ids):
        result = read(repository, ids)
        result.pop(blob, None)
        return result

    request = _request(world)
    with monkeypatch.context() as patch:
        patch.setattr(landing_module, "read_git_blobs_bytes", missing)
        answer = document_staleness(request).answer()
        assert answer is not None and answer["state"] == "uncomputable", answer
        final = replace(request, master_end=True, series_contract="/tmp/master.md")
        refusal = landing_gate_refusal(final)
        assert refusal is not None and "cannot be computed" in refusal, refusal
    (memory / path).write_text('{"schema":"ar-dependent/v1"}', encoding="utf-8")
    malformed = commit_all(memory, "malformed selected dependent; lookup retained")
    answer = document_staleness(replace(request, memory_commit=malformed)).answer()
    assert answer is not None and answer["state"] == "uncomputable", answer
    assert memory_base != malformed


def test_a_renamed_link_with_json_escaped_or_cr_paths_is_selected(tmp_path):
    """F12: exact Git path bytes and JSON lookup keys agree on both sides of a rename."""

    for position, special in enumerate(('a"q.py', "a\tt.py", "a\rc.py", "a\nn.py")):
        root = tmp_path / str(position)
        code = _repository(root / "code")
        old = "cli/" + special
        (code / "cli").mkdir()
        (code / old).write_text("one\n", encoding="utf-8")
        base = _commit(code, "original special path")
        memory = _repository(root / "memory")
        write_document(
            memory, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
        )
        write_document(
            memory,
            record_path("dependent", "DPD-7K3F9Q", "doc"),
            _dependent(stands_on=[{"kind": "file", "path": old, "blob": _blob(code, old)}]),
        )
        memory_base = _commit(memory, "dependent on the original path")
        (code / old).rename(code / "cli/new.py")
        tip = _commit(code, "rename the linked file")
        request = _request((memory, code, memory_base, memory_base, base, tip))
        assert old in landing_module._changed_paths(request)
        stale = document_staleness(request)
        assert stale.documents == ("docs/reference/settings-json.md",), (old, stale)


def test_a_master_retains_document_origin_across_a_checkpoint_and_sync(_bound_services, tmp_path):
    """F10/F11: a parent's adopted checkpoint tip cannot become the cumulative debt origin."""

    world = _landing_world(tmp_path)
    memory, code, memory_base, memory_tip, code_base, code_tip = world
    contract = _contract(memory, code, memory_tip, code_tip, "series")
    contract.sync_log = ({"codeBaseFrom": code_base, "memoryBaseFrom": memory_base},)
    # Ordinary checkpoint answers remain selective, even on a series.
    assert landing_document_answer(contract, code_commit=code_tip, memory_commit=memory_tip) is None
    final_answer = landing_document_answer(
        contract, code_commit=code_tip, memory_commit=memory_tip, master_end=True
    )
    assert final_answer is not None and final_answer["count"] == 1, final_answer
    final = replace(
        _request(world),
        code_base=code_tip,
        memory_bases=(memory_tip,),
        master_end=True,
        series_contract="/tmp/master.md",
        document_base=(code_base, memory_base),
    )
    refusal = landing_gate_refusal(final)
    assert refusal is not None and "1 document(s) are stale" in refusal, refusal


@pytest.mark.parametrize("answer", ["stands", "moved", "closed", "confirmed"])
def test_supported_history_rows_answer_the_real_parsed_predicates(tmp_path, answer):
    """F02: consume writer-persisted histories, covering literal and handoff aliases."""
    world = _writer_world(tmp_path)
    dependent = answer == "confirmed"
    factory = _dependent_record if dependent else _weakness_record
    assert _write(world, [factory()], commit=True, leaf="260928-MIK-L98").state == "written"
    stored = _stored_document(world.memory, "dependent" if dependent else "weakness")
    records = []
    if answer != "stands":
        fields = {}
        if answer == "closed":
            closing = git(world.code, "rev-parse", "HEAD")
            fields = {"state": "closed", "closed": {"task": WRITER_LEAF, "commit": closing}}
        if answer == "moved":
            (world.code / "pkg/landing.py").write_text("def land_pair():\n    return 2\n")
            _commit(world.code, "move the symbol")
        records = [factory(record_id=stored["id"], **fields)]
    subject = stored["id"] if not records else "handoff:" + records[0]["key"]
    report = _write(
        world,
        records,
        rows=[
            {
                "subject": subject,
                "disposition": "no_impact" if answer == "stands" else "changed",
                "reason": "The weakness still stands."
                if answer == "stands"
                else "The record answers this task.",
                **({} if answer == "stands" else {"effect": "clarify"}),
            }
        ],
        commit=True,
    )
    assert report.state == "written", report.problems
    candidate = KnowledgeSide.from_snapshot("K_C", directory_snapshot(world.memory))
    history = candidate.history(WRITER_LEAF)
    assert history is not None and history.row_about(stored["id"]) is not None
    code = open_code_tree(CodeTree(world.code, git(world.code, "rev-parse", "HEAD")))
    context = GateContext(owner=WRITER_LEAF, history=history, candidate=candidate, code=code)
    item = {
        "subject": stored["id"],
        "facts": {"document": WRITER_DOCUMENT, "confirmedTask": "260928-MIK-L98"},
    }
    predicate = changed_document_open if dependent else weakness_anchor_open
    assert predicate(item, context) is None


def test_symbol_movement_provenance_uses_selected_history_and_tracks_removal(tmp_path):
    code, record = _symbol_record(tmp_path)
    moved = _rewrite_symbol(code, _SYMBOL_CHANGED, "B moves the symbol")
    later = _rewrite_symbol(code, _SYMBOL_UNRELATED, "C changes another symbol")
    removed = _rewrite_symbol(code, "SETTING = 3\n", "D removes the linked symbol")
    for selected, expected in ((moved, moved), (later, moved), (removed, removed)):
        opened = open_code_tree(CodeTree(code, selected))
        state, links = dependent_movement(
            record, revision_of=lambda _: None, code=opened, tree=opened.tree
        )
        assert state == "stale" and links[0]["movingCommit"] == expected, links
    assert links[0]["movedByDeletion"] is True


def test_a_known_stale_link_does_not_hide_an_unobservable_sibling():
    record = DependentRecord.model_validate(
        _dependent(
            stands_on=[
                {"kind": "record", "id": BASE_INVARIANT, "revision": 1},
                {"kind": "file", "path": "a.py", "blob": "a" * 40},
            ]
        )
    )
    opened = SimpleNamespace(problem="the code tree cannot be read", files={}, tree=None)
    state, links = dependent_movement(record, revision_of=lambda _: 2, code=opened)
    assert state == "stale"
    assert any(one.get("state") == "unverifiable" for one in links)


@pytest.mark.parametrize("has_dependent", [True, False])
@pytest.mark.parametrize("memory_candidate_kind", ["directory", "git-tree"])
def test_a_changed_dependent_document_needs_no_onboarding_representation(
    tmp_path, has_dependent, memory_candidate_kind
):
    """F18/R124: the real worklist and refresh gate require confirmation, never a document card.

    A no-record control retains the older missing-card and unexplained-change obligations.
    """

    world = _writer_world(tmp_path)
    document = "README.md"
    (world.code / document).write_text("# Helpers\n", encoding="utf-8")
    base_code = commit_all(world.code, "the document before its own task")
    if has_dependent:
        report = _write(world, [_dependent_record(document=document)], commit=True)
        assert report.state == "written", report.render()
        commit_all(world.memory, f"the document's base knowledge\n\nCode-Commit: {base_code}")
    base = KnowledgeSide.from_snapshot("K_B", directory_snapshot(world.memory))
    (world.code / document).write_text(
        "# Helpers\n\nThe helper lands both halves.\n", encoding="utf-8"
    )
    snapshot = CodeSnapshot.capture(world.code)
    code = CodeTrees.open(world.code, base_code, snapshot.tree)
    run = compute_worklist(
        WorklistInputs(code=code, base=base, candidate=base, pairing={}, owner="260928-MIK-L100")
    )
    assert run["state"] == "complete", run
    own_document = [item for item in run["items"] if item["kind"] == "changed_document"]
    unexplained = [item for item in run["items"] if item["kind"].startswith("unexplained_")]
    assert len(own_document) == int(has_dependent)
    assert bool(unexplained) is not has_dependent, run
    context = SimpleNamespace(
        storage=StorageSettings(),
        code_repository_name="agents-remember",
        onboarding_root=world.memory / "onboarding",
    )
    plan = onboarding_refresh_plan_for_context(context, [document], working_paths=[document])
    assert (document in plan["missing"]) is not has_dependent, plan
    sides = onboarding_trace_sides(
        TraceSideRequest(
            owner="260928-MIK-L100",
            memory_repository=world.memory,
            memory_base=_git(world.memory, "rev-parse", "HEAD"),
            memory_candidate=world.memory
            if memory_candidate_kind == "directory"
            else _git(world.memory, "rev-parse", "HEAD"),
            code_repository=world.code,
            code_base=base_code,
        )
    )
    assert sides is not None and sides.incomplete is None, sides
    result, findings = onboarding_trace_gate_for_context(
        context,
        [document],
        sides,
        OnboardingTraceInputs(
            code_blobs={document: snapshot.blobs[document]}, identical_changes={}
        ),
    )
    if has_dependent:
        assert result.items == () and not findings, (result.summary(), findings)
    else:
        assert findings and any(
            row["code"] == "memory-refresh-attestation-failed" for row in findings
        )
    assert not (world.memory / "onboarding/README.md.md").exists()
    assert not (world.memory / "onboarding/README.md.json").exists()


def test_a_malformed_dependent_cannot_silently_exempt_a_document_from_onboarding(tmp_path):
    """Unreadable classification is a named input failure, not a broad missing-card exemption."""

    world = _writer_world(tmp_path)
    folder = world.memory / "knowledge/dependents"
    folder.mkdir(parents=True)
    (folder / "DPD-7K3F9Q-broken.json").write_text('{"schema":"ar-dependent/v1"}', encoding="utf-8")
    context = SimpleNamespace(
        storage=StorageSettings(),
        code_repository_name="agents-remember",
        onboarding_root=world.memory / "onboarding",
    )
    with pytest.raises(RuntimeError, match=r"DPD-7K3F9Q-broken\.json"):
        onboarding_refresh_plan_for_context(context, ["README.md"])
    sides = onboarding_trace_sides(
        TraceSideRequest(
            owner="260928-MIK-L100",
            memory_repository=world.memory,
            memory_base=_git(world.memory, "rev-parse", "HEAD"),
            memory_candidate=world.memory,
            code_repository=world.code,
            code_base=_git(world.code, "rev-parse", "HEAD"),
        )
    )
    assert sides is not None and sides.incomplete is None, sides
    _, findings = onboarding_trace_gate_for_context(
        context,
        [WRITER_DOCUMENT],
        sides,
        OnboardingTraceInputs(
            code_blobs={WRITER_DOCUMENT: CodeSnapshot.capture(world.code).blobs[WRITER_DOCUMENT]},
            identical_changes={},
        ),
    )
    assert findings and any("DPD-7K3F9Q-broken.json" in row["message"] for row in findings)
