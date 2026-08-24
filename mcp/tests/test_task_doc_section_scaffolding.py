"""Focused raw-section validation and register-scaffolding boundary tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    _build_doc,
    task_doc_tool,
)
from agents_remember.tasks import write_task_doc
from agents_remember.worktrees.queue.closeout_queue_evidence import (
    register_scaffold_sections,
)
from test_task_document import _config, _master


def _sprint_fields(*, sections: object) -> dict[str, Any]:
    return {
        "id": "SPRINT",
        "slug": "task",
        "kind": "master",
        "title": "Sprint",
        "repo": "agents-remember",
        "createdAt": "2026-08-24T00:00:00+00:00",
        "orchestrates": ["child"],
        "sections": sections,
    }


class TaskDocSectionScaffoldingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.coord = Path(self.temp.name)
        self.config = _config(self.coord)

    def test_hostile_section_containers_refuse_as_typed_errors_before_create(self) -> None:
        hostile: tuple[object, ...] = (42, "bad", {"heading": "object"}, None, ("tuple",))
        for index, sections in enumerate(hostile):
            task_name = f"invalid-{index}"
            task_root = self.coord / "tasks" / "agents-remember" / task_name
            with self.subTest(sections=sections), self.assertRaises(TaskDocError) as raised:
                task_doc_tool(
                    self.config,
                    TaskDocTarget(repo_id="agents-remember", task_name=task_name),
                    operation="create",
                    edit=TaskDocEdit(fields=_sprint_fields(sections=sections)),
                )
            self.assertIn("sections must be a list", str(raised.exception))
            self.assertFalse((task_root / "task.json").exists())
            self.assertFalse((task_root / "task.md").exists())

    def test_hostile_section_members_report_their_index_without_mutating_input(self) -> None:
        valid = {"kind": "freeform", "heading": "Custom", "body": "keep"}
        for index, member in enumerate((7, "bad", ["nested"])):
            sections: list[object] = [valid, member]
            before = list(sections)
            with self.subTest(member=member), self.assertRaises(TaskDocError) as raised:
                _build_doc(
                    _sprint_fields(sections=sections),
                    None,
                    self.coord / f"raw-{index}",
                )
            self.assertIn("sections[1] must be an object", str(raised.exception))
            self.assertEqual(sections, before)

    def test_replace_and_dry_run_refusals_preserve_existing_bytes_and_modes(self) -> None:
        task_root = self.coord / "tasks" / "agents-remember" / "series"
        json_path, markdown_path = write_task_doc(task_root, _master())
        before = {
            path: (path.read_bytes(), path.stat().st_mode) for path in (json_path, markdown_path)
        }
        replacement = _master().model_dump(by_alias=True)
        replacement.update({"orchestrates": ["child"], "sections": ["bad-member"]})

        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), self.assertRaises(TaskDocError):
                task_doc_tool(
                    self.config,
                    TaskDocTarget(repo_id="agents-remember", task_name="series"),
                    operation="replace",
                    edit=TaskDocEdit(fields=replacement),
                    call=TaskDocCall(dry_run=dry_run),
                )
            self.assertEqual(
                {
                    path: (path.read_bytes(), path.stat().st_mode)
                    for path in (json_path, markdown_path)
                },
                before,
            )

    def test_valid_sections_are_copied_and_only_missing_registers_are_appended(self) -> None:
        custom = {"kind": "freeform", "heading": "Custom", "body": "keep exactly"}
        first_scaffold = dict(register_scaffold_sections()[0])
        caller_sections = [custom, first_scaffold]
        before = [dict(section) for section in caller_sections]

        document = _build_doc(
            _sprint_fields(sections=caller_sections),
            None,
            self.coord / "valid",
        )

        self.assertEqual(caller_sections, before)
        headings = [section.heading for section in document.sections]
        self.assertEqual(headings[0], "Custom")
        for scaffold in register_scaffold_sections():
            self.assertEqual(headings.count(scaffold["heading"]), 1)

    def test_mapping_shape_passes_to_canonical_model_validation(self) -> None:
        with self.assertRaises(TaskDocError) as raised:
            _build_doc(
                _sprint_fields(sections=[{"kind": "freeform"}]),
                None,
                self.coord / "semantic",
            )
        self.assertIn("invalid task document", str(raised.exception))
        self.assertIn("heading", str(raised.exception))
