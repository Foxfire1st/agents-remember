from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.application.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.mcp.tools import task_doc_payload
from agents_remember.mcp.tools.base import PUBLIC_TOOLS
from agents_remember.models.task_doc import TaskDocResponse
from agents_remember.models.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from agents_remember.observer.ambient import reset_ambient
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.leaf_doc import TerminalLeafResolutionError, resolve_terminal_leaf_doc
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from test_task_document import _config, _doc, _master


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
                    "repo": "agents-remember",
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

    def test_remove_subtask_tolerates_one_already_missing_leaf_artifact(self) -> None:
        self._create()
        leaf_json, leaf_md = self._author_leaf()
        self._complete_row("1")
        leaf_md.unlink()
        result = self._op("remove_subtask", subtask={"number": "1"})
        self.assertFalse(leaf_json.exists())
        self.assertFalse(leaf_md.exists())
        self.assertIn(leaf_json.as_posix(), result["deletedFiles"])
        self.assertNotIn(leaf_md.as_posix(), result["deletedFiles"])
        self.assertEqual(read_task_doc(Path(str(result["docPath"]))).subTasks, [])

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
