"""Tests for the JSON-primary task-document layer (slice 3c, commit 1).

Covers the ``ar-task-document/v1`` schema (round-trip, alias, strictness, progress
helpers), the deterministic markdown renderer (the ``w-02-light-task-workflow``
template shape, checkbox mapping, escaping, empty sections), the JSON+markdown
store, the ``task_doc`` application operations and error paths (including contract
lifecycle-key pickup), and the MCP tool registration.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from pydantic import ValidationError

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.tasks.store as task_store
from agents_remember.application.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.mcp.tools import task_doc_payload
from agents_remember.mcp.tools.base import PUBLIC_TOOLS
from agents_remember.models.task_doc import TaskDocResponse
from agents_remember.models.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from agents_remember.observer.ambient import reset_ambient
from agents_remember.tasks import (
    TASK_DOCUMENT_SCHEMA,
    TaskDocument,
    completion_blockers,
    current_step,
    doc_stem,
    json_path_for,
    markdown_path_for,
    read_task_doc,
    render_markdown,
    step_done,
    step_total,
    write_task_doc,
    write_task_docs,
)
from agents_remember.tasks.leaf_doc import (
    TerminalLeafResolutionError,
    resolve_terminal_leaf_doc,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)


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
    """A lightweight stand-in: the task-doc entry point only reads coordination_root."""
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

    def test_progress_counts_every_declared_parent_and_child(self) -> None:
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
        # Parent S1 remains visible beside its two children, plus S2: 4 units, 2 done.
        self.assertEqual((step_done(doc), step_total(doc)), (2, 4))
        self.assertEqual(
            [(item.id, item.parentId, item.status) for item in completion_blockers(doc)],
            [("S1", None, "inProgress"), ("S1.b", "S1", "pending")],
        )

    def test_current_step_includes_active_and_pending_nested_units(self) -> None:
        active = _doc(
            steps=[
                {
                    "id": "S1",
                    "title": "Parent",
                    "status": "done",
                    "substeps": [{"id": "C1", "title": "Child", "status": "blocked"}],
                }
            ]
        )
        self.assertEqual(current_step(active), "S1/C1 — Child")
        pending = active.model_copy(
            update={
                "steps": [
                    active.steps[0].model_copy(
                        update={
                            "substeps": [
                                active.steps[0].substeps[0].model_copy(update={"status": "pending"})
                            ]
                        }
                    )
                ]
            }
        )
        self.assertEqual(current_step(pending), "S1/C1 — Child")

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

    def test_orchestrates_round_trips_on_master_and_is_master_only(self) -> None:
        # L14: an orchestration task is a master doc carrying `orchestrates` — additive, no new kind.
        master = _master(orchestrates=["260706_management-repo", "260707_settings-page"])
        again = TaskDocument.model_validate(master.model_dump(by_alias=True))
        self.assertEqual(again, master)
        self.assertEqual(again.orchestrates, ["260706_management-repo", "260707_settings-page"])
        # A leaf/light doc never commands masters.
        with self.assertRaises(ValidationError):
            _doc(orchestrates=["260706_management-repo"])
        # Docs without the field are untouched: it defaults to [] and validates as before.
        self.assertEqual(_master().orchestrates, [])


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

    def test_intentional_skip_is_distinct_in_parent_and_child_markdown(self) -> None:
        disposition = {
            "kind": "intentionalSkip",
            "reason": "Superseded by the accepted path.",
            "recordedAt": "2026-08-03T12:00:00+00:00",
            "recordedVia": "task_doc.skip_step",
        }
        md = render_markdown(
            _doc(
                steps=[
                    {
                        "id": "S1",
                        "title": "Skipped parent",
                        "status": "done",
                        "disposition": disposition,
                        "substeps": [
                            {
                                "id": "C1",
                                "title": "Skipped child",
                                "status": "done",
                                "disposition": disposition,
                            }
                        ],
                    },
                    {"id": "S2", "title": "Ordinary done", "status": "done"},
                ]
            )
        )
        self.assertIn("- [x] Skipped parent — SKIPPED: Superseded by the accepted path.", md)
        self.assertIn("  - [x] Skipped child — SKIPPED: Superseded by the accepted path.", md)
        ordinary = md.split("### S2 — Ordinary done", maxsplit=1)[1]
        self.assertNotIn("SKIPPED", ordinary)

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

    def test_master_orchestrates_header_line(self) -> None:
        # L14: the orchestration-command relation renders as a header line; absent field → no line.
        doc = _master(
            orchestrates=["260706_management-repo", "260707_settings-page"],
            sections=[{"kind": "subTasks", "heading": "Sub-tasks"}],
        )
        self.assertIn(
            "**Orchestrates:** `260706_management-repo`, `260707_settings-page`",
            render_markdown(doc),
        )
        self.assertNotIn(
            "**Orchestrates:**",
            render_markdown(_master(sections=[{"kind": "subTasks", "heading": "Sub-tasks"}])),
        )

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

    def test_batch_failure_removes_new_files_published_before_later_document(self) -> None:
        docs = [
            _doc(id="L1", slug="01_first", kind="subTask"),
            _doc(id="L2", slug="02_second", kind="subTask"),
        ]
        real_atomic_write = task_store.atomic_write_text
        call_count = 0

        def fail_on_second_document(path: Path, text: str) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise OSError("injected second-document failure")
            real_atomic_write(path, text)

        with (
            patch.object(task_store, "atomic_write_text", side_effect=fail_on_second_document),
            self.assertRaisesRegex(OSError, "injected second-document failure"),
        ):
            write_task_docs(self.root, docs)

        self.assertEqual(list(self.root.iterdir()), [])


class ApplicationTests(unittest.TestCase):
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
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x"),
            operation="create",
            edit=TaskDocEdit(fields=payload),
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
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x"),
            operation="create",
            edit=TaskDocEdit(fields=payload),
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
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation=operation,
            edit=TaskDocEdit(fields=fields, step=step, decision=decision),
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
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x"),
            operation="set_subtask",
            edit=TaskDocEdit(subtask={"number": "3C", "scope": "keep this prose"}),
        )

        result = self._call("set_field", fields={"title": "Renamed", "status": "inProgress"})

        self.assertEqual(result["masterSync"]["status"], "updated")
        master = read_task_doc(Path(str(result["masterSync"]["masterDocPath"])))
        [row] = master.subTasks
        self.assertEqual(row.name, "Renamed")
        self.assertEqual(row.status, "inProgress")
        self.assertEqual(row.scope, "keep this prose")

    def test_leaf_sync_populates_a_blank_legacy_parent_file(self) -> None:
        self._create_parent_master(
            subTasks=[
                {
                    "number": "3C",
                    "name": "Legacy row",
                    "file": "",
                    "status": "planning",
                    "scope": "preserved scope",
                }
            ]
        )

        result = self._create(master="task.md")

        self.assertEqual(result["masterSync"]["status"], "updated")
        master = read_task_doc(Path(str(result["masterSync"]["masterDocPath"])))
        self.assertEqual(master.subTasks[0].file, "03c_x.md")
        self.assertEqual(master.subTasks[0].scope, "preserved scope")

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

    def test_done_child_cannot_hide_pending_parent_from_progress_or_master_sync(self) -> None:
        self._create_parent_master()
        created = self._create(
            master="task.md",
            steps=[
                {
                    "id": "S1",
                    "title": "Parent",
                    "status": "pending",
                    "substeps": [{"id": "C1", "title": "Child", "status": "done"}],
                }
            ],
        )
        self.assertEqual((created["stepsDone"], created["stepsTotal"]), (1, 2))
        master = read_task_doc(Path(str(created["masterSync"]["masterDocPath"])))
        self.assertEqual(master.subTasks[0].status, "inProgress")

    def test_an_unreadable_parent_master_refuses_the_leaf_edit_rather_than_dropping_the_row(
        self,
    ) -> None:
        """A leaf that names a master owes it a row on every edit.

        If the master cannot be read the row cannot be computed, and writing the leaf anyway
        would leave the series silently describing the previous title forever. So the whole
        edit is refused, naming the file to repair -- and the leaf on disk is exactly what it
        was before the call.
        """

        self._create_parent_master()
        self._create(master="task.md")
        task_root = self.coord / "tasks" / "agents-remember" / "3c-x"
        master_path = task_root / "task.json"
        leaf_path = task_root / "03c_x.json"
        leaf_before = leaf_path.read_text(encoding="utf-8")
        master_path.write_text('{"schema": "ar-task-document/v1",', encoding="utf-8")

        with self.assertRaises(TaskDocError) as raised:
            self._call("set_field", fields={"title": "Renamed"})

        self.assertIn("cannot read parent master task document", str(raised.exception))
        self.assertIn("task.json", str(raised.exception))
        self.assertEqual(leaf_path.read_text(encoding="utf-8"), leaf_before)
        self.assertEqual(read_task_doc(leaf_path).title, "Smoke")

    def test_a_master_ref_naming_a_sibling_leaf_is_refused_by_kind(self) -> None:
        # The contrast case for the refusal above: the file parses perfectly, so the failure
        # is about what it is rather than about reading it. A leaf cannot own a subTasks
        # table, so syncing a row into one would either invent a section or drop the row.
        self._create_parent_master()
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "3D",
                    "slug": "03c_y",
                    "title": "Sibling",
                    "kind": "subTask",
                    "repo": "agents-remember",
                    "type": "Code",
                    "createdAt": "2026-01-01T00:00",
                }
            ),
        )

        with self.assertRaises(TaskDocError) as raised:
            self._create(master="03c_y.md")

        self.assertIn("parent task document is not a master", str(raised.exception))
        self.assertIn("03c_y.json", str(raised.exception))
        # The refused leaf was never written, and the sibling grew no subTasks table.
        self.assertFalse(
            (self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json").exists()
        )
        sibling = read_task_doc(self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_y.json")
        self.assertEqual(sibling.subTasks, [])

    def test_explicit_cross_series_master_ref_never_falls_back_to_local_master(self) -> None:
        created_master = self._create_parent_master()
        master_path = Path(str(created_master["docPath"]))
        master_before = master_path.read_bytes()

        result = self._create(master="../other-series/task.md")

        self.assertNotIn("masterSync", result)
        self.assertEqual(master_path.read_bytes(), master_before)
        self.assertEqual(read_task_doc(master_path).subTasks, [])

    def test_leaf_sync_refuses_duplicate_or_mispointed_exact_parent_row_before_write(
        self,
    ) -> None:
        self._create_parent_master()
        created_leaf = self._create(master="task.md")
        leaf_path = Path(str(created_leaf["docPath"]))
        master_path = self.coord / "tasks" / "agents-remember" / "3c-x" / "task.json"
        base = read_task_doc(master_path).model_dump(by_alias=True)

        candidates: list[tuple[str, dict[str, Any]]] = []
        duplicate = TaskDocument.model_validate(base).model_dump(by_alias=True)
        duplicate["subTasks"].append(dict(duplicate["subTasks"][0]))
        candidates.append(("at most one row", duplicate))
        mispointed = TaskDocument.model_validate(base).model_dump(by_alias=True)
        mispointed["subTasks"][0]["file"] = "03c_other.md"
        candidates.append(("points at", mispointed))

        for expected, candidate in candidates:
            write_task_doc(master_path.parent, TaskDocument.model_validate(candidate))
            before = {
                path: path.read_bytes()
                for path in (
                    leaf_path,
                    leaf_path.with_suffix(".md"),
                    master_path,
                    master_path.with_suffix(".md"),
                )
            }

            with self.subTest(expected=expected), self.assertRaises(TaskDocError) as raised:
                self._call("set_field", fields={"title": "Refused rename"})

            self.assertIn(expected, str(raised.exception))
            self.assertEqual({path: path.read_bytes() for path in before}, before)

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

    def test_leaf_sync_demotes_completed_master_when_work_becomes_unresolved(self) -> None:
        self._create_parent_master(status="Completed")
        self._create(
            master="task.md",
            status="Completed",
            steps=[{"id": "S1", "title": "One", "status": "done"}],
        )
        self._call("set_field", fields={"status": "inProgress"})
        master_path = self.coord / "tasks" / "agents-remember" / "3c-x" / "task.json"
        self.assertEqual(read_task_doc(master_path).status, "Completed")

        preview = self._call(
            "set_step",
            step={"id": "S1", "status": "pending"},
            dry_run=True,
        )

        self.assertEqual(preview["masterSync"]["status"], "would-update")
        self.assertIn("**Status:** inProgress", preview["masterSync"]["rendered"])
        self.assertEqual(read_task_doc(master_path).status, "Completed")

        self._call("set_step", step={"id": "S1", "status": "pending"})
        master = read_task_doc(master_path)
        self.assertEqual(master.status, "inProgress")
        self.assertEqual(master.subTasks[0].status, "inProgress")

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

    def test_set_field_orchestrates_on_master(self) -> None:
        # L14: `orchestrates` is a mutable flat string list on a master (the set_field path
        # makes an existing master an orchestration task without a replace); the render carries it.
        self._create_parent_master()
        updated = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x"),
            operation="set_field",
            edit=TaskDocEdit(fields={"orchestrates": ["260706_management-repo"]}),
        )
        doc = read_task_doc(Path(str(updated["docPath"])))
        self.assertEqual(doc.orchestrates, ["260706_management-repo"])
        rendered = Path(str(updated["renderedPath"])).read_text(encoding="utf-8")
        self.assertIn("**Orchestrates:** `260706_management-repo`", rendered)

    def test_set_field_orchestrates_rejected_on_leaf(self) -> None:
        self._create()
        with self.assertRaises(TaskDocError):
            self._call("set_field", fields={"orchestrates": ["260706_management-repo"]})

    def test_dry_run_create_renders_without_writing(self) -> None:
        result = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "3C",
                    "slug": "03c_x",
                    "title": "Smoke",
                    "kind": "subTask",
                    "repo": "agents-remember",
                    "type": "Code",
                    "createdAt": "2026-01-01T00:00",
                    "objective": "Preview me.",
                }
            ),
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
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation="set_field",
            edit=TaskDocEdit(fields={"objective": "changed"}),
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
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation="set_field",
            edit=TaskDocEdit(fields={"objective": "orig"}),
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
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation="set_field",
            edit=TaskDocEdit(fields={"objective": "orig"}),
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

    def test_skip_step_is_exact_audited_and_does_not_cascade(self) -> None:
        self._create(
            lifecycleId="LC-DOC",
            steps=[
                {
                    "id": "S1",
                    "title": "Parent",
                    "status": "pending",
                    "substeps": [
                        {"id": "C1", "title": "Child one", "status": "pending"},
                        {"id": "C2", "title": "Child two", "status": "blocked"},
                    ],
                }
            ],
        )

        parent_result = self._call(
            "skip_step",
            step={"id": "S1", "reason": "  Superseded by the accepted design.  "},
        )
        parent_doc = read_task_doc(Path(str(parent_result["docPath"])))
        parent = parent_doc.steps[0]
        parent_disposition = parent.disposition
        assert parent_disposition is not None
        self.assertEqual(parent.status, "done")
        self.assertEqual([sub.status for sub in parent.substeps], ["pending", "blocked"])
        self.assertEqual(parent_disposition.reason, "Superseded by the accepted design.")
        self.assertEqual(parent_disposition.kind, "intentionalSkip")
        self.assertEqual(parent_disposition.recordedVia, "task_doc.skip_step")
        self.assertEqual(parent_disposition.lifecycleId, "LC-DOC")
        self.assertRegex(parent_disposition.recordedAt, r"\+00:00$")
        self.assertEqual(parent_doc.decisions[-1].decision, "Intentionally skip step S1.")

        child_result = self._call(
            "skip_step",
            step={"id": "C1", "parent": "S1", "reason": "No longer required."},
        )
        child_doc = read_task_doc(Path(str(child_result["docPath"])))
        self.assertEqual(child_doc.steps[0].substeps[0].status, "done")
        self.assertEqual(child_doc.steps[0].substeps[1].status, "blocked")
        self.assertEqual(
            child_doc.decisions[-1].decision,
            "Intentionally skip step S1/C1.",
        )

    def test_skip_step_refuses_blank_missing_wrong_parent_and_ambiguity(self) -> None:
        self._create(
            steps=[
                {
                    "id": "S1",
                    "title": "First",
                    "substeps": [{"id": "C1", "title": "Child"}],
                },
                {"id": "S1", "title": "Duplicate"},
            ]
        )
        for step in (
            {"id": "S1", "reason": " "},
            {"id": "missing", "reason": "x"},
            {"id": "C1", "parent": "wrong", "reason": "x"},
            {"id": "S1", "reason": "x"},
        ):
            with self.subTest(step=step), self.assertRaises(TaskDocError):
                self._call("skip_step", step=step)

    def test_skip_step_accepts_each_unresolved_status(self) -> None:
        self._create(
            steps=[
                {"id": status, "title": status, "status": status}
                for status in ("pending", "inProgress", "blocked")
            ]
        )

        for status in ("pending", "inProgress", "blocked"):
            with self.subTest(status=status):
                self._call(
                    "skip_step",
                    step={"id": status, "reason": f"Skip the {status} unit."},
                )

        doc = read_task_doc(self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json")
        self.assertEqual([step.status for step in doc.steps], ["done", "done", "done"])
        self.assertEqual(len(doc.decisions), 3)

    def test_skip_step_refuses_done_units_without_changing_docs_or_audit(self) -> None:
        created = self._create(steps=[{"id": "S1", "title": "One", "status": "done"}])
        json_path = Path(str(created["docPath"]))
        markdown_path = Path(str(created["renderedPath"]))

        for mode in ("ordinary-done", "already-skipped"):
            if mode == "already-skipped":
                self._call("set_step", step={"id": "S1", "status": "pending"})
                self._call("skip_step", step={"id": "S1", "reason": "Original skip."})
            before_json = json_path.read_bytes()
            before_markdown = markdown_path.read_bytes()
            before_decisions = list(read_task_doc(json_path).decisions)

            with self.subTest(mode=mode), self.assertRaises(TaskDocError) as raised:
                self._call("skip_step", step={"id": "S1", "reason": "Second skip."})

            self.assertIn("already done", str(raised.exception))
            self.assertEqual(json_path.read_bytes(), before_json)
            self.assertEqual(markdown_path.read_bytes(), before_markdown)
            self.assertEqual(read_task_doc(json_path).decisions, before_decisions)

    def test_set_step_title_preserves_skip_but_explicit_status_clears_it(self) -> None:
        self._create(steps=[{"id": "S1", "title": "One", "status": "pending"}])
        self._call("skip_step", step={"id": "S1", "reason": "Not needed."})

        renamed = self._call("set_step", step={"id": "S1", "title": "Renamed"})
        renamed_doc = read_task_doc(Path(str(renamed["docPath"])))
        self.assertIsNotNone(renamed_doc.steps[0].disposition)

        executed = self._call("set_step", step={"id": "S1", "status": "done"})
        executed_doc = read_task_doc(Path(str(executed["docPath"])))
        self.assertIsNone(executed_doc.steps[0].disposition)

    def test_create_and_replace_cannot_author_skip_disposition(self) -> None:
        disposition = {
            "kind": "intentionalSkip",
            "reason": "No longer needed.",
            "recordedAt": "2026-08-03T12:00:00+00:00",
            "recordedVia": "task_doc.skip_step",
        }
        with self.assertRaises(TaskDocError):
            self._create(
                steps=[{"id": "S1", "title": "One", "status": "done", "disposition": disposition}]
            )

        self._create(steps=[{"id": "S1", "title": "One", "status": "pending"}])
        self._call("skip_step", step={"id": "S1", "reason": "No longer needed."})
        doc_path = self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json"
        replacement = read_task_doc(doc_path).model_dump(by_alias=True)
        replacement["steps"][0]["disposition"]["reason"] = "Changed out of band."
        with self.assertRaises(TaskDocError):
            self._call("replace", fields=replacement)

    def test_disposition_requires_done_status_for_parent_and_nested_units(self) -> None:
        disposition = {
            "kind": "intentionalSkip",
            "reason": "No longer needed.",
            "recordedAt": "2026-08-03T12:00:00+00:00",
            "recordedVia": "task_doc.skip_step",
        }
        for steps in (
            [
                {
                    "id": "S1",
                    "title": "Parent",
                    "status": "pending",
                    "disposition": disposition,
                }
            ],
            [
                {
                    "id": "S1",
                    "title": "Parent",
                    "status": "done",
                    "substeps": [
                        {
                            "id": "C1",
                            "title": "Child",
                            "status": "blocked",
                            "disposition": disposition,
                        }
                    ],
                }
            ],
        ):
            with self.subTest(steps=steps), self.assertRaises(ValidationError):
                _doc(steps=steps)

    def test_replace_cannot_keep_disposition_while_reopening_parent_or_child(self) -> None:
        self._create(
            steps=[
                {
                    "id": "S1",
                    "title": "Parent",
                    "status": "pending",
                    "substeps": [{"id": "C1", "title": "Child", "status": "pending"}],
                }
            ]
        )
        self._call("skip_step", step={"id": "S1", "reason": "Parent skip."})
        self._call(
            "skip_step",
            step={"id": "C1", "parent": "S1", "reason": "Child skip."},
        )
        doc_path = self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json"
        for target in ("parent", "child"):
            replacement = read_task_doc(doc_path).model_dump(by_alias=True)
            if target == "parent":
                replacement["steps"][0]["status"] = "pending"
            else:
                replacement["steps"][0]["substeps"][0]["status"] = "blocked"
            with self.subTest(target=target), self.assertRaises(TaskDocError):
                self._call("replace", fields=replacement)

    def test_replace_preserves_unresolved_parent_and_qualified_child_identity(self) -> None:
        created = self._create(
            steps=[
                {
                    "id": "S1",
                    "title": "Parent",
                    "status": "pending",
                    "substeps": [{"id": "C1", "title": "Child", "status": "blocked"}],
                }
            ]
        )
        original = read_task_doc(Path(str(created["docPath"]))).model_dump(by_alias=True)
        replacements = []
        dropped_parent = dict(original)
        dropped_parent["steps"] = []
        replacements.append(dropped_parent)
        dropped_child = TaskDocument.model_validate(original).model_dump(by_alias=True)
        dropped_child["steps"][0]["substeps"] = []
        replacements.append(dropped_child)
        renamed_child = TaskDocument.model_validate(original).model_dump(by_alias=True)
        renamed_child["steps"][0]["substeps"][0]["id"] = "C2"
        replacements.append(renamed_child)
        moved_child = TaskDocument.model_validate(original).model_dump(by_alias=True)
        child = moved_child["steps"][0]["substeps"].pop()
        moved_child["steps"].append(
            {"id": "S2", "title": "Other parent", "status": "done", "substeps": [child]}
        )
        replacements.append(moved_child)

        for replacement in replacements:
            with self.subTest(replacement=replacement), self.assertRaises(TaskDocError) as raised:
                self._call("replace", fields=replacement)
            self.assertIn("cannot remove or rename unresolved work units", str(raised.exception))

    def test_replace_preserves_multiplicity_of_duplicate_unresolved_ids(self) -> None:
        created = self._create(
            steps=[
                {"id": "S1", "title": "First", "status": "pending"},
                {"id": "S1", "title": "Second", "status": "blocked"},
            ]
        )
        replacement = read_task_doc(Path(str(created["docPath"]))).model_dump(by_alias=True)
        replacement["steps"].pop()
        with self.assertRaises(TaskDocError) as raised:
            self._call("replace", fields=replacement)
        self.assertIn("['S1']", str(raised.exception))

    def test_replace_cannot_drop_pending_work_before_or_during_completion(self) -> None:
        created = self._create(
            steps=[
                {
                    "id": "S1",
                    "title": "Parent",
                    "status": "done",
                    "substeps": [{"id": "C1", "title": "Child", "status": "pending"}],
                }
            ]
        )
        replacement = read_task_doc(Path(str(created["docPath"]))).model_dump(by_alias=True)
        replacement["steps"][0]["substeps"] = []
        replacement["status"] = "Completed"
        with self.assertRaises(TaskDocError):
            self._call("replace", fields=replacement)

        replacement["status"] = "inProgress"
        with self.assertRaises(TaskDocError):
            self._call("replace", fields=replacement)
        with self.assertRaises(TaskDocError) as terminal:
            self._call("set_status", fields={"status": "Completed"})
        self.assertIn("'id': 'C1'", str(terminal.exception))
        self.assertIn("'parentId': 'S1'", str(terminal.exception))

    def test_completion_paths_refuse_unresolved_nodes_and_allow_all_done(self) -> None:
        with self.assertRaises(TaskDocError):
            self._create(
                status="Completed",
                steps=[{"id": "S1", "title": "One", "status": "pending"}],
            )

        self._create(steps=[{"id": "S1", "title": "One", "status": "pending"}])
        for operation in ("set_status", "set_field"):
            with self.subTest(operation=operation), self.assertRaises(TaskDocError) as raised:
                self._call(operation, fields={"status": "Completed"})
            self.assertIn("'id': 'S1'", str(raised.exception))

        self._call("set_step", step={"id": "S1", "status": "done"})
        completed = self._call("set_status", fields={"status": "Completed"})
        self.assertEqual(completed["status"], "Completed")

    def test_legacy_inconsistent_completed_doc_is_readable_but_every_mutation_is_gated(
        self,
    ) -> None:
        task_root = self.coord / "tasks" / "agents-remember" / "3c-x"
        legacy = _doc(
            id="3C",
            slug="03c_x",
            kind="subTask",
            status="Completed",
            repo="agents-remember",
            steps=[{"id": "S1", "title": "Forgotten", "status": "pending"}],
        )
        json_path, markdown_path = write_task_doc(task_root, legacy)
        self.assertEqual(self._call("get")["status"], "Completed")
        before_json = json_path.read_bytes()
        before_markdown = markdown_path.read_bytes()
        mutations = (
            ("set_field", TaskDocEdit(fields={"objective": "Metadata repair."})),
            (
                "append_decision",
                TaskDocEdit(decision={"at": "t", "decision": "d", "rationale": "r"}),
            ),
            ("set_section", TaskDocEdit(section={"heading": "Note", "body": "repair"})),
        )
        for operation, edit in mutations:
            with self.subTest(operation=operation), self.assertRaises(TaskDocError):
                task_doc_tool(
                    self.cfg,
                    TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
                    operation=operation,
                    edit=edit,
                )
            self.assertEqual(json_path.read_bytes(), before_json)
            self.assertEqual(markdown_path.read_bytes(), before_markdown)
        self.assertEqual(read_task_doc(json_path).status, "Completed")

    def test_ready_completed_doc_allows_metadata_mutations(self) -> None:
        created = self._create(
            status="Completed",
            steps=[{"id": "S1", "title": "Done", "status": "done"}],
        )
        self._call("set_field", fields={"objective": "Metadata repair."})
        self._call(
            "append_decision",
            decision={"at": "t", "decision": "d", "rationale": "r"},
        )
        changed = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation="set_section",
            edit=TaskDocEdit(section={"heading": "Status history", "body": "still terminal"}),
        )
        doc = read_task_doc(Path(str(changed["docPath"])))
        self.assertEqual(doc.status, "Completed")
        self.assertEqual(doc.objective, "Metadata repair.")
        self.assertEqual(doc.decisions[-1].decision, "d")
        self.assertEqual(doc.sections[-1].heading, "Status history")
        self.assertEqual(Path(str(created["docPath"])), Path(str(changed["docPath"])))

    def test_terminal_candidate_can_truthfully_resolve_existing_work_in_same_call(self) -> None:
        created = self._create(
            status="inProgress",
            steps=[{"id": "S1", "title": "Forgotten", "status": "pending"}],
        )
        replacement = read_task_doc(Path(str(created["docPath"]))).model_dump(by_alias=True)
        replacement["status"] = "Completed"
        replacement["steps"][0]["status"] = "done"
        replaced = self._call("replace", fields=replacement)
        self.assertEqual(replaced["status"], "Completed")

        task_root = self.coord / "tasks" / "agents-remember" / "legacy-fix"
        legacy = _doc(
            id="LF",
            slug="01_legacy",
            kind="subTask",
            status="Completed",
            repo="agents-remember",
            steps=[{"id": "S1", "title": "Forgotten", "status": "pending"}],
        )
        json_path, _ = write_task_doc(task_root, legacy)
        fixed = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="legacy-fix", slug="01_legacy"),
            operation="set_step",
            edit=TaskDocEdit(step={"id": "S1", "status": "done"}),
        )
        self.assertEqual(fixed["status"], "Completed")
        self.assertEqual(read_task_doc(json_path).steps[0].status, "done")

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
            ContractTask(
                name="3c-x",
                repo_name="agents-remember",
                coordination_root=self.coord,
                workflow_kind="chat-task",
                memory_mode="disabled",
            ),
            leaf=LeafIdentity(worktree_name="3c-x", lifecycle_id="LC-CONTRACT"),
            code=RepoBranchPlan(
                repo_path=self.coord, source_branch="main", work_branch="wb", base_commit="abc123"
            ),
        )
        write_contract(contract.contract_path, contract)
        result = self._create()  # no lifecycleId in fields
        self.assertEqual(result["lifecycleId"], "LC-CONTRACT")

    def test_create_refuses_light_and_defaults_master_without_contract(self) -> None:
        base = {
            "id": "K1",
            "slug": "task",
            "title": "Kind",
            "repo": "agents-remember",
            "createdAt": "2026-01-01T00:00",
        }
        # Explicit light is refused: every task is wrapped master/leaf, even a single-file change.
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id="agents-remember", task_name="kind-x"),
                operation="create",
                edit=TaskDocEdit(fields={**base, "kind": "light"}),
            )
        # No contract + no kind defaults to a standalone master (not the retired "light" default).
        created = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="kind-x"),
            operation="create",
            edit=TaskDocEdit(fields=base),
        )
        self.assertEqual(created["kind"], "master")
        # replace shares _build_doc, so it refuses light on the same path.
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id="agents-remember", task_name="kind-x", slug="task"),
                operation="replace",
                edit=TaskDocEdit(fields={**base, "kind": "light"}),
            )

    def test_create_defaults_subtask_under_leaf_contract(self) -> None:
        contract = default_contract(
            ContractTask(
                name="leaf-x",
                repo_name="agents-remember",
                coordination_root=self.coord,
                workflow_kind="chat-task",
                memory_mode="disabled",
            ),
            leaf=LeafIdentity(worktree_name="leaf-x", lifecycle_id="LC-LEAF"),
            code=RepoBranchPlan(
                repo_path=self.coord, source_branch="main", work_branch="wb", base_commit="abc123"
            ),
        )
        write_contract(contract.contract_path, contract)
        # A bare create against a leaf contract is the leaf sub-task (context-aware default).
        result = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="leaf-x"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "L1",
                    "slug": "01_leaf",
                    "title": "Leaf",
                    "repo": "agents-remember",
                    "createdAt": "2026-01-01T00:00",
                }
            ),
        )
        self.assertEqual(result["kind"], "subTask")

    def test_resolve_by_contract_path(self) -> None:
        created = self._create()
        task_root = Path(str(created["docPath"])).parent
        result = task_doc_tool(
            self.cfg,
            TaskDocTarget(
                repo_id="agents-remember",
                contract_path=str(task_root / "series-contract.md"),
                slug="03c_x",
            ),
            operation="get",
        )
        self.assertEqual(result["taskId"], "3C")

    def test_error_paths(self) -> None:
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id="agents-remember", task_name="x"),
                operation="frob",
            )
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id="agents-remember"),
                operation="get",
            )
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


class MasterApplicationTests(unittest.TestCase):
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
            TaskDocTarget(repo_id="agents-remember", task_name="series"),
            operation="create",
            edit=TaskDocEdit(fields=payload),
        )

    def _op(self, operation: str, dry_run: bool = False, **kw: Any) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="series"),
            operation=operation,
            edit=TaskDocEdit(**kw),
            dry_run=dry_run,
        )

    def _complete_row(self, number: str) -> None:
        self._op("set_subtask", subtask={"number": number, "status": "Completed"})

    def test_create_master_writes_task_json_without_lifecycle(self) -> None:
        result = self._create()
        self.assertEqual(result["kind"], "master")
        self.assertTrue(str(result["docPath"]).endswith("task.json"))
        self.assertIsNone(result["lifecycleId"])

    def test_set_subtask_inserts_then_updates_by_number(self) -> None:
        self._create(subTasks=[{"number": "1", "name": "A", "status": "planning"}])
        self._author_leaf(number="1", slug="01_a")
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

    def test_set_subtask_completed_refuses_unready_or_missing_exact_leaf(self) -> None:
        self._create(subTasks=[{"number": "1", "name": "A", "status": "planning"}])
        with self.assertRaises(TaskDocError) as missing:
            self._op("set_subtask", subtask={"number": "1", "status": "Completed"})
        self.assertIn("no leaf task document exists", str(missing.exception))

        leaf_json, _leaf_md = self._author_leaf(number="1", slug="01_a")
        leaf = read_task_doc(leaf_json)
        data = leaf.model_dump(by_alias=True)
        data["steps"] = [{"id": "S1", "title": "Open", "status": "pending"}]
        write_task_doc(leaf_json.parent, TaskDocument.model_validate(data))
        with self.assertRaises(TaskDocError) as unresolved:
            self._op("set_subtask", subtask={"number": "1", "status": "Completed"})
        self.assertIn("'id': 'S1'", str(unresolved.exception))

    def test_set_subtask_revalidates_completed_target_on_missing_and_unready_repoint(self) -> None:
        self._create(subTasks=[{"number": "1", "name": "A", "status": "planning"}])
        leaf_json, _leaf_md = self._author_leaf(number="1", slug="01_a")
        self._op("set_subtask", subtask={"number": "1", "status": "Completed"})

        with self.assertRaises(TaskDocError) as missing:
            self._op("set_subtask", subtask={"number": "1", "file": "01_missing.md"})
        self.assertIn("asserted task document does not exist", str(missing.exception))

        leaf = read_task_doc(leaf_json)
        pending_data = leaf.model_dump(by_alias=True)
        pending_data["slug"] = "01_pending"
        pending_data["steps"] = [{"id": "S1", "title": "Open", "status": "pending"}]
        write_task_doc(leaf_json.parent, TaskDocument.model_validate(pending_data))
        leaf_json.unlink()
        with self.assertRaises(TaskDocError) as unresolved:
            self._op("set_subtask", subtask={"number": "1", "file": "01_pending.md"})
        self.assertIn("'id': 'S1'", str(unresolved.exception))

    def test_replace_revalidates_new_or_repointed_completed_row_identity(self) -> None:
        created = self._create(subTasks=[{"number": "1", "name": "A", "status": "planning"}])
        self._author_leaf(number="1", slug="01_a")
        self._op("set_subtask", subtask={"number": "1", "status": "Completed"})
        replacement = read_task_doc(Path(str(created["docPath"]))).model_dump(by_alias=True)
        replacement["subTasks"][0]["file"] = "01_missing.md"
        with self.assertRaises(TaskDocError) as missing:
            self._op("replace", fields=replacement)
        self.assertIn("asserted task document does not exist", str(missing.exception))

    def test_replace_allows_completed_row_name_and_scope_metadata_repair(self) -> None:
        task_root = self.coord / "tasks" / "agents-remember" / "series"
        legacy = _master(
            status="inProgress",
            subTasks=[
                {
                    "number": "1",
                    "name": "Old name",
                    "file": "missing.md",
                    "status": "Completed",
                    "scope": "old scope",
                }
            ],
        )
        master_json, _master_md = write_task_doc(task_root, legacy)
        replacement = read_task_doc(master_json).model_dump(by_alias=True)
        replacement["subTasks"][0]["name"] = "Corrected name"
        replacement["subTasks"][0]["scope"] = "corrected scope"
        result = self._op("replace", fields=replacement)
        [row] = read_task_doc(Path(str(result["docPath"]))).subTasks
        self.assertEqual((row.name, row.scope), ("Corrected name", "corrected scope"))

    def test_replace_cannot_erase_or_change_unresolved_row_identity_or_multiplicity(self) -> None:
        task_root = self.coord / "tasks" / "agents-remember" / "series"
        original = _master(
            subTasks=[
                {
                    "number": "1",
                    "name": "First",
                    "file": "01_a.md",
                    "status": "planning",
                },
                {
                    "number": "1",
                    "name": "Duplicate",
                    "file": "01_a.md",
                    "status": "inProgress",
                },
            ]
        )
        master_json, _ = write_task_doc(task_root, original)
        base = read_task_doc(master_json).model_dump(by_alias=True)
        candidates: list[dict[str, Any]] = []

        dropped = TaskDocument.model_validate(base).model_dump(by_alias=True)
        dropped["subTasks"].pop()
        candidates.append(dropped)
        repointed = TaskDocument.model_validate(base).model_dump(by_alias=True)
        repointed["subTasks"][0]["file"] = "01_other.md"
        candidates.append(repointed)
        renamed = TaskDocument.model_validate(base).model_dump(by_alias=True)
        renamed["subTasks"][0]["number"] = "2"
        candidates.append(renamed)
        changed_kind = TaskDocument.model_validate(base).model_dump(by_alias=True)
        changed_kind["kind"] = "subTask"
        changed_kind["slug"] = "task"
        changed_kind["subTasks"] = []
        changed_kind["sections"] = []
        candidates.append(changed_kind)

        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(TaskDocError) as raised:
                self._op("replace", fields=candidate)
            self.assertIn(
                "cannot remove, rename, or repoint unresolved master rows", str(raised.exception)
            )
            self.assertEqual(read_task_doc(master_json), original)

        metadata = TaskDocument.model_validate(base).model_dump(by_alias=True)
        metadata["subTasks"][0]["name"] = "Renamed metadata"
        metadata["subTasks"][0]["scope"] = "Clarified scope"
        changed = self._op("replace", fields=metadata)
        row = read_task_doc(Path(str(changed["docPath"]))).subTasks[0]
        self.assertEqual((row.name, row.scope), ("Renamed metadata", "Clarified scope"))

    def test_set_subtask_cannot_repoint_an_unresolved_row(self) -> None:
        self._create(subTasks=[{"number": "1", "name": "A", "file": "01_a.md"}])

        with self.assertRaises(TaskDocError) as raised:
            self._op("set_subtask", subtask={"number": "1", "file": "01_other.md"})

        self.assertIn(
            "cannot remove, rename, or repoint unresolved master rows", str(raised.exception)
        )

    def test_truthful_replace_can_complete_exact_row_and_master_together(self) -> None:
        created = self._create()
        self._author_leaf(number="1", slug="01_a")
        replacement = read_task_doc(Path(str(created["docPath"]))).model_dump(by_alias=True)
        replacement["subTasks"][0]["status"] = "Completed"
        replacement["status"] = "Completed"

        result = self._op("replace", fields=replacement)

        master = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual(master.status, "Completed")
        self.assertEqual(master.subTasks[0].status, "Completed")

    def test_replace_can_remove_a_row_only_after_it_is_completed(self) -> None:
        created = self._create()
        self._author_leaf(number="1", slug="01_a")
        self._complete_row("1")
        replacement = read_task_doc(Path(str(created["docPath"]))).model_dump(by_alias=True)
        replacement["subTasks"] = []

        result = self._op("replace", fields=replacement)

        self.assertEqual(read_task_doc(Path(str(result["docPath"]))).subTasks, [])

    def test_completed_master_mutations_are_gated_until_rows_are_truthful(self) -> None:
        task_root = self.coord / "tasks" / "agents-remember" / "series"
        legacy = _master(
            status="Completed",
            subTasks=[{"number": "1", "name": "Open", "status": "planning"}],
        )
        master_json, master_markdown = write_task_doc(task_root, legacy)
        before_json = master_json.read_bytes()
        before_markdown = master_markdown.read_bytes()
        self.assertEqual(self._op("get")["status"], "Completed")

        with self.assertRaises(TaskDocError):
            self._op("set_section", section={"heading": "Note", "body": "blocked"})

        self.assertEqual(master_json.read_bytes(), before_json)
        self.assertEqual(master_markdown.read_bytes(), before_markdown)

        pending_leaf = _doc(
            id="1",
            slug="01_a",
            kind="subTask",
            repo="agents-remember",
            master="task.md",
            steps=[{"id": "S1", "title": "Open", "status": "pending"}],
        )
        write_task_doc(task_root, pending_leaf)
        false_terminal = _master(
            status="Completed",
            subTasks=[
                {
                    "number": "1",
                    "name": "False terminal",
                    "file": "01_a.md",
                    "status": "Completed",
                }
            ],
        )
        write_task_doc(task_root, false_terminal)
        with self.assertRaises(TaskDocError) as unresolved_leaf:
            self._op(
                "append_decision",
                decision={"at": "t", "decision": "d", "rationale": "r"},
            )
        self.assertIn("'id': 'S1'", str(unresolved_leaf.exception))

        ready = _master(status="Completed")
        write_task_doc(task_root, ready)
        result = self._op("set_section", section={"heading": "Note", "body": "allowed"})
        self.assertEqual(read_task_doc(Path(str(result["docPath"]))).sections[-1].body, "allowed")

    def test_replace_revalidates_each_new_completed_duplicate_occurrence(self) -> None:
        task_root = self.coord / "tasks" / "agents-remember" / "series"
        original = _master(
            status="inProgress",
            subTasks=[
                {
                    "number": "1",
                    "name": "First",
                    "file": "01_a.md",
                    "status": "Completed",
                },
                {
                    "number": "1",
                    "name": "Second",
                    "file": "01_a.md",
                    "status": "planning",
                },
            ],
        )
        master_json, _ = write_task_doc(task_root, original)
        replacement = read_task_doc(master_json).model_dump(by_alias=True)
        replacement["subTasks"][1]["status"] = "Completed"

        with self.assertRaises(TaskDocError) as raised:
            self._op("replace", fields=replacement)

        self.assertIn("asserted task document does not exist", str(raised.exception))

    def test_master_completed_requires_every_subtask_row_completed(self) -> None:
        self._create(subTasks=[{"number": "1", "name": "A", "status": "planning"}])
        with self.assertRaises(TaskDocError) as raised:
            self._op("set_status", fields={"status": "Completed"})
        self.assertIn("'parentId': 'series'", str(raised.exception))

    def test_master_completion_revalidates_legacy_completed_row_exact_leaf(self) -> None:
        task_root = self.coord / "tasks" / "agents-remember" / "series"
        legacy = _master(
            status="inProgress",
            subTasks=[
                {
                    "number": "1",
                    "name": "False completion",
                    "file": "missing.md",
                    "status": "Completed",
                }
            ],
        )
        write_task_doc(task_root, legacy)
        with self.assertRaises(TaskDocError) as missing:
            self._op("set_status", fields={"status": "Completed"})
        self.assertIn("asserted task document does not exist", str(missing.exception))

    def test_master_completion_revalidates_pending_leaf_behind_completed_row(self) -> None:
        task_root = self.coord / "tasks" / "agents-remember" / "series"
        leaf = _doc(
            id="1",
            slug="01_a",
            kind="subTask",
            repo="agents-remember",
            master="task.md",
            steps=[{"id": "S1", "title": "Open", "status": "pending"}],
        )
        write_task_doc(task_root, leaf)
        legacy = _master(
            status="inProgress",
            subTasks=[
                {
                    "number": "1",
                    "name": "False completion",
                    "file": "01_a.md",
                    "status": "Completed",
                }
            ],
        )
        write_task_doc(task_root, legacy)
        with self.assertRaises(TaskDocError) as pending:
            self._op("set_status", fields={"status": "Completed"})
        self.assertIn("'id': 'S1'", str(pending.exception))

    def test_master_with_no_rows_is_vacuously_terminal_ready(self) -> None:
        self._create()
        completed = self._op("set_status", fields={"status": "Completed"})
        self.assertEqual(completed["status"], "Completed")

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
            ContractTask(
                name="series",
                repo_name="agents-remember",
                coordination_root=self.coord,
                workflow_kind="light-task",
                memory_mode="disabled",
            ),
            leaf=LeafIdentity(worktree_name="series", lifecycle_id="LC-X"),
            code=RepoBranchPlan(
                repo_path=self.coord, source_branch="main", work_branch="wb", base_commit="abc123"
            ),
        )
        write_contract(contract.contract_path, contract)
        self.assertIsNone(self._create()["lifecycleId"])

    def test_master_rejects_step_op(self) -> None:
        self._create()
        with self.assertRaises(TaskDocError):
            self._op("set_step", step={"id": "S1", "title": "x"})

    def test_subtask_op_rejects_non_master_but_section_allows_freeform(self) -> None:
        # A subTask leaf (the non-master kind now that "light" is no longer authorable). Its
        # slug is distinct from "task" so master-sync does not treat its own task.json as a master.
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="lite"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "L",
                    "slug": "01_leaf",
                    "title": "L",
                    "kind": "subTask",
                    "repo": "r",
                    "createdAt": "2026-01-01T00:00",
                }
            ),
        )
        # set_subtask stays master-only (the series index has no meaning on a leaf)
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id="agents-remember", task_name="lite", slug="01_leaf"),
                operation="set_subtask",
                edit=TaskDocEdit(subtask={"number": "1", "name": "x"}),
            )
        # set_section on a leaf adds a freeform extra section (R4)
        result = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="lite", slug="01_leaf"),
            operation="set_section",
            edit=TaskDocEdit(section={"heading": "Status history", "body": "old."}),
        )
        doc = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual([s.heading for s in doc.sections], ["Status history"])
        # a non-freeform section on a leaf is rejected (the validator backstop)
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id="agents-remember", task_name="lite", slug="01_leaf"),
                operation="set_section",
                edit=TaskDocEdit(section={"heading": "X", "kind": "subTasks"}),
            )

    def test_master_op_argument_errors(self) -> None:
        self._create()
        with self.assertRaises(TaskDocError):
            self._op("set_subtask", subtask={"name": "no number"})
        with self.assertRaises(TaskDocError):
            self._op("set_section", section={"body": "no heading"})

    def _author_leaf(self, *, number: str = "1", slug: str = "01_a") -> tuple[Path, Path]:
        leaf = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="series"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": number,
                    "slug": slug,
                    "title": f"Leaf {number}",
                    "kind": "subTask",
                    "master": "task.md",
                    "repo": "agents-remember",
                    "createdAt": "2026-01-01T00:00",
                }
            ),
        )
        return Path(str(leaf["docPath"])), Path(str(leaf["renderedPath"]))

    def test_remove_subtask_deletes_leaf_doc_and_row(self) -> None:
        # remove means remove: the master row AND the leaf doc (json + md) are gone.
        self._create()
        leaf_json, leaf_md = self._author_leaf()
        self._complete_row("1")
        self.assertTrue(leaf_json.exists() and leaf_md.exists())
        result = self._op("remove_subtask", subtask={"number": "1"})
        self.assertEqual(result["removedSubtask"], "1")
        master = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual([s.number for s in master.subTasks], [])
        self.assertFalse(leaf_json.exists())
        self.assertFalse(leaf_md.exists())
        self.assertIn(leaf_json.as_posix(), result["deletedFiles"])

    def test_remove_subtask_keep_file_retains_leaf_doc(self) -> None:
        # keep_file drops the index row but leaves the leaf doc on disk.
        self._create()
        leaf_json, leaf_md = self._author_leaf()
        self._complete_row("1")
        result = self._op("remove_subtask", subtask={"number": "1", "keep_file": True})
        master = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual([s.number for s in master.subTasks], [])
        self.assertTrue(leaf_json.exists() and leaf_md.exists())
        self.assertEqual(result["deletedFiles"], [])

    def test_remove_subtask_dry_run_previews_without_deleting(self) -> None:
        self._create()
        leaf_json, leaf_md = self._author_leaf()
        self._complete_row("1")
        result = self._op("remove_subtask", subtask={"number": "1"}, dry_run=True)
        self.assertTrue(result["dryRun"])
        self.assertIn(leaf_json.as_posix(), result["wouldDeleteFiles"])
        self.assertTrue(leaf_json.exists() and leaf_md.exists())
        master = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual([s.number for s in master.subTasks], ["1"])

    def test_remove_subtask_absent_or_no_number_raises(self) -> None:
        self._create()
        with self.assertRaises(TaskDocError):
            self._op("remove_subtask", subtask={"number": "ghost"})
        with self.assertRaises(TaskDocError):
            self._op("remove_subtask", subtask={"name": "no number"})

    def test_remove_subtask_refuses_unresolved_row_without_touching_any_file(self) -> None:
        created = self._create()
        leaf_json, leaf_markdown = self._author_leaf()
        master_json = Path(str(created["docPath"]))
        master_markdown = Path(str(created["renderedPath"]))
        before = {
            path: path.read_bytes()
            for path in (master_json, master_markdown, leaf_json, leaf_markdown)
        }

        with self.assertRaises(TaskDocError) as raised:
            self._op("remove_subtask", subtask={"number": "1"})

        self.assertIn(
            "cannot remove, rename, or repoint unresolved master rows", str(raised.exception)
        )
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_remove_subtask_rejects_non_master(self) -> None:
        self._create()
        self._author_leaf()
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id="agents-remember", task_name="series", slug="01_a"),
                operation="remove_subtask",
                edit=TaskDocEdit(subtask={"number": "1"}),
            )

    def test_remove_subtask_response_validates_on_both_paths(self) -> None:
        # FINDING 1 (260703-L18, closes friction F-N): the remove_subtask result must satisfy the
        # TaskDocResponse contract (extra=forbid). Before removedSubtask/deletedFiles/wouldDeleteFiles
        # were declared, the destructive success FAILED response validation, so the caller saw a tool
        # error after the removal already happened (and could retry an already-done op). Both the
        # delete-with-files and keep_file paths -- and the dry-run preview -- must validate.
        self._create()
        self._author_leaf(number="1", slug="01_a")
        self._complete_row("1")
        deleted = self._op("remove_subtask", subtask={"number": "1"})
        self.assertEqual(deleted["removedSubtask"], "1")
        self.assertTrue(deleted["deletedFiles"])  # the leaf json + md paths
        TaskDocResponse.model_validate(deleted)  # would raise ValidationError before the fix

        self._author_leaf(number="2", slug="02_b")
        self._complete_row("2")
        kept = self._op("remove_subtask", subtask={"number": "2", "keep_file": True})
        self.assertEqual(kept["deletedFiles"], [])
        TaskDocResponse.model_validate(kept)

        self._author_leaf(number="3", slug="03_c")
        self._complete_row("3")
        preview = self._op("remove_subtask", subtask={"number": "3"}, dry_run=True)
        self.assertTrue(preview["wouldDeleteFiles"])
        TaskDocResponse.model_validate(preview)


class TerminalLeafResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_absent_leaf_allows_unrelated_sibling_and_malformed_sibling(self) -> None:
        write_task_doc(
            self.root,
            _doc(id="SIBLING", slug="01_sibling", kind="subTask"),
        )
        (self.root / "broken-sibling.json").write_text("{", encoding="utf-8")
        self.assertIsNone(resolve_terminal_leaf_doc(self.root, "MISSING"))

    def test_matching_stem_unreadable_candidate_refuses(self) -> None:
        (self.root / "TARGET.json").write_text("{", encoding="utf-8")
        with self.assertRaises(TerminalLeafResolutionError) as raised:
            resolve_terminal_leaf_doc(self.root, "target")
        self.assertIn("cannot read terminal leaf candidate", str(raised.exception))

    def test_duplicate_identity_and_mispointed_assertion_refuse(self) -> None:
        first, _ = write_task_doc(
            self.root,
            _doc(id="TARGET", slug="01_first", kind="subTask"),
        )
        second, _ = write_task_doc(
            self.root,
            _doc(id="TARGET", slug="02_second", kind="subTask"),
        )
        with self.assertRaises(TerminalLeafResolutionError) as ambiguous:
            resolve_terminal_leaf_doc(self.root, "TARGET")
        self.assertIn("ambiguous", str(ambiguous.exception))

        second.unlink()
        sibling, _ = write_task_doc(
            self.root,
            _doc(id="SIBLING", slug="02_sibling", kind="subTask"),
        )
        with self.assertRaises(TerminalLeafResolutionError) as mispointed:
            resolve_terminal_leaf_doc(self.root, "TARGET", asserted_path=sibling)
        self.assertIn("not bound to contract leaf", str(mispointed.exception))
        resolved = resolve_terminal_leaf_doc(self.root, "TARGET")
        assert resolved is not None
        self.assertEqual(resolved[0], first)


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
            TaskDocTarget(repo_id="agents-remember", task_name="3c-reg"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "R1",
                    "slug": "task",
                    "title": "Reg",
                    "kind": "master",
                    "repo": "agents-remember",
                    "createdAt": "2026-01-01T00:00",
                }
            ),
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["operation"], "task_doc.create")
        self.assertIn("tokens", payload)
        # The emitted payload validates against the registered response model.
        TaskDocResponse.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
