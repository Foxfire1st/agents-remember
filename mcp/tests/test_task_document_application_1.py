from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path
from typing import Any

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_response import finalize_tool_response
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.document import step_done, step_total
from agents_remember.tasks.leaf_doc import plan_leaf_doc_lifecycle_restamp
from agents_remember.worktrees.task_resolver import leaf_enclosure_path, series_contract_path
from pydantic import ValidationError
from test_task_document import ApplicationTests


class ApplicationTests1(ApplicationTests):
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
        self.assertEqual((created["stepsDone"], created["stepsTotal"]), (0, 1))
        master = read_task_doc(Path(str(created["masterSync"]["masterDocPath"])))
        self.assertEqual(master.subTasks[0].status, "inProgress")

    def test_five_major_eight_children_share_the_tool_progress_count(self) -> None:
        # The same five/eight and states as step-marks.test.tsx, through the native writer.
        steps = [
            {
                "id": f"S{i + 1}",
                "title": f"Major {i + 1}",
                "status": "done" if i == 0 else "pending",
                "substeps": [
                    {
                        "id": f"S{i + 1}.{j + 1}",
                        "title": f"Child {i + 1}.{j + 1}",
                        "status": "done" if i == 0 or (i == 1 and j == 0) else "pending",
                    }
                    for j in range(2 if i < 4 else 0)
                ],
            }
            for i in range(5)
        ]
        self._create_parent_master()
        created = self._create(master="task.md", steps=steps)
        self.assertTrue(created["ok"])
        self.assertEqual((created["stepsDone"], created["stepsTotal"]), (1, 5))
        doc = read_task_doc(self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json")
        self.assertEqual(len(doc.steps), 5)
        self.assertEqual(sum(len(step.substeps) for step in doc.steps), 8)
        self.assertEqual((step_done(doc), step_total(doc)), (1, 5))

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

        result = self._call("set_field", fields={"title": "Renamed"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "master-sync-failed")
        self.assertIn("cannot read parent master task document", str(result["error"]))
        self.assertIn("task.json", str(result["error"]))
        self.assertEqual(result["masterSync"]["status"], "failed")
        self.assertEqual(leaf_path.read_text(encoding="utf-8"), leaf_before)
        self.assertEqual(read_task_doc(leaf_path).title, "Smoke")

    def test_explicit_cross_series_master_ref_is_refused_not_fallen_back(self) -> None:
        # MIK-R61 rule 2: a reference that leaves the task folder is refused with one
        # message (leaf, reference, fix), never silently fallen back to the local master.
        created_master = self._create_parent_master()
        master_path = Path(str(created_master["docPath"]))
        master_before = master_path.read_bytes()

        with self.assertRaises(TaskDocError) as raised:
            self._create(master="../other-series/task.md")

        self.assertIn("../other-series/task.md", str(raised.exception))
        self.assertIn("not a document of this task folder", str(raised.exception))
        self.assertIn("name a document of this folder", str(raised.exception))
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

            with self.subTest(expected=expected):
                result = self._call("set_field", fields={"title": "Refused rename"})

            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "master-sync-failed")
            self.assertIn(expected, str(result["error"]))
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_leaf_sync_demotes_completed_master_when_work_becomes_unresolved(self) -> None:
        """A leaf whose document is not ``Completed`` cannot hold a ``Completed`` row.

        Measured change (S6b of `260915-CAPS-L21`). The row used to stay ``Completed`` by
        DERIVATION from step state, so a leaf whose document had already left ``Completed`` --
        the state a leaf is in between finishing its steps and landing its closeout -- kept a
        ``Completed`` row, and the master could not be terminal over it. The row now follows
        the document, so the master leaves ``Completed`` on the same mutation and the step
        edit that follows has no row change left to make.
        """
        self._create_parent_master(status="Completed")
        self._create(
            master="task.md",
            status="Completed",
            steps=[{"id": "S1", "title": "One", "status": "done"}],
        )
        master_path = self.coord / "tasks" / "agents-remember" / "3c-x" / "task.json"
        self.assertEqual(read_task_doc(master_path).status, "Completed")
        self.assertEqual(read_task_doc(master_path).subTasks[0].status, "Completed")

        self._call("set_field", fields={"status": "inProgress"})

        demoted = read_task_doc(master_path)
        self.assertEqual(demoted.status, "inProgress")
        self.assertEqual(demoted.subTasks[0].status, "inProgress")

        preview = self._call(
            "set_step",
            step={"id": "S1", "status": "pending"},
            dry_run=True,
        )

        self.assertEqual(preview["masterSync"]["status"], "unchanged")
        self.assertEqual(read_task_doc(master_path).status, "inProgress")

        self._call("set_step", step={"id": "S1", "status": "pending"})
        master = read_task_doc(master_path)
        self.assertEqual(master.status, "inProgress")
        self.assertEqual(master.subTasks[0].status, "inProgress")

    def test_set_status_and_set_field(self) -> None:
        self._create()
        status_result = self._call("set_status", fields={"status": "inProgress"})
        self.assertEqual(status_result["status"], "inProgress")
        updated = self._call("set_field", fields={"objective": "new", "bogus": "x"})
        self.assertEqual(updated["operation"], "task_doc.set_field")
        self.assertEqual(read_task_doc(Path(str(updated["docPath"]))).objective, "new")

    def test_set_field_cannot_repoint_plane_owned_contract_identity(self) -> None:
        self._create()
        for fields in (
            {"seriesContractPath": "tasks/other/series-contract.md"},
            {
                "enclosures": [
                    {"leafId": "other", "enclosurePath": "tasks/other/series-contract.md"}
                ]
            },
        ):
            with self.subTest(fields=fields), self.assertRaises(TaskDocError):
                self._call("set_field", fields=fields)

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
            call=TaskDocCall(dry_run=True),
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
            call=TaskDocCall(dry_run=True),
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
            call=TaskDocCall(dry_run=True),
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

    def test_set_step_updates_only_and_names_the_parent_of_a_bare_substep_id(self) -> None:
        """The L30/L31/L32 defect: a bare substep id must never mint a top-level step.

        The old upsert matched a bare id only at top level, so a call meant for the
        substep ``S1.1`` created a new top-level step titled ``S1.1``, reported success,
        and let ``stepsDone`` rise while the real substep stayed ``pending``. It now
        refuses and names the parent the id actually belongs to.
        """
        created = self._create(
            steps=[
                {
                    "id": "S1",
                    "title": "One",
                    "status": "pending",
                    "substeps": [{"id": "S1.1", "title": "sub", "status": "pending"}],
                }
            ]
        )
        leaf_path = Path(str(created["docPath"]))
        before = leaf_path.read_bytes()

        with self.assertRaises(TaskDocError) as raised:
            self._call("set_step", step={"id": "S1.1", "status": "done"})

        self.assertIn("no top-level step 'S1.1'", str(raised.exception))
        self.assertIn("did you mean parent 'S1'?", str(raised.exception))
        self.assertEqual(leaf_path.read_bytes(), before)
        doc = read_task_doc(leaf_path)
        self.assertEqual([step.id for step in doc.steps], ["S1"])
        self.assertEqual(doc.steps[0].substeps[0].status, "pending")

        updated = self._call("set_step", step={"id": "S1.1", "parent": "S1", "status": "done"})
        doc = read_task_doc(Path(str(updated["docPath"])))
        self.assertEqual([step.id for step in doc.steps], ["S1"])
        self.assertEqual(doc.steps[0].substeps[0].status, "done")

    def test_set_step_names_the_parent_for_a_dotted_child_id(self) -> None:
        """A ``<parent>.<child>`` id whose child lives under that parent names both halves."""
        self._create(
            steps=[
                {
                    "id": "S1",
                    "title": "One",
                    "substeps": [{"id": "a", "title": "A", "status": "pending"}],
                }
            ]
        )

        with self.assertRaises(TaskDocError) as raised:
            self._call("set_step", step={"id": "S1.a", "status": "done"})

        self.assertIn("did you mean parent 'S1'", str(raised.exception))
        self.assertIn("step.id 'a'", str(raised.exception))

    def test_set_step_refuses_an_ambiguous_id(self) -> None:
        created = self._create(steps=[{"id": "S1", "title": "One"}])
        leaf_path = Path(str(created["docPath"]))
        data = read_task_doc(leaf_path).model_dump(by_alias=True)
        data["steps"].append(dict(data["steps"][0]))
        write_task_doc(leaf_path.parent, TaskDocument.model_validate(data))

        with self.assertRaises(TaskDocError) as raised:
            self._call("set_step", step={"id": "S1", "status": "done"})

        self.assertIn("top-level step 'S1' is ambiguous", str(raised.exception))

    def test_add_step_creates_one_unit_and_refuses_an_existing_id(self) -> None:
        created = self._create(steps=[{"id": "S1", "title": "One"}])
        leaf_path = Path(str(created["docPath"]))

        added = self._call("add_step", step={"id": "S2", "title": "Two", "status": "inProgress"})
        doc = read_task_doc(Path(str(added["docPath"])))
        self.assertEqual(
            [(step.id, step.status) for step in doc.steps],
            [("S1", "pending"), ("S2", "inProgress")],
        )

        for step, expected in (
            ({"id": "S2", "title": "duplicate"}, "top-level step 'S2' already exists"),
            ({"id": "S3"}, "add_step requires step.title"),
            ({"id": "S3", "title": "Three", "parent": "NOPE"}, "parent step 'NOPE' not found"),
        ):
            with self.subTest(step=step), self.assertRaises(TaskDocError) as raised:
                self._call("add_step", step=step)
            self.assertIn(expected, str(raised.exception))
        self.assertEqual([step.id for step in read_task_doc(leaf_path).steps], ["S1", "S2"])

        nested = self._call("add_step", step={"id": "C1", "title": "Child", "parent": "S1"})
        doc = read_task_doc(Path(str(nested["docPath"])))
        self.assertEqual([sub.id for sub in doc.steps[0].substeps], ["C1"])
        with self.assertRaises(TaskDocError) as raised:
            self._call("add_step", step={"id": "C1", "title": "Child", "parent": "S1"})
        self.assertIn("substep 'S1'/'C1' already exists", str(raised.exception))

    def test_remove_step_requires_a_reason_and_records_the_removal(self) -> None:
        created = self._create(steps=[{"id": "S1", "title": "One"}, {"id": "S2", "title": "Two"}])
        leaf_path = Path(str(created["docPath"]))
        before = leaf_path.read_bytes()

        for step in ({"id": "S1"}, {"id": "S1", "reason": "   "}):
            with self.subTest(step=step), self.assertRaises(TaskDocError) as raised:
                self._call("remove_step", step=step)
            self.assertIn("remove_step requires a nonblank step.reason", str(raised.exception))
        self.assertEqual(leaf_path.read_bytes(), before)

        result = self._call("remove_step", step={"id": "S2", "reason": "  never existed  "})
        doc = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual([step.id for step in doc.steps], ["S1"])
        self.assertEqual(doc.decisions[-1].decision, "Removed step S2.")
        self.assertEqual(doc.decisions[-1].rationale, "never existed")

        with self.assertRaises(TaskDocError) as raised:
            self._call("remove_step", step={"id": "S2", "reason": "again"})
        self.assertIn("no top-level step 'S2'", str(raised.exception))

    def test_remove_step_deletes_a_done_step_and_repairs_a_completed_document(self) -> None:
        """A reasoned removal may target done work and a Completed document.

        The motivating repair removed four spurious top-level steps from an
        already-``Completed`` leaf document. A Completed document whose checklist still
        carries unresolved units refuses every other mutation (asserted below), so
        without this reasoned exception the only route left would be a full-document
        ``replace`` -- correctly refused as too destructive. The decision entry is what
        substitutes for the guard.
        """
        created = self._create(steps=[{"id": "S1", "title": "done unit", "status": "done"}])
        removed = self._call("remove_step", step={"id": "S1", "reason": "never existed"})
        self.assertEqual(read_task_doc(Path(str(removed["docPath"]))).steps, [])

        leaf_path = Path(str(created["docPath"]))
        data = read_task_doc(leaf_path).model_dump(by_alias=True)
        data["status"] = "Completed"
        data["steps"] = [
            {"id": "S1", "title": "real", "status": "done"},
            {"id": "S1.1", "title": "S1.1", "status": "pending"},
            {"id": "S1.2", "title": "S1.2", "status": "pending"},
        ]
        write_task_doc(leaf_path.parent, TaskDocument.model_validate(data))

        with self.assertRaises(TaskDocError) as raised:
            self._call("set_step", step={"id": "S1", "status": "pending"})
        self.assertIn("unresolved work units", str(raised.exception))

        # Two spurious units: the first removal leaves the other still blocking, so it is
        # exactly this reasoned exception -- not a vacuous no-blockers pass -- that admits it.
        repaired = self._call("remove_step", step={"id": "S1.1", "reason": "spurious junk step"})
        doc = read_task_doc(Path(str(repaired["docPath"])))
        self.assertEqual([step.id for step in doc.steps], ["S1", "S1.2"])
        self.assertEqual(doc.status, "Completed")
        self.assertEqual(doc.decisions[-1].decision, "Removed step S1.1.")
        self.assertEqual(doc.decisions[-1].rationale, "spurious junk step")

        with self.assertRaises(TaskDocError) as raised:
            self._call("remove_step", step={"id": "S1.2"})
        self.assertIn("remove_step requires a nonblank step.reason", str(raised.exception))

        repaired = self._call("remove_step", step={"id": "S1.2", "reason": "spurious junk step"})
        doc = read_task_doc(Path(str(repaired["docPath"])))
        self.assertEqual([step.id for step in doc.steps], ["S1"])
        self.assertEqual(doc.status, "Completed")

    def test_a_top_level_note_persists_instead_of_being_discarded(self) -> None:
        """The top-level key set omitted ``note``, so the caller's explicit field vanished."""
        created = self._create(steps=[{"id": "S1", "title": "One"}])

        result = self._call("set_step", step={"id": "S1", "note": "why this unit changed"})
        doc = read_task_doc(Path(str(result["docPath"])))
        self.assertEqual(doc.steps[0].note, "why this unit changed")
        self.assertIn('"note"', Path(str(created["docPath"])).read_text(encoding="utf-8"))

        added = self._call("add_step", step={"id": "S2", "title": "Two", "note": "created note"})
        doc = read_task_doc(Path(str(added["docPath"])))
        self.assertEqual(doc.steps[1].note, "created note")

    def test_a_top_level_note_is_rendered_into_the_markdown(self) -> None:
        """A note that persists in the JSON but never reaches the ``.md`` is still invisible.

        The rendered document is the human-facing view, so the note suffixes the step's
        checkbox line exactly as a substep's does -- including on a step with no outcome
        to hang that line on, which is why the note joins the condition that draws it.
        """
        self._create(
            steps=[
                {"id": "S1", "title": "Bare", "status": "pending"},
                {"id": "S2", "title": "Ship", "outcome": "ship it", "status": "pending"},
            ]
        )
        self._call("set_step", step={"id": "S1", "note": "bare step note"})
        result = self._call("set_step", step={"id": "S2", "note": "outcome step note"})

        rendered = Path(str(result["renderedPath"])).read_text(encoding="utf-8")
        self.assertIn("- [ ] Bare — bare step note", rendered)
        self.assertIn("- [ ] ship it — outcome step note", rendered)

    def test_removed_read_operations_are_refused_with_file_names(self) -> None:
        created = self._create(
            objective="an objective no read call may drag along",
            steps=[
                {
                    "id": "S1",
                    "title": "One",
                    "status": "done",
                    "note": "top note",
                    "substeps": [
                        {"id": "C1", "title": "Child", "status": "pending", "note": "sub note"}
                    ],
                }
            ],
        )
        leaf_path = Path(str(created["docPath"]))
        markdown_path = Path(str(created["renderedPath"]))
        before = (leaf_path.read_bytes(), markdown_path.read_bytes())

        for operation in ("get", "read_steps"):
            result = self._call(operation)

            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "operation-removed")
            self.assertIn("was removed", str(result["error"]))
            self.assertIn(markdown_path.as_posix(), str(result["error"]))
            self.assertIn(leaf_path.as_posix(), str(result["error"]))
            self.assertIn("file tool", str(result["nextAction"]))
        self.assertEqual((leaf_path.read_bytes(), markdown_path.read_bytes()), before)

    def test_removed_read_refusal_validates_against_the_model_the_tool_advertises(self) -> None:
        """The removal refusal must survive its own declared response contract.

        The refusal carries the document identity plus ``code``/``error`` and
        nothing else new: an undeclared key still fails, so the envelope is
        not widened to smuggle reads back in.
        """
        self._create(
            steps=[
                {
                    "id": "S1",
                    "title": "One",
                    "status": "done",
                    "note": "top note",
                    "substeps": [
                        {"id": "C1", "title": "Child", "status": "pending", "note": "sub note"}
                    ],
                }
            ]
        )
        payload = self._call("read_steps")

        response = finalize_tool_response("task_doc", payload)

        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "operation-removed")
        self.assertNotIn("steps", response)
        # The declaration is the refusal shape, not a relaxed envelope: an
        # undeclared key still fails.
        with self.assertRaises(ValidationError):
            finalize_tool_response("task_doc", {**payload, "undeclaredStepKey": []})

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


