"""MIK-R94 rules 1-5: the direct-execution grant store and its give/take-back discipline.

Uses a synthetic coordination root with a canonical master + leaf pair -- never
the live tree. The grant lives outside task documents and harness settings, is
read at the moment of a call, and changes only through give/revoke.
"""

from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.application import agent_binding as binding_module
from agents_remember.application import direct_execution_grants as architect_module
from agents_remember.application import direct_execution_grants as mutation_module
from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.direct_execution_grants import (
    ARCHITECT_GRANTER,
    DEVELOPER_GRANTER,
    DirectExecutionGrant,
    DirectGrantError,
    GrantChange,
    GrantReclaim,
    direct_execution_grant_tool,
    give_grant,
    grant_path_for,
    grant_store_dir,
    open_direct_sibling,
    read_grant,
    reclaim_direct_execution_grants,
    refuse_open_direct_sibling,
    require_grant,
    revoke_grant,
)
from agents_remember.application.lifecycle.direct_landing import direct_landing_tool
from agents_remember.application.task_docs.task_doc_response import task_doc_preview
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.kernel.direct_execution_grants import grants_for_task, write_grant_file
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.task_document import DocStatus
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving import direct_execution_grants as developer_module
from agents_remember.serving.direct_execution_grants import (
    DeveloperGrantChange,
    give_grant_developer,
    revoke_grant_developer,
)
from agents_remember.serving.projections.snapshots_impl._task_documents import (
    read_series_documents,
    read_task_documents,
)
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocument,
    TaskEnclosureRef,
    TaskExecutionRegistration,
    read_task_doc,
    render_markdown,
    write_task_doc,
)
from agents_remember.tasks.route_review import RouteReviewRecord
from agents_remember.tasks.store import markdown_path_for
from agents_remember.worktrees.direct_landing import DirectLandingRequest
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    operation_key,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    lifecycle_operation_locator_path,
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    operation_record_path,
)
from agents_remember.worktrees.task_leaf_binding import resolve_leaf_task_binding
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    default_series_contract,
    write_contract,
)
from test_worktree_support import git, init_repo

LEAF_ID = "TEST-DIRECT-LEAF"
LEAF_SLUG = "test-direct-leaf"
MASTER_NAME = "direct-master"


def _fixture_lineage(root: Path) -> tuple[SimpleNamespace, TaskDocumentRef, TaskDocumentRef]:
    coord = root / "coord"
    task_root = coord / "tasks" / "repo-a" / MASTER_NAME
    task_root.mkdir(parents=True)
    master = TaskDocument.model_validate(
        {
            "id": "TEST-DIRECT-MASTER",
            "slug": MASTER_NAME,
            "title": "Direct master",
            "kind": "master",
            "status": "planning",
            "repo": "repo-a",
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
    )
    write_task_doc(task_root, master)
    leaf = TaskDocument.model_validate(
        {
            "id": LEAF_ID,
            "slug": LEAF_SLUG,
            "title": "Test direct leaf",
            "kind": "subTask",
            "status": "planning",
            "repo": "repo-a",
            "createdAt": "2026-10-06T00:00:00+00:00",
            "objective": "Exercise the grant store.",
            "requirements": ["The fixture leaf takes a grant."],
        }
    )
    write_task_doc(task_root, leaf)
    config = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=coord,
        workspace_root=root,
        transcript_root=coord / "logs" / "mcp",
    )
    leaf_ref = TaskDocumentRef(repository="repo-a", path=f"{MASTER_NAME}/{LEAF_SLUG}.json")
    master_ref = TaskDocumentRef(repository="repo-a", path=f"{MASTER_NAME}/task.json")
    return SimpleNamespace(config=config, coord=coord), leaf_ref, master_ref


class DirectExecutionGrantTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture, self.leaf_ref, self.master_ref = _fixture_lineage(Path(self._tmp.name))
        self.config = self.fixture.config
        self.coord = self.fixture.coord

    def test_give_records_architect_grant_outside_tasks(self) -> None:
        change = give_grant(
            self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER, granter_agent_id="agent-1"
        )
        self.assertTrue(change.changed)
        self.assertIsNotNone(change.grant)
        assert change.grant is not None
        self.assertEqual(change.grant.granted_by, ARCHITECT_GRANTER)
        self.assertEqual(change.grant.granter_agent_id, "agent-1")
        self.assertTrue(change.grant.granted_at)
        path = grant_path_for(self.coord, self.leaf_ref)
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, grant_store_dir(self.coord))
        # Outside the task documents and outside every harness settings file.
        self.assertNotIn("tasks", path.relative_to(self.coord).parts)
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["leaf"], {"path": self.leaf_ref.path, "repository": "repo-a"})
        reread = read_grant(self.coord, self.leaf_ref)
        self.assertEqual(reread, change.grant)
        self.assertIn("architect agent-1", change.grant.describe())

    def test_give_names_developer_giver(self) -> None:
        change = give_grant(self.config, self.leaf_ref, granted_by=DEVELOPER_GRANTER)
        assert change.grant is not None
        self.assertIn("developer", change.grant.describe())

    def test_give_existing_changes_nothing_and_says_so(self) -> None:
        first = give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = give_grant(self.config, self.leaf_ref, granted_by=DEVELOPER_GRANTER)
        self.assertFalse(second.changed)
        self.assertEqual(second.grant, first.grant)
        self.assertIn("already has", second.detail)
        self.assertIn("nothing changed", second.detail)

    def test_revoke_before_start_and_absent_revoke_says_so(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        revoked = revoke_grant(self.config, self.leaf_ref)
        self.assertTrue(revoked.changed)
        self.assertIsNone(revoked.grant)
        self.assertIsNone(read_grant(self.coord, self.leaf_ref))
        again = revoke_grant(self.config, self.leaf_ref)
        self.assertFalse(again.changed)
        self.assertIn("no direct-execution grant", again.detail)
        self.assertIn("nothing changed", again.detail)

    def test_grant_for_master_is_refused(self) -> None:
        with self.assertRaises(DirectGrantError) as raised:
            give_grant(self.config, self.master_ref, granted_by=ARCHITECT_GRANTER)
        self.assertEqual(raised.exception.status, "direct-grant-leaf-only")

    def test_grant_for_light_task_is_refused(self) -> None:
        light_root = self.coord / "tasks" / "repo-a" / "light-task"
        light_root.mkdir(parents=True)
        write_task_doc(
            light_root,
            TaskDocument.model_validate(
                {
                    "id": "TEST-LIGHT-TASK",
                    "slug": "light-task",
                    "title": "Light task",
                    "kind": "light",
                    "status": "planning",
                    "repo": "repo-a",
                    "createdAt": "2026-10-06T00:00:00+00:00",
                    "objective": "A light task is never direct.",
                    "requirements": ["The light task stays light."],
                }
            ),
        )
        light_ref = TaskDocumentRef(repository="repo-a", path="light-task/task.json")
        with self.assertRaises(DirectGrantError) as raised:
            give_grant(self.config, light_ref, granted_by=ARCHITECT_GRANTER)
        self.assertEqual(raised.exception.status, "direct-grant-leaf-only")
        with self.assertRaises(DirectGrantError) as revoked:
            revoke_grant(self.config, light_ref)
        self.assertEqual(revoked.exception.status, "direct-grant-leaf-only")

    def test_grant_for_started_leaf_names_evidence_and_remainder(self) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / f"{LEAF_SLUG}.json")
        write_task_doc(
            task_root,
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
        with self.assertRaises(DirectGrantError) as raised:
            give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        self.assertEqual(raised.exception.status, "direct-grant-leaf-started")
        self.assertIn("execution-progress", raised.exception.detail)
        self.assertIn("Finish the leaf as it is", raised.exception.detail)

    def test_revoke_after_start_is_refused(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / f"{LEAF_SLUG}.json")
        write_task_doc(
            task_root,
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
        with self.assertRaises(DirectGrantError) as raised:
            revoke_grant(self.config, self.leaf_ref)
        self.assertEqual(raised.exception.status, "direct-grant-leaf-started")
        self.assertIsNotNone(read_grant(self.coord, self.leaf_ref))

    def test_unreadable_grant_refuses(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        grant_path_for(self.coord, self.leaf_ref).write_text("{not json", encoding="utf-8")
        with self.assertRaises(DirectGrantError) as raised:
            read_grant(self.coord, self.leaf_ref)
        self.assertEqual(raised.exception.status, "direct-grant-unreadable")
        with self.assertRaises(DirectGrantError):
            require_grant(self.coord, self.leaf_ref)

    def test_missing_grant_refusal_names_givers_not_settings(self) -> None:
        with self.assertRaises(DirectGrantError) as raised:
            require_grant(self.coord, self.leaf_ref)
        self.assertEqual(raised.exception.status, "direct-grant-missing")
        self.assertIn("dashboard", raised.exception.detail)
        self.assertIn("direct_execution_grant", raised.exception.detail)
        self.assertNotIn("directExecutionEnabled", raised.exception.detail)
        self.assertNotIn("setting", raised.exception.detail.lower())

    def test_grant_model_dump_round_trip(self) -> None:
        grant = DirectExecutionGrant(
            leaf=self.leaf_ref,
            granted_by=ARCHITECT_GRANTER,
            granted_at="2026-10-06T02:00:00+00:00",
            granter_agent_id="agent-9",
        )
        dumped = grant.model_dump()
        self.assertEqual(dumped["leaf"], {"path": self.leaf_ref.path, "repository": "repo-a"})
        self.assertEqual(dumped["grantedBy"], "architect")

    def _second_leaf(self) -> TaskDocumentRef:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        master = read_task_doc(task_root / "task.json")
        write_task_doc(
            task_root,
            master.model_copy(
                update={
                    "subTasks": [
                        *master.subTasks,
                        SubTaskRef(
                            number="TEST-DIRECT-LEAF-2",
                            name="Second direct leaf",
                            file="test-direct-leaf-2.md",
                            status="planning",
                        ),
                    ]
                }
            ),
        )
        write_task_doc(
            task_root,
            TaskDocument.model_validate(
                {
                    "id": "TEST-DIRECT-LEAF-2",
                    "slug": "test-direct-leaf-2",
                    "title": "Second direct leaf",
                    "kind": "subTask",
                    "status": "planning",
                    "repo": "repo-a",
                    "createdAt": "2026-10-06T00:00:00+00:00",
                    "objective": "Exercise the sibling refusal.",
                    "requirements": ["The fixture second leaf waits its turn."],
                }
            ),
        )
        return TaskDocumentRef(repository="repo-a", path=f"{MASTER_NAME}/test-direct-leaf-2.json")

    def _mark_started(self, ref: TaskDocumentRef) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / Path(ref.path).name)
        write_task_doc(
            task_root,
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

    def test_second_leaf_refused_while_first_is_in_work(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=ARCHITECT_GRANTER)
        self._mark_started(self.leaf_ref)
        found = open_direct_sibling(self.config, second)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found[0], self.leaf_ref)
        with self.assertRaises(DirectGrantError) as raised:
            refuse_open_direct_sibling(self.config, second, "direct landing")
        self.assertEqual(raised.exception.status, "direct-grant-sibling-open")
        self.assertIn(self.leaf_ref.path, raised.exception.detail)
        self.assertIn("one direct leaf at a time", raised.exception.detail)

    def test_no_sibling_refusal_when_first_is_unstarted(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=ARCHITECT_GRANTER)
        self.assertIsNone(open_direct_sibling(self.config, second))
        refuse_open_direct_sibling(self.config, second, "direct landing")

    def test_no_sibling_refusal_when_checkout_is_undeterminable(self) -> None:
        # This fixture writes no series contract, so checkout dirt cannot be
        # determined and the document-status guard releases a Completed leaf.
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=ARCHITECT_GRANTER)
        self._mark_started(self.leaf_ref)
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / Path(self.leaf_ref.path).name)
        write_task_doc(task_root, leaf.model_copy(update={"status": "Completed"}))
        self.assertIsNone(open_direct_sibling(self.config, second))

    def _sibling_series_with_checkout(self) -> Path:
        """A series contract over a code checkout holding its branch."""

        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        code = self.coord / "code"
        init_repo(code, "main")
        git(code, "checkout", "-b", "ar/direct-task", "main")
        contract = default_series_contract(
            ContractTask(
                name="Direct master",
                repo_name="repo-a",
                coordination_root=self.coord,
                workflow_kind="light-task",
                memory_mode="external",
            ),
            task_root=task_root,
            code=RepoBranchPlan(
                repo_path=code,
                source_branch="main",
                work_branch="ar/direct-task",
                base_commit="0" * 40,
            ),
        )
        write_contract(contract.contract_path, contract)
        return code

    def _mark_completed(self, ref: TaskDocumentRef) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / Path(ref.path).name)
        write_task_doc(task_root, leaf.model_copy(update={"status": "Completed"}))

    def _mark_abandoned(self, ref: TaskDocumentRef) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / Path(ref.path).name)
        write_task_doc(task_root, leaf.model_copy(update={"status": "abandoned"}))

    def test_completed_started_leaf_with_uncommitted_work_still_guards(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=ARCHITECT_GRANTER)
        self._mark_started(self.leaf_ref)
        self._mark_completed(self.leaf_ref)
        code = self._sibling_series_with_checkout()
        (code / "uncommitted.py").write_text("print(1)\n", encoding="utf-8")
        found = open_direct_sibling(self.config, second)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found[0], self.leaf_ref)
        with self.assertRaises(DirectGrantError) as raised:
            refuse_open_direct_sibling(self.config, second, "direct landing")
        self.assertEqual(raised.exception.status, "direct-grant-sibling-open")
        self.assertIn(self.leaf_ref.path, raised.exception.detail)

    def test_completed_started_leaf_with_clean_checkout_releases(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=ARCHITECT_GRANTER)
        self._mark_started(self.leaf_ref)
        self._mark_completed(self.leaf_ref)
        self._sibling_series_with_checkout()
        self.assertIsNone(open_direct_sibling(self.config, second))
        refuse_open_direct_sibling(self.config, second, "direct landing")

    def test_abandoned_leaf_with_uncommitted_work_releases(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=ARCHITECT_GRANTER)
        self._mark_started(self.leaf_ref)
        self._mark_abandoned(self.leaf_ref)
        code = self._sibling_series_with_checkout()
        (code / "uncommitted.py").write_text("print(1)\n", encoding="utf-8")
        self.assertIsNone(open_direct_sibling(self.config, second))
        refuse_open_direct_sibling(self.config, second, "direct landing")

    def test_landing_for_second_leaf_names_completed_dirty_first(self) -> None:
        # F05 through the landing consumer: the tool refuses before admission.

        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=ARCHITECT_GRANTER)
        self._mark_started(self.leaf_ref)
        self._mark_completed(self.leaf_ref)
        code = self._sibling_series_with_checkout()
        (code / "uncommitted.py").write_text("print(1)\n", encoding="utf-8")
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        refused = direct_landing_tool(
            self.config,
            DirectLandingRequest(
                contract_path=(task_root / "series-contract.md").as_posix(),
                code_commit=None,
                leaf_task_document_ref=second,
                code_commit_message="second work",
                memory_commit_message="second memory",
                intent_note="second approval",
                dry_run=True,
            ),
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "direct-landing-sibling-open")
        detail = refused["detail"]
        assert isinstance(detail, str)
        self.assertIn(self.leaf_ref.path, detail)

    def test_completed_leaf_with_uncommitted_work_holds_without_registrations(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=ARCHITECT_GRANTER)
        self._mark_completed(self.leaf_ref)
        code = self._sibling_series_with_checkout()
        (code / "uncommitted.py").write_text("print(1)\n", encoding="utf-8")
        found = open_direct_sibling(self.config, second)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found[0], self.leaf_ref)

    def _binding(self, role: str) -> AgentBinding:
        return AgentBinding(
            agent_id="11111111-1111-4111-8111-111111111111",
            role=role,
            request_id="22222222-2222-4222-8222-222222222222",
            report_path="/reports/worker",
        )

    def test_concurrent_givers_produce_one_changed_grant(self) -> None:

        barrier = threading.Barrier(2)
        outcomes: dict[str, GrantChange] = {}
        failures: dict[str, BaseException] = {}

        def give(name: str) -> None:
            try:
                barrier.wait(timeout=30)
                outcomes[name] = mutation_module.give_grant(
                    self.config,
                    self.leaf_ref,
                    granted_by=ARCHITECT_GRANTER,
                    granter_agent_id=name,
                )
            except BaseException as error:
                failures[name] = error

        threads = [
            threading.Thread(target=give, args=(name,)) for name in ("architect-A", "architect-B")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        self.assertEqual(failures, {})
        changed = [name for name, outcome in outcomes.items() if outcome.changed]
        unchanged = [name for name, outcome in outcomes.items() if not outcome.changed]
        self.assertEqual(len(changed), 1)
        self.assertEqual(len(unchanged), 1)
        stored = read_grant(self.coord, self.leaf_ref)
        assert stored is not None
        # The stored grant is the one changed response; the unchanged response
        # retains its issuer and time.
        self.assertEqual(stored.granter_agent_id, changed[0])
        self.assertEqual(outcomes[unchanged[0]].grant, stored)

    def test_concurrent_give_and_revoke_stay_deterministic(self) -> None:

        barrier = threading.Barrier(2)
        outcomes: dict[str, GrantChange] = {}

        def give() -> None:
            barrier.wait(timeout=30)
            outcomes["give"] = mutation_module.give_grant(
                self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER
            )

        def revoke() -> None:
            barrier.wait(timeout=30)
            outcomes["revoke"] = mutation_module.revoke_grant(self.config, self.leaf_ref)

        threads = [threading.Thread(target=give), threading.Thread(target=revoke)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        # Two valid serializations: give-then-revoke (both changed, absent
        # afterwards) or revoke-then-give (revoke unchanged, give changed,
        # stored). The store always matches the last mutation.
        stored = None
        try:
            stored = read_grant(self.coord, self.leaf_ref)
        except DirectGrantError:
            stored = "unreadable"
        self.assertNotEqual(stored, "unreadable")
        if outcomes["revoke"].changed:
            self.assertTrue(outcomes["give"].changed)
            self.assertIsNone(stored)
        else:
            self.assertFalse(outcomes["revoke"].changed)
            self.assertTrue(outcomes["give"].changed)
            self.assertEqual(stored, outcomes["give"].grant)

    def test_concurrent_architect_and_developer_givers_produce_one_changed_grant(
        self,
    ) -> None:
        # Sealed F08 across the two authorized writers: both calls prove
        # absence together at the publication barrier, yet exactly one create
        # wins and the loser reports the stored grant unchanged.

        barrier = threading.Barrier(2)
        actual = architect_module.create_grant_file

        def at_publication(path, grant):
            barrier.wait(timeout=30)
            return actual(path, grant)

        outcomes: dict[str, object] = {}
        failures: dict[str, BaseException] = {}

        def give_architect() -> None:
            try:
                outcomes["architect"] = architect_module.give_grant(
                    self.config,
                    self.leaf_ref,
                    granted_by=ARCHITECT_GRANTER,
                    granter_agent_id="architect-A",
                )
            except BaseException as error:
                failures["architect"] = error

        def give_developer() -> None:
            try:
                outcomes["developer"] = developer_module.give_grant_developer(
                    self.config, self.leaf_ref
                )
            except BaseException as error:
                failures["developer"] = error

        threads = [
            threading.Thread(target=give_architect),
            threading.Thread(target=give_developer),
        ]
        with (
            mock.patch.object(architect_module, "create_grant_file", side_effect=at_publication),
            mock.patch.object(developer_module, "create_grant_file", side_effect=at_publication),
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
        self.assertEqual(failures, {})
        changed = [name for name, outcome in outcomes.items() if getattr(outcome, "changed", False)]
        unchanged = [
            name for name, outcome in outcomes.items() if not getattr(outcome, "changed", True)
        ]
        self.assertEqual(len(changed), 1)
        self.assertEqual(len(unchanged), 1)
        stored = read_grant(self.coord, self.leaf_ref)
        assert stored is not None
        loser = outcomes[unchanged[0]]
        assert isinstance(loser, (GrantChange, DeveloperGrantChange))
        self.assertEqual(loser.grant, stored)

    def test_concurrent_givers_across_processes_produce_one_changed_grant(self) -> None:
        outcome_path = Path(self._tmp.name) / "proc-outcomes.json"

        def grant_json(grant) -> dict | None:
            if grant is None:
                return None
            return {
                "leaf": {"repository": grant.leaf.repository, "path": grant.leaf.path},
                "granted_by": grant.granted_by,
                "granted_at": grant.granted_at,
                "granter_agent_id": grant.granter_agent_id,
            }

        def give_in_process(role: str) -> None:
            import json  # noqa: PLC0415  # the forked child imports its own modules

            from agents_remember.kernel.primitives.runtime_config import (  # noqa: PLC0415  # the forked child imports its own modules
                McpRuntimeConfig as ChildConfig,
            )
            from agents_remember.models.task_document_ref import (  # noqa: PLC0415  # the forked child imports its own modules
                TaskDocumentRef as ChildRef,
            )

            child_config = ChildConfig(
                config_path=Path(self._tmp.name) / "settings.json",
                coordination_root=self.coord,
                workspace_root=Path(self._tmp.name),
                transcript_root=self.coord / "logs" / "mcp",
            )
            child_ref = ChildRef(repository="repo-a", path=f"{MASTER_NAME}/{LEAF_SLUG}.json")
            if role == "architect":
                from agents_remember.application.direct_execution_grants import (  # noqa: PLC0415  # the forked child imports its own modules
                    give_grant as child_give,
                )

                change = child_give(
                    child_config, child_ref, granted_by="architect", granter_agent_id=role
                )
            else:
                from agents_remember.serving.direct_execution_grants import (  # noqa: PLC0415  # the forked child imports its own modules
                    give_grant_developer as child_give_developer,
                )

                change = child_give_developer(child_config, child_ref)
            with open(outcome_path, "a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"role": role, "changed": change.changed, "grant": grant_json(change.grant)}
                    )
                    + "\n"
                )

        context = multiprocessing.get_context("fork")
        processes = [
            context.Process(target=give_in_process, args=(role,))
            for role in ("architect", "developer")
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=120)
        self.assertEqual([process.exitcode for process in processes], [0, 0])
        outcomes = [
            json.loads(line) for line in outcome_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(outcomes), 2)
        changed = [row for row in outcomes if row["changed"]]
        unchanged = [row for row in outcomes if not row["changed"]]
        self.assertEqual(len(changed), 1)
        self.assertEqual(len(unchanged), 1)
        stored = read_grant(self.coord, self.leaf_ref)
        assert stored is not None
        self.assertEqual(unchanged[0]["grant"], grant_json(stored))

    def test_give_after_start_is_refused_and_writes_nothing(self) -> None:
        self._mark_started(self.leaf_ref)
        with self.assertRaises(DirectGrantError) as raised:
            give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        self.assertEqual(raised.exception.status, "direct-grant-leaf-started")
        self.assertIsNone(read_grant(self.coord, self.leaf_ref))

    def test_grant_survives_a_later_start(self) -> None:
        change = give_grant(
            self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER, granter_agent_id="kept"
        )
        self.assertTrue(change.changed)
        self._mark_started(self.leaf_ref)
        stored = read_grant(self.coord, self.leaf_ref)
        self.assertEqual(stored, change.grant)

    def test_give_removes_its_grant_when_start_interleaves_before_publication(
        self,
    ) -> None:
        # A start that lands between the proof and the publication must not
        # leave a grant behind: the give remediates its own write and refuses.

        real_create = mutation_module.create_grant_file
        started: list[bool] = []

        def interleaving_create(path, grant):
            if not started:
                started.append(True)
                self._mark_started(self.leaf_ref)
            return real_create(path, grant)

        with (
            mock.patch.object(
                mutation_module, "create_grant_file", side_effect=interleaving_create
            ),
            self.assertRaises(DirectGrantError) as raised,
        ):
            give_grant(
                self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER, granter_agent_id="late"
            )
        self.assertEqual(raised.exception.status, "direct-grant-leaf-started")
        self.assertIsNone(read_grant(self.coord, self.leaf_ref))

    def test_revoke_restores_grant_when_start_interleaves_before_removal(
        self,
    ) -> None:
        # Sealed F08 revoke/start boundary, both owners: a real started
        # registration scheduled at the removal boundary refuses the revoke
        # and keeps the exact grant record.

        for module, _give, revoke, fresh_leaf in (
            (architect_module, architect_module.give_grant, architect_module.revoke_grant, False),
            (
                developer_module,
                developer_module.give_grant_developer,
                developer_module.revoke_grant_developer,
                True,
            ),
        ):
            with self.subTest(owner=module.__name__.rsplit(".", 1)[-1]):
                leaf_ref = self._second_leaf() if fresh_leaf else self.leaf_ref
                if module is architect_module:
                    gave = architect_module.give_grant(
                        self.config,
                        leaf_ref,
                        granted_by=ARCHITECT_GRANTER,
                        granter_agent_id="revoke-start",
                    )
                else:
                    gave = developer_module.give_grant_developer(self.config, leaf_ref)
                self.assertTrue(gave.changed)
                before = read_grant(self.coord, leaf_ref)
                assert before is not None
                actual_paths = module.grant_removal_paths

                def start_after_proof(*args, _paths=actual_paths, _leaf=leaf_ref, **kwargs):
                    self._mark_started(_leaf)
                    return _paths(*args, **kwargs)

                with (
                    mock.patch.object(module, "grant_removal_paths", side_effect=start_after_proof),
                    self.assertRaises(DirectGrantError) as refused,
                ):
                    revoke(self.config, leaf_ref)
                self.assertEqual(refused.exception.status, "direct-grant-leaf-started")
                self.assertEqual(read_grant(self.coord, leaf_ref), before)

    def test_task_census_reads_only_its_task_files_at_two_sizes(self) -> None:
        for size in (100, 200):
            coord = Path(self._tmp.name) / f"census-{size}"
            for index in range(size):
                ref = TaskDocumentRef(repository="repo-a", path=f"task-{index:03d}/leaf.json")
                write_grant_file(
                    grant_path_for(coord, ref),
                    DirectExecutionGrant(
                        leaf=ref,
                        granted_by=ARCHITECT_GRANTER,
                        granted_at="2026-10-06T00:00:00+00:00",
                        granter_agent_id="census",
                    ),
                )
            reads = 0
            real_read_bytes = Path.read_bytes

            def counting_read_bytes(path_self, _real_read_bytes=real_read_bytes):
                nonlocal reads
                reads += 1
                return _real_read_bytes(path_self)

            with mock.patch.object(Path, "read_bytes", counting_read_bytes):
                found = grants_for_task(coord, "repo-a", "task-042")
            self.assertEqual(sorted(found), ["task-042/leaf.json"])
            # Bounded: only the task's own file is read, however many tasks
            # share the store.
            self.assertEqual(reads, 1)

    def _reclaim_fixture(self) -> tuple[TaskDocumentRef, Path, Path]:
        """Two granted leaves plus a series checkout, for reclamation."""
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=ARCHITECT_GRANTER)
        code = self._sibling_series_with_checkout()
        return second, code, self.coord / "tasks" / "repo-a" / MASTER_NAME

    def test_reclaim_takes_only_abandoned_clean_grants_and_keeps_the_rest(self) -> None:
        second, _code, _task_root = self._reclaim_fixture()
        self._mark_started(self.leaf_ref)
        self._mark_completed(self.leaf_ref)
        self._mark_abandoned(second)
        # A broken file in this task's addressed name is preserved, not removed.
        store = grant_store_dir(self.coord)
        broken = store / f"repo-a--{MASTER_NAME}--{'0' * 64}.json"
        broken.write_text("{not json", encoding="utf-8")
        preview = reclaim_direct_execution_grants(self.config, self.leaf_ref, dry_run=True)
        self.assertEqual(preview.reclaimed, (f"repo-a:{second.path}",))
        self.assertIsNotNone(read_grant(self.coord, second))
        result = reclaim_direct_execution_grants(self.config, self.leaf_ref)
        self.assertIsInstance(result, GrantReclaim)
        self.assertEqual(result.reclaimed, (f"repo-a:{second.path}",))
        self.assertIsNone(read_grant(self.coord, second))
        # The started Completed leaf keeps its grant: completion is not
        # landing proof and no packet amendment authorizes taking it (rule 5).
        self.assertIsNotNone(read_grant(self.coord, self.leaf_ref))
        reasons = {
            entry.get("ref", entry.get("file")): entry["reason"] for entry in result.preserved
        }
        self.assertIn("rule 5", reasons[f"repo-a:{MASTER_NAME}/{LEAF_SLUG}.json"])
        self.assertTrue(broken.is_file())
        # The tool action reports the same outcome for the architect.
        tool_result = direct_execution_grant_tool(
            self.config, second, "reclaim", binding=self._binding("architect")
        )
        self.assertTrue(tool_result["ok"])
        self.assertEqual(tool_result["state"], "reclaimed")
        self.assertEqual(tool_result["reclaimed"], [])
        self.assertTrue(tool_result["preserved"])

    def test_reclaim_preserves_started_abandoned_leaf(self) -> None:
        # Sealed F09: a registration followed by abandonment is still a
        # started leaf: ordinary revoke refuses and reclaim preserves.
        self._reclaim_fixture()
        self._mark_started(self.leaf_ref)
        self._mark_abandoned(self.leaf_ref)
        self.assertIsNotNone(read_grant(self.coord, self.leaf_ref))
        with self.assertRaises(DirectGrantError) as revoked:
            revoke_grant(self.config, self.leaf_ref)
        self.assertEqual(revoked.exception.status, "direct-grant-leaf-started")
        result = reclaim_direct_execution_grants(self.config, self.leaf_ref)
        self.assertEqual(result.reclaimed, ())
        self.assertIsNotNone(read_grant(self.coord, self.leaf_ref))
        reasons = {
            entry.get("ref", entry.get("file")): entry["reason"] for entry in result.preserved
        }
        self.assertIn("rule 5", reasons[f"repo-a:{MASTER_NAME}/{LEAF_SLUG}.json"])

    def test_reclaim_preserves_abandoned_leaf_with_unreadable_locator(self) -> None:
        # Sealed F09 (R4 Reviewer repro, ambiguous-locator cell): an
        # unreadable locator is not proof of no work — the abandoned grant
        # is preserved, not reclaimed.
        self._reclaim_fixture()
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        binding = resolve_leaf_task_binding(self.coord, "repo-a", task_root, LEAF_ID)
        locator_path = lifecycle_operation_locator_path(self.coord, binding.contract_path)
        locator_path.parent.mkdir(parents=True, exist_ok=True)
        locator_path.write_text("{not json", encoding="utf-8")
        try:
            self._mark_abandoned(self.leaf_ref)
            result = reclaim_direct_execution_grants(self.config, self.leaf_ref)
            self.assertEqual(result.reclaimed, ())
            self.assertIsNotNone(read_grant(self.coord, self.leaf_ref))
            reasons = {
                entry.get("ref", entry.get("file")): entry["reason"] for entry in result.preserved
            }
            self.assertIn("rule 5", reasons[f"repo-a:{MASTER_NAME}/{LEAF_SLUG}.json"])
        finally:
            locator_path.unlink(missing_ok=True)

    def test_reclaim_preserves_dirty_checkout(self) -> None:
        second, code, _task_root = self._reclaim_fixture()
        self._mark_started(self.leaf_ref)
        self._mark_completed(self.leaf_ref)
        self._mark_abandoned(second)
        (code / "uncommitted.py").write_text("print(1)\n", encoding="utf-8")
        result = reclaim_direct_execution_grants(self.config, self.leaf_ref)
        self.assertEqual(result.reclaimed, ())
        reasons = {
            entry.get("ref", entry.get("file")): entry["reason"] for entry in result.preserved
        }
        self.assertIn("rule 5", reasons[f"repo-a:{MASTER_NAME}/{LEAF_SLUG}.json"])
        self.assertIn("still guards", reasons[f"repo-a:{second.path}"])
        self.assertIsNotNone(read_grant(self.coord, self.leaf_ref))
        self.assertIsNotNone(read_grant(self.coord, second))

    def test_reclaim_removes_abandoned_grants_at_two_sizes(self) -> None:
        # Sealed F09 actual reclamation: every never-started abandoned leaf's
        # grant is removed at two increasing store sizes, while started
        # leaves keep theirs. Abandonment reads on the leaf document; the
        # master rows stay planning.
        for size in (100, 200):
            coord = Path(self._tmp.name) / f"reclaim-{size}"
            for index in range(size):
                task_name = f"task-{index:03d}"
                task_root = coord / "tasks" / "repo-a" / task_name
                task_root.mkdir(parents=True)
                status = "abandoned" if index % 2 == 0 else "Completed"
                write_task_doc(
                    task_root,
                    TaskDocument.model_validate(
                        {
                            "id": f"TEST-{index:03d}",
                            "slug": task_name,
                            "title": f"Task {index:03d}",
                            "kind": "master",
                            "status": "planning",
                            "repo": "repo-a",
                            "createdAt": "2026-10-06T00:00:00+00:00",
                            "executionNature": "atomic",
                            "subTasks": [
                                {
                                    "number": f"LEAF-{index:03d}",
                                    "name": f"Leaf {index:03d}",
                                    "file": "leaf.md",
                                    "status": "planning",
                                }
                            ],
                        }
                    ),
                )
                leaf_ref = TaskDocumentRef(repository="repo-a", path=f"{task_name}/leaf.json")
                write_task_doc(
                    task_root,
                    TaskDocument.model_validate(
                        {
                            "id": f"LEAF-{index:03d}",
                            "slug": "leaf",
                            "title": f"Leaf {index:03d}",
                            "kind": "subTask",
                            "status": status,
                            "repo": "repo-a",
                            "createdAt": "2026-10-06T00:00:00+00:00",
                            "objective": "Reclamation census.",
                            "requirements": ["The abandoned grant is removed."],
                        }
                    ),
                )
                write_grant_file(
                    grant_path_for(coord, leaf_ref),
                    DirectExecutionGrant(
                        leaf=leaf_ref,
                        granted_by=ARCHITECT_GRANTER,
                        granted_at="2026-10-06T00:00:00+00:00",
                        granter_agent_id="reclaim",
                    ),
                )
            config = McpRuntimeConfig(
                config_path=Path(self._tmp.name) / "settings.json",
                coordination_root=coord,
                workspace_root=Path(self._tmp.name),
                transcript_root=coord / "logs" / "mcp",
            )
            reclaimed: list[str] = []
            for index in range(size):
                task_name = f"task-{index:03d}"
                outcome = reclaim_direct_execution_grants(
                    config, TaskDocumentRef(repository="repo-a", path=f"{task_name}/leaf.json")
                )
                reclaimed.extend(outcome.reclaimed)
            self.assertEqual(len(reclaimed), size // 2)
            for index in range(size):
                task_name = f"task-{index:03d}"
                leaf_ref = TaskDocumentRef(repository="repo-a", path=f"{task_name}/leaf.json")
                if index % 2 == 0:
                    self.assertIsNone(read_grant(coord, leaf_ref))
                else:
                    self.assertIsNotNone(read_grant(coord, leaf_ref))

    def test_retired_key_notices_once_per_load_under_default_policy(self) -> None:
        settings = Path(self._tmp.name) / "retired-settings.json"
        workspace = Path(self._tmp.name) / "retired-workspace"
        workspace.mkdir(exist_ok=True)
        for value in (True, False, {"mode": "always"}):
            settings.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "coordinationRoot": self.coord.as_posix(),
                        "workspaceRoot": workspace.as_posix(),
                        "repositories": {"repo-a": {}},
                        "directExecutionEnabled": value,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertLogs(
                "agents_remember.kernel.primitives.runtime_config", level="WARNING"
            ) as first:
                load_config(settings)
            with self.assertLogs(
                "agents_remember.kernel.primitives.runtime_config", level="WARNING"
            ) as second:
                loaded = load_config(settings)
            # F11: two actual loads emit exactly one notice each, even under
            # the ordinary default notice configuration.
            self.assertEqual(len(first.records), 1)
            self.assertEqual(len(second.records), 1)
            for records in (first.records, second.records):
                text = records[0].getMessage()
                self.assertIn("directExecutionEnabled", text)
                self.assertIn("retired", text)
                self.assertIn("direct-execution grant per task", text)
            self.assertFalse(hasattr(loaded, "direct_execution_enabled"))

    def test_function_give_and_revoke_as_architect(self) -> None:
        given = direct_execution_grant_tool(
            self.config, self.leaf_ref, "give", binding=self._binding("architect")
        )
        self.assertTrue(given["ok"])
        self.assertEqual(given["state"], "granted")
        self.assertTrue(given["changed"])
        self.assertEqual(given["grant"]["grantedBy"], "architect")
        self.assertEqual(given["grant"]["granterAgentId"], "11111111-1111-4111-8111-111111111111")
        revoked = direct_execution_grant_tool(
            self.config, self.leaf_ref, "revoke", binding=self._binding("architect")
        )
        self.assertTrue(revoked["ok"])
        self.assertEqual(revoked["state"], "revoked")
        self.assertIsNone(read_grant(self.coord, self.leaf_ref))

    def test_function_unknown_action_is_refused(self) -> None:
        answer = direct_execution_grant_tool(
            self.config, self.leaf_ref, "delete", binding=self._binding("architect")
        )
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], "direct-grant-action-unknown")

    def test_function_refuses_every_other_role(self) -> None:
        for role in (
            "worker",
            "reviewer",
            "curator",
            "manager",
            "orchestrator",
            "system-specialist",
            "investigator",
        ):
            with self.subTest(role=role):
                answer = direct_execution_grant_tool(
                    self.config, self.leaf_ref, "give", binding=self._binding(role)
                )
                self.assertFalse(answer["ok"])
                self.assertEqual(answer["state"], "refused")
                self.assertEqual(answer["status"], "direct-grant-architect-only")
                self.assertIn(role, answer["detail"])
        self.assertIsNone(read_grant(self.coord, self.leaf_ref))

    def test_function_refuses_caller_without_binding(self) -> None:
        cleared = {name: "" for name in binding_module.CLEARED_SEAT_VARIABLES}
        cleared.update(
            {
                binding_module.AGENT_ID_VARIABLE: "",
                binding_module.ROLE_VARIABLE: "",
                binding_module.REQUEST_ID_VARIABLE: "",
                binding_module.REPORT_PATH_VARIABLE: "",
                binding_module.SPRINT_REF_VARIABLE: "",
                binding_module.MASTER_REF_VARIABLE: "",
                binding_module.TASK_REF_VARIABLE: "",
            }
        )
        with mock.patch.dict("os.environ", cleared):
            answer = direct_execution_grant_tool(self.config, self.leaf_ref, "give")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["status"], "direct-grant-architect-only")
        self.assertIsNone(read_grant(self.coord, self.leaf_ref))

    def test_grants_for_task_lists_task_grants_keyed_by_leaf_path(self) -> None:
        self.assertEqual(grants_for_task(self.coord, "repo-a", MASTER_NAME), {})
        change = give_grant(
            self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER, granter_agent_id="a1"
        )
        assert change.grant is not None
        found = grants_for_task(self.coord, "repo-a", MASTER_NAME)
        self.assertEqual(list(found), [self.leaf_ref.path])
        self.assertEqual(found[self.leaf_ref.path], change.grant)
        # Another task folder and another repository are not listed.
        self.assertEqual(grants_for_task(self.coord, "repo-a", "other-task"), {})
        self.assertEqual(grants_for_task(self.coord, "repo-b", MASTER_NAME), {})
        # An unreadable grant file is skipped, never breaking the task view.
        (grant_store_dir(self.coord) / "broken.json").write_text("{nope", encoding="utf-8")
        found = grants_for_task(self.coord, "repo-a", MASTER_NAME)
        self.assertEqual(list(found), [self.leaf_ref.path])

    def test_render_without_grant_matches_stored_markdown_byte_for_byte(self) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / f"{LEAF_SLUG}.json")
        stored = (task_root / f"{LEAF_SLUG}.md").read_text(encoding="utf-8")
        self.assertEqual(render_markdown(leaf), stored)

    def test_render_with_grant_adds_exactly_one_header_line(self) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / f"{LEAF_SLUG}.json")
        change = give_grant(
            self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER, granter_agent_id="a1"
        )
        assert change.grant is not None
        plain = render_markdown(leaf).splitlines()
        granted = render_markdown(leaf, direct_grant=change.grant).splitlines()
        self.assertEqual(len(granted), len(plain) + 1)
        line = f"**Direct execution:** {change.grant.describe()}"
        self.assertIn(line, granted)
        created = next(i for i, text in enumerate(plain) if text.startswith("**Created:**"))
        self.assertEqual(granted.index(line), created + 1)
        self.assertIn("architect a1", line)
        self.assertNotIn("directExecutionEnabled", "\n".join(granted))

    def test_grant_store_carries_live_grant_and_who_gave_it(self) -> None:
        # MIK-R111 rule 2: the live grant reads from the one record, never a get answer.
        leaf_key = f"{MASTER_NAME}/{LEAF_SLUG}.json"
        store = grants_for_task(self.config.coordination_root, "repo-a", MASTER_NAME)
        self.assertNotIn(leaf_key, store)
        change = give_grant(self.config, self.leaf_ref, granted_by=DEVELOPER_GRANTER)
        assert change.grant is not None
        store = grants_for_task(self.config.coordination_root, "repo-a", MASTER_NAME)
        self.assertEqual(
            store[leaf_key].model_dump(),
            {
                "leaf": {"path": self.leaf_ref.path, "repository": "repo-a"},
                "grantedBy": "developer",
                "granterAgentId": None,
                "grantedAt": change.grant.granted_at,
            },
        )
        # The store carries no grant record for any other document of the task.
        self.assertEqual(set(store), {leaf_key})
        # Taking the grant back removes it from the store: nothing is stored twice.
        revoke_grant(self.config, self.leaf_ref)
        self.assertNotIn(
            leaf_key,
            grants_for_task(self.config.coordination_root, "repo-a", MASTER_NAME),
        )

    def test_task_document_model_carries_no_grant_field(self) -> None:
        # Rule 1: no task_doc write can make a leaf direct; the model is unchanged.
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / f"{LEAF_SLUG}.json")
        with self.assertRaises(ValueError):
            TaskDocument.model_validate({**leaf.model_dump(mode="json"), "directGrant": {}})
        with self.assertRaises(ValueError):
            TaskDocument.model_validate({**leaf.model_dump(mode="json"), "direct_grant": {}})

    def test_give_and_revoke_refuse_every_started_evidence_class(self) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        base_master = TaskDocument.model_validate(
            {
                "id": "TEST-DIRECT-MASTER",
                "slug": MASTER_NAME,
                "title": "Direct master",
                "kind": "master",
                "status": "planning",
                "repo": "repo-a",
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
        )
        base_leaf = {
            "id": LEAF_ID,
            "slug": LEAF_SLUG,
            "title": "Test direct leaf",
            "kind": "subTask",
            "status": "planning",
            "repo": "repo-a",
            "createdAt": "2026-10-06T00:00:00+00:00",
            "objective": "Exercise the grant store.",
            "requirements": ["The fixture leaf takes a grant."],
        }

        def rewrite(row_status: DocStatus = "planning", **leaf_updates):
            master = base_master.model_copy(
                update={
                    "subTasks": [
                        SubTaskRef(
                            number=LEAF_ID,
                            name="Test direct leaf",
                            file=f"{LEAF_SLUG}.md",
                            status=row_status,
                        ),
                    ]
                }
            )
            write_task_doc(task_root, master)
            write_task_doc(task_root, TaskDocument.model_validate({**base_leaf, **leaf_updates}))

        cases = {
            "row-status": lambda: rewrite(row_status="inProgress"),
            "leaf-status": lambda: rewrite(status="inProgress"),
            "lifecycle-id": lambda: rewrite(lifecycleId="094000000001"),
            "enclosures": lambda: rewrite(
                enclosures=[
                    TaskEnclosureRef(leafId=LEAF_ID, enclosurePath="enclosures/x").model_dump(
                        mode="json"
                    )
                ]
            ),
            "route-review": lambda: rewrite(
                routeReview=RouteReviewRecord.model_validate(
                    {
                        "candidateTree": "0" * 40,
                        "verdict": "pass",
                        "verdictRef": "review",
                        "reviewedAt": "2026-10-06T00:00:00+00:00",
                        "routes": [{"route": "code", "verdict": "pass", "evidenceRef": "evidence"}],
                        "taskIntent": {"schema": "task-intent/v1", "digest": "0" * 64},
                    }
                )
            ),
            "step": lambda: rewrite(steps=[{"id": "s1", "title": "One", "status": "inProgress"}]),
        }
        for name, setup in cases.items():
            with self.subTest(evidence_class=name):
                setup()
                with self.assertRaises(DirectGrantError) as given:
                    give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
                self.assertEqual(given.exception.status, "direct-grant-leaf-started")
                write_grant_file(
                    grant_path_for(self.coord, self.leaf_ref),
                    DirectExecutionGrant(
                        leaf=self.leaf_ref,
                        granted_by=ARCHITECT_GRANTER,
                        granted_at="2026-10-06T00:00:00+00:00",
                        granter_agent_id="matrix",
                    ),
                )
                with self.assertRaises(DirectGrantError) as taken:
                    revoke_grant(self.config, self.leaf_ref)
                self.assertEqual(taken.exception.status, "direct-grant-leaf-started")
                grant_path_for(self.coord, self.leaf_ref).unlink(missing_ok=True)

    def _assert_grant_pair_refused(self) -> None:
        """Give and revoke must both refuse with the started-leaf words."""

        with self.assertRaises(DirectGrantError) as given:
            give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        self.assertEqual(given.exception.status, "direct-grant-leaf-started")
        write_grant_file(
            grant_path_for(self.coord, self.leaf_ref),
            DirectExecutionGrant(
                leaf=self.leaf_ref,
                granted_by=ARCHITECT_GRANTER,
                granted_at="2026-10-06T00:00:00+00:00",
                granter_agent_id="matrix",
            ),
        )
        try:
            with self.assertRaises(DirectGrantError) as taken:
                revoke_grant(self.config, self.leaf_ref)
            self.assertEqual(taken.exception.status, "direct-grant-leaf-started")
        finally:
            grant_path_for(self.coord, self.leaf_ref).unlink(missing_ok=True)

    def _publish_enclosure_chain(self):
        """A valid locator + manifest for this leaf through the production path."""

        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        binding = resolve_leaf_task_binding(self.coord, "repo-a", task_root, LEAF_ID)
        enclosure_path = binding.contract_path
        code_repo = self.coord / "e2-code"
        memory_repo = self.coord / "e2-memory"
        code_repo.mkdir(parents=True, exist_ok=True)
        memory_repo.mkdir(parents=True, exist_ok=True)
        contract = default_contract(
            ContractTask(
                name=MASTER_NAME,
                repo_name="repo-a",
                coordination_root=self.coord,
                workflow_kind="light-task",
                memory_mode="external",
            ),
            leaf=LeafIdentity(worktree_name=LEAF_SLUG, leaf_id=LEAF_ID),
            code=RepoBranchPlan(
                repo_path=code_repo,
                source_branch="main",
                work_branch="ar/e2",
                base_commit="0" * 40,
            ),
            memory=RepoBranchPlan(
                repo_path=memory_repo,
                source_branch="main",
                work_branch="ar/e2",
                base_commit="0" * 40,
            ),
        )
        self.assertEqual(
            contract.contract_path.resolve(strict=False),
            enclosure_path.resolve(strict=False),
        )
        write_contract(contract.contract_path, contract)
        location = publish_new_lifecycle_operation_location(
            contract,
            contract_text=contract.contract_path.read_text(encoding="utf-8"),
        )
        return contract, location, enclosure_path, memory_repo

    def _write_running_direct_journal(
        self, contract, location, enclosure_path: Path, memory_repo: Path
    ) -> None:
        """A running direct-landing record in the published lifecycle journals."""

        journal_path = operation_record_path(location.worktree_group, "direct-landing")
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "headRef": "refs/heads/ar/e2",
            "head": "d" * 40,
            "headTree": "e" * 40,
            "refLogFingerprint": "f" * 64,
            "indexTree": "e" * 40,
            "candidateTree": "e" * 40,
            "statusFingerprint": "f" * 64,
        }
        record = LifecycleOperationRecord.model_validate(
            {
                "taskId": "TEST-DIRECT-MASTER",
                "taskName": MASTER_NAME,
                "contractPath": enclosure_path.as_posix(),
                "operationKind": "direct-landing",
                "candidateState": "a" * 64,
                "candidateTree": "c" * 40,
                "fingerprint": "b" * 64,
                "operationKey": operation_key(enclosure_path, "direct-landing", "b" * 64),
                "input": DirectLandingOperationInput.model_validate(
                    {
                        "configPath": str(Path(self._tmp.name) / "settings.json"),
                        "contractPath": enclosure_path.as_posix(),
                        "leafTaskDocumentRef": self.leaf_ref.model_dump(mode="json"),
                        "effectiveInput": EffectiveCloseoutInput.model_validate(
                            {
                                "route": "direct-landing",
                                "contractKind": "leaf",
                                "memoryMode": "external",
                                "code": {
                                    "state": "not-applicable",
                                    "reason": "verified-existing",
                                },
                                "memory": {
                                    "state": "enabled",
                                    "reason": "direct landing",
                                    "message": "e2 memory",
                                },
                            }
                        ).model_dump(mode="json"),
                        "approvalNote": "e2 approval",
                        "codeCommit": "b" * 40,
                        "codeTree": "c" * 40,
                        "candidateTree": "c" * 40,
                        "memoryRepository": memory_repo.as_posix(),
                        "memoryBranch": "ar/e2",
                        "memoryRef": "refs/heads/ar/e2",
                        "memoryBefore": snapshot,
                    }
                ).model_dump(mode="json"),
                "status": "running",
                "phase": "direct-preflight",
                "queuedAt": "2026-10-06T00:00:00+00:00",
                "reportPath": location.report_path("direct-landing").as_posix(),
                "mutationEvidence": {
                    "memory": {
                        "leg": "memory",
                        "repository": contract.memory_worktree.as_posix(),
                        "state": "pre-mutation",
                    }
                },
            }
        )
        journal_path.write_text(
            record.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_give_and_revoke_refuse_unreadable_locator(self) -> None:
        # Sealed F14 E2, locator class: garbage at the canonical locator
        # address reads ambiguous and refuses both mutations.
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        binding = resolve_leaf_task_binding(self.coord, "repo-a", task_root, LEAF_ID)
        locator_path = lifecycle_operation_locator_path(self.coord, binding.contract_path)
        locator_path.parent.mkdir(parents=True, exist_ok=True)
        locator_path.write_text("{not json", encoding="utf-8")
        try:
            self._assert_grant_pair_refused()
        finally:
            locator_path.unlink(missing_ok=True)

    def test_give_and_revoke_refuse_published_locator_and_manifest(self) -> None:
        # Sealed F14 E2, locator/manifest class: the published chain reads
        # started and refuses both mutations.
        self._publish_enclosure_chain()
        self._assert_grant_pair_refused()

    def test_give_and_revoke_refuse_running_operation_journal(self) -> None:
        # Sealed F14 E2, operation class: a running journal record in the
        # published lifecycle journals refuses both mutations.
        contract, location, enclosure_path, memory_repo = self._publish_enclosure_chain()
        self._write_running_direct_journal(contract, location, enclosure_path, memory_repo)
        self._assert_grant_pair_refused()

    def test_task_doc_cannot_self_grant(self) -> None:
        target = TaskDocTarget(repo_id="repo-a", task_name=MASTER_NAME, slug=LEAF_SLUG)
        smuggled = {
            "directGrant": {
                "leaf": {"path": self.leaf_ref.path, "repository": "repo-a"},
                "grantedBy": "architect",
            }
        }
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.config,
                TaskDocTarget(repo_id="repo-a", task_name=MASTER_NAME, slug="smuggled"),
                operation="create",
                edit=TaskDocEdit(
                    fields={
                        "id": "TEST-SMUGGLED",
                        "slug": "smuggled",
                        "title": "Smuggled",
                        "kind": "subTask",
                        "repo": "repo-a",
                        "createdAt": "2026-10-06T00:00:00+00:00",
                        **smuggled,
                    }
                ),
                call=TaskDocCall(),
            )
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.config,
                target,
                operation="replace",
                edit=TaskDocEdit(fields={"objective": "Smuggled.", **smuggled}),
                call=TaskDocCall(),
            )
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.config,
                target,
                operation="set_field",
                edit=TaskDocEdit(fields=dict(smuggled)),
                call=TaskDocCall(),
            )
        # None of the attempts granted the leaf or changed its document.
        self.assertIsNone(read_grant(self.coord, self.leaf_ref))
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        self.assertFalse((task_root / "smuggled.json").exists())
        leaf = read_task_doc(task_root / f"{LEAF_SLUG}.json")
        self.assertEqual(leaf.objective, "Exercise the grant store.")

    def test_revoke_after_start_is_refused_like_give(self) -> None:
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        self._mark_started(self.leaf_ref)
        with self.assertRaises(DirectGrantError) as raised:
            revoke_grant(self.config, self.leaf_ref)
        self.assertEqual(raised.exception.status, "direct-grant-leaf-started")
        self.assertIsNotNone(read_grant(self.coord, self.leaf_ref))

    def test_grant_holds_from_next_call_on_every_server(self) -> None:
        # One server: the same call is refused before the grant and admitted after it.
        with self.assertRaises(DirectGrantError):
            require_grant(self.coord, self.leaf_ref)
        change = give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        assert change.grant is not None
        self.assertEqual(require_grant(self.coord, self.leaf_ref), change.grant)
        # A second harness over the same root is admitted with no restart.
        other = McpRuntimeConfig(
            config_path=Path(self._tmp.name) / "other-settings.json",
            coordination_root=self.coord,
            workspace_root=Path(self._tmp.name) / "other-workspace",
            transcript_root=self.coord / "logs" / "other-mcp",
        )
        self.assertEqual(require_grant(other.coordination_root, self.leaf_ref), change.grant)

    def test_series_projection_shows_grant_and_giver_per_leaf(self) -> None:

        now = datetime(2026, 10, 6, tzinfo=UTC)
        nodes = read_series_documents(self.coord, now=now)
        self.assertEqual(len(nodes), 1)
        self.assertIsNone(nodes[0].subTasks[0].directGrant)
        change = give_grant(
            self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER, granter_agent_id="a1"
        )
        assert change.grant is not None
        nodes = read_series_documents(self.coord, now=now)
        grant = nodes[0].subTasks[0].directGrant
        self.assertIsNotNone(grant)
        assert grant is not None
        self.assertEqual(grant.grantedBy, "architect a1")
        self.assertEqual(grant.grantedAt, change.grant.granted_at)
        # A second leaf without a grant projects no grant node.
        second = self._second_leaf()
        give_grant(self.config, second, granted_by=DEVELOPER_GRANTER)
        nodes = read_series_documents(self.coord, now=now)
        by_number = {sub.number: sub for sub in nodes[0].subTasks}
        first_grant = by_number[LEAF_ID].directGrant
        assert first_grant is not None
        self.assertEqual(first_grant.grantedBy, "architect a1")
        second_grant = by_number["TEST-DIRECT-LEAF-2"].directGrant
        assert second_grant is not None
        self.assertEqual(second_grant.grantedBy, "developer")

    def test_malformed_grant_records_skip_projection_but_gate_refuses(self) -> None:

        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        store = grant_store_dir(self.coord)
        # Valid JSON with a structurally invalid canonical reference.
        (store / f"repo-a--{MASTER_NAME}--{'1' * 64}.json").write_text(
            json.dumps(
                {
                    "schema": "ar-direct-execution-grant/v1",
                    "leaf": {"repository": "repo-a", "path": f"{MASTER_NAME}/../evil.json"},
                    "grantedBy": "architect",
                    "granterAgentId": "fixture",
                    "grantedAt": "2026-10-06T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        # Broken syntax in this task's addressed name.
        (store / f"repo-a--{MASTER_NAME}--{'2' * 64}.json").write_text(
            "{not json", encoding="utf-8"
        )
        now = datetime(2026, 10, 6, tzinfo=UTC)
        series = read_series_documents(self.coord, now=now)
        self.assertEqual(len(series), 1)
        self.assertIsNotNone(series[0].subTasks[0].directGrant)
        nodes = read_task_documents(self.coord, enclosures=[], now=now)
        self.assertTrue(nodes)
        # The valid sibling grant still projects; gate reads refuse mismatch.
        other = TaskDocumentRef(repository="repo-a", path=f"{MASTER_NAME}/other.json")
        grant_path_for(self.coord, other).write_text(
            json.dumps(
                {
                    "schema": "ar-direct-execution-grant/v1",
                    "leaf": {"repository": "repo-a", "path": self.leaf_ref.path},
                    "grantedBy": "architect",
                    "granterAgentId": "fixture",
                    "grantedAt": "2026-10-06T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(DirectGrantError) as raised:
            read_grant(self.coord, other)
        self.assertEqual(raised.exception.status, "direct-grant-unreadable")

    def test_task_document_projection_carries_leaf_grant(self) -> None:

        now = datetime(2026, 10, 6, tzinfo=UTC)
        nodes = read_task_documents(self.coord, enclosures=[], now=now)
        by_id = {node.id: node for node in nodes}
        self.assertIsNone(by_id[LEAF_ID].directGrant)
        give_grant(self.config, self.leaf_ref, granted_by=DEVELOPER_GRANTER)
        nodes = read_task_documents(self.coord, enclosures=[], now=now)
        by_id = {node.id: node for node in nodes}
        self.assertIsNotNone(by_id[LEAF_ID].directGrant)
        grant = by_id[LEAF_ID].directGrant
        assert grant is not None
        self.assertEqual(grant.grantedBy, "developer")
        self.assertIsNone(by_id["TEST-DIRECT-MASTER"].directGrant)

    def test_stored_markdown_shows_grant_line_only_when_granted(self) -> None:

        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / f"{LEAF_SLUG}.json")
        markdown_path = markdown_path_for(task_root, leaf)
        write_task_doc(task_root, leaf)
        plain = markdown_path.read_text(encoding="utf-8")
        self.assertNotIn("Direct execution:", plain)
        # The architect give rewrites the stored Markdown itself: exactly one
        # current header with no extra task_doc write.
        change = give_grant(
            self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER, granter_agent_id="a1"
        )
        assert change.grant is not None
        granted = markdown_path.read_text(encoding="utf-8")
        lines = [line for line in granted.splitlines() if "Direct execution:" in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("architect a1", lines[0])
        self.assertIn(change.grant.granted_at, lines[0])
        # The revoke takes the header back out again by itself.
        revoke_grant(self.config, self.leaf_ref)
        # Ungranted Markdown is byte-identical to before the grant.
        self.assertEqual(markdown_path.read_text(encoding="utf-8"), plain)

    def test_developer_give_and_revoke_rewrite_stored_markdown(self) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / f"{LEAF_SLUG}.json")
        markdown_path = markdown_path_for(task_root, leaf)
        write_task_doc(task_root, leaf)
        plain = markdown_path.read_text(encoding="utf-8")
        gave = give_grant_developer(self.config, self.leaf_ref)
        self.assertTrue(gave.changed)
        lines = [
            line
            for line in markdown_path.read_text(encoding="utf-8").splitlines()
            if "Direct execution:" in line
        ]
        self.assertEqual(len(lines), 1)
        self.assertIn("developer", lines[0])
        took = revoke_grant_developer(self.config, self.leaf_ref)
        self.assertTrue(took.changed)
        self.assertEqual(markdown_path.read_text(encoding="utf-8"), plain)

    def test_ordinary_mutation_keeps_exactly_one_grant_header(self) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        give_grant(self.config, self.leaf_ref, granted_by=ARCHITECT_GRANTER)
        task_doc_tool(
            self.config,
            TaskDocTarget(repo_id="repo-a", task_name=MASTER_NAME, slug=LEAF_SLUG),
            operation="set_field",
            edit=TaskDocEdit(fields={"objective": "An ordinary edit."}),
            call=TaskDocCall(),
        )
        lines = [
            line
            for line in (task_root / f"{LEAF_SLUG}.md").read_text(encoding="utf-8").splitlines()
            if "Direct execution:" in line
        ]
        self.assertEqual(len(lines), 1)

    def test_dry_run_preview_agrees_with_stored_markdown(self) -> None:
        task_root = self.coord / "tasks" / "repo-a" / MASTER_NAME
        leaf = read_task_doc(task_root / f"{LEAF_SLUG}.json")
        give_grant(
            self.config,
            self.leaf_ref,
            granted_by=DEVELOPER_GRANTER,
        )
        preview = task_doc_preview("get", leaf, task_root)
        write_task_doc(task_root, leaf)
        stored = (task_root / f"{LEAF_SLUG}.md").read_text(encoding="utf-8")
        self.assertIn("Direct execution:", preview["rendered"])
        self.assertIn("developer", preview["rendered"])
        self.assertEqual(preview["rendered"], stored)
        self.assertEqual(markdown_path_for(task_root, leaf).read_text(encoding="utf-8"), stored)


if __name__ == "__main__":
    unittest.main()
