"""Tests for the JSON-primary task-document layer (slice 3c, commit 1).

Covers the ``ar-task-document/v1`` schema (round-trip, alias, strictness, progress
helpers), the deterministic markdown renderer (the ``w-02-light-task-workflow``
template shape, checkbox mapping, escaping, empty sections), the JSON+markdown
store, the ``task_doc`` controller operations and error paths (including contract
lifecycle-key pickup), and the MCP tool registration.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from pydantic import ValidationError

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controllers.task_doc_tools import TaskDocError, task_doc_tool
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.mcp.tools import task_doc_payload
from agents_remember.mcp.tools.base import PUBLIC_TOOLS
from agents_remember.models.task_doc import TaskDocResponse
from agents_remember.models.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from agents_remember.observer.ambient import reset_ambient
from agents_remember.tasks import (
    TASK_DOCUMENT_SCHEMA,
    TaskDocument,
    current_step,
    doc_stem,
    json_path_for,
    markdown_path_for,
    read_task_doc,
    render_markdown,
    step_done,
    step_total,
    write_task_doc,
)
from agents_remember.worktrees.worktree_contract import default_contract, write_contract


def _doc(**over: Any) -> TaskDocument:
    base: dict[str, Any] = {
        "id": "T1",
        "slug": "task",
        "title": "Hello",
        "kind": "light",
        "repo": "r",
        "type": "Docs",
        "createdAt": "2026-01-01T00:00",
    }
    base.update(over)
    return TaskDocument.model_validate(base)


def _config(coord: Path) -> McpRuntimeConfig:
    """A lightweight stand-in: the task-doc controller only reads coordination_root."""
    return cast(McpRuntimeConfig, SimpleNamespace(coordination_root=coord))


class SchemaTests(unittest.TestCase):
    def test_roundtrip_when_dumped_and_revalidated(self) -> None:
        doc = _doc(
            objective="Do.",
            requirements=["a"],
            steps=[{"id": "S1", "title": "One", "status": "done"}],
            decisions=[{"at": "t", "decision": "d", "rationale": "r"}],
        )
        again = TaskDocument.model_validate(doc.model_dump(by_alias=True))
        self.assertEqual(again, doc)

    def test_schema_field_serializes_under_alias(self) -> None:
        dumped = _doc().model_dump(by_alias=True)
        self.assertEqual(dumped["schema"], TASK_DOCUMENT_SCHEMA)
        self.assertNotIn("schema_", dumped)

    def test_extra_keys_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TaskDocument.model_validate({**_doc().model_dump(by_alias=True), "bogus": 1})

    def test_progress_counts_substeps_when_present_else_step(self) -> None:
        doc = _doc(
            steps=[
                {
                    "id": "S1",
                    "title": "One",
                    "status": "inProgress",
                    "substeps": [
                        {"id": "S1.a", "title": "a", "status": "done"},
                        {"id": "S1.b", "title": "b", "status": "pending"},
                    ],
                },
                {"id": "S2", "title": "Two", "status": "done"},
            ]
        )
        # Two substeps under S1 (1 done) + S2 itself (done) = 3 leaves, 2 done.
        self.assertEqual((step_done(doc), step_total(doc)), (2, 3))

    def test_current_step_prefers_active_then_first_unfinished_then_none(self) -> None:
        active = _doc(steps=[{"id": "S1", "title": "One", "status": "blocked"}])
        self.assertEqual(current_step(active), "S1 — One")
        pending = _doc(
            steps=[
                {"id": "S1", "title": "One", "status": "done"},
                {"id": "S2", "title": "Two", "status": "pending"},
            ]
        )
        self.assertEqual(current_step(pending), "S2 — Two")
        finished = _doc(steps=[{"id": "S1", "title": "One", "status": "done"}])
        self.assertIsNone(current_step(finished))


class RenderTests(unittest.TestCase):
    def test_golden_small_light_doc(self) -> None:
        doc = _doc(
            status="planning",
            objective="Obj.",
            requirements=["one"],
            steps=[{"id": "S1", "title": "Do", "status": "done"}],
            references=["ref"],
        )
        expected = (
            "\n".join(
                [
                    "# Task: Hello",
                    "",
                    "**Status:** planning",
                    "**Repo:** r",
                    "**Type:** Docs",
                    "**Created:** 2026-01-01T00:00",
                    "",
                    "---",
                    "",
                    "## Objective",
                    "",
                    "Obj.",
                    "",
                    "---",
                    "",
                    "## Requirements",
                    "",
                    "- one",
                    "",
                    "---",
                    "",
                    "## Design",
                    "",
                    "No design reasoning needed.",
                    "",
                    "---",
                    "",
                    "## Implementation Steps",
                    "",
                    "### S1 — Do",
                    "",
                    "- [x] Do",
                    "",
                    "---",
                    "",
                    "## Proposed Code Examples",
                    "",
                    "No code examples are needed for this task.",
                    "",
                    "---",
                    "",
                    "## Decision Log",
                    "",
                    "_None recorded._",
                    "",
                    "---",
                    "",
                    "## Open Questions",
                    "",
                    "- None.",
                    "",
                    "---",
                    "",
                    "## References",
                    "",
                    "- ref",
                ]
            )
            + "\n"
        )
        self.assertEqual(render_markdown(doc), expected)

    def test_render_is_deterministic(self) -> None:
        doc = _doc(objective="x", steps=[{"id": "S1", "title": "a", "status": "pending"}])
        self.assertEqual(render_markdown(doc), render_markdown(doc))

    def test_subtask_title_suffix_and_master_line(self) -> None:
        md = render_markdown(_doc(kind="subTask", slug="03c_x", id="3C", master="task.md"))
        self.assertIn("# Task: Hello (Sub-task 3C)", md)
        self.assertIn("**Master:** `task.md`", md)

    def test_checkbox_and_substep_note(self) -> None:
        md = render_markdown(
            _doc(
                steps=[
                    {
                        "id": "S1",
                        "title": "Parent",
                        "status": "inProgress",
                        "substeps": [
                            {"id": "S1.a", "title": "child", "status": "done", "note": "n"}
                        ],
                    }
                ]
            )
        )
        self.assertIn("- [ ] Parent", md)
        self.assertIn("  - [x] child — n", md)

    def test_decision_cell_escapes_pipe_and_newline(self) -> None:
        md = render_markdown(
            _doc(decisions=[{"at": "t", "decision": "a | b\nc", "rationale": "r"}])
        )
        self.assertIn(r"| t | a \| b c | r |", md)

    def test_empty_sections_have_placeholders(self) -> None:
        md = render_markdown(_doc())
        self.assertIn("_No steps defined yet._", md)
        self.assertIn("No code examples are needed for this task.", md)
        self.assertIn("- _None._", md)  # empty requirements/references

    def test_code_example_fence_preserves_blank_lines(self) -> None:
        md = render_markdown(
            _doc(
                codeExamples=[
                    {
                        "id": "E1",
                        "title": "Ex",
                        "distinctChange": "c",
                        "why": "w",
                        "language": "python",
                        "snippet": "a = 1\n\nb = 2",
                    }
                ]
            )
        )
        self.assertIn("```python\na = 1\n\nb = 2\n```", md)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_doc_stem_light_vs_subtask(self) -> None:
        self.assertEqual(doc_stem(_doc(kind="light")), "task")
        self.assertEqual(doc_stem(_doc(kind="subTask", slug="03c_x")), "03c_x")

    def test_write_then_read_roundtrips_and_leaves_no_tmp(self) -> None:
        doc = _doc(objective="o", steps=[{"id": "S1", "title": "a", "status": "done"}])
        json_path, md_path = write_task_doc(self.root, doc)
        self.assertEqual(json_path, json_path_for(self.root, doc))
        self.assertEqual(md_path, markdown_path_for(self.root, doc))
        self.assertTrue(json_path.exists() and md_path.exists())
        self.assertEqual(read_task_doc(json_path), doc)
        self.assertEqual(md_path.read_text(encoding="utf-8"), render_markdown(doc))
        self.assertEqual(list(self.root.glob("*.tmp")), [])


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coord = Path(tempfile.mkdtemp())
        self.cfg = _config(self.coord)

    def _create(self, **fields: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": "3C",
            "slug": "03c_x",
            "title": "Smoke",
            "kind": "subTask",
            "repo": "agents-remember",
            "type": "Code",
            "createdAt": "2026-01-01T00:00",
        }
        payload.update(fields)
        return task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="create",
            task_name="3c-x",
            fields=payload,
        )

    def _call(
        self,
        operation: str,
        *,
        fields: dict[str, Any] | None = None,
        step: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation=operation,
            task_name="3c-x",
            slug="03c_x",
            fields=fields,
            step=step,
            decision=decision,
        )

    def test_create_writes_both_files(self) -> None:
        result = self._create(steps=[{"id": "S1", "title": "One", "status": "inProgress"}])
        self.assertEqual(result["operation"], "task_doc.create")
        self.assertEqual((result["stepsDone"], result["stepsTotal"]), (0, 1))
        self.assertTrue(Path(str(result["docPath"])).exists())
        self.assertTrue(Path(str(result["renderedPath"])).exists())

    def test_create_rejects_duplicate(self) -> None:
        self._create()
        with self.assertRaises(TaskDocError):
            self._create()

    def test_set_status_and_set_field(self) -> None:
        self._create()
        status_result = self._call("set_status", fields={"status": "inProgress"})
        self.assertEqual(status_result["status"], "inProgress")
        updated = self._call("set_field", fields={"objective": "new", "bogus": "x"})
        self.assertEqual(updated["operation"], "task_doc.set_field")
        self.assertEqual(read_task_doc(Path(str(updated["docPath"]))).objective, "new")

    def test_set_step_inserts_then_updates_without_duplicating(self) -> None:
        self._create(steps=[{"id": "S1", "title": "One", "status": "pending"}])
        self._call(
            "set_step",
            step={"id": "S1.a", "title": "sub", "status": "pending", "parent": "S1"},
        )
        result = self._call(
            "set_step",
            step={"id": "S1.a", "title": "sub", "status": "done", "parent": "S1"},
        )
        doc = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual(len(doc.steps[0].substeps), 1)
        self.assertEqual(doc.steps[0].substeps[0].status, "done")

    def test_append_decision_accumulates(self) -> None:
        self._create()
        self._call("append_decision", decision={"at": "t1", "decision": "d1", "rationale": "r"})
        result = self._call(
            "append_decision", decision={"at": "t2", "decision": "d2", "rationale": "r"}
        )
        self.assertEqual(len(read_task_doc(Path(str(result["docPath"]))).decisions), 2)

    def test_get_does_not_mutate(self) -> None:
        self._create()
        before = Path(str(self._call("get")["docPath"])).read_text(encoding="utf-8")
        after = Path(str(self._call("get")["docPath"])).read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_create_picks_up_contract_lifecycle_id(self) -> None:
        contract = default_contract(
            task_name="3c-x",
            repo_name="agents-remember",
            workflow_kind="chat-task",
            memory_mode="disabled",
            coordination_root=self.coord,
            code_repo_path=self.coord,
            code_source_branch="main",
            code_work_branch="wb",
            code_base_commit="abc123",
            worktree_name="3c-x",
            lifecycle_id="LC-CONTRACT",
        )
        write_contract(contract.contract_path, contract)
        result = self._create()  # no lifecycleId in fields
        self.assertEqual(result["lifecycleId"], "LC-CONTRACT")

    def test_resolve_by_contract_path(self) -> None:
        created = self._create()
        task_root = Path(str(created["docPath"])).parent
        result = task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="get",
            contract_path=str(task_root / "contract.md"),
            slug="03c_x",
        )
        self.assertEqual(result["taskId"], "3C")

    def test_error_paths(self) -> None:
        with self.assertRaises(TaskDocError):
            task_doc_tool(self.cfg, repo_id="agents-remember", operation="frob", task_name="x")
        with self.assertRaises(TaskDocError):
            task_doc_tool(self.cfg, repo_id="agents-remember", operation="get")
        with self.assertRaises(TaskDocError):
            self._call("get")  # doc not created yet
        self._create()
        with self.assertRaises(TaskDocError):
            self._call("set_status", fields={})
        with self.assertRaises(TaskDocError):
            self._call("set_field", fields={"unknown": "x"})
        with self.assertRaises(TaskDocError):
            self._call("set_step", step={"title": "no id"})
        with self.assertRaises(TaskDocError):
            self._call("set_step", step={"id": "S9.a", "title": "x", "parent": "ghost"})


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_ambient()
        self.coord = Path(tempfile.mkdtemp())
        self.cfg = _config(self.coord)

    def test_task_doc_registered(self) -> None:
        self.assertIn("task_doc", PUBLIC_TOOLS)
        self.assertIs(PUBLIC_TOOL_RESPONSE_MODELS["task_doc"], TaskDocResponse)

    def test_payload_builder_returns_valid_token_stamped_response(self) -> None:
        payload = task_doc_payload(
            self.cfg,
            repo_id="agents-remember",
            operation="create",
            task_name="3c-reg",
            fields={
                "id": "R1",
                "slug": "task",
                "title": "Reg",
                "kind": "light",
                "repo": "agents-remember",
                "createdAt": "2026-01-01T00:00",
            },
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["operation"], "task_doc.create")
        self.assertIn("tokens", payload)
        # The emitted payload validates against the registered response model.
        TaskDocResponse.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