class LeafDocMasterLinkBindingTests(ApplicationTests):
    """The restamp half of the start binding, on the unit population.

    A leaf authored before its master's series contract exists carries neither
    ``seriesContractPath`` nor ``enclosures[]``. The restamp used to write only
    ``lifecycleId`` and return no candidate whenever that id already matched, so a
    document missing only its master link was silently skipped and nothing repaired it.
    The end-to-end proof that start binds it lives in
    ``test_leaf_doc_master_link_binding.py`` (integration lane); this is the focused
    decision-table check.
    """

    def _leaf_doc_path(self) -> Path:
        return self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json"

    def _rewrite_leaf_doc(self, **fields: Any) -> None:
        task_root = self.coord / "tasks" / "agents-remember" / "3c-x"
        document = read_task_doc(self._leaf_doc_path()).model_dump(by_alias=True)
        document.update(fields)
        write_task_doc(task_root, TaskDocument.model_validate(document))

    def _plan(self, lifecycle_id: str = "LC-SAME"):
        task_root = self.coord / "tasks" / "agents-remember" / "3c-x"
        return plan_leaf_doc_lifecycle_restamp(task_root, "3C", lifecycle_id)

    def test_binds_only_the_derived_fields_that_are_absent(self) -> None:
        self._create()
        task_root = self.coord / "tasks" / "agents-remember" / "3c-x"
        expected_path = series_contract_path(task_root).as_posix()
        expected_enclosure = leaf_enclosure_path(task_root, "3C").as_posix()
        already_bound = [{"leafId": "3C", "enclosurePath": expected_enclosure}]

        # The reachable pre-contract states, and what a restamp must do with each. A
        # document missing *only* its enclosure binding is not among them: the identity
        # lookup finds a leaf through its own enclosures[] refs, so the series contract
        # path is what survives from a partial write.
        cases = (
            (
                "both derived fields absent",
                {"lifecycleId": "LC-SAME", "seriesContractPath": None, "enclosures": []},
                expected_path,
                already_bound,
            ),
            (
                "only the series contract path absent",
                {"lifecycleId": "LC-SAME", "seriesContractPath": None, "enclosures": already_bound},
                expected_path,
                already_bound,
            ),
        )

        for name, written, expected_series_path, expected_enclosures in cases:
            with self.subTest(case=name):
                self._rewrite_leaf_doc(**written)

                plan = self._plan()

                candidate = plan.candidate
                if candidate is None:
                    self.fail(f"{name}: restamp produced no candidate, so nothing would be written")
                self.assertTrue(plan.changed)
                # Existing bindings are never rewired; only the absent one is filled in.
                self.assertEqual(candidate.seriesContractPath, expected_series_path)
                self.assertEqual(
                    [ref.model_dump() for ref in candidate.enclosures], expected_enclosures
                )

        # A fresh lifecycle still overwrites a stale binding even when both derived fields
        # are already present (a reopened leaf follows the fresh lifecycle).
        self._rewrite_leaf_doc(
            lifecycleId="LC-OLD",
            seriesContractPath=expected_path,
            enclosures=already_bound,
        )
        reopened = self._plan("LC-FRESH")
        assert reopened.candidate is not None
        self.assertEqual(reopened.candidate.lifecycleId, "LC-FRESH")

        # The one exact no-op: everything bound and current.
        self._rewrite_leaf_doc(
            lifecycleId="LC-FRESH",
            seriesContractPath=expected_path,
            enclosures=already_bound,
        )
        untouched = self._plan("LC-FRESH")
        self.assertFalse(untouched.changed)
        self.assertIsNone(untouched.candidate)


