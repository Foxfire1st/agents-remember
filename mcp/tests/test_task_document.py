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


def _master(**over: Any) -> TaskDocument:
    base: dict[str, Any] = {
        "id": "series",
        "slug": "series",
        "title": "Series",
        "kind": "master",
        "repo": "agents-remember",
        "type": "Master (Code)",
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

    def test_master_roundtrips_with_subtasks_and_sections(self) -> None:
        doc = _master(
            subTasks=[
                {
                    "number": "3c",
                    "name": "Persist",
                    "file": "03c.md",
                    "status": "inProgress",
                    "scope": "x",
                }
            ],
            sections=[{"kind": "freeform", "heading": "H", "body": "b"}],
        )
        again = TaskDocument.model_validate(doc.model_dump(by_alias=True))
        self.assertEqual(again, doc)

    def test_master_forbids_steps_and_lifecycle_id(self) -> None:
        with self.assertRaises(ValidationError):
            _master(steps=[{"id": "S1", "title": "x"}])
        with self.assertRaises(ValidationError):
            _master(lifecycleId="LC")

    def test_leaf_forbids_subtasks_and_non_freeform_sections(self) -> None:
        # the master series index stays master-only
        with self.assertRaises(ValidationError):
            _doc(subTasks=[{"number": "1", "name": "x"}])
        # a non-freeform section is master-only too
        with self.assertRaises(ValidationError):
            _doc(sections=[{"heading": "H", "kind": "subTasks"}])
        # but a freeform section is a legal leaf extension (R4)
        leaf = _doc(sections=[{"heading": "Status history"}])
        self.assertEqual(leaf.sections[0].heading, "Status history")

    def test_r4_extension_fields_round_trip(self) -> None:
        doc = _doc(
            statusNote="desc",
            headerNotes=[{"label": "Verified", "value": "v"}],
            sections=[{"heading": "H", "body": "b"}],
        )
        self.assertEqual(TaskDocument.model_validate(doc.model_dump(by_alias=True)), doc)
        # statusNote is None-omitted when unset (like other optional scalars)
        self.assertNotIn("statusNote", _doc().model_dump_json(by_alias=True, exclude_none=True))

    def test_master_forbids_code_examples_note(self) -> None:
        with self.assertRaises(ValidationError):
            _master(codeExamplesNote="x")

    def test_code_examples_note_requires_empty_examples(self) -> None:
        # The note explains an absence; it cannot coexist with drafted examples.
        with self.assertRaises(ValidationError):
            _doc(
                codeExamplesNote="Drafted at the plan gate.",
                codeExamples=[{"id": "E1", "title": "t", "distinctChange": "c", "why": "w"}],
            )

    def test_code_examples_note_roundtrips_and_omits_when_none(self) -> None:
        doc = _doc(codeExamplesNote="Drafted at the plan gate.")
        self.assertEqual(TaskDocument.model_validate(doc.model_dump(by_alias=True)), doc)
        # exclude_none keeps existing note-less JSON byte-identical (no codeExamplesNote key).
        self.assertNotIn(
            "codeExamplesNote", _doc().model_dump_json(by_alias=True, exclude_none=True)
        )


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

    def test_step_outcome_on_checkbox_and_bare_step_has_no_echo(self) -> None:
        md = render_markdown(
            _doc(
                steps=[
                    {"id": "S1", "title": "schema", "outcome": "ar-task-document/v1 lands"},
                    {"id": "S2", "title": "bare", "status": "done"},
                ]
            )
        )
        # the checkbox carries the distinct outcome, not the heading title
        self.assertIn("### S1 — schema", md)
        self.assertIn("- [ ] ar-task-document/v1 lands", md)
        # a bare step (no outcome, no substeps) is just its heading -- no redundant title echo
        self.assertIn("### S2 — bare", md)
        self.assertNotIn("] bare", md)

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

    def test_code_examples_note_renders_when_examples_empty(self) -> None:
        md = render_markdown(_doc(codeExamplesNote="Drafted at the plan gate."))
        self.assertIn("Drafted at the plan gate.", md)
        self.assertNotIn("No code examples are needed for this task.", md)

    def test_status_note_and_header_notes_render(self) -> None:
        md = render_markdown(
            _doc(
                statusNote="core JSON format landed",
                headerNotes=[{"label": "Verified", "value": "2026-06-18 — 3 commits"}],
            )
        )
        self.assertIn("**Status:** planning — core JSON format landed", md)
        self.assertIn("**Verified:** 2026-06-18 — 3 commits", md)

    def test_leaf_freeform_sections_render_after_references(self) -> None:
        md = render_markdown(
            _doc(
                references=["ref"],
                sections=[{"heading": "Status history", "body": "line a\nline b"}],
            )
        )
        self.assertIn("## Status history", md)
        self.assertIn("line a\nline b", md)
        # the freeform extra comes after the standard References section
        self.assertLess(md.index("## References"), md.index("## Status history"))

    def test_real_subtask_extensions_round_trip_content_complete(self) -> None:
        # Models this 03c sub-task's extensions (R4 acceptance): a descriptive status, extra
        # header lines, and bespoke freeform sections beyond the bare template.
        doc = _doc(
            kind="subTask",
            id="3C",
            slug="03c_x",
            master="task.md",
            status="inProgress",
            statusNote="core JSON format landed",
            headerNotes=[
                {"label": "Verified", "value": "2026-06-18 — 3 commits landed"},
                {"label": "Reopened", "value": "2026-06-19 — pilot surfaced gaps"},
            ],
            objective="Make the task document JSON-primary.",
            sections=[
                {"heading": "Reopened", "body": "gaps the pilot surfaced"},
                {"heading": "Status history", "body": "verbatim, pre-normalization"},
            ],
        )
        md = render_markdown(doc)
        self.assertIn("**Status:** inProgress — core JSON format landed", md)
        self.assertIn("**Verified:** 2026-06-18 — 3 commits landed", md)
        self.assertIn("**Reopened:** 2026-06-19 — pilot surfaced gaps", md)
        self.assertIn("## Reopened", md)
        self.assertIn("## Status history", md)
        # the JSON round-trips losslessly
        self.assertEqual(TaskDocument.model_validate(doc.model_dump(by_alias=True)), doc)


class MasterRenderTests(unittest.TestCase):
    def test_golden_master(self) -> None:
        doc = _master(
            title="Series X",
            type="Master (Code / Docs)",
            status="inProgress",
            createdAt="2026-06-12T15:58",
            subTasks=[
                {
                    "number": "1",
                    "name": "Design",
                    "file": "01_d.md",
                    "status": "Completed",
                    "scope": "keystone",
                },
                {"number": "3c", "name": "Persist", "file": "03c_p.md", "status": "inProgress"},
                {"number": "4", "name": "Serve", "status": "planning"},
            ],
            decisions=[{"at": "2026-06-12T15:58", "decision": "8 slices", "rationale": "fits"}],
            sections=[
                {"kind": "freeform", "heading": "Objective", "body": "Ship 3.0.0."},
                {"kind": "subTasks", "heading": "Sub-tasks (execution order)", "body": "> note"},
                {"kind": "sharedDecisions", "heading": "Shared Decisions"},
                {"kind": "freeform", "heading": "Invariants", "body": "- never weaker"},
            ],
        )
        expected = (
            "\n".join(
                [
                    "# Task: Series X",
                    "",
                    "**Status:** inProgress",
                    "**Repo:** agents-remember",
                    "**Type:** Master (Code / Docs)",
                    "**Created:** 2026-06-12T15:58",
                    "",
                    "---",
                    "",
                    "## Objective",
                    "",
                    "Ship 3.0.0.",
                    "",
                    "---",
                    "",
                    "## Sub-tasks (execution order)",
                    "",
                    "> note",
                    "",
                    "1. ✅ **Design** · `01_d.md` — keystone",
                    "3c. 🔨 **Persist** · `03c_p.md`",
                    "4. ⬜ **Serve**",
                    "",
                    "---",
                    "",
                    "## Shared Decisions",
                    "",
                    "| Date-Time | Decision | Rationale |",
                    "| --- | --- | --- |",
                    "| 2026-06-12T15:58 | 8 slices | fits |",
                    "",
                    "---",
                    "",
                    "## Invariants",
                    "",
                    "- never weaker",
                ]
            )
            + "\n"
        )
        self.assertEqual(render_markdown(doc), expected)

    def test_master_render_is_deterministic(self) -> None:
        doc = _master(
            subTasks=[{"number": "1", "name": "a", "status": "planning"}],
            sections=[{"kind": "subTasks", "heading": "Sub-tasks"}],
        )
        self.assertEqual(render_markdown(doc), render_markdown(doc))

    def test_master_markers_map_status(self) -> None:
        doc = _master(
            subTasks=[
                {"number": "1", "name": "a", "status": "Completed"},
                {"number": "2", "name": "b", "status": "inProgress"},
                {"number": "3", "name": "c", "status": "planning"},
            ],
            sections=[{"kind": "subTasks", "heading": "S"}],
        )
        md = render_markdown(doc)
        self.assertIn("1. ✅ **a**", md)
        self.assertIn("2. 🔨 **b**", md)
        self.assertIn("3. ⬜ **c**", md)

    def test_master_empty_subtasks_placeholder(self) -> None:
        doc = _master(sections=[{"kind": "subTasks", "heading": "Sub-tasks"}])
        self.assertIn("_No sub-tasks defined yet._", render_markdown(doc))

    def test_master_preserves_bespoke_prose_verbatim(self) -> None:
        # Bespoke prose sections (Resume / North-Star / Mandated ...) survive byte-for-byte,
        # including internal blank lines and nested bullets -- the S4 acceptance.
        resume = "**Where we are:** slices 01-03 done.\n\n- code @ abc\n  - nested\n- memory @ def"
        north_star = "1. Design test.\n2. Client-agnostic API."
        doc = _master(
            sections=[
                {"kind": "freeform", "heading": "Resume / Current State", "body": resume},
                {"kind": "freeform", "heading": "North-Star Constraints", "body": north_star},
            ],
        )
        md = render_markdown(doc)
        self.assertIn(f"## Resume / Current State\n\n{resume}\n", md)
        self.assertIn(f"## North-Star Constraints\n\n{north_star}\n", md)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_doc_stem_light_vs_subtask(self) -> None:
        self.assertEqual(doc_stem(_doc(kind="light")), "task")
        self.assertEqual(doc_stem(_doc(kind="subTask", slug="03c_x")), "03c_x")

    def test_doc_stem_master_is_task(self) -> None:
        self.assertEqual(doc_stem(_master(slug="series")), "task")

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

    def _create_parent_master(self, **fields: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": "series",
            "slug": "series",
            "title": "Series",
            "kind": "master",
            "repo": "agents-remember",
            "type": "Master (Code)",
            "createdAt": "2026-01-01T00:00",
            "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
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
        dry_run: bool = False,
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
            dry_run=dry_run,
        )

    def test_create_writes_both_files(self) -> None:
        result = self._create(steps=[{"id": "S1", "title": "One", "status": "inProgress"}])
        self.assertEqual(result["operation"], "task_doc.create")
        self.assertEqual((result["stepsDone"], result["stepsTotal"]), (0, 1))
        self.assertTrue(Path(str(result["docPath"])).exists())
        self.assertTrue(Path(str(result["renderedPath"])).exists())
        self.assertNotIn("masterSync", result)

    def test_leaf_create_syncs_parent_master_row(self) -> None:
        self._create_parent_master()
        result = self._create(master="task.md")

        sync = result["masterSync"]
        self.assertEqual(sync["status"], "created")
        master = read_task_doc(Path(str(sync["masterDocPath"])))
        self.assertEqual(len(master.subTasks), 1)
        [row] = master.subTasks
        self.assertEqual(row.number, "3C")
        self.assertEqual(row.name, "Smoke")
        self.assertEqual(row.file, "03c_x.md")
        self.assertEqual(row.status, "planning")
        self.assertEqual(row.scope, "")

    def test_leaf_updates_preserve_manual_master_scope(self) -> None:
        self._create_parent_master()
        self._create(master="task.md")
        task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="set_subtask",
            task_name="3c-x",
            subtask={"number": "3C", "scope": "keep this prose"},
        )

        result = self._call("set_field", fields={"title": "Renamed", "status": "inProgress"})

        self.assertEqual(result["masterSync"]["status"], "updated")
        master = read_task_doc(Path(str(result["masterSync"]["masterDocPath"])))
        [row] = master.subTasks
        self.assertEqual(row.name, "Renamed")
        self.assertEqual(row.status, "inProgress")
        self.assertEqual(row.scope, "keep this prose")

    def test_leaf_step_progress_derives_master_row_status(self) -> None:
        self._create_parent_master()
        self._create(
            master="task.md",
            steps=[{"id": "S1", "title": "One", "status": "pending"}],
        )

        blocked = self._call("set_step", step={"id": "S1", "title": "One", "status": "blocked"})
        master = read_task_doc(Path(str(blocked["masterSync"]["masterDocPath"])))
        self.assertEqual(master.subTasks[0].status, "inProgress")

        done = self._call("set_step", step={"id": "S1", "title": "One", "status": "done"})
        master = read_task_doc(Path(str(done["masterSync"]["masterDocPath"])))
        self.assertEqual(master.subTasks[0].status, "Completed")

    def test_leaf_dry_run_includes_master_sync_preview_without_writing(self) -> None:
        self._create_parent_master()
        self._create(master="task.md")
        master_path = self.coord / "tasks" / "agents-remember" / "3c-x" / "task.json"
        before = master_path.read_text(encoding="utf-8")

        result = self._call("set_field", fields={"title": "Preview Rename"}, dry_run=True)

        sync = result["masterSync"]
        self.assertEqual(sync["status"], "would-update")
        self.assertIn("Preview Rename", sync["rendered"])
        self.assertIn("Preview Rename", sync["diff"])
        self.assertIsInstance(sync["wouldLose"], bool)
        self.assertEqual(master_path.read_text(encoding="utf-8"), before)

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

    def test_set_field_code_examples_note(self) -> None:
        self._create()
        updated = self._call("set_field", fields={"codeExamplesNote": "Drafted at the plan gate."})
        doc = read_task_doc(Path(str(updated["docPath"])))
        self.assertEqual(doc.codeExamplesNote, "Drafted at the plan gate.")

    def test_set_field_status_note(self) -> None:
        self._create()
        updated = self._call("set_field", fields={"statusNote": "core JSON format landed"})
        doc = read_task_doc(Path(str(updated["docPath"])))
        self.assertEqual(doc.statusNote, "core JSON format landed")

    def test_dry_run_create_renders_without_writing(self) -> None:
        result = task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="create",
            task_name="3c-x",
            fields={
                "id": "3C",
                "slug": "03c_x",
                "title": "Smoke",
                "kind": "subTask",
                "repo": "agents-remember",
                "type": "Code",
                "createdAt": "2026-01-01T00:00",
                "objective": "Preview me.",
            },
            dry_run=True,
        )
        self.assertTrue(result["dryRun"])
        self.assertIn("Preview me.", str(result["rendered"]))
        # nothing written: neither the json source nor the rendered md exists
        self.assertFalse(Path(str(result["docPath"])).exists())
        self.assertFalse(Path(str(result["renderedPath"])).exists())

    def test_dry_run_does_not_mutate_existing_files(self) -> None:
        created = self._create(objective="orig")
        json_path = Path(str(created["docPath"]))
        md_path = Path(str(created["renderedPath"]))
        before_json = json_path.read_text(encoding="utf-8")
        before_md = md_path.read_text(encoding="utf-8")
        result = task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="set_field",
            task_name="3c-x",
            slug="03c_x",
            fields={"objective": "changed"},
            dry_run=True,
        )
        self.assertIn("changed", str(result["rendered"]))  # the would-be render reflects the edit
        # …but disk is untouched
        self.assertEqual(json_path.read_text(encoding="utf-8"), before_json)
        self.assertEqual(md_path.read_text(encoding="utf-8"), before_md)

    def test_dry_run_would_lose_flags_unmodeled_md_content(self) -> None:
        created = self._create(objective="orig")
        md_path = Path(str(created["renderedPath"]))
        # a clean re-preview (no real change) matches disk exactly: no loss, empty diff
        clean = task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="set_field",
            task_name="3c-x",
            slug="03c_x",
            fields={"objective": "orig"},
            dry_run=True,
        )
        self.assertFalse(clean["wouldLose"])
        self.assertEqual(clean["diff"], "")
        # a hand-authored line the JSON does not model → wouldLose true + the diff shows it dropped
        md_path.write_text(
            md_path.read_text(encoding="utf-8") + "\n## Bespoke hand note\nkeep me\n",
            encoding="utf-8",
        )
        lossy = task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="set_field",
            task_name="3c-x",
            slug="03c_x",
            fields={"objective": "orig"},
            dry_run=True,
        )
        self.assertTrue(lossy["wouldLose"])
        self.assertIn("keep me", str(lossy["diff"]))

    def test_replace_rewrites_structural_fields_and_decisions(self) -> None:
        created = self._create(
            objective="old",
            steps=[{"id": "S1", "title": "Old step", "status": "done"}],
            codeExamples=[
                {
                    "id": "E1",
                    "title": "Old example",
                    "distinctChange": "old",
                    "why": "old",
                }
            ],
            decisions=[{"at": "t1", "decision": "old", "rationale": "old"}],
        )
        json_path = Path(str(created["docPath"]))
        result = self._call(
            "replace",
            fields={
                "id": "3C",
                "slug": "03c_x",
                "title": "Smoke reset",
                "kind": "subTask",
                "repo": "agents-remember",
                "type": "Code",
                "createdAt": "2026-01-01T00:00",
                "objective": "new",
                "steps": [{"id": "S2", "title": "New step", "status": "pending"}],
                "codeExamples": [
                    {
                        "id": "E2",
                        "title": "New example",
                        "distinctChange": "new",
                        "why": "new",
                    }
                ],
                "decisions": [],
            },
        )
        self.assertEqual(result["operation"], "task_doc.replace")
        doc = read_task_doc(json_path)
        self.assertEqual(doc.title, "Smoke reset")
        self.assertEqual([step.id for step in doc.steps], ["S2"])
        self.assertEqual([example.id for example in doc.codeExamples], ["E2"])
        self.assertEqual(doc.decisions, [])

    def test_replace_dry_run_does_not_mutate_existing_files(self) -> None:
        created = self._create(objective="old")
        json_path = Path(str(created["docPath"]))
        md_path = Path(str(created["renderedPath"]))
        before_json = json_path.read_text(encoding="utf-8")
        before_md = md_path.read_text(encoding="utf-8")
        result = self._call(
            "replace",
            fields={
                "id": "3C",
                "slug": "03c_x",
                "title": "Smoke",
                "kind": "subTask",
                "repo": "agents-remember",
                "type": "Code",
                "createdAt": "2026-01-01T00:00",
                "objective": "replacement preview",
            },
            dry_run=True,
        )
        self.assertTrue(result["dryRun"])
        self.assertIn("replacement preview", str(result["rendered"]))
        self.assertEqual(json_path.read_text(encoding="utf-8"), before_json)
        self.assertEqual(md_path.read_text(encoding="utf-8"), before_md)

    def test_replace_rejects_document_path_change(self) -> None:
        self._create()
        with self.assertRaises(TaskDocError):
            self._call(
                "replace",
                fields={
                    "id": "3C",
                    "slug": "different",
                    "title": "Moved",
                    "kind": "subTask",
                    "repo": "agents-remember",
                    "type": "Code",
                    "createdAt": "2026-01-01T00:00",
                },
            )

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
            contract_path=str(task_root / "series-contract.md"),
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


class MasterControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coord = Path(tempfile.mkdtemp())
        self.cfg = _config(self.coord)

    def _create(self, **fields: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": "series",
            "slug": "series",
            "title": "Series",
            "kind": "master",
            "repo": "agents-remember",
            "type": "Master (Code)",
            "createdAt": "2026-01-01T00:00",
            "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
        }
        payload.update(fields)
        return task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="create",
            task_name="series",
            fields=payload,
        )

    def _op(self, operation: str, **kw: Any) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation=operation,
            task_name="series",
            **kw,
        )

    def test_create_master_writes_task_json_without_lifecycle(self) -> None:
        result = self._create()
        self.assertEqual(result["kind"], "master")
        self.assertTrue(str(result["docPath"]).endswith("task.json"))
        self.assertIsNone(result["lifecycleId"])

    def test_set_subtask_inserts_then_updates_by_number(self) -> None:
        self._create(subTasks=[{"number": "1", "name": "A", "status": "planning"}])
        self._op("set_subtask", subtask={"number": "3c", "name": "B", "status": "inProgress"})
        result = self._op(
            "set_subtask", subtask={"number": "1", "status": "Completed", "scope": "done"}
        )
        doc = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual(
            [(s.number, s.status) for s in doc.subTasks],
            [("1", "Completed"), ("3c", "inProgress")],
        )
        self.assertEqual(doc.subTasks[0].scope, "done")

    def test_set_section_upserts_by_heading(self) -> None:
        self._create()
        self._op("set_section", section={"heading": "Invariants", "body": "- x"})
        result = self._op("set_section", section={"heading": "Invariants", "body": "- y"})
        doc = read_task_doc(Path(str(result["docPath"])))
        invariants = [s for s in doc.sections if s.heading == "Invariants"]
        self.assertEqual(len(invariants), 1)
        self.assertEqual(invariants[0].body, "- y")

    def test_master_create_ignores_contract_lifecycle_id(self) -> None:
        contract = default_contract(
            task_name="series",
            repo_name="agents-remember",
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=self.coord,
            code_repo_path=self.coord,
            code_source_branch="main",
            code_work_branch="wb",
            code_base_commit="abc123",
            worktree_name="series",
            lifecycle_id="LC-X",
        )
        write_contract(contract.contract_path, contract)
        self.assertIsNone(self._create()["lifecycleId"])

    def test_master_rejects_step_op(self) -> None:
        self._create()
        with self.assertRaises(TaskDocError):
            self._op("set_step", step={"id": "S1", "title": "x"})

    def test_subtask_op_rejects_non_master_but_section_allows_freeform(self) -> None:
        task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="create",
            task_name="lite",
            fields={
                "id": "L",
                "slug": "task",
                "title": "L",
                "kind": "light",
                "repo": "r",
                "createdAt": "2026-01-01T00:00",
            },
        )
        # set_subtask stays master-only (the series index has no meaning on a leaf)
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                repo_id="agents-remember",
                operation="set_subtask",
                task_name="lite",
                subtask={"number": "1", "name": "x"},
            )
        # set_section on a leaf adds a freeform extra section (R4)
        result = task_doc_tool(
            self.cfg,
            repo_id="agents-remember",
            operation="set_section",
            task_name="lite",
            section={"heading": "Status history", "body": "old."},
        )
        doc = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual([s.heading for s in doc.sections], ["Status history"])
        # a non-freeform section on a leaf is rejected (the validator backstop)
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                repo_id="agents-remember",
                operation="set_section",
                task_name="lite",
                section={"heading": "X", "kind": "subTasks"},
            )

    def test_master_op_argument_errors(self) -> None:
        self._create()
        with self.assertRaises(TaskDocError):
            self._op("set_subtask", subtask={"name": "no number"})
        with self.assertRaises(TaskDocError):
            self._op("set_section", section={"body": "no heading"})


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
