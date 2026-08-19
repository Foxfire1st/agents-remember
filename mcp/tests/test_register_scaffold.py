"""L13-R6: grade machinery kept, with scaffolded registers and write-time shape validation.

Sprint creation scaffolds the empty canonical Judgment and Priority Register
sections (so set-grade never dead-ends on a missing register), and every write
through a register heading must keep the canonical table shape. Read paths stay
tolerant; malformed registers are facts, never crashes.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.application.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.closeout_queue_evidence import (
    JUDGMENT_REGISTER_HEADING,
    JUDGMENT_REGISTER_SECTION,
    PRIORITY_REGISTER_HEADING,
    PRIORITY_REGISTER_SECTION,
    register_scaffold_sections,
    register_section_facts,
)
from test_task_execution_topology import REPOSITORY, _config, _judgment_row, _master
from test_worktree_support import git, init_repo

_JUDGMENT_HEADER_ROW = (
    "| Judgment id | Kind (dependency meaning, execution nature, blast radius, priority, "
    "blocker placement, reprioritization, or leaf move) | Subject | Decision | Rationale | "
    "Evidence/fact refs | Author | Confidence | Supersedes |"
)


class RegisterScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.coord = Path(self.temp.name)
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.tasks.mkdir(parents=True)
        self.code = self.coord / "code"
        init_repo(self.code)
        git(self.code, "branch", "super", "main")
        self.cfg = _config(self.coord, self.code)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create_sprint(self, fields: dict[str, object] | None = None) -> dict[str, object]:
        # Doctrine: the commanded master document exists before the sprint adopts it.
        write_task_doc(self.tasks / "master-a", _master(identity="MASTER-A"))
        base: dict[str, object] = {
            "id": "SPRINT",
            "slug": "sprint",
            "title": "Sprint",
            "kind": "master",
            "repo": REPOSITORY,
            "createdAt": "2026-08-19T00:00:00+00:00",
            "orchestrates": ["master-a"],
            "integrationBranch": "super",
        }
        base.update(fields or {})
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation="create",
            edit=TaskDocEdit(fields=base),
        )

    def _read_sprint(self):
        return read_task_doc(self.tasks / "sprint" / "task.json")

    def test_sprint_creation_scaffolds_empty_canonical_registers(self) -> None:
        self._create_sprint()
        doc = self._read_sprint()
        headings = [section.heading for section in doc.sections]
        self.assertIn(JUDGMENT_REGISTER_HEADING, headings)
        self.assertIn(PRIORITY_REGISTER_HEADING, headings)
        # The scaffold parses clean (empty table) and is single-sourced from the
        # canonical evidence module.
        facts = register_section_facts(
            ResolvedTaskDocument(
                ref=TaskDocumentRef(repository=REPOSITORY, path="sprint/task.json"),
                path=self.tasks / "sprint" / "task.json",
                document=doc,
            )
        )
        self.assertEqual(facts, {"judgmentRegister": "ok", "priorityRegister": "ok"})
        scaffold = register_scaffold_sections()
        self.assertEqual(scaffold[0]["heading"], JUDGMENT_REGISTER_HEADING)
        self.assertIn("---", scaffold[0]["body"])

    def test_creation_preserves_a_supplied_valid_register(self) -> None:
        body = "\n".join(
            [
                _JUDGMENT_HEADER_ROW,
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                _judgment_row("J-1"),
            ]
        )
        self._create_sprint(
            {"sections": [{"kind": "freeform", "heading": JUDGMENT_REGISTER_HEADING, "body": body}]}
        )
        doc = self._read_sprint()
        judgment = next(s for s in doc.sections if s.heading == JUDGMENT_REGISTER_HEADING)
        self.assertIn("J-1", judgment.body)
        headings = [section.heading for section in doc.sections]
        self.assertEqual(headings.count(JUDGMENT_REGISTER_HEADING), 1)

    def test_create_with_malformed_register_is_refused_at_write_time(self) -> None:
        with self.assertRaisesRegex(TaskDocError, "register"):
            self._create_sprint(
                {
                    "sections": [
                        {
                            "kind": "freeform",
                            "heading": JUDGMENT_REGISTER_HEADING,
                            "body": "not a canonical table",
                        }
                    ]
                }
            )
        self.assertFalse((self.tasks / "sprint" / "task.json").exists())

    def test_set_section_register_shape_is_validated(self) -> None:
        self._create_sprint()
        with self.assertRaisesRegex(TaskDocError, "register"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="set_section",
                edit=TaskDocEdit(
                    section={"heading": PRIORITY_REGISTER_HEADING, "body": "| broken |"}
                ),
            )
        # A valid row write succeeds.
        result = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation="set_section",
            edit=TaskDocEdit(
                section={
                    "heading": JUDGMENT_REGISTER_HEADING,
                    "body": "\n".join(
                        [
                            _JUDGMENT_HEADER_ROW,
                            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                            _judgment_row("J-2"),
                        ]
                    ),
                }
            ),
        )
        self.assertEqual(result["ok"], True)
        self.assertIn("J-2", self._read_sprint().sections[0].body)

    def test_replace_with_malformed_register_is_refused(self) -> None:
        self._create_sprint()
        doc = self._read_sprint()
        data = doc.model_dump(by_alias=True)
        data["sections"] = [
            {
                "kind": "freeform",
                "heading": JUDGMENT_REGISTER_HEADING,
                "body": "garbage",
            }
        ]
        with self.assertRaisesRegex(TaskDocError, "register"):
            task_doc_tool(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
                operation="replace",
                edit=TaskDocEdit(fields=data),
            )

    def test_plain_master_creation_scaffolds_nothing(self) -> None:
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="plain"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "PLAIN",
                    "slug": "plain",
                    "title": "Plain",
                    "kind": "master",
                    "repo": REPOSITORY,
                    "createdAt": "2026-08-19T00:00:00+00:00",
                }
            ),
        )
        doc = read_task_doc(self.tasks / "plain" / "task.json")
        headings = {section.heading.strip().casefold() for section in doc.sections}
        self.assertNotIn(JUDGMENT_REGISTER_SECTION, headings)
        self.assertNotIn(PRIORITY_REGISTER_SECTION, headings)


if __name__ == "__main__":
    unittest.main()
