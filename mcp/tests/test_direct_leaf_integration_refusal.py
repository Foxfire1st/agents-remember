"""MIK-R94 rule 11: integration while a direct leaf is in work names that leaf.

A focused unit over ``_require_clean_branch_checkout``: with a granted,
started direct leaf of the same master, the dirty-checkout refusal names it;
without one the refusal reads exactly as before.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents_remember.kernel.direct_execution_grants import (
    DirectExecutionGrant,
    grant_path_for,
    write_grant_file,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SubTaskRef, TaskDocument, write_task_doc
from agents_remember.worktrees.integration.integration_ref_transaction import (
    _require_clean_branch_checkout,
)
from test_worktree_support import git, init_repo

REPO = "repo-a"
MASTER = "master"
DIRECT_ID = "TEST-DIRECT-A"
DIRECT_SLUG = "leaf-a"
OTHER_ID = "TEST-OTHER-B"


def _fixture(root: Path) -> SimpleNamespace:
    coord = root / "coord"
    task_root = coord / "tasks" / REPO / MASTER
    task_root.mkdir(parents=True)
    master = TaskDocument.model_validate(
        {
            "id": "TEST-MASTER",
            "slug": MASTER,
            "title": "Master",
            "kind": "master",
            "status": "inProgress",
            "repo": REPO,
            "createdAt": "2026-10-06T00:00:00+00:00",
            "executionNature": "atomic",
            "subTasks": [
                SubTaskRef(
                    number=DIRECT_ID,
                    name="Direct leaf",
                    file=f"{DIRECT_SLUG}.md",
                    status="inProgress",
                ).model_dump(mode="json"),
                SubTaskRef(
                    number=OTHER_ID,
                    name="Other leaf",
                    file="leaf-b.md",
                    status="inProgress",
                ).model_dump(mode="json"),
            ],
        }
    )
    write_task_doc(task_root, master)
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": DIRECT_ID,
                "slug": DIRECT_SLUG,
                "title": "Direct leaf",
                "kind": "subTask",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-06T00:00:00+00:00",
                "objective": "Work directly on the line.",
                "requirements": ["The fixture leaf holds the checkout."],
            }
        ),
    )
    code = root / "code"
    init_repo(code, "main")
    (code / "file.txt").write_text("code\n", encoding="utf-8")
    git(code, "add", ".")
    git(code, "commit", "-m", "base")
    git(code, "checkout", "-b", "ar/master", "main")
    head = git(code, "rev-parse", "HEAD")
    direct_ref = TaskDocumentRef(repository=REPO, path=f"{MASTER}/{DIRECT_SLUG}.json")
    write_grant_file(
        grant_path_for(coord, direct_ref),
        DirectExecutionGrant(
            leaf=direct_ref,
            granted_by="architect",
            granted_at="2026-10-06T00:00:00+00:00",
            granter_agent_id="fixture",
        ),
    )
    contract = SimpleNamespace(
        coordination_root=coord,
        task_root=task_root,
        repo_name=REPO,
        leaf_id=OTHER_ID,
    )
    return SimpleNamespace(
        coord=coord, code=code, head=head, direct_ref=direct_ref, contract=contract
    )


class DirectLeafIntegrationRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = _fixture(Path(self._tmp.name))

    def test_dirty_checkout_names_the_direct_leaf_in_work(self) -> None:
        code = self.fixture.code
        (code / "direct-change.txt").write_text("direct work\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "is not clean") as raised:
            _require_clean_branch_checkout(
                code, "ar/master", self.fixture.head, contract=self.fixture.contract
            )
        self.assertIn(
            f"the direct leaf {REPO}:{MASTER}/{DIRECT_SLUG}.json of this master",
            str(raised.exception),
        )

    def test_clean_checkout_integrates(self) -> None:
        _require_clean_branch_checkout(
            self.fixture.code,
            "ar/master",
            self.fixture.head,
            contract=self.fixture.contract,
        )

    def test_dirty_checkout_without_grant_reads_as_before(self) -> None:
        code = self.fixture.code
        (code / "direct-change.txt").write_text("direct work\n", encoding="utf-8")
        grant_path_for(self.fixture.coord, self.fixture.direct_ref).unlink()
        with self.assertRaisesRegex(RuntimeError, "is not clean") as raised:
            _require_clean_branch_checkout(
                code, "ar/master", self.fixture.head, contract=self.fixture.contract
            )
        self.assertNotIn("direct leaf", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
