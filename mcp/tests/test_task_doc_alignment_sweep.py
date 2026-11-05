"""Inherited MIK-R62 rule 14: task_doc names sibling packets without gating writes."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.mcp.registration.tasks import _TASK_DOC_TOOL_DESCRIPTION
from agents_remember.models.task_doc import TaskDocAlignmentLeaf
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import bound_for
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from pydantic import TypeAdapter, ValidationError
from test_task_document import _config

MESSAGE = "Sweep these against the new or revised packet before release (operations/planning.md)."


def _leaf(index: int, **overrides: Any) -> dict[str, Any]:
    return {
        "id": f"L{index}",
        "slug": f"leaf-{index}",
        "title": f"Leaf {index}",
        "kind": "subTask",
        "repo": "agents-remember",
        "createdAt": "2026-01-01T00:00:00Z",
        "master": "task.md",
        "requirements": [f"Read MIK-R{index}@v2 and MIK-R{index}@v2."],
        **overrides,
    }


def _world(tmp_path: Path, count: int = 2):
    config = _config(tmp_path)
    root = tmp_path / "tasks" / "agents-remember" / "sweep"
    rows = []
    for index in range(1, count + 1):
        document = TaskDocument.model_validate(_leaf(index))
        write_task_doc(root, document)
        rows.append({"number": document.id, "name": document.title, "file": f"{document.slug}.md"})
    write_task_doc(
        root,
        TaskDocument.model_validate(
            {
                "id": "series",
                "slug": "series",
                "title": "Series",
                "kind": "master",
                "repo": "agents-remember",
                "createdAt": "2026-01-01T00:00:00Z",
                "subTasks": rows,
                "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
            }
        ),
    )
    return config, root


def _deliver(config, operation: str = "create", *, dry_run: bool = False, fields=None):
    whole = task_doc_tool(
        config,
        TaskDocTarget(repo_id="agents-remember", task_name="sweep", slug="leaf-99"),
        operation=operation,
        edit=TaskDocEdit(fields=_leaf(99) if fields is None else fields),
        call=TaskDocCall(dry_run=dry_run),
    )
    with tool_call_context("task_doc", {"operation": operation, "dry_run": dry_run}, config=config):
        answer = complete_tool_response("task_doc", whole, config=config)
    return whole, answer


def _prepare_edit(config, operation):
    if operation == "create":
        return None
    _deliver(config)
    fields = {"requirements": ["MIK-R99@v3"]}
    return _leaf(99, **fields) if operation == "replace" else fields


@pytest.mark.parametrize("operation", ["create", "set_field", "replace"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_declared_missing_sibling_proceeds_and_retains_unavailable_id(tmp_path, operation, dry_run):
    config, root = _world(tmp_path)
    fields = _prepare_edit(config, operation)
    (root / "leaf-1.json").unlink()
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    whole, answer = _deliver(config, operation, dry_run=dry_run, fields=fields)
    expected = [
        {"id": "L1", "state": "unavailable"},
        {"id": "L2", "state": "available", "requirements": ["MIK-R2@v2"]},
    ]
    assert whole["alignmentSweep"]["openLeaves"] == expected
    assert answer["alignmentSweep"]["openLeaves"] == expected
    if dry_run:
        assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == before
    else:
        assert read_task_doc(root / "leaf-99.json").id == "L99"


@pytest.mark.parametrize(
    ("reference", "cause"),
    [
        ({}, "no-file"),
        ({"file": ""}, "no-file"),
        ({"file": "."}, "no-name"),
        ({"file": " \t "}, "no-file"),
        ({"file": "not-written.md"}, "missing"),
    ],
)
def test_unavailable_reference_keeps_master_bytes_and_names_its_cause(
    tmp_path, caplog, reference, cause
):
    config, root = _world(tmp_path)
    master_path = root / "task.json"
    master = json.loads(master_path.read_text())
    master["subTasks"][0].pop("file")
    master["subTasks"][0].update(reference)
    master_path.write_text(json.dumps(master, indent=3) + "\n")
    # A usable document at the row-number spelling must not become an invented reference.
    (root / "L1.json").write_bytes((root / "leaf-1.json").read_bytes())
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    with caplog.at_level(
        logging.WARNING, logger="agents_remember.application.task_docs.alignment_sweep"
    ):
        whole, answer = _deliver(config, dry_run=True)
    expected = [
        {"id": "L1", "state": "unavailable"},
        {"id": "L2", "state": "available", "requirements": ["MIK-R2@v2"]},
    ]
    assert whole["alignmentSweep"]["openLeaves"] == expected
    assert answer["alignmentSweep"]["openLeaves"] == expected
    assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == before
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == "agents_remember.application.task_docs.alignment_sweep"
    ] == [f"Alignment sweep: row 'L1' unavailable ({cause})"]


@pytest.mark.parametrize("operation", ["create", "set_field", "replace"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_declared_present_empty_sibling_is_available(tmp_path, operation, dry_run):
    config, root = _world(tmp_path, 1)
    fields = _prepare_edit(config, operation)
    write_task_doc(root, TaskDocument.model_validate(_leaf(1, requirements=[])))
    whole, answer = _deliver(config, operation, dry_run=dry_run, fields=fields)
    expected = [{"id": "L1", "state": "available", "requirements": []}]
    assert whole["alignmentSweep"]["openLeaves"] == expected
    assert answer["alignmentSweep"]["openLeaves"] == expected


@pytest.mark.parametrize("dry_run", [False, True])
def test_undeclared_sibling_has_no_read_or_report_row(tmp_path, dry_run):
    config, root = _world(tmp_path, 1)
    write_task_doc(root, TaskDocument.model_validate(_leaf(2)))
    with patch(
        "agents_remember.application.task_docs.alignment_sweep.read_task_doc", wraps=read_task_doc
    ) as reader:
        _whole, answer = _deliver(config, dry_run=dry_run)
    assert [call.args[0] for call in reader.call_args_list] == [
        root / "task.json",
        root / "leaf-1.json",
    ]
    assert [row["id"] for row in answer["alignmentSweep"]["openLeaves"]] == ["L1"]


@pytest.mark.parametrize("operation", ["create", "set_field", "replace"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_malformed_declared_sibling_remains_a_loud_error(tmp_path, operation, dry_run):
    config, root = _world(tmp_path, 1)
    fields = _prepare_edit(config, operation)
    (root / "leaf-1.json").write_text("{not JSON", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    with pytest.raises(ValidationError, match="Invalid JSON"):
        _deliver(config, operation, dry_run=dry_run, fields=fields)
    assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == before


@pytest.mark.parametrize(
    "row",
    [
        {"id": "L1", "state": "unavailable"},
        {"id": "L1", "state": "available", "requirements": []},
    ],
)
def test_alignment_leaf_states_validate_without_coercing_missing_into_empty(row):
    assert TypeAdapter(TaskDocAlignmentLeaf).validate_python(row).model_dump() == row


@pytest.mark.parametrize(
    "row",
    [
        {"id": "L1", "requirements": []},
        {"id": "L1", "state": "unknown"},
        {"id": "L1", "state": "available"},
        {"id": "L1", "state": "unavailable", "requirements": []},
        {"id": "L1", "state": "unavailable", "requirements": None},
    ],
)
def test_alignment_leaf_unknown_or_incompatible_states_refuse(row):
    with pytest.raises(ValidationError):
        TypeAdapter(TaskDocAlignmentLeaf).validate_python(row)


@pytest.mark.parametrize("dry_run", [False, True])
def test_create_delivers_both_open_siblings_through_complete_response(tmp_path, dry_run):
    config, root = _world(tmp_path)
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    whole, answer = _deliver(config, dry_run=dry_run)
    expected = {
        "message": MESSAGE,
        "openLeaves": [
            {"id": "L1", "state": "available", "requirements": ["MIK-R1@v2"]},
            {"id": "L2", "state": "available", "requirements": ["MIK-R2@v2"]},
        ],
    }
    assert whole.get("alignmentSweep") == expected
    assert answer.get("alignmentSweep") == expected
    assert count_response_tokens(answer) <= bound_for("task_doc")
    if dry_run:
        assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == before


@pytest.mark.parametrize("operation", ["set_field", "replace"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_requirement_edits_deliver_sweep_and_exclude_the_subject(tmp_path, operation, dry_run):
    config, _root = _world(tmp_path)
    _deliver(config)
    fields = {"requirements": ["MIK-R99@v3"]}
    if operation == "replace":
        fields = _leaf(99, **fields)
    _whole, answer = _deliver(config, operation, dry_run=dry_run, fields=fields)
    assert [leaf["id"] for leaf in answer["alignmentSweep"]["openLeaves"]] == ["L1", "L2"]


def test_structured_requirements_take_their_own_identity_not_the_path(tmp_path):
    config, root = _world(tmp_path, 1)
    sibling = read_task_doc(root / "leaf-1.json")
    sibling = sibling.model_copy(
        update={
            "requirements": TaskDocument.model_validate(
                _leaf(
                    1,
                    requirements=[
                        {
                            "kind": "approved-requirement-packet",
                            "stableId": "MIK-R1",
                            "version": "v7",
                            "path": "requirements/MIK-R999-v1-irrelevant-filename.md",
                        },
                        "Also MIK-R2@v3 and MIK-R2@v3; then MIK-R3@v10.",
                    ],
                )
            ).requirements
        }
    )
    write_task_doc(root, sibling)
    _whole, answer = _deliver(config)
    assert answer["alignmentSweep"]["openLeaves"] == [
        {"id": "L1", "state": "available", "requirements": ["MIK-R1@v7", "MIK-R2@v3", "MIK-R3@v10"]}
    ]


@pytest.mark.parametrize("status", ["Completed", "abandoned"])
def test_terminal_master_rows_do_not_become_open_siblings(tmp_path, status):
    config, root = _world(tmp_path, 2)
    master = read_task_doc(root / "task.json")
    master = master.model_copy(
        update={"subTasks": [row.model_copy(update={"status": status}) for row in master.subTasks]}
    )
    write_task_doc(root, master)
    whole, answer = _deliver(config)
    assert "alignmentSweep" not in whole
    assert "alignmentSweep" not in answer


@pytest.mark.parametrize("siblings", [0, 2])
def test_no_other_open_leaf_and_nonrequirement_edits_have_no_sweep(tmp_path, siblings):
    config, _root = _world(tmp_path, siblings)
    _whole, answer = _deliver(config)
    assert ("alignmentSweep" in answer) is bool(siblings)
    assert "alignmentSweep" not in _deliver(config, "set_field", fields={"title": "Renamed"})[1]


def test_refusal_does_not_carry_a_successful_sweep(tmp_path):
    config, _root = _world(tmp_path)
    whole, answer = _deliver(config, "get")
    assert whole["ok"] is False
    assert "alignmentSweep" not in whole
    assert "alignmentSweep" not in answer


def test_more_than_twelve_siblings_are_bounded_by_count_and_master_location(tmp_path):
    config, root = _world(tmp_path, 13)
    whole, answer = _deliver(config)
    assert whole["alignmentSweep"] == {
        "message": MESSAGE,
        "openLeafCount": 13,
        "masterTaskDocument": str(root / "task.md"),
    }
    assert answer["alignmentSweep"] == whole["alignmentSweep"]
    assert count_response_tokens(answer) <= bound_for("task_doc")


def test_twelve_siblings_still_carry_each_requirement_identity(tmp_path):
    config, _root = _world(tmp_path, 12)
    _whole, answer = _deliver(config)
    assert answer["alignmentSweep"] == {
        "message": MESSAGE,
        "openLeaves": [
            {"id": f"L{index}", "state": "available", "requirements": [f"MIK-R{index}@v2"]}
            for index in range(1, 13)
        ],
    }
    assert count_response_tokens(answer) <= bound_for("task_doc")


def test_tool_description_names_field_within_existing_six_hundred_character_bound():
    assert "alignmentSweep" in _TASK_DOC_TOOL_DESCRIPTION
    assert len(_TASK_DOC_TOOL_DESCRIPTION) <= 600


def test_sweep_is_answer_only_and_does_not_change_the_written_document(tmp_path):
    config, root = _world(tmp_path)
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    whole, answer = _deliver(config)
    assert "alignmentSweep" in whole
    with_sweep = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    assert b"alignmentSweep" not in with_sweep["leaf-99.json"]
    for path in root.iterdir():
        if path.is_file() and path.name not in before:
            path.unlink()
    for name, data in before.items():
        (root / name).write_bytes(data)
    with patch(
        "agents_remember.application.task_docs.task_doc_tools.alignment_sweep", return_value=None
    ):
        without_whole, without_answer = _deliver(config)
    assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == with_sweep
    assert {key: value for key, value in whole.items() if key != "alignmentSweep"} == without_whole
    # Log locations and token counts are derived from the submitted response,
    # rather than authored fields of the original task-document operation.
    response_observations = {"alignmentSweep", "log", "tokens"}
    assert {key: value for key, value in answer.items() if key not in response_observations} == {
        key: value for key, value in without_answer.items() if key not in response_observations
    }