class ApplicationWriteAnswers(ApplicationTests):
    """MIK-R111 rule 2, evidence items 3, 11, 12: what writes answer."""

    def _answer(
        self, _operation: str, arguments: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        with tool_call_context("task_doc", arguments, config=self.cfg):
            answer = complete_tool_response("task_doc", dict(payload), config=self.cfg)
        answer["_measured_tokens"] = count_response_tokens(dict(answer))
        return answer

    def _deterministic_tokens(self, answer: dict[str, Any]) -> int:
        """Count one answer with random segments fixed (evidence item 3).

        The framework names the detail file ``<12-digit stamp>-<tool>-<8 hex>.json``
        and the test root is a random ``tmpXXXXXXXX``. All three vary run to run, and
        the product counter (tiktoken BPE) counts their characters differently,
        so a bound assertion on the raw answer is flaky by a wide margin. The
        replacements below fix the stamp and hex segments to equal-length runs
        and stand the coordination root at a fixed length of 40 characters: the
        bound is for the shape of the answer in the ordinary case, and an
        installation's path length is not the tool's to choose.
        """

        coord = str(self.coord)
        fixed = {}
        for key, value in answer.items():
            if key == "_measured_tokens":
                continue
            if isinstance(value, str):
                text = value.replace(coord, "C" * 40)
                text = re.sub(r"\d{12}", "0" * 12, text)
                text = re.sub(r"[0-9a-f]{8}", "0" * 8, text)
            else:
                text = value
            fixed[key] = text
        return count_response_tokens(fixed)

    def test_ordinary_writes_answer_within_100_tokens(self) -> None:
        self._create(
            steps=[{"id": "S1", "title": "One", "status": "pending"}],
        )
        calls: list[tuple[str, dict[str, Any], str]] = [
            ("set_step", {"step": {"id": "S1", "status": "done"}}, "operation"),
            (
                "append_decision",
                {"decision": {"at": "2026-10-08", "decision": "d", "rationale": "r"}},
                "operation",
            ),
            ("set_field", {"fields": {"title": "Renamed"}}, "operation"),
            ("set_status", {"fields": {"status": "inProgress"}}, "operation"),
        ]
        for operation, kwargs, _ in calls:
            payload = self._call(operation, **kwargs)
            answer = self._answer(operation, {"operation": operation}, payload)
            tokens = self._deterministic_tokens(answer)
            self.assertLessEqual(
                tokens,
                100,
                f"{operation} answer is {tokens} tokens: {sorted(answer)}",
            )
            self.assertTrue(answer["ok"])
            self.assertIn("updated", answer)
            self.assertNotIn("masterSync", answer)
            self.assertNotIn("renderedPath", answer)
            self.assertNotIn("reviewState", answer)
            self.assertNotIn("projectionEffects", answer)

    def test_create_names_only_the_rendered_file(self) -> None:
        created = self._create()
        answer = self._answer("create", {"operation": "create"}, created)
        self.assertTrue(answer["ok"])
        self.assertIn("created", str(answer["updated"]))
        self.assertEqual(answer["renderedPath"], created["renderedPath"])
        self.assertLessEqual(self._deterministic_tokens(answer), 100, str(sorted(answer)))

    def test_begin_review_carries_review_state(self) -> None:
        self._create()
        payload = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation="begin_review",
            edit=TaskDocEdit(review={}),
            call=TaskDocCall(),
        )
        self.assertTrue(payload["ok"])
        answer = self._answer("begin_review", {"operation": "begin_review"}, payload)
        self.assertIn("reviewState", answer)
        self.assertIn("round", str(answer["updated"]))

    def test_dry_run_answers_within_bound_and_nothing_of_the_master(self) -> None:
        master_title = "Master of unusual size for dry-run evidence"
        self._create_parent_master(title=master_title, objective="objective\n" + "x" * 12000)
        self._create(
            master="task.md",
            steps=[{"id": "S1", "title": "One", "status": "pending"}],
        )
        payload = self._call("set_field", fields={"title": "Renamed"}, dry_run=True)
        answer = self._answer("set_field", {"operation": "set_field", "dry_run": True}, payload)
        self.assertLessEqual(answer["_measured_tokens"], 1500, str(sorted(answer)))
        self.assertIn("wouldLose", answer)
        self.assertIn("diff", answer)
        self.assertIn("log", answer)
        self.assertNotIn("rendered", answer)
        self.assertNotIn("masterSync", answer)
        self.assertNotIn(master_title, str(answer))

    def test_section_body_file_matches_inline_and_names_sha256(self) -> None:
        body = "section body line\n" * 750
        assert len(body) >= 12000
        section_file = Path(self.coord) / "section-body.md"
        section_file.write_text(body, encoding="utf-8")
        digest = hashlib.sha256(body.encode()).hexdigest()

        target = TaskDocTarget(repo_id="agents-remember", task_name="3c-x")
        self._create_parent_master(
            sections=[{"heading": "Notes", "kind": "freeform", "body": "old"}],
        )
        by_file = task_doc_tool(
            self.cfg,
            target,
            operation="set_section",
            edit=TaskDocEdit(
                section={"heading": "Notes", "kind": "freeform"},
                section_body_file=section_file.as_posix(),
            ),
            call=TaskDocCall(),
        )
        self.assertTrue(by_file["ok"])
        self.assertEqual(by_file["inputFile"], section_file.as_posix())
        self.assertEqual(by_file["inputFileSha256"], digest)

        task_root = self.coord / "tasks" / "agents-remember" / "3c-x"
        file_bytes = (task_root / "task.json").read_bytes()

        by_inline = task_doc_tool(
            self.cfg,
            target,
            operation="set_section",
            edit=TaskDocEdit(section={"heading": "Notes", "kind": "freeform", "body": body}),
            call=TaskDocCall(),
        )
        self.assertTrue(by_inline["ok"])
        self.assertEqual(
            (task_root / "task.json").read_bytes(), file_bytes, "file and inline differ"
        )

        with self.assertRaises(TaskDocError) as raised:
            task_doc_tool(
                self.cfg,
                target,
                operation="set_section",
                edit=TaskDocEdit(
                    section={"heading": "Notes", "kind": "freeform", "body": body},
                    section_body_file=section_file.as_posix(),
                ),
                call=TaskDocCall(),
            )
        self.assertIn("not both", str(raised.exception))

        with self.assertRaises(TaskDocError) as raised:
            task_doc_tool(
                self.cfg,
                target,
                operation="set_section",
                edit=TaskDocEdit(section_body_file=(self.coord / "no-such-file.md").as_posix()),
                call=TaskDocCall(),
            )
        self.assertIn("no regular file", str(raised.exception))


