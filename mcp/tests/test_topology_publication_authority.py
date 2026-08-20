"""Production task-publication forcing for protected branch authority."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from agents_remember.application.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.worktree_contract import write_contract
from test_closeout_queue import MASTER_A, MASTER_B, REPO, QueueFixture
from test_worktree_support import git


def _config(fixture: QueueFixture) -> McpRuntimeConfig:
    scope = RepositoryScope(REPO, fixture.code, fixture.memory)
    return cast(
        McpRuntimeConfig,
        SimpleNamespace(
            coordination_root=fixture.coord,
            repositories={REPO: scope},
        ),
    )


class TopologyPublicationAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_preview_and_apply_refuse_promoting_a_live_leaf_work_branch(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        config = _config(fixture)
        contract = fixture.contracts[MASTER_A]
        before = (fixture.tasks / "sprint" / "task.json").read_bytes()
        work_tip = git(fixture.code, "rev-parse", contract.code_work_branch)

        for dry_run in (True, False):
            with (
                self.subTest(dry_run=dry_run),
                self.assertRaisesRegex(
                    TaskDocError,
                    "live leaf workbench",
                ),
            ):
                task_doc_tool(
                    config,
                    TaskDocTarget(repo_id=REPO, task_name="sprint"),
                    operation="set_field",
                    edit=TaskDocEdit(fields={"integrationBranch": contract.code_work_branch}),
                    call=TaskDocCall(dry_run=dry_run),
                )

        self.assertEqual((fixture.tasks / "sprint" / "task.json").read_bytes(), before)
        self.assertEqual(git(fixture.code, "rev-parse", contract.code_work_branch), work_tip)

    def test_atomic_nature_cannot_be_removed_while_its_series_is_live(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), atomic_b=True)
        config = _config(fixture)
        before = (fixture.tasks / "master-b" / "task.json").read_bytes()

        for dry_run in (True, False):
            with (
                self.subTest(dry_run=dry_run),
                self.assertRaisesRegex(
                    TaskDocError,
                    "retains a live series contract",
                ),
            ):
                task_doc_tool(
                    config,
                    TaskDocTarget(repo_id=REPO, task_name="master-b"),
                    operation="set_field",
                    edit=TaskDocEdit(fields={"executionNature": "organizational"}),
                    call=TaskDocCall(dry_run=dry_run),
                )

        self.assertEqual((fixture.tasks / "master-b" / "task.json").read_bytes(), before)

    def test_atomic_master_cannot_be_detached_from_its_nondefault_sprint_source(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), atomic_b=True)
        config = _config(fixture)
        sprint_path = fixture.tasks / "sprint" / "task.json"
        before = sprint_path.read_bytes()
        data = read_task_doc(sprint_path).model_dump(by_alias=True)
        data["orchestrates"] = ["master-a"]
        data["executionGraph"] = {
            "nodes": [MASTER_A.model_dump(mode="json")],
            "edges": [],
        }

        for dry_run in (True, False):
            with (
                self.subTest(dry_run=dry_run),
                self.assertRaisesRegex(
                    TaskDocError,
                    "standalone atomic series code source is not the repository default",
                ),
            ):
                task_doc_tool(
                    config,
                    TaskDocTarget(repo_id=REPO, task_name="sprint"),
                    operation="replace",
                    edit=TaskDocEdit(fields=data),
                    call=TaskDocCall(dry_run=dry_run),
                )

        self.assertEqual(sprint_path.read_bytes(), before)

    def test_organizational_master_with_live_leaf_cannot_be_detached_from_shared_super(
        self,
    ) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        config = _config(fixture)
        sprint_path = fixture.tasks / "sprint" / "task.json"
        before = sprint_path.read_bytes()
        data = read_task_doc(sprint_path).model_dump(by_alias=True)
        data["orchestrates"] = ["master-b"]
        data["executionGraph"] = {
            "nodes": [MASTER_B.model_dump(mode="json")],
            "edges": [],
        }

        for dry_run in (True, False):
            with (
                self.subTest(dry_run=dry_run),
                self.assertRaisesRegex(
                    TaskDocError,
                    "live leaf would lose its exact owning master/sprint authority",
                ),
            ):
                task_doc_tool(
                    config,
                    TaskDocTarget(repo_id=REPO, task_name="sprint"),
                    operation="replace",
                    edit=TaskDocEdit(fields=data),
                    call=TaskDocCall(dry_run=dry_run),
                )

        self.assertEqual(sprint_path.read_bytes(), before)

    def test_live_leaf_document_must_remain_in_its_exact_owning_master_root(self) -> None:
        for name, leaf_file, escape in (
            ("sibling-master", "../master-b/leaf-b.md", False),
            ("symlink-outside-repository", "escape/leaf-a.md", True),
        ):
            with self.subTest(name=name):
                fixture = QueueFixture(Path(self.temp.name) / name)
                config = _config(fixture)
                master_path = fixture.tasks / "master-a" / "task.json"
                master = read_task_doc(master_path)
                if escape:
                    outside = fixture.coord / "outside-task-tree"
                    outside.mkdir()
                    (master_path.parent / "escape").symlink_to(outside, target_is_directory=True)
                changed = master.model_copy(
                    update={
                        "subTasks": [
                            row.model_copy(update={"file": leaf_file})
                            if row.number == "LEAF-A"
                            else row
                            for row in master.subTasks
                        ]
                    }
                )
                write_task_doc(master_path.parent, changed)
                sprint_path = fixture.tasks / "sprint" / "task.json"
                sprint_before = sprint_path.read_bytes()
                contract = fixture.contracts[MASTER_A]
                contract_before = contract.contract_path.read_bytes()
                reason = "owning master task root" if not escape else "escapes its repository"

                for dry_run in (True, False):
                    with (
                        self.subTest(dry_run=dry_run),
                        self.assertRaisesRegex(TaskDocError, reason),
                    ):
                        task_doc_tool(
                            config,
                            TaskDocTarget(repo_id=REPO, task_name="sprint"),
                            operation="set_field",
                            edit=TaskDocEdit(fields={"statusNote": "authority probe"}),
                            call=TaskDocCall(dry_run=dry_run),
                        )

                self.assertEqual(sprint_path.read_bytes(), sprint_before)
                self.assertEqual(contract.contract_path.read_bytes(), contract_before)

    def test_new_leaf_override_must_declare_the_target_repository(self) -> None:
        fixture = QueueFixture(Path(self.temp.name) / "foreign-leaf")
        config = _config(fixture)
        leaf_path = fixture.tasks / "master-a" / "leaf-a.json"
        leaf = read_task_doc(leaf_path)
        markdown_path = leaf_path.with_suffix(".md")
        leaf_path.unlink()
        markdown_path.unlink()
        master_path = fixture.tasks / "master-a" / "task.json"
        master_before = master_path.read_bytes()
        contract = fixture.contracts[MASTER_A]
        contract_before = contract.contract_path.read_bytes()
        fields = leaf.model_copy(update={"repo": "foreign-repository"}).model_dump(by_alias=True)
        fields.pop("routeReview", None)

        for dry_run in (True, False):
            with (
                self.subTest(dry_run=dry_run),
                self.assertRaisesRegex(TaskDocError, "declares repo 'foreign-repository'"),
            ):
                task_doc_tool(
                    config,
                    TaskDocTarget(repo_id=REPO, task_name="master-a", slug="leaf-a"),
                    operation="create",
                    edit=TaskDocEdit(fields=fields),
                    call=TaskDocCall(dry_run=dry_run),
                )

        self.assertFalse(leaf_path.exists())
        self.assertFalse(markdown_path.exists())
        self.assertEqual(master_path.read_bytes(), master_before)
        self.assertEqual(contract.contract_path.read_bytes(), contract_before)

    def test_new_atomic_task_refuses_an_orphan_preexisting_target_ref(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        config = _config(fixture)
        git(fixture.code, "branch", "ar/orphan", "main")
        git(fixture.memory, "branch", "ar/orphan", "main")
        document = TaskDocument.model_validate(
            {
                "id": "ORPHAN",
                "slug": "orphan",
                "title": "Orphan atomic master",
                "kind": "master",
                "status": "planning",
                "repo": REPO,
                "createdAt": "2026-08-15T00:00:00+00:00",
                "executionNature": "atomic",
            }
        )

        for dry_run in (True, False):
            with (
                self.subTest(dry_run=dry_run),
                self.assertRaisesRegex(
                    TaskDocError,
                    "already exists without its exact task-owned series contract",
                ),
            ):
                task_doc_tool(
                    config,
                    TaskDocTarget(repo_id=REPO, task_name="orphan"),
                    operation="create",
                    edit=TaskDocEdit(fields=document.model_dump(by_alias=True)),
                    call=TaskDocCall(dry_run=dry_run),
                )

        self.assertFalse((fixture.tasks / "orphan" / "task.json").exists())
        self.assertEqual(
            git(fixture.code, "rev-parse", "ar/orphan"),
            git(fixture.code, "rev-parse", "main"),
        )

    def test_cleaned_atomic_leaf_row_cannot_be_removed_before_series_closeout(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), atomic_b=True)
        config = _config(fixture)
        contract = replace(fixture.contracts[MASTER_B], cleanup="completed")
        write_contract(contract.contract_path, contract)
        master_path = fixture.tasks / "master-b" / "task.json"
        master = read_task_doc(master_path)
        completed_master = master.model_copy(
            update={
                "subTasks": [
                    row.model_copy(update={"status": "Completed"}) for row in master.subTasks
                ]
            }
        )
        write_task_doc(master_path.parent, completed_master)
        leaf_path = fixture.tasks / "master-b" / "leaf-b.json"
        leaf = read_task_doc(leaf_path)
        write_task_doc(leaf_path.parent, leaf.model_copy(update={"status": "Completed"}))
        before = master_path.read_bytes()

        for dry_run in (True, False):
            with (
                self.subTest(dry_run=dry_run),
                self.assertRaisesRegex(
                    TaskDocError,
                    "live leaf is not declared by one exact owning-master row",
                ),
            ):
                task_doc_tool(
                    config,
                    TaskDocTarget(repo_id=REPO, task_name="master-b"),
                    operation="remove_subtask",
                    edit=TaskDocEdit(subtask={"number": "LEAF-B", "keep_file": True}),
                    call=TaskDocCall(dry_run=dry_run),
                )

        self.assertEqual(master_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
