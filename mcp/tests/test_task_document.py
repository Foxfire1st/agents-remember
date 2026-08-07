"""Tests for the JSON-primary task-document layer (slice 3c, commit 1).

Covers the ``ar-task-document/v1`` schema (round-trip, alias, strictness, progress
helpers), the deterministic markdown renderer (the ``w-02-light-task-workflow``
template shape, checkbox mapping, escaping, empty sections), the JSON+markdown
store, the ``task_doc`` application operations and error paths (including contract
lifecycle-key pickup), and the MCP tool registration.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import (
    Any,
    cast,
)
from unittest.mock import patch

from pydantic import ValidationError

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.tasks.store as task_store
from agents_remember.application.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.mcp.config import McpRuntimeConfig
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