class TaskDocReadRemovalSweepTests(unittest.TestCase):
    """MIK-R111 rule 6, evidence item 7: no role text, skill or capsule tells a read.

    ``task_doc`` writes and does not read: no instruction may call ``get`` or
    ``read_steps``, and the deleted read helpers stay deleted. Documentary
    mentions of the removal (the refusal itself, the reference) are not
    instructions and are scoped out by shape, not by path.
    """

    ROOTS = (
        "mcp/src/agents_remember",
        "mcp/src/agents_remember/package_data/runtime/skills",
        "skills",
        "docs",
        ".claude/skills",
        ".codex/skills",
        ".cursor/skills",
        ".github-vscode/skills",
        ".hermes/skills",
        ".openclaw/workspace/skills",
        ".pi/skills",
        ".agents/skills",
    )

    # Call shapes: an instruction that reads through task_doc.
    CALL_SHAPES = (
        re.compile(r"""operation\s*=\s*["']?get\b"""),
        re.compile(r'''"operation":\s*"get"'''),
        re.compile(r"""task_doc\.get\b"""),
        re.compile(r"""task_doc\s+get\b"""),
        re.compile(
            r"read the supplied task[^.]{0,300}taskDocReadArgs[^.]{0,150}"
            r"on\s*`agents-remember-task`",
            re.IGNORECASE,
        ),
        re.compile(r"`task_context`\s+(?:requires|carries)\s+both the canonical task"),
    )

    def _files(self) -> list[Path]:
        root = Path(__file__).resolve().parent.parent.parent
        found: list[Path] = []
        for rel in self.ROOTS:
            base = root / rel
            if not base.exists():
                continue
            if base.is_file():
                found.append(base)
            else:
                found.extend(
                    path
                    for path in sorted(base.rglob("*"))
                    if path.is_file() and path.suffix in {".py", ".md"}
                )
        return found

    def test_no_instruction_calls_task_doc_get(self) -> None:
        hits = []
        for path in self._files():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for shape in self.CALL_SHAPES:
                for match in shape.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    hits.append(f"{path}:{line}: {match.group(0)}")
        self.assertEqual(hits, [])

    def test_read_steps_survives_only_where_the_removal_is_named(self) -> None:
        hits = []
        for path in self._files():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, start=1):
                if "read_steps" in line and "emov" not in line.lower():
                    hits.append(f"{path}:{number}")
        self.assertEqual(hits, [])

    def test_deleted_read_helpers_stay_deleted(self) -> None:
        hits = []
        for path in self._files():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for name in ("linkage_facts_for_get", "step_payloads"):
                if name in text:
                    hits.append(f"{path}: {name}")
        self.assertEqual(hits, [])
