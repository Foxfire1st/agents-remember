"""MIK-R128 authoring and pure master order witnesses, through real task_doc writes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.serving.projections.snapshots_impl._task_documents import read_task_documents
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.master_order import derive_master_order, load_master_children
from test_task_document import _config


def document(task_id: str, **fields: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": task_id,
            "slug": task_id.lower(),
            "title": task_id,
            "repo": "agents-remember",
            "kind": "subTask",
            "type": "Code",
            "createdAt": "2026-01-01T00:00",
            **fields,
        }
    )


@pytest.fixture
def world(tmp_path: Path):
    config = _config(tmp_path)
    root = tmp_path / "tasks" / "agents-remember" / "series"
    master = document(
        "series",
        kind="master",
        subTasks=[
            {"number": name, "name": name, "file": f"{name.lower()}.md"} for name in ("A", "B", "C")
        ],
    )
    write_task_doc(root, master)
    for name in ("A", "B", "C"):
        write_task_doc(root, document(name, master="task.md"))

    def call(task_id: str, operation: str = "set_field", *, dry_run: bool = False, **edits: Any):
        return task_doc_tool(
            config,
            TaskDocTarget(
                repo_id="agents-remember",
                task_name="series",
                slug=None if task_id == "series" else task_id.lower(),
            ),
            operation=operation,
            edit=TaskDocEdit(**edits),
            call=TaskDocCall(dry_run=dry_run),
        )

    return root, call


def test_waits_for_unknown_exact_id_refuses_without_writing(world):
    root, call = world
    before = (root / "a.json").read_bytes()
    with pytest.raises(TaskDocError, match=r"waitsFor.*outside"):
        call("A", fields={"waitsFor": ["outside"]})
    assert (root / "a.json").read_bytes() == before


@pytest.mark.parametrize(
    "field,heading",
    [
        ("objective", "oBJeCtIvE"),
        ("requirements", "Requirements"),
        ("design", "Design"),
        ("nonGoals", "Non-goals"),
        ("acceptance", "Acceptance"),
        ("append_decision", "Decisions"),
        ("set_subtask", "Sub-tasks"),
        ("waitsFor", "Execution order"),
    ],
)
def test_new_reserved_freeform_headings_refuse_correct_home(world, field, heading):
    root, call = world
    before = (root / "task.json").read_bytes()
    with pytest.raises(TaskDocError, match=field):
        call("series", "set_section", section={"kind": "freeform", "heading": heading, "body": "x"})
    assert (root / "task.json").read_bytes() == before


def test_derived_order_uses_states_and_latest_unfinished_prerequisite():
    children = {
        name: document(name, **fields)
        for name, fields in {
            "A": {"status": "planning", "waitsFor": []},
            "B": {"waitsFor": []},
            "C": {"waitsFor": ["B"]},
            "D": {"waitsFor": ["A", "C", "F"]},
            "E": {"waitsFor": ["F"]},
            "F": {"status": "Completed"},
        }.items()
    }
    master = document(
        "series",
        kind="master",
        subTasks=[
            {
                "number": child.id,
                "name": child.title,
                "file": f"{child.slug}.md",
                "status": "inProgress" if child.id == "A" else child.status,
            }
            for child in children.values()
        ],
    )
    order = derive_master_order(master, children)
    assert order is not None
    assert order.ready_ids == ("B", "E")
    assert {task.id: task.wave for task in order.tasks} == {
        "A": 0,
        "B": 1,
        "C": 2,
        "D": 3,
        "E": 1,
        "F": -1,
    }
    assert order.cycle_ids == ()
    assert next(task for task in order.tasks if task.id == "D").waits_for == ("A", "C", "F")


@pytest.mark.parametrize("operation", ["create", "replace", "set_field"])
@pytest.mark.parametrize("bad_id", ["A", "unknown", "b", " B "])
def test_waits_for_validation_covers_all_field_writers(world, operation, bad_id):
    root, call = world
    payload = read_task_doc(root / "a.json").model_dump(by_alias=True, exclude_none=True)
    if operation == "create":
        (root / "a.json").unlink()
        (root / "a.md").unlink()
    fields = (
        {"waitsFor": [bad_id]} if operation == "set_field" else {**payload, "waitsFor": [bad_id]}
    )
    with pytest.raises(TaskDocError) as refused:
        call("A", operation, fields=fields)
    assert "waitsFor" in str(refused.value)
    assert repr(bad_id) in str(refused.value)


def test_cycle_refusal_names_exact_participants_and_keeps_source(world):
    root, call = world
    assert call("B", fields={"waitsFor": ["A"]})["ok"]
    assert call("C", fields={"waitsFor": ["B"]})["ok"]
    before = (root / "a.json").read_bytes()
    with pytest.raises(TaskDocError, match="cycle") as refused:
        call("A", fields={"waitsFor": ["C"]})
    assert all(repr(name) in str(refused.value) for name in ("A", "B", "C"))
    assert (root / "a.json").read_bytes() == before


@pytest.mark.parametrize("gone", ["abandoned", "removed", "missing"])
def test_gone_prerequisites_survive_other_writes_but_must_drop_on_field_write(world, gone):
    root, call = world
    assert call("A", fields={"waitsFor": ["B"]})["ok"]
    if gone == "abandoned":
        assert call("B", "set_status", fields={"status": "abandoned"})["ok"]
    elif gone == "removed":
        assert call("B", "set_status", fields={"status": "Completed"})["ok"]
        assert call("series", "remove_subtask", subtask={"number": "B"})["ok"]
    else:
        (root / "b.json").unlink()
    assert call("A", fields={"title": "Still editable"})["ok"]
    assert read_task_doc(root / "a.json").waitsFor == ["B"]
    if gone == "missing":
        assert call("A", fields={"waitsFor": ["B"]})["ok"]  # The row still lists B.
    else:
        with pytest.raises(TaskDocError, match=r"waitsFor.*B"):
            call("A", fields={"waitsFor": ["B"]})
    assert call("A", fields={"waitsFor": []})["ok"]
    assert read_task_doc(root / "a.json").waitsFor == []


@pytest.mark.parametrize("operation", ["create", "replace"])
def test_new_header_notes_name_the_field_and_legacy_notes_remain_editable(world, operation):
    root, call = world
    base = read_task_doc(root / "a.json").model_dump(by_alias=True, exclude_none=True)
    if operation == "create":
        (root / "a.json").unlink()
        (root / "a.md").unlink()
    for label, field in [
        ("mAsTeR", "master"),
        ("Parent sprint", "master"),
        ("Dependencies", "waitsFor"),
        ("Depends on", "waitsFor"),
        ("Execution nature", "executionNature"),
        ("Integration branch", "integrationBranch"),
    ]:
        with pytest.raises(TaskDocError, match=field):
            call("A", operation, fields={**base, "headerNotes": [{"label": label, "value": "x"}]})
    write_task_doc(
        root, document("A", master="task.md", headerNotes=[{"label": "Master", "value": "old"}])
    )
    legacy = read_task_doc(root / "a.json").model_dump(by_alias=True, exclude_none=True)
    legacy["headerNotes"][0]["value"] = "edited"
    assert call("A", "replace", fields=legacy)["ok"]
    assert read_task_doc(root / "a.json").headerNotes[0].value == "edited"
    legacy["headerNotes"] = []
    assert call("A", "replace", fields=legacy)["ok"]


def test_existing_reserved_freeform_sections_can_edit_and_remove_not_duplicate(world):
    root, call = world
    write_task_doc(
        root,
        document(
            "A",
            master="task.md",
            sections=[
                {"kind": "freeform", "heading": "Execution order", "body": "legacy"},
            ],
        ),
    )
    assert call("A", "set_section", section={"heading": "Execution order", "body": "edited"})["ok"]
    legacy = read_task_doc(root / "a.json").model_dump(by_alias=True, exclude_none=True)
    assert legacy["sections"][0]["body"] == "edited"
    with pytest.raises(TaskDocError, match="waitsFor"):
        call("A", "replace", fields={**legacy, "sections": legacy["sections"] * 2})
    assert call("A", "replace", fields={**legacy, "sections": []})["ok"]


@pytest.mark.parametrize("operation", ["create", "replace"])
def test_full_document_writers_cannot_create_reserved_sections(world, operation):
    root, call = world
    base = read_task_doc(root / "a.json").model_dump(by_alias=True, exclude_none=True)
    if operation == "create":
        (root / "a.json").unlink()
        (root / "a.md").unlink()
    with pytest.raises(TaskDocError, match="design"):
        call(
            "A",
            operation,
            fields={
                **base,
                "sections": [
                    {"kind": "freeform", "heading": "dEsIgN", "body": "x"},
                ],
            },
        )


def test_cycles_and_gone_tasks_are_visible_never_ready():
    children = {
        "A": document("A", waitsFor=["B"]),
        "B": document("B", waitsFor=["A"]),
        "C": document("C", waitsFor=["A", "removed"]),
        "D": document("D", status="abandoned"),
        "E": document("E", waitsFor=["D"]),
    }
    master = document(
        "series",
        kind="master",
        subTasks=[
            {
                "number": name,
                "name": name,
                "file": f"{name.lower()}.md",
                "status": "abandoned" if name == "D" else "planning",
            }
            for name in [*children, "missing"]
        ],
    )
    order = derive_master_order(master, children)
    assert order is not None
    tasks = {task.id: task for task in order.tasks}
    assert order.cycle_ids == ("A", "B")
    assert tasks["A"].cycle and tasks["B"].cycle
    assert tasks["A"].wave == tasks["B"].wave
    assert tasks["C"].wave == tasks["A"].wave + 1
    assert tasks["removed"].status == "gone"
    assert tasks["missing"].status == "planning" and tasks["missing"].file == "missing.md"
    assert tasks["missing"].waits_for == ()
    assert tasks["D"].status == "abandoned"
    assert order.ready_ids == ("missing",)


def test_absent_order_includes_all_empty_dependency_fields():
    master = document(
        "series", kind="master", subTasks=[{"number": "A", "name": "A", "file": "a.md"}]
    )
    assert derive_master_order(master, {"A": document("A")}) is None
    assert derive_master_order(master, {"unlisted": document("unlisted", waitsFor=[])}) is None
    assert derive_master_order(master, {"A": document("A", waitsFor=[])}) is None


def test_child_loader_reads_only_listed_files_with_exact_doc_ids(tmp_path):

    write_task_doc(tmp_path, document("A", slug="explicit"))
    write_task_doc(tmp_path, document("B", waitsFor=["A"]))
    master = document(
        "series",
        kind="master",
        subTasks=[
            {"number": "A", "name": "A", "file": "explicit.md"},
            {"number": "wrong", "name": "wrong", "file": "b.md"},
            {"number": "B", "name": "B"},
        ],
    )
    assert list(load_master_children(tmp_path, master)) == ["A", "B"]


def test_field_write_refreshes_derived_markdown_and_projected_heads_without_storing_waves(world):
    root, call = world
    master_before = (root / "task.json").read_bytes()
    assert "## Order" not in (root / "task.md").read_text()
    assert call("A", fields={"waitsFor": ["B"]})["ok"]
    assert (root / "task.json").read_bytes() == master_before
    rendered = (root / "task.md").read_text()
    assert "## Order" in rendered
    assert "| 1 | [B — B](b.md) | planning |" in rendered
    assert "| 2 | [A — A](a.md) | planning | B |" in rendered
    projected = {
        node.id: node
        for node in read_task_documents(
            root.parents[2], enclosures=[], now=datetime(2026, 1, 1, tzinfo=UTC)
        )
    }
    graph = projected["series"].executionGraphView
    assert graph is not None
    assert {node.masterTitle: (node.waveIndex, node.frontierState) for node in graph.nodes} == {
        "A": (2, "waiting"),
        "B": (1, "ready"),
        "C": (1, "ready"),
    }
    assert [(item.id, item.status) for item in projected["A"].waitsForTasks or []] == [
        ("B", "planning")
    ]
    assert [(item.id, item.status) for item in projected["B"].neededByTasks or []] == [
        ("A", "planning")
    ]
    assert call("A", fields={"waitsFor": []})["ok"]
    assert "## Order" not in (root / "task.md").read_text()
    projected = {
        node.id: node
        for node in read_task_documents(
            root.parents[2], enclosures=[], now=datetime(2026, 1, 1, tzinfo=UTC)
        )
    }
    assert projected["series"].executionGraphView is None
    assert (root / "task.json").read_bytes() == master_before


def test_removed_prerequisite_projects_gone_and_a_legacy_cycle_stays_one_wave(world):
    root, call = world
    assert call("A", fields={"waitsFor": ["B"]})["ok"]
    assert call("B", "set_status", fields={"status": "Completed"})["ok"]
    assert call("series", "remove_subtask", subtask={"number": "B"})["ok"]
    projected = {
        node.id: node
        for node in read_task_documents(
            root.parents[2], enclosures=[], now=datetime(2026, 1, 1, tzinfo=UTC)
        )
    }
    gone = (projected["A"].waitsForTasks or [])[0]
    assert (gone.id, gone.status, gone.docPath) == ("B", "gone", None)
    graph = projected["series"].executionGraphView
    assert graph is not None
    blocked = next(node for node in graph.nodes if node.masterTitle == "A")
    assert blocked.frontierState == "waiting"
    assert blocked.predecessors[0].predecessorTitle == "B (gone)"
    master = document(
        "series",
        kind="master",
        subTasks=[
            {"number": "A", "name": "A", "file": "a.md", "status": "inProgress"},
            {"number": "B", "name": "B", "file": "b.md", "status": "planning"},
        ],
    )
    write_task_doc(root, document("B", master="task.md", waitsFor=["A"]))
    write_task_doc(root, master)
    projected = {
        node.id: node
        for node in read_task_documents(
            root.parents[2], enclosures=[], now=datetime(2026, 1, 1, tzinfo=UTC)
        )
    }
    graph = projected["series"].executionGraphView
    assert graph is not None
    assert all(node.cycle for node in graph.nodes)
    assert len({node.waveIndex for node in graph.nodes}) == 1
    assert {node.frontierState for node in graph.nodes} == {"waiting"}
    rendered = (root / "task.md").read_text()
    assert "| 0 (cycle) | [A" in rendered
    assert "| 0 (cycle) | [B" in rendered


def test_replace_omitted_dependency_refreshes_parent_order(world):
    root, call = world
    assert call("A", fields={"waitsFor": ["B"]})["ok"]
    assert "## Order" in (root / "task.md").read_text()
    parent_json = (root / "task.json").read_bytes()
    replacement = read_task_doc(root / "a.json").model_dump(by_alias=True, exclude_none=True)
    replacement.pop("waitsFor")
    assert call("A", "replace", fields=replacement)["ok"]
    assert read_task_doc(root / "a.json").waitsFor is None
    assert "## Order" not in (root / "task.md").read_text()
    projected = read_task_documents(
        root.parents[2], enclosures=[], now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert next(node for node in projected if node.id == "series").executionGraphView is None
    assert (root / "task.json").read_bytes() == parent_json


def test_noop_master_preview_preserves_derived_order(world):
    root, call = world
    assert call("A", fields={"waitsFor": ["B"]})["ok"]
    before = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
    preview = call("series", fields={"title": "series"}, dry_run=True)
    assert preview["rendered"] == (root / "task.md").read_text()
    assert preview["diff"] == ""
    assert preview["wouldLose"] is False
    assert {path: path.read_bytes() for path in before} == before


def test_dependency_preview_uses_candidate_for_parent_order(world):
    root, call = world
    assert call("A", fields={"waitsFor": ["B"]})["ok"]
    before = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
    preview = call("A", fields={"waitsFor": ["C"]}, dry_run=True)
    parent = preview["masterSync"]["rendered"]
    assert "| 2 | [A — A](a.md) | planning | C |" in parent
    assert "| 2 | [A — A](a.md) | planning | B |" not in parent
    assert {path: path.read_bytes() for path in before} == before
    assert call("A", fields={"waitsFor": ["C"]})["ok"]
    assert (root / "task.md").read_text() == parent


@pytest.mark.parametrize("legacy", [False, True])
def test_master_field_remedies_render_fixed_prose_and_preserve_sections(world, legacy):
    root, call = world
    sections = (
        [
            {"kind": "freeform", "heading": "Objective", "body": "Stored old objective."},
            {"kind": "freeform", "heading": "Local notes", "body": "Stored notes."},
        ]
        if legacy
        else []
    )
    if legacy:
        base = read_task_doc(root / "task.json").model_dump(by_alias=True)
        write_task_doc(root, TaskDocument.model_validate({**base, "sections": sections}))
    assert call(
        "series",
        fields={
            "objective": "Field objective.",
            "nonGoals": "Field non-goals.",
            "requirements": ["Field requirement."],
            "design": "Field design.",
            "acceptance": "Field acceptance.",
        },
    )["ok"]
    rendered = (root / "task.md").read_text()
    blocks = [
        "Field objective.",
        "Field non-goals.",
        "Field requirement.",
        "Field design.",
        "Field acceptance.",
    ]
    assert all(block in rendered for block in blocks)
    assert [rendered.index(block) for block in blocks] == sorted(
        rendered.index(block) for block in blocks
    )
    assert [
        section.model_dump() for section in read_task_doc(root / "task.json").sections
    ] == sections
    if legacy:
        assert (
            rendered.index("Field acceptance.")
            < rendered.index("Stored old objective.")
            < rendered.index("Stored notes.")
        )


@pytest.mark.parametrize(
    "states", [("inProgress", "planning"), ("Completed", "planning"), ("Completed", "Completed")]
)
def test_legacy_cycle_identity_keeps_completed_members(world, states):
    root, _call = world
    master_data = read_task_doc(root / "task.json").model_dump(by_alias=True)
    for row in master_data["subTasks"]:
        if row["number"] in ("A", "B"):
            row["status"] = states[("A", "B").index(row["number"])]
    for name, other, status in zip(("A", "B"), ("B", "A"), states, strict=True):
        write_task_doc(root, document(name, master="task.md", status=status, waitsFor=[other]))
    master = TaskDocument.model_validate(master_data)
    write_task_doc(root, master)
    before = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
    order = derive_master_order(master, load_master_children(root, master))
    assert order is not None
    tasks = {task.id: task for task in order.tasks}
    assert order.cycle_ids == ("A", "B")
    assert tasks["A"].cycle and tasks["B"].cycle
    assert tasks["A"].wave == tasks["B"].wave
    assert not set(("A", "B")) & set(order.ready_ids)
    graph = next(
        node
        for node in read_task_documents(
            root.parents[2], enclosures=[], now=datetime(2026, 1, 1, tzinfo=UTC)
        )
        if node.id == "series"
    ).executionGraphView
    assert graph is not None
    assert all(node.cycle for node in graph.nodes if node.masterTitle in ("A", "B"))
    rendered = (root / "task.md").read_text()
    assert f"Completed tasks: {states.count('Completed')}." in rendered
    if states != ("Completed", "Completed"):
        assert "Legacy cycle members: A, B" in rendered
    assert {path: path.read_bytes() for path in before} == before
