"""MIK-R94 rule 12: a direct leaf has no enclosure of its own.

``role_start`` preparation gives a direct leaf's roles the checkouts of the
master's branch (bound by the leaf document and the series contract) and
creates no leaf enclosure; ``worktree_start`` for a direct leaf refuses; task
scoping and revival admit the direct binding through the grant.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.application.task_scoped_mcp import task_scoped_mcp_config_for_task
from agents_remember.application.worktree_tool_requests import TaskIdentity
from agents_remember.application.worktree_tools import (
    _direct_leaf_start_refusal,
    worktree_start_tool,
)
from agents_remember.cli import role_launch_workspace
from agents_remember.cli.role_launch_preparation import _verify_leaf_revival_scope
from agents_remember.cli.role_launch_workspace import _resolve_workspace
from agents_remember.errors import RolePreparationError
from agents_remember.kernel.direct_execution_grants import (
    DirectExecutionGrant,
    grant_path_for,
    write_grant_file,
)
from agents_remember.kernel.primitives import checkout_coordination
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
    load_config,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocument,
    TaskExecutionRegistration,
    read_task_doc,
    write_task_doc,
)
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    contract_publication_text,
    default_series_contract,
    write_contract,
)
from test_worktree_support import git, init_repo

REPO = "repo-a"
MASTER_NAME = "direct-master"
LEAF_ID = "TEST-DIRECT-LEAF"
LEAF_SLUG = "test-direct-leaf"


def _fixture(root: Path) -> SimpleNamespace:
    coord = root / "coord"
    task_root = coord / "tasks" / REPO / MASTER_NAME
    task_root.mkdir(parents=True)
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "TEST-DIRECT-MASTER",
                "slug": MASTER_NAME,
                "title": "Direct master",
                "kind": "master",
                "status": "planning",
                "repo": REPO,
                "createdAt": "2026-10-06T00:00:00+00:00",
                "executionNature": "atomic",
                "subTasks": [
                    SubTaskRef(
                        number=LEAF_ID,
                        name="Test direct leaf",
                        file=f"{LEAF_SLUG}.md",
                        status="planning",
                    ).model_dump(mode="json"),
                ],
            }
        ),
    )
    leaf_doc = TaskDocument.model_validate(
        {
            "id": LEAF_ID,
            "slug": LEAF_SLUG,
            "title": "Test direct leaf",
            "kind": "subTask",
            "status": "planning",
            "repo": REPO,
            "createdAt": "2026-10-06T00:00:00+00:00",
            "objective": "Work directly on the line.",
            "requirements": ["The fixture leaf works without an enclosure."],
        }
    )
    write_task_doc(task_root, leaf_doc)
    code = root / REPO
    init_repo(code, "main")
    (code / "file.txt").write_text("code\n", encoding="utf-8")
    git(code, "add", ".")
    git(code, "commit", "-m", "base")
    memory = coord / "memory-repos" / f"ar-{REPO}"
    init_repo(memory, "main")
    (memory / "memory.md").write_text("# memory\n", encoding="utf-8")
    git(memory, "add", ".")
    git(memory, "commit", "-m", "ledger")
    series = default_series_contract(
        ContractTask(
            name="Direct master",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        task_root=task_root,
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="main",
            work_branch="ar/direct-master",
            base_commit="0" * 40,
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="main",
            work_branch="ar/direct-master",
            base_commit="0" * 40,
        ),
    )
    series = replace(series, memory_worktree=memory)
    series.worktree_group.mkdir(parents=True, exist_ok=True)
    write_contract(series.contract_path, series)
    publish_new_lifecycle_operation_location(
        series,
        contract_text=contract_publication_text(series.contract_path, series),
    )
    leaf_ref = TaskDocumentRef(repository=REPO, path=f"{MASTER_NAME}/{LEAF_SLUG}.json")
    write_grant_file(
        grant_path_for(coord, leaf_ref),
        DirectExecutionGrant(
            leaf=leaf_ref,
            granted_by="architect",
            granted_at="2026-10-06T00:00:00+00:00",
            granter_agent_id="fixture",
        ),
    )
    config = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=coord,
        workspace_root=root,
        transcript_root=coord / "logs" / "mcp",
        repositories={REPO: RepositoryScope(repo_id=REPO, path=code, memory_root=memory)},
    )
    settings_path = root / "settings" / "ar.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "coordinationRoot": coord.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO: {}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    master_doc = read_task_doc(task_root / "task.json")
    context = RoleLaunchContext(
        role="worker",
        sprint=ResolvedTaskDocument(
            ref=TaskDocumentRef(repository=REPO, path=f"{MASTER_NAME}/task.json"),
            path=task_root / "task.json",
            document=master_doc,
        ),
        master=ResolvedTaskDocument(
            ref=TaskDocumentRef(repository=REPO, path=f"{MASTER_NAME}/task.json"),
            path=task_root / "task.json",
            document=master_doc,
        ),
        task=ResolvedTaskDocument(
            ref=leaf_ref, path=task_root / f"{LEAF_SLUG}.json", document=leaf_doc
        ),
        effective_task=ResolvedTaskDocument(
            ref=leaf_ref, path=task_root / f"{LEAF_SLUG}.json", document=leaf_doc
        ),
    )
    return SimpleNamespace(
        config=config,
        coord=coord,
        task_root=task_root,
        code=code,
        memory=memory,
        series=series,
        leaf_ref=leaf_ref,
        context=context,
        settings_path=settings_path,
    )


def _identity(fixture: SimpleNamespace, leaf_id: str | None) -> TaskIdentity:
    return TaskIdentity(
        repo_id=REPO,
        task_name=MASTER_NAME,
        worktree_name="direct-master",
        leaf_id=leaf_id,
        parent_task=MASTER_NAME,
    )


class DirectLeafRoleStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = _fixture(Path(self._tmp.name))

    def test_direct_workspace_uses_series_checkouts_and_creates_no_enclosure(self) -> None:
        workspace = _resolve_workspace(self.fixture.config, self.fixture.context)
        self.assertEqual(workspace["contractPath"], self.fixture.series.contract_path.as_posix())
        self.assertEqual(workspace["codeRoot"], self.fixture.code.resolve().as_posix())
        self.assertEqual(workspace["memoryRoot"], self.fixture.memory.resolve().as_posix())
        self.assertEqual(workspace["enclosurePreparation"], "direct")
        self.assertNotIn("enclosures", workspace["contractPath"])
        enclosure = leaf_enclosure_path(self.fixture.task_root, LEAF_ID)
        self.assertFalse(enclosure.exists())
        # The role's own folders lie in the series group, apart from the checkouts.
        group = self.fixture.series.worktree_group.resolve()
        self.assertEqual(Path(workspace["path"]), group)
        self.assertTrue(Path(workspace["taskReportAccessRoot"]).is_relative_to(group))
        self.assertFalse(
            Path(workspace["taskReportAccessRoot"]).is_relative_to(self.fixture.code.resolve())
        )

    def test_direct_workspace_without_series_contract_is_refused(self) -> None:
        self.fixture.series.contract_path.unlink()
        with self.assertRaises(RolePreparationError) as raised:
            _resolve_workspace(self.fixture.config, self.fixture.context)
        self.assertEqual(raised.exception.status, "direct-series-contract-missing")

    def test_direct_workspace_without_code_checkout_is_refused(self) -> None:
        shutil.rmtree(self.fixture.code)
        with self.assertRaises(RolePreparationError) as raised:
            _resolve_workspace(self.fixture.config, self.fixture.context)
        self.assertEqual(raised.exception.status, "direct-checkout-unavailable")

    def test_role_start_names_first_direct_leaf_in_work(self) -> None:
        leaf = read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json")
        write_task_doc(
            self.fixture.task_root,
            leaf.model_copy(
                update={
                    "executionRegistrations": [
                        TaskExecutionRegistration(
                            sourceKind="terminal-catalog-seat",
                            role="worker",
                            sourceId="worker-seat-1",
                            observedAt="2026-10-06T01:00:00+00:00",
                        )
                    ]
                }
            ),
        )
        second = TaskDocument.model_validate(
            {
                "id": "TEST-DIRECT-LEAF-2",
                "slug": "test-direct-leaf-2",
                "title": "Second direct leaf",
                "kind": "subTask",
                "status": "planning",
                "repo": REPO,
                "createdAt": "2026-10-06T00:00:00+00:00",
                "objective": "Wait its turn.",
                "requirements": ["The second leaf starts after the first."],
            }
        )
        write_task_doc(self.fixture.task_root, second)
        second_ref = TaskDocumentRef(repository=REPO, path=f"{MASTER_NAME}/test-direct-leaf-2.json")
        write_grant_file(
            grant_path_for(self.fixture.coord, second_ref),
            DirectExecutionGrant(
                leaf=second_ref,
                granted_by="architect",
                granted_at="2026-10-06T00:00:00+00:00",
                granter_agent_id="fixture",
            ),
        )
        context = replace(
            self.fixture.context,
            task=ResolvedTaskDocument(
                ref=second_ref,
                path=self.fixture.task_root / "test-direct-leaf-2.json",
                document=second,
            ),
            effective_task=ResolvedTaskDocument(
                ref=second_ref,
                path=self.fixture.task_root / "test-direct-leaf-2.json",
                document=second,
            ),
        )
        with self.assertRaises(RolePreparationError) as raised:
            _resolve_workspace(self.fixture.config, context)
        self.assertEqual(raised.exception.status, "direct-grant-sibling-open")
        self.assertIn(f"{MASTER_NAME}/{LEAF_SLUG}.json", raised.exception.detail)

    def test_role_start_names_completed_dirty_first_direct_leaf(self) -> None:
        # F05 through the start consumer: a Completed first leaf with started
        # markers and uncommitted work still refuses the second leaf's start.
        leaf = read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json")
        write_task_doc(
            self.fixture.task_root,
            leaf.model_copy(
                update={
                    "status": "Completed",
                    "executionRegistrations": [
                        TaskExecutionRegistration(
                            sourceKind="terminal-catalog-seat",
                            role="worker",
                            sourceId="worker-seat-1",
                            observedAt="2026-10-06T01:00:00+00:00",
                        )
                    ],
                }
            ),
        )
        git(self.fixture.code, "checkout", "-b", "ar/direct-master", "main")
        (self.fixture.code / "uncommitted.py").write_text("print(1)\n", encoding="utf-8")
        second = TaskDocument.model_validate(
            {
                "id": "TEST-DIRECT-LEAF-2",
                "slug": "test-direct-leaf-2",
                "title": "Second direct leaf",
                "kind": "subTask",
                "status": "planning",
                "repo": REPO,
                "createdAt": "2026-10-06T00:00:00+00:00",
                "objective": "Wait its turn.",
                "requirements": ["The second leaf starts after the first."],
            }
        )
        write_task_doc(self.fixture.task_root, second)
        second_ref = TaskDocumentRef(repository=REPO, path=f"{MASTER_NAME}/test-direct-leaf-2.json")
        write_grant_file(
            grant_path_for(self.fixture.coord, second_ref),
            DirectExecutionGrant(
                leaf=second_ref,
                granted_by="architect",
                granted_at="2026-10-06T00:00:00+00:00",
                granter_agent_id="fixture",
            ),
        )
        context = replace(
            self.fixture.context,
            task=ResolvedTaskDocument(
                ref=second_ref,
                path=self.fixture.task_root / "test-direct-leaf-2.json",
                document=second,
            ),
            effective_task=ResolvedTaskDocument(
                ref=second_ref,
                path=self.fixture.task_root / "test-direct-leaf-2.json",
                document=second,
            ),
        )
        with self.assertRaises(RolePreparationError) as raised:
            _resolve_workspace(self.fixture.config, context)
        self.assertEqual(raised.exception.status, "direct-grant-sibling-open")
        self.assertIn(f"{MASTER_NAME}/{LEAF_SLUG}.json", raised.exception.detail)

    def test_worktree_start_refuses_direct_leaf_and_says_so(self) -> None:
        result = worktree_start_tool(self.fixture.config, _identity(self.fixture, LEAF_ID))
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "direct-leaf-no-enclosure")
        self.assertIn(f"{MASTER_NAME}/{LEAF_SLUG}.json", result["summary"])
        self.assertIn("role_start", result["summary"])
        self.assertIn("no enclosure of its own", result["summary"])

    def test_direct_start_refusal_helper_passes_other_starts(self) -> None:
        self.assertIsNone(
            _direct_leaf_start_refusal(self.fixture.config, _identity(self.fixture, None))
        )
        self.assertIsNone(
            _direct_leaf_start_refusal(self.fixture.config, _identity(self.fixture, "NOPE"))
        )

    def test_scoped_readers_admit_direct_leaf_through_grant(self) -> None:
        with patch.dict(checkout_coordination._declared, {"mode": "test"}):
            base_config = load_config(self.fixture.settings_path)
            scoped = task_scoped_mcp_config_for_task(
                base_config,
                self.fixture.leaf_ref,
                self.fixture.series.contract_path,
            )
        self.assertEqual(scoped.repositories[REPO].path, self.fixture.code.resolve(strict=False))
        self.assertEqual(
            scoped.repositories[REPO].memory_root, self.fixture.memory.resolve(strict=False)
        )

    def test_scoped_readers_refuse_wrong_contract_for_direct_leaf(self) -> None:
        with self.assertRaises(ValueError) as raised:
            task_scoped_mcp_config_for_task(
                self.fixture.config,
                self.fixture.leaf_ref,
                self.fixture.task_root / "series-contract.md.bak",
            )
        self.assertIn("series contract", str(raised.exception))

    def test_revival_scope_matches_direct_launch_binding(self) -> None:
        receipt = {
            "arMcpContext": {
                "taskContext": {
                    "task_document_ref": self.fixture.leaf_ref.model_dump(mode="json"),
                    "contract_path": self.fixture.series.contract_path.as_posix(),
                }
            }
        }
        _verify_leaf_revival_scope(self.fixture.config, self.fixture.context, receipt)
        stale = {
            "arMcpContext": {
                "taskContext": {
                    "task_document_ref": self.fixture.leaf_ref.model_dump(mode="json"),
                    "contract_path": leaf_enclosure_path(
                        self.fixture.task_root, LEAF_ID
                    ).as_posix(),
                }
            }
        }
        with self.assertRaises(ValueError):
            _verify_leaf_revival_scope(self.fixture.config, self.fixture.context, stale)

    def test_ungranted_leaf_takes_the_enclosure_path(self) -> None:
        # Regression: the branch decision is the grant boolean, so a leaf
        # without a grant keeps its enclosure workspace (never the direct one).
        grant_path_for(self.fixture.coord, self.fixture.leaf_ref).unlink()
        group = Path(self._tmp.name) / "group"
        group.mkdir()
        status = {
            "worktree_group": str(group),
            "code_worktree": str(group / "code"),
            "memory_worktree": str(group / "memory"),
            "enclosurePreparation": "found",
        }
        (group / "code").mkdir()
        (group / "memory").mkdir()
        expected_contract = leaf_enclosure_path(self.fixture.task_root, LEAF_ID)
        with (
            patch.object(
                role_launch_workspace,
                "_resolve_direct_workspace",
                side_effect=AssertionError("an ungranted leaf takes no direct workspace"),
            ),
            patch.object(
                role_launch_workspace,
                "_ensure_leaf_enclosure",
                return_value=(expected_contract, status),
            ) as ensure,
        ):
            workspace = _resolve_workspace(self.fixture.config, self.fixture.context)
        ensure.assert_called_once()
        self.assertEqual(workspace["enclosurePreparation"], "found")
        self.assertEqual(workspace["contractPath"], expected_contract.as_posix())


if __name__ == "__main__":
    unittest.main()
