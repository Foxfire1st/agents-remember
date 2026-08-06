from __future__ import annotations

from pathlib import Path

from agents_remember.application.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from pydantic import ValidationError
from test_task_document import ApplicationTests, _doc


class ApplicationTests2(ApplicationTests):
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
