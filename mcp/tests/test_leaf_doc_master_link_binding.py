"""The leaf document's derived master link: authored late, bound at start, never dropped.

Planning a master necessarily authors its leaf documents *before* the master's first
``worktree_start`` bootstraps the series contract. At that moment ``task_doc`` cannot
stamp the two derived fields (``seriesContractPath`` and ``enclosures[]``), so start is
the only place they can ever be bound. This module proves the real sequence end to end —
author the master and its leaf through ``task_doc``, start the leaf, read the document
back — and pins the two halves of the fix together:

* start binds a missing master link without disturbing anything else in the document, and
* the authoring plane refuses a leaf under a task root with no master document at all,
  because nothing would ever bind that link, while the planning case (master document
  present, series contract not yet bootstrapped) still succeeds.

The restamp's own decision table is covered on the unit population in
``test_task_document_application_1.py``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.task_resolver import leaf_enclosure_path, series_contract_path
from test_worktree_support import git, init_repo, initialized_memory_repo

MASTER_ID = "260624_master"
LEAF_ID = "15_leaf"
LEAF_DOC_ID = "15"


class LeafDocMasterLinkBindingTests(unittest.TestCase):
    """One scratch coordination root, code repo, and task tree per case."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.code_repo = self.workspace / "repo-a"
        code_base = init_repo(self.code_repo, "main")
        git(self.code_repo, "branch", "super", "main")
        self.coordination_root = self.workspace / "ar-coordination"
        memory_root = self.coordination_root / "memory-repos" / "ar-repo-a"
        initialized_memory_repo(memory_root, "repo-a", "main", "main", code_base)
        git(memory_root, "branch", "super", "main")
        self.task_root = self.coordination_root / "tasks" / "repo-a" / MASTER_ID
        self.task_root.mkdir(parents=True, exist_ok=True)
        self.master_path = self.task_root / "task.json"
        self.leaf_doc_path = self.task_root / f"{LEAF_ID}.json"
        self.config = McpRuntimeConfig(
            config_path=self.coordination_root / "mcp.settings.json",
            coordination_root=self.coordination_root,
            workspace_root=self.workspace,
            transcript_root=self.coordination_root / "logs",
            repositories={"repo-a": RepositoryScope(repo_id="repo-a", path=self.code_repo)},
        )

    # --- fixtures ----------------------------------------------------------

    def _task_doc(self, operation: str, **kw):
        return task_doc_tool(
            self.config,
            TaskDocTarget(repo_id="repo-a", task_name=MASTER_ID),
            operation=operation,
            **kw,
        )

    def _author_master(self) -> None:
        self._task_doc(
            "create",
            edit=TaskDocEdit(
                fields={
                    "id": "master",
                    "slug": "task",
                    "title": "Master Series",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": "repo-a",
                    "createdAt": "2026-06-24T02:00",
                    "executionNature": "atomic",
                }
            ),
        )

    def _author_leaf(self, **over: object) -> dict[str, object]:
        fields: dict[str, object] = {
            "id": LEAF_DOC_ID,
            "slug": LEAF_ID,
            "title": "Leaf task",
            "kind": "subTask",
            "status": "planning",
            "repo": "repo-a",
            "createdAt": "2026-06-24T02:01",
            "master": "task.md",
            "steps": [{"id": "S1", "title": "do the thing", "status": "pending"}],
        }
        fields.update(over)
        return self._task_doc("create", edit=TaskDocEdit(fields=fields))

    def _write_leaf_document(self, **over: object) -> TaskDocument:
        """Author the pre-contract document state directly, without the task_doc plane.

        The master gains its live row for this leaf first, because that row is what start
        proves the leaf's identity against; what is deliberately withheld here is the
        series contract and the two derived fields, not the master's own index entry.
        """

        master = read_task_doc(self.master_path).model_dump(by_alias=True)
        master["subTasks"] = [
            {
                "number": LEAF_DOC_ID,
                "name": "Leaf task",
                "file": f"{LEAF_ID}.md",
                "status": "planning",
            }
        ]
        write_task_doc(self.task_root, TaskDocument.model_validate(master))
        payload: dict[str, object] = {
            "id": LEAF_DOC_ID,
            "slug": LEAF_ID,
            "title": "Leaf task",
            "kind": "subTask",
            "status": "planning",
            "repo": "repo-a",
            "createdAt": "2026-06-24T02:01",
            "master": "task.md",
            "steps": [{"id": "S1", "title": "do the thing", "status": "pending"}],
        }
        payload.update(over)
        write_task_doc(self.task_root, TaskDocument.model_validate(payload))
        return read_task_doc(self.leaf_doc_path)

    def _start_leaf(self, lifecycle_id: str = "LC-LEAF"):
        return worktree_manager.start_result(
            worktree_manager.WorktreeArgs(
                code_repository_name="repo-a",
                workspace_root=self.workspace,
                coordination_root=self.coordination_root,
                code_repository_root=self.code_repo,
                topology="external",
                task_name=MASTER_ID,
                worktree_name=LEAF_ID,
                leaf_id=LEAF_ID,
                workflow_kind="light-task",
                memory_mode="disabled",
                skip_provider_setup=True,
                lifecycle_id=lifecycle_id,
            )
        )

    # --- the real sequence -------------------------------------------------

    def test_leaf_authored_before_its_series_contract_gets_its_link_at_start(self) -> None:
        """The exact sequence that produced this master's first leaf with no master link.

        The master and its leaf are authored through ``task_doc`` while no
        ``series-contract.md`` exists anywhere in the task root, which is the normal
        planning order. Then the leaf is started, and the document it left behind must
        carry the series contract path the authoring plane could not know yet.
        """

        self._author_master()
        created = self._author_leaf()
        authored = read_task_doc(Path(str(created["docPath"])))

        # Precondition, asserted rather than assumed: authoring could not stamp either
        # derived field, which is why start has to bind them.
        self.assertFalse(series_contract_path(self.task_root).exists())
        self.assertIsNone(authored.seriesContractPath)
        self.assertEqual(authored.enclosures, [])

        result = self._start_leaf()

        self.assertEqual(result.returncode, 0, result.payload)
        leaf_contract = leaf_enclosure_path(self.task_root, LEAF_DOC_ID)
        self.assertTrue(leaf_contract.exists())
        started = read_task_doc(self.leaf_doc_path)
        self.assertEqual(
            started.seriesContractPath, series_contract_path(self.task_root).as_posix()
        )
        self.assertEqual(
            [ref.model_dump() for ref in started.enclosures],
            [{"leafId": LEAF_DOC_ID, "enclosurePath": leaf_contract.as_posix()}],
        )
        self.assertEqual(started.lifecycleId, "LC-LEAF")

    def test_an_existing_damaged_document_is_repaired_by_its_next_start(self) -> None:
        """A document that predates the contract is repaired by start, not by a script.

        This is the already-damaged case: the leaf document exists with no master link
        while the rest of it (title, objective, requirements, steps) is authored content,
        and start must add the link without rewriting any of it.
        """

        self._author_master()
        self._write_leaf_document(
            lifecycleId="LC-OLD",
            objective="keep this objective",
            requirements=["keep this requirement"],
        )

        result = self._start_leaf(lifecycle_id="LC-REPAIR")

        self.assertEqual(result.returncode, 0, result.payload)
        repaired = read_task_doc(self.leaf_doc_path)
        self.assertEqual(
            repaired.seriesContractPath, series_contract_path(self.task_root).as_posix()
        )
        self.assertEqual(
            [ref.model_dump() for ref in repaired.enclosures],
            [
                {
                    "leafId": LEAF_DOC_ID,
                    "enclosurePath": leaf_enclosure_path(self.task_root, LEAF_DOC_ID).as_posix(),
                }
            ],
        )
        # The repair binds the link (and follows the fresh lifecycle) and changes nothing else.
        self.assertEqual(repaired.lifecycleId, "LC-REPAIR")
        self.assertEqual(repaired.objective, "keep this objective")
        self.assertEqual(list(repaired.requirements), ["keep this requirement"])
        self.assertEqual([(step.id, step.status) for step in repaired.steps], [("S1", "pending")])
        self.assertEqual(repaired.title, "Leaf task")

    def test_authoring_a_leaf_with_no_master_document_is_refused_with_its_remedy(self) -> None:
        """Fail closed: a leaf whose master link nothing would ever bind is refused.

        The task root has no series contract and no master document, so neither the
        authoring plane nor any later start can bind the derived fields.
        """

        with self.assertRaises(TaskDocError) as raised:
            self._author_leaf()

        message = str(raised.exception)
        self.assertIn("would carry no master link", message)
        self.assertIn("seriesContractPath", message)
        self.assertIn(self.master_path.as_posix(), message)
        self.assertIn("Author the master document first", message)
        self.assertFalse(self.leaf_doc_path.exists())

    def test_authoring_a_master_and_its_leaves_before_any_start_still_succeeds(self) -> None:
        """The planning flow stays usable: master first, then its leaves, no start needed."""

        self._author_master()
        first = self._author_leaf()
        second = self._author_leaf(id="16", slug="16_second", title="Second leaf")

        master = read_task_doc(self.master_path)
        self.assertEqual(master.kind, "master")
        self.assertEqual([row.number for row in master.subTasks], [LEAF_DOC_ID, "16"])
        for created in (first, second):
            document = read_task_doc(Path(str(created["docPath"])))
            # Still unstamped — and that is the documented, repairable state: the first
            # start binds both fields, which the first case above proves.
            self.assertIsNone(document.seriesContractPath)
            self.assertEqual(document.enclosures, [])
            self.assertIsNone(document.lifecycleId)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
