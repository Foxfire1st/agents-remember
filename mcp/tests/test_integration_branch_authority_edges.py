"""Production-bound edge forcing for protected integration refs."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.application import memory_tools
from agents_remember.application.lifecycle_operation_worker import OperationRuntime
from agents_remember.application.memory_tools import CarryoverSelection
from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.memory import carryover
from agents_remember.memory.carryover import CarryoverApplyOptions, CarryoverRequest
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    IntegrateOperationInput,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees import lifecycle_operations
from agents_remember.worktrees.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.closeout_queue_lifecycle import (
    certify_queue_candidate_closeout,
    claim_queue_candidate_for_closeout,
)
from agents_remember.worktrees.integration_branch_authority import integration_surfaces
from agents_remember.worktrees.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationRefSnapshot,
    merge_integrated_commits,
    prepare_integration_ref_move,
)
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules import integrate as integrate_module
from agents_remember.worktrees.modules import start as start_module
from agents_remember.worktrees.modules import start_contract
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import cleanup_result
from agents_remember.worktrees.modules.closeout import closeout_result
from agents_remember.worktrees.modules.git import branch_exists, ensure_worktree
from agents_remember.worktrees.modules.integrate import IntegrationSources, integrate_result
from agents_remember.worktrees.route_review import code_candidate_tree
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
    load_contract,
    write_contract,
)
from integration_branch_authority_test_support import (
    _acquire_atomic_barrier,
    _add_atomic_master_to_sprint,
    _assert_exact_series_preview,
    _authority_fixture,
    _closed_external_leaf_worktrees,
    _closed_leaf_worktree,
    _complete_atomic_master,
    _doc,
    _record_atomic_leaf_landing,
)
from test_closeout_queue import LEAF_A, MASTER_A, QueueFixture
from test_source_lineage import _commit_on, _git, _repo


class IntegrationBranchAuthorityEdgeTests(unittest.TestCase):
    def test_carryover_authority_refuses_each_configured_repository_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            memory = fixture.leaf_contract.memory_repo_path
            assert memory is not None
            request = CarryoverRequest(
                config_path=fixture.config_path,
                target_contract_path=fixture.leaf_contract.contract_path,
                code_repository_root=fixture.code_repo,
                official_code_ref="main",
                source_code_ref="leaf",
                old_base=fixture.leaf_contract.code_base_commit,
                target_memory=memory,
                source_memory=memory,
                code_repository_name="repo",
            )
            authority = load_config(request.config_path)
            cases = (
                (
                    replace(request, code_repository_root=memory),
                    "code repository does not match",
                ),
                (
                    replace(request, target_memory=fixture.code_repo),
                    "target memory does not match",
                ),
            )
            for changed, reason in cases:
                with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, reason):
                    carryover._require_carryover_authority(changed, authority)

        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            memory = fixture.leaf_contract.memory_repo_path
            assert memory is not None
            request = CarryoverRequest(
                config_path=fixture.config_path,
                target_contract_path=fixture.leaf_contract.contract_path,
                code_repository_root=fixture.code_repo,
                official_code_ref="main",
                source_code_ref="leaf",
                old_base=fixture.leaf_contract.code_base_commit,
                target_memory=memory,
                source_memory=memory,
                code_repository_name="repo",
            )
            authority = load_config(request.config_path)
            configured = authority.repositories["repo"]
            authority = replace(
                authority,
                repositories={
                    **authority.repositories,
                    "repo": replace(configured, memory_root=None),
                },
            )
            with self.assertRaisesRegex(RuntimeError, "configured external memory"):
                carryover._require_carryover_authority(
                    request,
                    authority,
                )

    def test_series_attach_is_not_an_integration_branch_workbench(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))

            with self.assertRaisesRegex(RuntimeError, "not a resumable workbench"):
                start_module.attach_result(
                    WorktreeArgs(contract_path=fixture.master_contract.contract_path)
                )

    def test_lowest_worktree_creator_refuses_series_code_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            series = fixture.master_contract
            code_before = _git(fixture.code_repo, "show-ref")
            memory_repo = series.memory_repo_path
            assert memory_repo is not None
            memory_before = _git(memory_repo, "show-ref")

            for side in ("code", "memory"):
                with (
                    self.subTest(side=side),
                    self.assertRaisesRegex(RuntimeError, "ordinary leaf contract"),
                ):
                    ensure_worktree(series, side=side, dry_run=False)

            self.assertEqual(_git(fixture.code_repo, "show-ref"), code_before)
            self.assertEqual(_git(memory_repo, "show-ref"), memory_before)

    def test_terminal_series_cleanup_refuses_symbolic_branch_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            memory = fixture.master_contract.memory_repo_path
            assert memory is not None
            _complete_atomic_master(fixture)
            _git(fixture.code_repo, "branch", "-D", fixture.leaf_contract.code_work_branch)
            _git(memory, "branch", "-D", fixture.leaf_contract.memory_work_branch)
            for side, repository in (("code", fixture.code_repo), ("memory", memory)):
                alias = f"alias-{side}"
                _git(
                    repository,
                    "symbolic-ref",
                    f"refs/heads/{alias}",
                    "refs/heads/ar/master",
                )
                malicious = replace(
                    fixture.master_contract,
                    **(
                        {"code_work_branch": alias}
                        if side == "code"
                        else {"memory_work_branch": alias}
                    ),
                )
                retired_child = replace(
                    fixture.leaf_contract,
                    cleanup="completed",
                    **(
                        {"code_source_branch": alias}
                        if side == "code"
                        else {"memory_source_branch": alias}
                    ),
                )
                write_contract(malicious.contract_path, malicious)
                write_contract(retired_child.contract_path, retired_child)
                with (
                    self.subTest(side=side),
                    self.assertRaisesRegex(RuntimeError, "exact task-owned spelling"),
                ):
                    cleanup_result(
                        WorktreeArgs(contract_path=malicious.contract_path, dry_run=True)
                    )
                self.assertTrue(branch_exists(repository, "ar/master"))

    def test_terminal_series_cleanup_refuses_while_its_atomic_barrier_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            _acquire_atomic_barrier(fixture)
            _complete_atomic_master(fixture)
            integrated = _git(fixture.code_repo, "rev-parse", "ar/master")
            series = replace(
                fixture.master_contract,
                integration_status="completed",
                integrated_code_commit=integrated,
            )
            write_contract(series.contract_path, series)

            with self.assertRaisesRegex(
                CloseoutQueueError,
                "landing barrier to be released",
            ):
                cleanup_result(WorktreeArgs(contract_path=series.contract_path, dry_run=True))

            self.assertTrue(branch_exists(fixture.code_repo, "ar/master"))

    def test_direct_ref_writer_requires_a_plane_prepared_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            before = _git(fixture.code_repo, "rev-parse", "refs/heads/ar/master")
            target = _git(fixture.code_repo, "rev-parse", "refs/heads/leaf")

            with self.assertRaisesRegex(RuntimeError, "plane-prepared integration capability"):
                merge_integrated_commits(
                    fixture.leaf_contract,
                    IntegratedCommits(code=target, memory_content="", ledger=""),
                    IntegrationRefSnapshot(code_branch="ar/master", code_before=before),
                )
            self.assertEqual(_git(fixture.code_repo, "rev-parse", "refs/heads/ar/master"), before)

    def test_prepare_ref_move_requires_the_plane_owned_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            _commit_on(fixture.code_repo, "leaf", "candidate.txt")
            candidate = _git(fixture.code_repo, "rev-parse", "leaf")
            source = _git(fixture.code_repo, "rev-parse", "ar/master")
            contract = replace(
                fixture.leaf_contract,
                closeout_status="completed",
                code_commit=candidate,
            )

            with self.assertRaisesRegex(RuntimeError, "plane-owned journaled"):
                prepare_integration_ref_move(
                    contract,
                    IntegratedCommits(code=candidate, memory_content="", ledger=""),
                    WorktreeArgs(),
                    IntegrationSources(
                        current_code_source=source,
                        current_memory_source="",
                        code_replay_required=False,
                        memory_replay_required=False,
                    ),
                )
            self.assertEqual(_git(fixture.code_repo, "rev-parse", "ar/master"), source)

    def test_direct_bootstrap_ref_writer_requires_the_journal_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            base = _git(fixture.code_repo, "rev-parse", "refs/heads/super")

            with self.assertRaisesRegex(RuntimeError, "journaled bootstrap capability"):
                start_contract._require_bootstrap_ref(
                    start_contract._BootstrapRef(
                        repository=fixture.code_repo,
                        branch="ar/direct-helper",
                        commit=base,
                        source_branch="super",
                        source_commit=base,
                    ),
                )
            self.assertFalse(branch_exists(fixture.code_repo, "ar/direct-helper"))

    def test_bootstrap_ignores_same_named_tag_and_creates_the_exact_local_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            task_root = fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(fixture, task_root)
            base = _git(fixture.code_repo, "rev-parse", "refs/heads/super")
            _git(fixture.code_repo, "tag", "ar/atomic-three", base)

            contract = start_contract.ensure_master_series_contract(
                start_contract.MasterSeriesContractSpec(
                    coordination_root=fixture.coordination,
                    repo_name="repo",
                    code_repo=fixture.code_repo,
                    memory_root=None,
                    task_root=task_root,
                    task_name="atomic-three",
                    parent_task_name="sprint",
                    protected_branch="super",
                )
            )
            self.assertEqual(
                _git(fixture.code_repo, "rev-parse", "refs/heads/ar/atomic-three"), base
            )
            self.assertEqual(contract.code_work_branch, "ar/atomic-three")

    def test_tag_cannot_masquerade_as_the_declared_series_source_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            task_root = fixture.coordination / "tasks" / "repo" / "atomic-three"
            _add_atomic_master_to_sprint(fixture, task_root)
            _git(fixture.code_repo, "tag", "tag-only-super", "refs/heads/super")
            sprint_path = fixture.coordination / "tasks" / "repo" / "sprint" / "task.json"
            sprint = read_task_doc(sprint_path)
            write_task_doc(
                sprint_path.parent,
                sprint.model_copy(update={"integrationBranch": "tag-only-super"}),
            )

            with self.assertRaisesRegex(RuntimeError, "source branch does not exist"):
                start_contract.ensure_master_series_contract(
                    start_contract.MasterSeriesContractSpec(
                        coordination_root=fixture.coordination,
                        repo_name="repo",
                        code_repo=fixture.code_repo,
                        memory_root=None,
                        task_root=task_root,
                        task_name="atomic-three",
                        parent_task_name="sprint",
                        protected_branch="tag-only-super",
                    )
                )
            self.assertFalse(branch_exists(fixture.code_repo, "ar/atomic-three"))

    def test_standalone_atomic_bootstrap_requires_the_repository_default_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            task_root = fixture.coordination / "tasks" / "repo" / "standalone"
            write_task_doc(
                task_root,
                _doc(
                    id="STANDALONE",
                    slug="standalone",
                    title="Standalone",
                    kind="master",
                    executionNature="atomic",
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "repository-default branch"):
                start_contract.ensure_master_series_contract(
                    start_contract.MasterSeriesContractSpec(
                        coordination_root=fixture.coordination,
                        repo_name="repo",
                        code_repo=fixture.code_repo,
                        memory_root=None,
                        task_root=task_root,
                        task_name="standalone",
                        parent_task_name="",
                        protected_branch="super",
                    )
                )

    def test_organizational_leaf_sources_super_without_a_series_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            task_root = fixture.coordination / "tasks" / "repo" / "org-master"
            write_task_doc(
                task_root,
                _doc(
                    id="ORG-MASTER",
                    slug="org-master",
                    title="Organizational Master",
                    kind="master",
                    executionNature="organizational",
                ),
            )
            sprint_path = fixture.coordination / "tasks" / "repo" / "sprint" / "task.json"
            sprint = read_task_doc(sprint_path)
            assert sprint.executionGraph is not None
            org_ref = TaskDocumentRef(repository="repo", path="org-master/task.json")
            write_task_doc(
                sprint_path.parent,
                sprint.model_copy(
                    update={
                        "orchestrates": [*sprint.orchestrates, "org-master"],
                        "executionGraph": sprint.executionGraph.model_copy(
                            update={"nodes": [*sprint.executionGraph.nodes, org_ref]}
                        ),
                    }
                ),
            )
            context = type(
                "Context",
                (),
                {
                    "coordination_root": fixture.coordination,
                    "code_repository_name": "repo",
                    "code_repository_root": fixture.code_repo,
                    "memory_mode": "disabled",
                },
            )()
            with (
                mock.patch.object(
                    start_contract,
                    "resolve_active_task_root",
                    return_value=task_root,
                ),
                mock.patch.object(
                    start_contract,
                    "resolve_start_leaf_doc_id",
                    return_value="ORG-LEAF",
                ),
            ):
                contract = start_contract._build_start_contract(
                    context,
                    WorktreeArgs(
                        task_name="org-master",
                        worktree_name="org-leaf",
                        leaf_id="ORG-LEAF",
                        memory_mode="disabled",
                        dry_run=True,
                    ),
                )
            self.assertIsInstance(contract, WorktreeContract)
            assert isinstance(contract, WorktreeContract)
            self.assertEqual(contract.code_source_branch, "super")
            self.assertIsNone(contract.parent_contract_path)
            self.assertFalse(branch_exists(fixture.code_repo, "ar/org-master"))
            self.assertFalse((task_root / "series-contract.md").exists())

    def test_same_git_common_dir_cannot_masquerade_as_external_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            alias = root / "code-as-memory"
            alias.symlink_to(fixture.code_repo, target_is_directory=True)
            malicious = replace(
                fixture.leaf_contract,
                memory_mode="external",
                memory_repo_path=alias,
                memory_source_branch="ar/master",
                memory_work_branch="leaf",
                memory_worktree=alias,
                memory_base_commit=fixture.leaf_contract.code_base_commit,
                ledger_path=alias / "memory.md",
            )

            with self.assertRaisesRegex(RuntimeError, "must not share.*Git common-dir"):
                integration_surfaces(malicious)

    def test_cross_sprint_super_collision_is_refused_repo_globally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            task_root = fixture.coordination / "tasks" / "repo"
            other_ref = TaskDocumentRef(repository="repo", path="other-master/task.json")
            write_task_doc(
                task_root / "other-master",
                _doc(
                    id="OTHER",
                    slug="other-master",
                    title="Other",
                    kind="master",
                    executionNature="organizational",
                ),
            )
            write_task_doc(
                task_root / "other-sprint",
                _doc(
                    id="OTHER-SPRINT",
                    slug="other-sprint",
                    title="Other Sprint",
                    kind="master",
                    orchestrates=["other-master"],
                    integrationBranch="super",
                    executionGraph={"nodes": [other_ref.model_dump()], "edges": []},
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "authority collision"):
                integration_surfaces(fixture.leaf_contract)

    def test_atomic_alias_collision_between_distinct_owners_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            task_root = fixture.coordination / "tasks" / "repo"
            sprint_path = task_root / "sprint" / "task.json"
            sprint = read_task_doc(sprint_path)
            alias_ref = TaskDocumentRef(repository="repo", path="alias-atomic/task.json")
            assert sprint.executionGraph is not None
            write_task_doc(
                task_root / "alias-atomic",
                _doc(
                    id="ALIAS-ATOMIC",
                    slug="alias-atomic",
                    title="Alias Atomic",
                    kind="master",
                    executionNature="atomic",
                ),
            )
            write_task_doc(
                sprint_path.parent,
                sprint.model_copy(
                    update={
                        "orchestrates": [*sprint.orchestrates, "alias-atomic"],
                        "executionGraph": sprint.executionGraph.model_copy(
                            update={"nodes": [*sprint.executionGraph.nodes, alias_ref]}
                        ),
                    }
                ),
            )
            _git(
                fixture.code_repo,
                "symbolic-ref",
                "refs/heads/ar/alias-atomic",
                "refs/heads/ar/master",
            )
            contract = default_series_contract(
                ContractTask(
                    "alias-atomic",
                    "repo",
                    fixture.coordination,
                    "light-task",
                    "disabled",
                ),
                code=RepoBranchPlan(
                    fixture.code_repo,
                    "super",
                    "ar/alias-atomic",
                    _git(fixture.code_repo, "rev-parse", "super"),
                ),
                task_root=task_root / "alias-atomic",
            )
            write_contract(contract.contract_path, contract)

            with self.assertRaisesRegex(RuntimeError, "authority collision"):
                integration_surfaces(fixture.leaf_contract)

    def test_configured_repository_identity_refuses_foreign_contract_before_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            foreign = _repo(root / "foreign")
            foreign_candidate = fixture.leaf_contract.worktree_group / "foreign-candidate"
            foreign_candidate.parent.mkdir(parents=True, exist_ok=True)
            _git(foreign, "worktree", "add", foreign_candidate.as_posix(), "leaf")
            malicious = replace(
                _closed_leaf_worktree(fixture, root, candidate_commit=True),
                code_repo_path=foreign,
                code_worktree=foreign_candidate,
                code_source_branch="ar/master",
                code_work_branch="leaf",
                code_commit=_git(foreign, "rev-parse", "leaf"),
            )
            write_contract(malicious.contract_path, malicious)
            record_path = operation_record_path(malicious.worktree_group, "integrate")

            with self.assertRaisesRegex(RuntimeError, "does not match configured authority"):
                lifecycle_operations.start_or_observe_operation(
                    IntegrateOperationInput(
                        configPath=fixture.config_path.as_posix(),
                        contractPath=malicious.contract_path.as_posix(),
                    ),
                    launcher=lambda *_: None,
                )
            self.assertFalse(record_path.exists())

    def test_configured_authority_refuses_foreign_coordination_and_task_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            closed = _closed_leaf_worktree(fixture, root, candidate_commit=True)
            foreign = root / "foreign-coordination"
            candidates = (
                replace(closed, coordination_root=foreign),
                replace(
                    closed,
                    task_root=foreign / "tasks" / "repo" / "master",
                    task_artifact=foreign / "tasks" / "repo" / "master" / "task.md",
                ),
            )
            for malicious in candidates:
                write_contract(closed.contract_path, malicious)
                record_path = operation_record_path(closed.worktree_group, "integrate")
                with (
                    self.subTest(task_root=malicious.task_root),
                    self.assertRaisesRegex(
                        RuntimeError,
                        "coordination root|task root|contract path",
                    ),
                ):
                    lifecycle_operations.start_or_observe_operation(
                        IntegrateOperationInput(
                            configPath=fixture.config_path.as_posix(),
                            contractPath=closed.contract_path.as_posix(),
                        ),
                        launcher=lambda *_: None,
                    )
                self.assertFalse(record_path.exists())

    def test_lifecycle_authority_requires_the_configured_memory_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = _authority_fixture(root / "external", external_memory=True)
            external_contract = _closed_external_leaf_worktrees(
                external,
                root / "external",
            )
            for memory_mode in ("disabled", "internal"):
                with self.subTest(configured="external", contract=memory_mode):
                    forged = replace(external_contract, memory_mode=memory_mode)
                    write_contract(forged.contract_path, forged)
                    with self.assertRaisesRegex(RuntimeError, "memory mode"):
                        lifecycle_operations.start_or_observe_operation(
                            IntegrateOperationInput(
                                configPath=external.config_path.as_posix(),
                                contractPath=forged.contract_path.as_posix(),
                            ),
                            launcher=lambda *_: None,
                        )
                    self.assertFalse(
                        operation_record_path(forged.worktree_group, "integrate").exists()
                    )

            disabled = _authority_fixture(root / "disabled")
            disabled_contract = _closed_leaf_worktree(
                disabled,
                root / "disabled",
                candidate_commit=False,
            )
            memory_repo = _repo(root / "disabled-memory")
            memory_worktree = disabled_contract.worktree_group / "memory-leaf"
            _git(memory_repo, "worktree", "add", memory_worktree.as_posix(), "leaf")
            forged_external = replace(
                disabled_contract,
                memory_mode="external",
                memory_repo_path=memory_repo,
                memory_source_branch="ar/master",
                memory_work_branch="leaf",
                memory_worktree=memory_worktree,
                memory_base_commit=_git(memory_repo, "rev-parse", "ar/master"),
                ledger_path=memory_worktree / "memory.md",
            )
            write_contract(forged_external.contract_path, forged_external)
            with self.assertRaisesRegex(RuntimeError, "memory mode"):
                lifecycle_operations.start_or_observe_operation(
                    IntegrateOperationInput(
                        configPath=disabled.config_path.as_posix(),
                        contractPath=forged_external.contract_path.as_posix(),
                    ),
                    launcher=lambda *_: None,
                )

    def test_carryover_apply_cannot_commit_on_official_protected_memory_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            memory = fixture.leaf_contract.memory_repo_path
            assert memory is not None
            protected = replace(
                fixture.leaf_contract,
                memory_worktree=memory,
                memory_work_branch=_git(memory, "branch", "--show-current"),
                ledger_path=memory / "memory.md",
            )
            write_contract(protected.contract_path, protected)
            request = CarryoverRequest(
                config_path=fixture.config_path,
                target_contract_path=protected.contract_path,
                code_repository_root=fixture.code_repo,
                official_code_ref="main",
                source_code_ref="leaf",
                old_base=fixture.leaf_contract.code_base_commit,
                target_memory=memory,
                source_memory=memory,
                code_repository_name="repo",
            )

            with (
                mock.patch.object(carryover, "build_plan_for_request") as plan,
                self.assertRaisesRegex(RuntimeError, "integration-branch-is-not-a-workbench"),
            ):
                carryover._apply_carryover_for_request(
                    request,
                    authority=load_config(request.config_path),
                    options=CarryoverApplyOptions(intent_note="attempt protected write"),
                )
            plan.assert_not_called()

    def test_mcp_carryover_uses_the_same_shared_protected_checkout_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            config = load_config(fixture.config_path)
            memory = fixture.leaf_contract.memory_repo_path
            assert memory is not None
            source_memory = fixture.coordination / "carryover-source"
            _git(memory, "worktree", "add", source_memory.as_posix(), "leaf")
            protected = replace(
                fixture.leaf_contract,
                memory_worktree=memory,
                memory_work_branch=_git(memory, "branch", "--show-current"),
                ledger_path=memory / "memory.md",
            )
            write_contract(protected.contract_path, protected)

            with self.assertRaisesRegex(RuntimeError, "integration-branch-is-not-a-workbench"):
                memory_tools.memory_carryover_apply_tool(
                    config,
                    CarryoverSelection(
                        repo_id="repo",
                        contract_path=protected.contract_path.as_posix(),
                        source_memory=source_memory.as_posix(),
                        official_code_ref="main",
                        source_code_ref="leaf",
                        old_base=fixture.leaf_contract.code_base_commit,
                    ),
                    intent_note="attempt protected write",
                )

    def test_carryover_refuses_external_memory_aliasing_the_code_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            alias = Path(tmp) / "memory-alias"
            alias.symlink_to(fixture.code_repo, target_is_directory=True)
            configured_memory = fixture.coordination / "memory-repos" / "ar-repo"
            configured_memory.unlink()
            configured_memory.symlink_to(fixture.code_repo, target_is_directory=True)
            aliased_contract = replace(
                fixture.leaf_contract,
                memory_repo_path=alias,
                memory_worktree=alias,
                ledger_path=alias / "memory.md",
            )
            write_contract(aliased_contract.contract_path, aliased_contract)
            request = CarryoverRequest(
                config_path=fixture.config_path,
                target_contract_path=aliased_contract.contract_path,
                code_repository_root=fixture.code_repo,
                official_code_ref="main",
                source_code_ref="leaf",
                old_base=aliased_contract.code_base_commit,
                target_memory=alias,
                source_memory=alias,
                code_repository_name="repo",
            )

            with self.assertRaisesRegex(RuntimeError, "must not share.*Git common-dir"):
                carryover._apply_carryover_for_request(
                    request,
                    authority=load_config(request.config_path),
                    options=CarryoverApplyOptions(intent_note="invalid shared repo"),
                )

    def test_contract_finalizes_before_queue_lane_release_and_retry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = QueueFixture(root)
            fixture.declare(MASTER_A)
            fixture.mutate("select", candidate=LEAF_A)
            candidate = fixture.contracts[MASTER_A]
            lifecycle_operations.start_or_observe_operation(
                CloseoutOperationInput(
                    configPath=fixture.config_path.as_posix(),
                    contractPath=candidate.contract_path.as_posix(),
                    codeCommitMessage="close candidate",
                    approvalNote="approved",
                ),
                launcher=lambda *_: None,
            )
            closeout = LifecycleOperationStore(
                operation_record_path(candidate.worktree_group, "closeout")
            ).read()
            assert closeout is not None
            claim_queue_candidate_for_closeout(candidate, closeout.operationKey)
            closed = fixture.close_contract(MASTER_A)
            certify_queue_candidate_closeout(closed, closeout.operationKey)
            closeout_store = LifecycleOperationStore(
                operation_record_path(candidate.worktree_group, "closeout")
            )
            closeout_store.update(
                lambda record: record.model_copy(
                    update={"status": "running", "phase": "contract-finalization"}
                )
            )
            closeout_store.update(
                lambda record: record.model_copy(
                    update={
                        "status": "completed",
                        "phase": "completed",
                        "finishedAt": "2026-08-15T00:01:00+00:00",
                    }
                )
            )
            _git(closed.code_repo_path, "checkout", closed.code_source_branch)
            assert closed.memory_repo_path is not None
            _git(closed.memory_repo_path, "checkout", closed.memory_source_branch)
            lifecycle_operations.start_or_observe_operation(
                IntegrateOperationInput(
                    configPath=fixture.config_path.as_posix(),
                    contractPath=closed.contract_path.as_posix(),
                ),
                launcher=lambda *_: None,
            )
            store = LifecycleOperationStore(
                operation_record_path(closed.worktree_group, "integrate")
            )
            runtime = OperationRuntime(store)
            running = runtime.start()

            def fail_after_contract(*_args, **_kwargs) -> None:
                self.assertEqual(
                    load_contract(closed.contract_path).integration_status,
                    "completed",
                )
                raise RuntimeError("queue publication crash")

            with (
                mock.patch.object(
                    integrate_module,
                    "_run_integration_quality_gate",
                    return_value=({"passed": True}, None),
                ),
                mock.patch.object(
                    integrate_module,
                    "complete_queue_candidate_integration",
                    side_effect=fail_after_contract,
                ),
                self.assertRaisesRegex(RuntimeError, "queue publication crash"),
            ):
                integrate_result(
                    WorktreeArgs(
                        contract_path=closed.contract_path,
                        approved=True,
                        operation_key=running.operationKey,
                        operation_progress=runtime.progress,
                    )
                )

            recorded = store.read()
            assert recorded is not None
            recovered = integrate_result(
                WorktreeArgs(
                    contract_path=closed.contract_path,
                    approved=True,
                    operation_key=running.operationKey,
                    recovery_commits=recorded.recoveryCommits,
                )
            )
            self.assertEqual(recovered.payload["state"], "already-integrated")
            self.assertEqual(
                _git(closed.code_repo_path, "rev-parse", closed.code_source_branch),
                closed.code_commit,
            )
            projected = fixture.status()
            for lane in ("ready", "inFlight", "blocked"):
                self.assertFalse(
                    any(item["taskDocumentRef"] == LEAF_A.model_dump() for item in projected[lane])
                )

    def test_clean_linked_owner_is_refreshed_after_exact_named_ref_cas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            closed = _closed_leaf_worktree(fixture, root, candidate_commit=True)
            write_contract(closed.contract_path, closed)
            _git(fixture.code_repo, "switch", "main")
            owner = root / "atomic-owner"
            _git(fixture.code_repo, "worktree", "add", owner.as_posix(), "ar/master")
            lifecycle_operations.start_or_observe_operation(
                IntegrateOperationInput(
                    configPath=fixture.config_path.as_posix(),
                    contractPath=closed.contract_path.as_posix(),
                ),
                launcher=lambda *_: None,
            )
            store = LifecycleOperationStore(
                operation_record_path(closed.worktree_group, "integrate")
            )
            running = OperationRuntime(store).start()

            with (
                mock.patch.object(
                    integrate_module,
                    "_run_integration_quality_gate",
                    return_value=({"passed": True}, None),
                ),
                mock.patch.object(integrate_module, "claim_queue_candidate_for_integration"),
                mock.patch.object(
                    integrate_module,
                    "publish_queue_candidate_integration_under_authority",
                    side_effect=lambda _contract, publication, **_kwargs: publication(),
                ),
                mock.patch.object(integrate_module, "complete_queue_candidate_integration"),
            ):
                result = integrate_result(
                    WorktreeArgs(
                        contract_path=closed.contract_path,
                        approved=True,
                        operation_key=running.operationKey,
                    )
                )

            self.assertEqual(result.payload["state"], "integrated")
            self.assertEqual(_git(owner, "rev-parse", "HEAD"), closed.code_commit)

    def test_series_closeout_uses_named_refs_and_ignores_dirty_ambient_super(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            _acquire_atomic_barrier(fixture)
            memory = fixture.master_contract.memory_repo_path
            assert memory is not None
            _commit_on(fixture.code_repo, "ar/master", "atomic-code.txt")
            code_commit = _git(fixture.code_repo, "rev-parse", "ar/master")
            _commit_on(memory, "ar/master", "atomic-memory.md")
            memory_commit = _git(memory, "rev-parse", "ar/master")
            write_ledger(
                memory / "memory.md",
                create_initial_ledger("repo", code_commit, memory_commit),
            )
            _git(memory, "add", "memory.md")
            _git(memory, "commit", "-m", "atomic ledger")
            ledger_commit = _git(memory, "rev-parse", "ar/master")
            _record_atomic_leaf_landing(
                fixture,
                code_commit,
                memory_content_commit=memory_commit,
                ledger_commit=ledger_commit,
            )
            _git(fixture.code_repo, "switch", "super")
            _git(memory, "switch", "super")
            (fixture.code_repo / "ambient.txt").write_text("dirty super\n", encoding="utf-8")
            (memory / "ambient.md").write_text("dirty super\n", encoding="utf-8")
            series = load_contract(fixture.master_contract.contract_path)
            _complete_atomic_master(fixture)

            preview = closeout_result(
                WorktreeArgs(contract_path=series.contract_path, dry_run=True)
            )
            _assert_exact_series_preview(self, preview)

            result = closeout_result(
                WorktreeArgs(
                    contract_path=series.contract_path,
                    approved=True,
                    approval_note="approve exact landed atomic refs",
                    candidate_tree=code_candidate_tree(series),
                    recovery_commits=LifecycleOperationRecoveryCommits(codeCommit=code_commit),
                )
            )
            self.assertEqual(result.payload["code_commit"], code_commit)
            self.assertEqual(result.payload["memory_content_commit"], memory_commit)
            self.assertEqual(result.payload["ledger_commit"], ledger_commit)
            self.assertEqual(_git(fixture.code_repo, "rev-parse", "super"), series.code_base_commit)

    def test_series_integration_moves_named_super_while_ambient_checkout_is_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            _acquire_atomic_barrier(fixture)
            _commit_on(fixture.code_repo, "ar/master", "atomic-block.txt")
            candidate = _git(fixture.code_repo, "rev-parse", "ar/master")
            _record_atomic_leaf_landing(fixture, candidate)
            _complete_atomic_master(fixture)
            series = replace(
                fixture.master_contract,
                closeout_status="completed",
                approved_for_commit=True,
                human_review_status="approved",
                code_commit=candidate,
            )
            write_contract(series.contract_path, series)
            lifecycle_operations.start_or_observe_operation(
                IntegrateOperationInput(
                    configPath=fixture.config_path.as_posix(),
                    contractPath=series.contract_path.as_posix(),
                ),
                launcher=lambda *_: None,
            )
            store = LifecycleOperationStore(
                operation_record_path(series.worktree_group, "integrate")
            )
            running = OperationRuntime(store).start()
            _git(fixture.code_repo, "switch", "main")
            main_before = _git(fixture.code_repo, "rev-parse", "main")

            with mock.patch.object(
                integrate_module,
                "_run_integration_quality_gate",
                return_value=({"passed": True}, None),
            ):
                result = integrate_result(
                    WorktreeArgs(
                        contract_path=series.contract_path,
                        approved=True,
                        operation_key=running.operationKey,
                    )
                )

            self.assertEqual(result.payload["state"], "integrated")
            self.assertEqual(_git(fixture.code_repo, "rev-parse", "super"), candidate)
            self.assertEqual(_git(fixture.code_repo, "rev-parse", "main"), main_before)

    def test_series_integration_refuses_without_its_active_atomic_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            _commit_on(fixture.code_repo, "ar/master", "atomic-block.txt")
            candidate = _git(fixture.code_repo, "rev-parse", "ar/master")
            _complete_atomic_master(fixture)
            series = replace(
                fixture.master_contract,
                closeout_status="completed",
                approved_for_commit=True,
                human_review_status="approved",
                code_commit=candidate,
            )
            write_contract(series.contract_path, series)
            lifecycle_operations.start_or_observe_operation(
                IntegrateOperationInput(
                    configPath=fixture.config_path.as_posix(),
                    contractPath=series.contract_path.as_posix(),
                ),
                launcher=lambda *_: None,
            )
            store = LifecycleOperationStore(
                operation_record_path(series.worktree_group, "integrate")
            )
            running = OperationRuntime(store).start()
            super_before = _git(fixture.code_repo, "rev-parse", "super")

            with (
                mock.patch.object(
                    integrate_module,
                    "_run_integration_quality_gate",
                    return_value=({"passed": True}, None),
                ),
                self.assertRaisesRegex(CloseoutQueueError, "active sprint landing barrier"),
            ):
                integrate_result(
                    WorktreeArgs(
                        contract_path=series.contract_path,
                        approved=True,
                        operation_key=running.operationKey,
                    )
                )
            self.assertEqual(_git(fixture.code_repo, "rev-parse", "super"), super_before)

    def test_external_series_integration_recovers_exact_pair_without_ambient_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            _acquire_atomic_barrier(fixture)
            memory_repo = fixture.master_contract.memory_repo_path
            assert memory_repo is not None
            _commit_on(fixture.code_repo, "ar/master", "atomic-code.txt")
            code_candidate = _git(fixture.code_repo, "rev-parse", "ar/master")
            _commit_on(memory_repo, "ar/master", "atomic-memory.md")
            memory_content = _git(memory_repo, "rev-parse", "ar/master")
            write_ledger(
                memory_repo / "memory.md",
                create_initial_ledger("repo", code_candidate, memory_content),
            )
            _git(memory_repo, "add", "memory.md")
            _git(memory_repo, "commit", "-m", "Record atomic ledger")
            ledger_commit = _git(memory_repo, "rev-parse", "ar/master")
            _record_atomic_leaf_landing(
                fixture,
                code_candidate,
                memory_content_commit=memory_content,
                ledger_commit=ledger_commit,
            )
            _complete_atomic_master(fixture)
            series = replace(
                fixture.master_contract,
                closeout_status="completed",
                approved_for_commit=True,
                human_review_status="approved",
                code_commit=code_candidate,
                memory_content_commit=memory_content,
                ledger_commit=ledger_commit,
            )
            write_contract(series.contract_path, series)
            lifecycle_operations.start_or_observe_operation(
                IntegrateOperationInput(
                    configPath=fixture.config_path.as_posix(),
                    contractPath=series.contract_path.as_posix(),
                ),
                launcher=lambda *_: None,
            )
            store = LifecycleOperationStore(
                operation_record_path(series.worktree_group, "integrate")
            )
            runtime = OperationRuntime(store)
            running = runtime.start()
            _git(fixture.code_repo, "switch", "main")
            _git(memory_repo, "switch", "main")
            code_main_before = _git(fixture.code_repo, "rev-parse", "main")
            memory_main_before = _git(memory_repo, "rev-parse", "main")

            with (
                mock.patch.object(
                    integrate_module,
                    "_run_integration_quality_gate",
                    return_value=({"passed": True}, None),
                ),
                mock.patch.object(
                    integrate_module,
                    "write_contract",
                    side_effect=RuntimeError("crash before contract finalization"),
                ),
                self.assertRaisesRegex(RuntimeError, "crash before contract finalization"),
            ):
                integrate_result(
                    WorktreeArgs(
                        contract_path=series.contract_path,
                        approved=True,
                        operation_key=running.operationKey,
                        operation_progress=runtime.progress,
                    )
                )

            self.assertEqual(_git(fixture.code_repo, "rev-parse", "super"), code_candidate)
            self.assertEqual(_git(memory_repo, "rev-parse", "super"), ledger_commit)
            crashed = store.read()
            assert crashed is not None
            assert crashed.recoveryCommits is not None
            recovered = integrate_result(
                WorktreeArgs(
                    contract_path=series.contract_path,
                    approved=True,
                    operation_key=running.operationKey,
                    recovery_commits=crashed.recoveryCommits,
                    operation_progress=runtime.progress,
                )
            )

            self.assertEqual(recovered.payload["state"], "integrated")
            completed = load_contract(series.contract_path)
            self.assertEqual(completed.integrated_code_commit, code_candidate)
            self.assertEqual(completed.integrated_memory_content_commit, memory_content)
            self.assertEqual(completed.integrated_ledger_commit, ledger_commit)
            self.assertEqual(_git(fixture.code_repo, "rev-parse", "main"), code_main_before)
            self.assertEqual(_git(memory_repo, "rev-parse", "main"), memory_main_before)
