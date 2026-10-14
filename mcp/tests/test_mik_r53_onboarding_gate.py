"""MIK-R53 Part A: the onboarding gate answers through source-bound rows only.

RED-first coverage for packet rules 1-5: the ``onboarding_trace`` row's ``updated``
disposition and ``sources`` binding (rule 1), the narrowed counted change and the
writer/gate refusal of an ``updated`` row without one (rule 2), source-equality
currency at the gate (rule 3), old rows in open vs closed files and the crossing
sync's sources (rule 4). Rule 5 is a lifecycle step in the skill files.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.application.knowledge_gate.predicates import (
    GateContext,
    onboarding_row_open,
    unexplained_row_open,
)
from agents_remember.application.knowledge_worklist.classify import Classifier
from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.application.knowledge_worklist.onboarding_trace import (
    ONBOARDING_TRACE_KIND,
    worktree_code_blobs,
)
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.application.knowledge_writer.carry import mapped_anchor
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.memory.conversion.code_objects import CodeObjectError, CodeObjects
from agents_remember.memory.conversion.convert import MemoryInput
from agents_remember.memory.conversion.crossing_sync import marker_rows
from agents_remember.memory_quality.knowledge_validator.trees import (
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import (
    HistoryFile,
    OnboardingTraceRow,
    merged_leaf_history,
)
from agents_remember.models.knowledge_files.shapes import Anchor
from agents_remember.models.knowledge_files.unexplained import unexplained_satisfied_by
from agents_remember.worktrees.modules.onboarding_trace import (
    counted_change,
    counted_sidecar_change,
    onboarding_item_open,
    onboarding_trace_result,
)
from knowledge_writer_test_support import (
    CODE_FILE,
    LEAF_ID,
    TASK_ID,
    build_world,
    commit_all,
    git,
    write,
)

OWNER = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)
HISTORY_PATH = f"knowledge/history/{LEAF_ID}.json"
BLOB_OLD = "a" * 40
BLOB_NEW = "b" * 40


def _sidecar(
    references: dict[str, Any] | None = None,
    realizes: list[dict[str, Any]] | None = None,
    proves: list[dict[str, Any]] | None = None,
) -> bytes:
    anchor = {
        "locator": {"kind": "file"},
        "blob": BLOB_OLD,
        "content": "sha256:" + "0" * 64,
    }
    return canonical_text(
        {
            "schema": "ar-onboarding-file/v1",
            "path": "pkg/a.py",
            "references": references if references is not None else {},
            "realizes": (
                realizes
                if realizes is not None
                else [
                    {
                        "id": "RLZ-A00001",
                        "invariant": "INV-AAAAAA",
                        "anchor": dict(anchor),
                        "role": "support",
                        "rationale": "A carries the rule.",
                    }
                ]
            ),
            **({"proves": proves} if proves is not None else {}),
        }
    ).encode("utf-8")


def _row_document(**fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "ROW-AAAAAA",
        "subject": "onboarding:pkg/a.py",
        "disposition": "no_impact",
        "reason": "The card was read and needs no change.",
        "items": [],
    }
    row.update(fields)
    return row


def _history(rows: list[dict[str, Any]], *, closed: bool = False) -> HistoryFile:
    return HistoryFile.model_validate(
        {"schema": "ar-history/v1", "leaf": LEAF_ID, "closed": closed, "rows": rows}
    )


def _context(history: HistoryFile) -> GateContext:
    candidate = SimpleNamespace(revision=lambda record_id: None)
    return GateContext(
        owner=LEAF_ID, history=history, candidate=cast(Any, candidate), code=cast(Any, None)
    )


def _item(*, counted: bool = False, blobs: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": "sha256:" + "c" * 64,
        "kind": "onboarding_trace",
        "subject": "onboarding:pkg/a.py",
        "facts": {
            "sources": ["pkg/a.py"],
            "markdown": "onboarding/pkg/a.py.md",
            "sidecar": "onboarding/pkg/a.py.json",
            "countedChange": counted,
            "sourceBlobs": blobs if blobs is not None else {"pkg/a.py": BLOB_NEW},
        },
    }


# rule 1: the row's dispositions and source binding ---------------------------------------------


def test_updated_row_carries_sources() -> None:
    row = OnboardingTraceRow.model_validate(
        _row_document(
            disposition="updated",
            sources=[{"path": "pkg/a.py", "blob": BLOB_NEW}],
        )
    )
    assert row.disposition == "updated"
    assert [(source.path, source.blob) for source in row.sources or ()] == [("pkg/a.py", BLOB_NEW)]


def test_updated_row_without_sources_is_refused() -> None:
    with pytest.raises(ValueError, match="sources"):
        OnboardingTraceRow.model_validate(_row_document(disposition="updated"))


# rule 2: a counted change is Markdown or references beyond mechanical fields ------------------


def test_realizes_only_edit_is_not_counted() -> None:
    before = _sidecar()
    after = _sidecar(
        realizes=[
            {
                "id": "RLZ-A00002",
                "invariant": "INV-AAAAAA",
                "anchor": {
                    "locator": {"kind": "file"},
                    "blob": BLOB_NEW,
                    "content": "sha256:" + "1" * 64,
                },
                "role": "support",
                "rationale": "A still carries the rule.",
            }
        ]
    )
    assert before != after
    assert counted_sidecar_change(before, after) is False


def test_proves_only_edit_is_not_counted() -> None:
    before = _sidecar()
    after = _sidecar(
        proves=[
            {
                "id": "PRF-A00001",
                "invariant": "INV-AAAAAA",
                "anchor": {
                    "locator": {"kind": "symbol", "name": "test_a"},
                    "blob": BLOB_NEW,
                    "content": "sha256:" + "1" * 64,
                },
                "facet": "It proves A.",
            }
        ]
    )
    assert counted_sidecar_change(before, after) is False


def test_references_change_beyond_mechanical_fields_is_counted() -> None:
    before = _sidecar(references={})
    after = _sidecar(
        references={
            "1": {
                "targets": [
                    {
                        "anchor": {
                            "locator": {"kind": "file"},
                            "blob": BLOB_NEW,
                            "content": "sha256:" + "1" * 64,
                        }
                    }
                ]
            }
        }
    )
    assert counted_sidecar_change(before, after) is True


def test_writer_refuses_updated_row_without_counted_change(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": f"onboarding:{CODE_FILE}",
                        "disposition": "updated",
                        "reason": "The card was rewritten.",
                    }
                ]
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={CODE_FILE: BLOB_NEW}),
    )
    assert report.state == "refused"
    assert any("counted change" in problem.render() for problem in report.problems)


def test_writer_fills_row_sources_from_the_code_candidate(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": f"onboarding:{CODE_FILE}",
                        "disposition": "no_impact",
                        "reason": "The card was read and needs no change.",
                    }
                ]
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={CODE_FILE: BLOB_NEW}),
    )
    assert report.state == "written"
    stored = HistoryFile.model_validate(
        json.loads((world.memory / HISTORY_PATH).read_text(encoding="utf-8"))
    )
    row = stored.row_about(f"onboarding:{CODE_FILE}")
    assert isinstance(row, OnboardingTraceRow)
    assert [(source.path, source.blob) for source in row.sources or ()] == [(CODE_FILE, BLOB_NEW)]


def test_writer_marks_a_deleted_source_absent(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": "onboarding:pkg/gone.py",
                        "disposition": "no_impact",
                        "reason": "The card was read and needs no change.",
                    }
                ]
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )
    assert report.state == "written"
    stored = HistoryFile.model_validate(
        json.loads((world.memory / HISTORY_PATH).read_text(encoding="utf-8"))
    )
    row = stored.row_about("onboarding:pkg/gone.py")
    assert isinstance(row, OnboardingTraceRow)
    assert [(source.path, source.blob) for source in row.sources or ()] == [
        ("pkg/gone.py", "absent")
    ]


# rule 3: a row is current while its sources are -------------------------------------------------


def test_gate_accepts_a_row_whose_sources_equal_the_facts() -> None:
    history = _history([_row_document(sources=[{"path": "pkg/a.py", "blob": BLOB_NEW}])])
    assert onboarding_row_open(_item(), _context(history)) is None


def test_gate_reopens_when_the_file_changed_after_the_row() -> None:
    history = _history([_row_document(sources=[{"path": "pkg/a.py", "blob": BLOB_OLD}])])
    reason = onboarding_row_open(_item(), _context(history))
    assert reason is not None
    assert "changed after" in reason
    assert "again" in reason


def test_gate_refuses_an_updated_row_without_counted_change() -> None:
    history = _history(
        [
            _row_document(
                disposition="updated",
                sources=[{"path": "pkg/a.py", "blob": BLOB_NEW}],
            )
        ]
    )
    reason = onboarding_row_open(_item(counted=False), _context(history))
    assert reason is not None
    assert "counted change" in reason


def test_gate_accepts_an_updated_row_with_counted_change() -> None:
    history = _history(
        [
            _row_document(
                disposition="updated",
                sources=[{"path": "pkg/a.py", "blob": BLOB_NEW}],
            )
        ]
    )
    assert onboarding_row_open(_item(counted=True), _context(history)) is None


def test_uncovered_unexplained_change_is_answered_by_the_same_current_row() -> None:
    history = _history([_row_document(sources=[{"path": "pkg/a.py", "blob": BLOB_NEW}])])
    item = {
        "id": "sha256:" + "d" * 64,
        "kind": "unexplained_file",
        "subject": "file:pkg/a.py@" + BLOB_NEW,
        "facts": {
            "coverage": {"state": "uncovered"},
            "onboardingTrace": {
                "subject": "onboarding:pkg/a.py",
                "countedChange": False,
                "sourceBlobs": {"pkg/a.py": BLOB_NEW},
            },
        },
    }
    assert unexplained_row_open(item, _context(history)) is None


def test_uncovered_unexplained_change_reopens_with_a_stale_row() -> None:
    history = _history([_row_document(sources=[{"path": "pkg/a.py", "blob": BLOB_OLD}])])
    item = {
        "id": "sha256:" + "d" * 64,
        "kind": "unexplained_file",
        "subject": "file:pkg/a.py@" + BLOB_NEW,
        "facts": {
            "coverage": {"state": "uncovered"},
            "onboardingTrace": {
                "subject": "onboarding:pkg/a.py",
                "countedChange": False,
                "sourceBlobs": {"pkg/a.py": BLOB_NEW},
            },
        },
    }
    assert unexplained_row_open(item, _context(history)) is not None


# rule 4: rows written before this leaf -----------------------------------------------------------


def test_sourceless_row_in_an_open_file_answers_nothing() -> None:
    history = _history([_row_document()], closed=False)
    reason = onboarding_row_open(_item(), _context(history))
    assert reason is not None
    assert "again" in reason


def test_sourceless_row_in_a_closed_file_stays_valid() -> None:
    history = _history([_row_document()], closed=True)
    assert onboarding_row_open(_item(), _context(history)) is None


def test_crossing_marker_rows_carry_sources_from_the_paired_commit() -> None:
    base = MemoryInput(label="base", files={}, database=None)
    own = MemoryInput(
        label="own",
        files={
            "onboarding/pkg/a.py.md": b"# a\n\n## Update History\n\n- 2026-10-01: No content impact: none.\n"
        },
        database=None,
    )
    rows = marker_rows(base, own, code_blobs={"pkg/a.py": BLOB_NEW})
    assert rows
    assert rows[0]["sources"] == [{"path": "pkg/a.py", "blob": BLOB_NEW}]


def test_crossing_marker_row_marks_a_deleted_source_absent() -> None:
    base = MemoryInput(label="base", files={}, database=None)
    own = MemoryInput(
        label="own",
        files={
            "onboarding/pkg/gone.py.md": b"# gone\n\n## Update History\n\n- 2026-10-01: No content impact: none.\n"
        },
        database=None,
    )
    rows = marker_rows(base, own, code_blobs={})
    assert rows
    assert rows[0]["sources"] == [{"path": "pkg/gone.py", "blob": "absent"}]


def test_item_facts_carry_the_candidate_blobs() -> None:
    context = SimpleNamespace(storage=StorageSettings(), code_repository_name="demo")
    card = "onboarding/pkg/a.py.md"
    sides = SimpleNamespace(
        owner=LEAF_ID,
        base={},
        candidate={card: b"# a\n"},
        pairing={},
        incomplete=None,
    )
    result = onboarding_trace_result(
        context, ["pkg/a.py"], cast(Any, sides), code_blobs={"pkg/a.py": BLOB_NEW}
    )
    assert [item.subject for item in result.items] == ["onboarding:pkg/a.py"]
    assert result.items[0].to_document()["facts"]["sourceBlobs"] == {"pkg/a.py": BLOB_NEW}


CARD_BEFORE = b"# a\n"
CARD_AFTER = b"# a\n\nThe card now describes the change.\n"


def _trace_sides(
    *,
    counted_card: bool,
    rows: list[dict[str, Any]] | None = None,
    closed: bool = False,
) -> tuple[Any, Any]:
    """Producer sides with a card edit (or not) and history rows (MIK-R53 F01)."""
    sidecar = _sidecar()
    base = {"onboarding/pkg/a.py.md": CARD_BEFORE, "onboarding/pkg/a.py.json": sidecar}
    candidate = {
        "onboarding/pkg/a.py.md": CARD_AFTER if counted_card else CARD_BEFORE,
        "onboarding/pkg/a.py.json": sidecar,
    }
    candidate[HISTORY_PATH] = json.dumps(
        {"schema": "ar-history/v1", "leaf": LEAF_ID, "closed": closed, "rows": rows or []}
    ).encode("utf-8")
    sides = SimpleNamespace(
        owner=LEAF_ID, base=base, candidate=candidate, pairing={}, incomplete=None
    )
    context = SimpleNamespace(storage=StorageSettings(), code_repository_name="demo")
    return context, sides


def _produced_item(*, counted_card: bool, rows: list[dict[str, Any]] | None = None):
    context, sides = _trace_sides(counted_card=counted_card, rows=rows)
    result = onboarding_trace_result(
        context, ["pkg/a.py"], cast(Any, sides), code_blobs={"pkg/a.py": BLOB_NEW}
    )
    assert [item.subject for item in result.items] == ["onboarding:pkg/a.py"]
    return result.items[0]


def _current_sources(disposition: str = "no_impact") -> list[dict[str, Any]]:
    return [
        _row_document(disposition=disposition, sources=[{"path": "pkg/a.py", "blob": BLOB_NEW}])
    ]


# F01 (round 1): only a current eligible authored row answers ------------------


def test_rowless_counted_edit_leaves_the_item_open() -> None:
    item = _produced_item(counted_card=True)
    assert item.open
    assert item.to_document()["satisfiedBy"] is None


def test_stale_row_with_a_persistent_counted_edit_stays_open() -> None:
    rows = [_row_document(sources=[{"path": "pkg/a.py", "blob": BLOB_OLD}])]
    item = _produced_item(counted_card=True, rows=rows)
    assert item.open
    assert item.to_document()["satisfiedBy"] is None


def test_current_no_impact_row_closes_without_a_counted_edit() -> None:
    item = _produced_item(counted_card=False, rows=_current_sources())
    assert not item.open
    assert item.to_document()["satisfiedBy"] == "ROW-AAAAAA"


def test_current_updated_row_with_counted_change_closes_by_row() -> None:
    item = _produced_item(counted_card=True, rows=_current_sources("updated"))
    assert not item.open
    assert item.to_document()["satisfiedBy"] == "ROW-AAAAAA"


def test_current_updated_row_without_counted_change_stays_open() -> None:
    item = _produced_item(counted_card=False, rows=_current_sources("updated"))
    assert item.open
    assert item.to_document()["satisfiedBy"] is None


def test_gate_fact_rowless_counted_change_stays_open() -> None:
    history = _history([])
    assert onboarding_row_open(_item(counted=True), _context(history)) is not None


def test_generic_gate_rowless_counted_change_stays_open() -> None:
    assert onboarding_item_open(_item(counted=True), {}) is True


def test_stored_uncovered_rule_needs_the_trace_row() -> None:
    item = {
        "id": "sha256:" + "e" * 64,
        "kind": "unexplained_file",
        "subject": "file:pkg/a.py@" + BLOB_NEW,
        "facts": {
            "coverage": {"state": "uncovered"},
            "onboardingTrace": {
                "subject": "onboarding:pkg/a.py",
                "countedChange": True,
                "sourceBlobs": {"pkg/a.py": BLOB_NEW},
            },
        },
    }
    assert unexplained_satisfied_by(item, {}) is None
    assert unexplained_satisfied_by(item, {"onboarding:pkg/a.py": "ROW-AAAAAA"}) == "ROW-AAAAAA"


# F03 (round 1): the winning row keeps its own file's closed flag ----------------


def _attempt_file(rows: list[dict[str, Any]], *, attempt: int, closed: bool) -> HistoryFile:
    document: dict[str, Any] = {
        "schema": "ar-history/v1",
        "leaf": LEAF_ID,
        "closed": closed,
        "rows": rows,
    }
    if attempt > 1:
        document["attempt"] = attempt
    return HistoryFile.model_validate(document)


def _merged_context(*files: HistoryFile) -> GateContext:
    merged = merged_leaf_history(list(files))
    assert merged is not None
    candidate = SimpleNamespace(
        revision=lambda record_id: None,
        parsed=SimpleNamespace(
            history_files={f"knowledge/history/{LEAF_ID}-{n}.json": f for n, f in enumerate(files)}
        ),
    )
    return GateContext(
        owner=LEAF_ID, history=merged, candidate=cast(Any, candidate), code=cast(Any, None)
    )


def test_closed_first_attempt_sourceless_row_stays_valid() -> None:
    first = _attempt_file([_row_document()], attempt=1, closed=True)
    second = _attempt_file(
        [_row_document(id="ROW-BBBBBB", subject="onboarding:pkg/b.py")],
        attempt=2,
        closed=False,
    )
    context = _merged_context(first, second)
    assert context.history is not None and context.history.closed is False
    assert onboarding_row_open(_item(), context) is None


def test_superseding_open_sourceless_row_refuses() -> None:
    first = _attempt_file([_row_document()], attempt=1, closed=True)
    second = _attempt_file([_row_document(id="ROW-BBBBBB")], attempt=2, closed=False)
    context = _merged_context(first, second)
    reason = onboarding_row_open(_item(), context)
    assert reason is not None
    assert "again" in reason


# F02 (round 1): absent sidecar means empty references --------------------------


def test_created_empty_reference_sidecar_is_not_counted() -> None:
    assert counted_sidecar_change(None, _sidecar()) is False


def test_deleted_empty_reference_sidecar_is_not_counted() -> None:
    assert counted_sidecar_change(_sidecar(), None) is False


def test_created_authored_references_are_counted() -> None:
    after = _sidecar(references={"anchor": {"locator": {"kind": "file"}, "note": "read"}})
    assert counted_sidecar_change(None, after) is True


def test_deleted_nonempty_references_are_counted() -> None:
    before = _sidecar(references={"anchor": {"locator": {"kind": "file"}, "note": "read"}})
    assert counted_sidecar_change(before, None) is True


# F04 (round 1): the writer compares the card against the leaf's base ----------


def _counted_worklist(subject: str, *, counted: bool) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": "sha256:" + "f" * 64,
                "kind": "onboarding_trace",
                "subject": subject,
                "facts": {"countedChange": counted, "sourceBlobs": {CODE_FILE: BLOB_NEW}},
            }
        ]
    }


def _write_updated(tmp_path: Path, worklist: dict[str, Any] | None):
    world = build_world(tmp_path)
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": f"onboarding:{CODE_FILE}",
                        "disposition": "updated",
                        "reason": "The card was rewritten.",
                    }
                ]
            },
            worklist=worklist,
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={CODE_FILE: BLOB_NEW}),
    )


def test_writer_accepts_updated_when_the_leaf_base_counts_it(tmp_path: Path) -> None:
    # The card is unchanged in the worktree (nothing counted against own HEAD),
    # but the leaf's worklist -- the gate's own base -- carries the counted change.
    report = _write_updated(tmp_path, _counted_worklist(f"onboarding:{CODE_FILE}", counted=True))
    assert report.state == "written", report.render()


def test_writer_refuses_updated_when_the_leaf_base_counts_nothing(tmp_path: Path) -> None:
    # The card differs from own HEAD, but the leaf's worklist says the leaf base
    # carries no counted change: the writer must agree with the gate, not HEAD.
    world = build_world(tmp_path)
    card = world.memory / f"onboarding/{CODE_FILE}.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_bytes(b"# rewritten\n")
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": f"onboarding:{CODE_FILE}",
                        "disposition": "updated",
                        "reason": "The card was rewritten.",
                    }
                ]
            },
            worklist=_counted_worklist(f"onboarding:{CODE_FILE}", counted=False),
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={CODE_FILE: BLOB_NEW}),
    )
    assert report.state == "refused"
    assert any("counted change" in problem.render() for problem in report.problems)


# F15 (round 1): extant symlinks bind their target blob ------------------------


def _link_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "linkrepo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "symlink fixture")
    write(repo, {"target.py": "VALUE = 1\n"})
    (repo / "link.py").symlink_to("target.py")
    commit_all(repo, "link")
    return repo


def test_snapshot_binds_an_extant_symlink_blob(tmp_path: Path) -> None:
    repo = _link_repo(tmp_path)
    snapshot = CodeSnapshot.of_tree(repo, git(repo, "rev-parse", "HEAD^{tree}"))
    assert snapshot.blobs["link.py"] == git(repo, "rev-parse", "HEAD:link.py")
    assert snapshot.has_file("link.py")


def test_symlink_target_change_rebinds_and_deletion_binds_absent(tmp_path: Path) -> None:
    repo = _link_repo(tmp_path)
    before = CodeSnapshot.of_tree(repo, git(repo, "rev-parse", "HEAD^{tree}")).blobs["link.py"]
    (repo / "link.py").unlink()
    (repo / "link.py").symlink_to("other.py")
    write(repo, {"other.py": "VALUE = 2\n"})
    commit_all(repo, "retarget")
    after = CodeSnapshot.of_tree(repo, git(repo, "rev-parse", "HEAD^{tree}")).blobs["link.py"]
    assert after != before
    git(repo, "rm", "-q", "link.py")
    commit_all(repo, "drop")
    gone = CodeSnapshot.of_tree(repo, git(repo, "rev-parse", "HEAD^{tree}"))
    assert "link.py" not in gone.blobs and not gone.has_file("link.py")


def test_conversion_tree_binds_a_symlink_without_parsing_it(tmp_path: Path) -> None:
    repo = _link_repo(tmp_path)
    objects = CodeObjects(repo)
    bound = objects.tree(git(repo, "rev-parse", "HEAD"))
    assert bound["link.py"] == git(repo, "rev-parse", "HEAD:link.py")
    assert objects.symbol_span("link.py", bound["link.py"], "VALUE") is None


def test_trace_blobs_bind_an_extant_symlink(tmp_path: Path) -> None:
    repo = _link_repo(tmp_path)
    blobs = worktree_code_blobs(repo, ["link.py", "target.py"])
    assert blobs["link.py"] == git(repo, "rev-parse", "HEAD:link.py")
    assert blobs["target.py"] is not None


def test_onboarding_satisfying_row_requires_current_row_not_counted_alone() -> None:
    text = ONBOARDING_TRACE_KIND.satisfying_row
    assert text.startswith("the leaf's current onboarding_trace row")
    assert "disposition updated with a counted change" in text
    assert "disposition no_impact" in text


# rule 2 (round 3): the writer compares the leaf's paired base, not its own HEAD ---------------

CARD_MD = f"onboarding/{CODE_FILE}.md"
CARD_SIDECAR = f"onboarding/{CODE_FILE}.json"


def _closeout_world(tmp_path: Path) -> Any:
    """A reopened leaf: the product-owned closeout froze attempt 1 with a no_impact row."""

    world = build_world(tmp_path)
    blob = git(world.code, "rev-parse", f"HEAD:{CODE_FILE}")
    closed = HistoryFile.model_validate(
        {
            "schema": "ar-history/v1",
            "leaf": LEAF_ID,
            "closed": True,
            "rows": [
                {
                    "id": "ROW-AAAAAA",
                    "subject": f"onboarding:{CODE_FILE}",
                    "disposition": "no_impact",
                    "reason": "The card was read and needs no change.",
                    "items": [],
                    "sources": [{"path": CODE_FILE, "blob": blob}],
                }
            ],
        }
    )
    write(world.memory, {HISTORY_PATH: canonical_text(closed.model_dump(mode="json"))})
    return world


def _write_paired(world: Any, *, memory_base: str | None) -> Any:
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": f"onboarding:{CODE_FILE}",
                        "disposition": "updated",
                        "reason": "The card was rewritten after the closeout.",
                    }
                ]
            },
            commit=False,
            memory_base=memory_base,
        ),
        code=CodeSnapshot.capture(world.code),
    )


def test_writer_follows_the_leaf_base_when_the_card_differs_from_kb_only(
    tmp_path: Path,
) -> None:
    """F04 case 1: the candidate differs from K_B but not from own HEAD.

    The product-owned closeout updated the card on the operation's own line, so the
    HEAD comparison sees no change while the gate's K_B comparison does. The writer must
    author the row (as the gate does); the HEAD fallback refuses it.
    """

    world = _closeout_world(tmp_path)
    kb = git(world.memory, "rev-parse", "HEAD")
    card = world.memory / CARD_MD
    write(world.memory, {CARD_MD: card.read_bytes() + b"\nA product-owned clarification.\n"})
    commit_all(world.memory, "product-owned card update")

    head_only = _write_paired(world, memory_base=None)
    assert head_only.state == "refused"
    assert not [row for row in head_only.rows if row.disposition == "updated"]
    assert any("counted change" in problem.render() for problem in head_only.problems)

    paired = _write_paired(world, memory_base=kb)
    assert not any("counted change" in problem.render() for problem in paired.problems)
    authored = [row for row in paired.rows if row.disposition == "updated"]
    assert len(authored) == 1
    assert "attempt-2" in authored[0].history

    kb_files = knowledge_tree_from_git(world.memory, kb).files
    candidate_files = knowledge_tree_from_directory(world.memory).files
    assert counted_change(kb_files, candidate_files, CARD_MD, CARD_SIDECAR) is True


def test_writer_refuses_when_the_candidate_matches_the_leaf_base(tmp_path: Path) -> None:
    """F04 case 2: the candidate matches K_B while own HEAD carries a temporary card edit.

    The HEAD comparison sees a change the gate's K_B comparison does not. The writer must
    refuse the row (as the gate does); the HEAD fallback wrongly qualifies it.
    """

    world = _closeout_world(tmp_path)
    kb = git(world.memory, "rev-parse", "HEAD")
    card = world.memory / CARD_MD
    edited = card.read_bytes() + b"\nA temporary edit.\n"
    write(world.memory, {CARD_MD: edited})
    commit_all(world.memory, "temporary card edit")
    write(world.memory, {CARD_MD: card.read_bytes().replace(b"\nA temporary edit.\n", b"")})

    head_only = _write_paired(world, memory_base=None)
    assert [row for row in head_only.rows if row.disposition == "updated"]

    paired = _write_paired(world, memory_base=kb)
    assert paired.state == "refused"
    assert not [row for row in paired.rows if row.disposition == "updated"]
    assert any("counted change" in problem.render() for problem in paired.problems)

    kb_files = knowledge_tree_from_git(world.memory, kb).files
    candidate_files = knowledge_tree_from_directory(world.memory).files
    assert counted_change(kb_files, candidate_files, CARD_MD, CARD_SIDECAR) is False


def test_snapshot_refuses_a_symbol_locator_on_a_symlink(tmp_path: Path) -> None:
    """F15 round 3: the writer refuses a symbol anchor on a mode-120000 entry before parsing."""

    repo = _link_repo(tmp_path)
    snapshot = CodeSnapshot.capture(repo)
    assert "link.py" in snapshot.symlinks
    assert "target.py" not in snapshot.symlinks
    with pytest.raises(AnchorResolutionError, match=r"symlink.*120000"):
        snapshot.resolve("link.py", {"kind": "symbol", "name": "VALUE"})
    # A file locator on the link still resolves; the target's own symbols still bind.
    snapshot.resolve("link.py", {"kind": "file"})
    anchor = snapshot.resolve("target.py", {"kind": "symbol", "name": "VALUE"})
    assert anchor.blob == snapshot.blobs["target.py"]


def test_conversion_refuses_a_symbol_span_on_a_symlink(tmp_path: Path) -> None:
    """F15 round 3: the conversion refuses a symbol span on a mode-120000 entry."""

    repo = _link_repo(tmp_path)
    objects = CodeObjects(repo)
    commit = objects.commit("HEAD")
    assert commit is not None
    blobs = objects.tree(commit)
    symlinks = CodeObjects.link_paths(objects.tree_modes(commit))
    assert symlinks == frozenset({"link.py"})
    with pytest.raises(CodeObjectError, match=r"symlink.*120000"):
        objects.symbol_span("link.py", blobs["link.py"], "VALUE", symlinks=symlinks)
    with pytest.raises(CodeObjectError, match=r"symlink.*120000"):
        objects.content(
            blobs["link.py"], {"kind": "symbol", "name": "VALUE"}, "link.py", symlinks=symlinks
        )
    # File locators still read the link bytes; the target's own symbols still bind, while
    # an unbound name stays unresolved.
    assert objects.content(blobs["link.py"], {"kind": "file"}, "link.py") is not None
    assert objects.symbol_span("target.py", blobs["target.py"], "VALUE") == (1, 1)
    assert objects.symbol_span("target.py", blobs["target.py"], "MISSING") is None


def _write_paired_with_worklist(world: Any, *, memory_base: str | None, counted: bool) -> Any:
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": f"onboarding:{CODE_FILE}",
                        "disposition": "updated",
                        "reason": "The card was rewritten after the closeout.",
                    }
                ]
            },
            commit=False,
            memory_base=memory_base,
            worklist=_counted_worklist(f"onboarding:{CODE_FILE}", counted=counted),
        ),
        code=CodeSnapshot.capture(world.code),
    )


def test_stale_counted_flag_does_not_override_the_known_leaf_base(tmp_path: Path) -> None:
    """F04 round 4: a persisted true flag loses to a candidate that matches K_B.

    The worklist was computed before the leaf restored the card; the known paired base
    decides, so the writer refuses the row as the fresh gate would.
    """

    world = _closeout_world(tmp_path)
    commit_all(world.memory, "product-owned closeout")
    kb = git(world.memory, "rev-parse", "HEAD")
    report = _write_paired_with_worklist(world, memory_base=kb, counted=True)
    assert report.state == "refused"
    assert not [row for row in report.rows if row.disposition == "updated"]
    assert any("counted change" in problem.render() for problem in report.problems)


def test_stale_uncounted_flag_does_not_override_the_known_leaf_base(tmp_path: Path) -> None:
    """F04 round 4: a persisted false flag loses to a candidate that differs from K_B.

    The worklist was computed before the leaf re-edited the card; the known paired base
    decides, so the writer authors the row as the fresh gate would.
    """

    world = _closeout_world(tmp_path)
    kb = git(world.memory, "rev-parse", "HEAD")
    card = world.memory / CARD_MD
    write(world.memory, {CARD_MD: card.read_bytes() + b"\nA reopened clarification.\n"})
    commit_all(world.memory, "reopened card update")
    report = _write_paired_with_worklist(world, memory_base=kb, counted=False)
    assert not any("counted change" in problem.render() for problem in report.problems)
    assert len([row for row in report.rows if row.disposition == "updated"]) == 1


def _def_link_repo(tmp_path: Path) -> Path:
    """A repo whose symlink bytes parse as a definition: the extractor trap."""

    repo = tmp_path / "deflinkrepo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "symlink fixture")
    write(repo, {"target.py": "VALUE = 1\n"})
    (repo / "link.py").symlink_to("def foo():")
    commit_all(repo, "link")
    return repo


def test_code_trees_resolve_refuses_a_symbol_on_a_symlink(tmp_path: Path) -> None:
    """F15 round 4: the worklist extractor never parses link bytes for symbols."""

    repo = _def_link_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    link_blob = git(repo, "rev-parse", "HEAD:link.py")
    code = CodeTrees.open(repo, head, head)
    assert code.candidate_symlinks() == frozenset({"link.py"})
    with pytest.raises(CodeReadError, match=r"symlink.*120000"):
        code.resolve("link.py", {"kind": "symbol", "name": "foo"}, link_blob, link_blob)
    # A file locator still resolves; the target's own symbols still bind.
    assert code.resolve("link.py", {"kind": "file"}, link_blob, link_blob) is not None
    target_blob = git(repo, "rev-parse", "HEAD:target.py")
    assert (
        code.resolve("target.py", {"kind": "symbol", "name": "VALUE"}, target_blob, target_blob)
        is not None
    )


def test_carry_mapped_anchor_skips_a_symbol_on_a_symlink(tmp_path: Path) -> None:
    """F15 round 4: carry re-recording never parses link bytes for symbols."""

    repo = _def_link_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    link_blob = git(repo, "rev-parse", "HEAD:link.py")
    code = CodeTrees(CodeObjects(repo), head, head)
    anchor = {
        "locator": {"kind": "symbol", "name": "foo"},
        "blob": "0" * 40,
        "content": "sha256:" + "3" * 64,
    }
    assert mapped_anchor(code, "link.py", anchor, link_blob) is None


def _link_then_regular_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Two commits: B holds a parsing symlink at link.py, C a regular file there."""

    repo = tmp_path / "bclinkrepo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "symlink fixture")
    write(repo, {"target.py": "VALUE = 1\n"})
    (repo / "link.py").symlink_to("def foo():")
    commit_all(repo, "link at B")
    base_commit = git(repo, "rev-parse", "HEAD")
    (repo / "link.py").unlink()
    write(repo, {"link.py": "def foo():\n    return 1\n"})
    commit_all(repo, "regular at C")
    head = git(repo, "rev-parse", "HEAD")
    return repo, base_commit, head


def test_classifier_b_blob_symlink_refuses(tmp_path: Path) -> None:
    """F15 round 5: B-blob symbol resolution uses B modes and refuses the link."""

    repo, base_commit, head = _link_then_regular_repo(tmp_path)
    base_link_blob = git(repo, "rev-parse", f"{base_commit}:link.py")
    code = CodeTrees.open(repo, base_commit, head)
    assert code.base_symlinks() == frozenset({"link.py"})
    assert code.candidate_symlinks() == frozenset()
    anchor = Anchor.model_validate(
        {
            "path": "link.py",
            "locator": {"kind": "symbol", "name": "foo"},
            "blob": base_link_blob,
            "content": "sha256:" + "0" * 64,
        }
    )
    classifier = Classifier(code=code, base=None, renames={})  # type: ignore[arg-type]
    with pytest.raises(CodeReadError, match=r"symlink.*120000"):
        classifier._classify_anchor("k", "realization", "INV-AAAAAA", "link.py", anchor)


def test_base_side_spans_skip_symlink_symbols(tmp_path: Path) -> None:
    """F15 round 5: the base side of span computation never parses link bytes."""

    repo, base_commit, head = _link_then_regular_repo(tmp_path)
    base_link_blob = git(repo, "rev-parse", f"{base_commit}:link.py")
    code = CodeTrees.open(repo, base_commit, head)
    locator = {"kind": "symbol", "name": "foo"}
    with pytest.raises(Exception, match=r"symlink.*120000"):
        code.resolve("link.py", locator, base_link_blob, base_link_blob, side="base")
    candidate_blob = git(repo, "rev-parse", f"{head}:link.py")
    assert (
        code.resolve("link.py", locator, candidate_blob, candidate_blob, side="candidate")
        is not None
    )
