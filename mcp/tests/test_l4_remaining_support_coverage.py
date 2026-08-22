"""Reach the remaining small L4 authority branches through their real owners."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.application import memory_tools
from agents_remember.application.lifecycle_operation_worker import OperationRuntime
from agents_remember.application.structural import agent_tools
from agents_remember.application.task_docs import task_doc_tools, task_execution_topology
from agents_remember.controlplane import closeout_queue_store
from agents_remember.kernel import memory_init
from agents_remember.memory import baseline, carryover
from agents_remember.models.lifecycles.operation import IntegrateOperationInput
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import document_refs
from agents_remember.worktrees import atomic_series_seal, series_closeout, source_lineage
from agents_remember.worktrees.integration import integration_quality_checkout, lifecycle_operations
from agents_remember.worktrees.integration.integration_ref_transaction import IntegratedCommits
from agents_remember.worktrees.modules import (
    abandon,
    cleanup,
    closeout,
    git,
    guidance,
    integrate,
    sync,
    terminal_validation,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.queue import (
    closeout_preview,
    closeout_queue_candidate_evidence,
    closeout_recovery,
)
from integration_branch_authority_test_support import _authority_fixture


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class ApplicationAuthorityRemainderTests(unittest.TestCase):
    def test_worker_refuses_a_queued_record_reserved_for_another_process(self) -> None:
        record = SimpleNamespace(status="queued", workerPid=99_999_999)
        store = SimpleNamespace(update=lambda callback: callback(record))
        with self.assertRaisesRegex(RuntimeError, "reserved for another worker"):
            OperationRuntime(cast(Any, store)).start()

    def test_memory_scope_and_carryover_shape_refusals(self) -> None:
        config = SimpleNamespace()
        series = SimpleNamespace(kind="series", repo_name="repo")
        with (
            mock.patch.object(
                memory_tools,
                "require_repo",
                return_value=SimpleNamespace(repo_id="repo"),
            ),
            mock.patch.object(memory_tools, "require_within_coordination", return_value=Path("/c")),
            mock.patch.object(memory_tools, "load_contract", return_value=series),
            self.assertRaisesRegex(memory_tools.AuthorityError, "leaf worktree contract"),
        ):
            memory_tools._memory_scope(cast(Any, config), repo_id="repo", contract_path="/c")

        selection = SimpleNamespace(
            repo_id="repo",
            source_memory=Path("/source"),
            contract_path=Path("/contract"),
            official_code_ref="main",
            source_code_ref="leaf",
            source_memory_ref="leaf",
            replace_existing=False,
        )
        configured = SimpleNamespace(
            repo_id="repo", memory_root=Path("/memory"), path=Path("/code")
        )
        invalid = SimpleNamespace(
            kind="series",
            repo_name="repo",
            memory_mode="external",
            memory_worktree=Path("/memory"),
        )
        with (
            mock.patch.object(memory_tools, "require_repo", return_value=configured),
            mock.patch.object(
                memory_tools,
                "require_within_coordination",
                side_effect=[Path("/source"), Path("/contract")],
            ),
            mock.patch.object(memory_tools, "load_contract", return_value=invalid),
            self.assertRaisesRegex(memory_tools.AuthorityError, "external-memory leaf"),
        ):
            memory_tools._carryover_request(cast(Any, config), cast(Any, selection))

        closed = SimpleNamespace(
            kind="leaf",
            repo_name="repo",
            memory_mode="external",
            memory_worktree=Path("/memory"),
            closeout_status="completed",
            integration_status="not-started",
        )
        with (
            mock.patch.object(memory_tools, "require_repo", return_value=configured),
            mock.patch.object(
                memory_tools,
                "require_within_coordination",
                side_effect=[Path("/source"), Path("/contract")],
            ),
            mock.patch.object(memory_tools, "load_contract", return_value=closed),
            self.assertRaisesRegex(memory_tools.AuthorityError, "must be open"),
        ):
            memory_tools._carryover_request(cast(Any, config), cast(Any, selection))

    def test_manager_dispatch_covers_invalid_nature_and_standalone_default(self) -> None:
        ref = TaskDocumentRef(repository="repo", path="master/task.json")
        resolved = SimpleNamespace(
            ref=ref,
            path=Path("/coordination/tasks/repo/master/task.json"),
            document=SimpleNamespace(slug="master", executionNature=None),
        )
        topology = SimpleNamespace(altitude=lambda _ref: "master", parent=lambda _ref: None)
        config = SimpleNamespace(coordination_root=Path("/coordination"))
        # L13-R5e: a nature-less standalone master is atomic by default and
        # dispatches its manager series with the repository default branch.
        repo = SimpleNamespace(repo_id="repo", path=Path("/code"), memory_root=None)
        with (
            mock.patch.object(agent_tools, "TaskDocumentTopology", return_value=topology),
            mock.patch.object(agent_tools, "require_repo", return_value=repo),
            mock.patch.object(agent_tools, "repository_default_branch", return_value="main"),
            mock.patch.object(agent_tools, "ensure_master_series_contract") as ensure,
        ):
            self.assertIsNone(
                agent_tools._manager_series_bootstrap_refusal(
                    cast(Any, config), cast(Any, resolved)
                )
            )
        spec = ensure.call_args.args[0]
        self.assertEqual((spec.parent_task_name, spec.protected_branch), ("", "main"))

        # A nature-less master under an authored graph is still a refusal naming
        # the missing nature cell (the set_nature recovery lives there).
        sprint = SimpleNamespace(document=SimpleNamespace(executionGraph={"nodes": []}))
        graphed = SimpleNamespace(
            altitude=lambda _ref: "master",
            parent=lambda _ref: object(),
            resolve=lambda _ref: sprint,
        )
        with mock.patch.object(agent_tools, "TaskDocumentTopology", return_value=graphed):
            outcome = agent_tools._manager_series_bootstrap_refusal(
                cast(Any, config), cast(Any, resolved)
            )
        assert outcome is not None
        assert outcome.detail is not None
        self.assertIn("executionNature", outcome.detail)

    def test_task_document_publication_rejects_escape_and_wraps_authority_error(self) -> None:
        context = SimpleNamespace(
            config=SimpleNamespace(coordination_root=Path("/coordination")),
            target=SimpleNamespace(repo_id="repo"),
            task_root=Path("/coordination/tasks/repo/master"),
            documents=(SimpleNamespace(),),
        )
        with (
            mock.patch.object(task_doc_tools, "json_path_for", return_value=Path("/outside.json")),
            self.assertRaisesRegex(task_doc_tools.TaskDocError, "escapes tasks root"),
        ):
            task_doc_tools._task_doc_publication_overrides(cast(Any, context))

        request = SimpleNamespace(
            coordination_root=Path("/coordination"),
            repo_id="repo",
            code_repository=Path("/code"),
            memory_repository=None,
        )
        with (
            mock.patch.object(
                task_execution_topology,
                "require_topology_migration_authority",
                side_effect=RuntimeError("wrong authority"),
            ),
            self.assertRaisesRegex(
                task_execution_topology.ExecutionTopologyError,
                "wrong authority",
            ),
        ):
            task_execution_topology._require_authoring_publication_authority(cast(Any, request), {})


class BootstrapAndMemoryRemainderTests(unittest.TestCase):
    def test_memory_init_existing_head_and_failed_config_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            with mock.patch.object(memory_init, "run_git", return_value=_result()):
                self.assertEqual(
                    memory_init._git_init_result(root, dry_run=False, initialize_git=True),
                    {"requested": True, "ran": False},
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                memory_init,
                "run_git",
                side_effect=[_result(), _result(1, stderr="config failed")],
            ):
                result = memory_init._git_init_result(root, dry_run=False, initialize_git=True)
            self.assertEqual((result["ran"], result["returncode"]), (True, 1))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                memory_init,
                "run_git",
                return_value=_result(1, stderr="init failed"),
            ):
                result = memory_init._git_init_result(root, dry_run=False, initialize_git=True)
            self.assertEqual((result["ran"], result["returncode"]), (True, 1))

    def test_baseline_default_authority_covers_existing_error_unborn_and_existing_ledger(
        self,
    ) -> None:
        with (
            mock.patch.object(
                baseline,
                "run_git",
                side_effect=[_result(1), _result(stdout="main\n"), _result()],
            ),
            mock.patch.object(
                baseline,
                "memory_repository_default_branch",
                return_value="main",
            ),
        ):
            self.assertEqual(baseline._baseline_default_branch(Path("/memory")), "main")

        with (
            mock.patch.object(
                baseline,
                "run_git",
                side_effect=[_result(1), _result(stdout="main\n"), _result(2)],
            ),
            self.assertRaisesRegex(RuntimeError, "cannot verify"),
        ):
            baseline._baseline_default_branch(Path("/memory"))

        with (
            mock.patch.object(
                baseline,
                "run_git",
                side_effect=[
                    _result(1),
                    _result(stdout="main\n"),
                    _result(1),
                    _result(stdout="refs/heads/other\n"),
                    _result(stdout="refs/heads/main\n"),
                ],
            ),
            self.assertRaisesRegex(RuntimeError, "exact unborn"),
        ):
            baseline._baseline_default_branch(Path("/memory"))

        context = SimpleNamespace(
            memory_root=Path("/memory"),
            ledger_path=Path("/memory/memory.md"),
        )
        with (
            mock.patch.object(Path, "exists", return_value=True),
            self.assertRaisesRegex(RuntimeError, "before the first ledger"),
        ):
            baseline.adopt_initial_baseline(context, "main", "main")

    def test_carryover_candidate_and_authority_refusal_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            target = root / "target.md"
            source.write_text("new\n", encoding="utf-8")
            target.write_text("old\n", encoding="utf-8")
            refs = carryover.CarryoverRefs(
                code_repository_root=root,
                official_ref="main",
                source_ref="leaf",
                old_base="old",
                target_memory=root / "target",
                source_memory=root / "source",
            )
            (refs.source_memory / "onboarding").mkdir(parents=True)
            (refs.target_memory / "onboarding").mkdir(parents=True)
            (refs.source_memory / "onboarding" / "source.md").write_text("new\n", encoding="utf-8")
            (refs.target_memory / "onboarding" / "source.md").write_text("old\n", encoding="utf-8")
            with mock.patch.object(
                carryover, "evidence_for_path", return_value=("same-path-changed", "changed")
            ):
                candidate = carryover.candidate_for_path(refs, "source", replace_existing=False)
            self.assertEqual(candidate.decision, "review-required")

            onboarding = root / "onboarding"
            onboarding.mkdir()
            branch_doc = onboarding / "same.md"
            branch_doc.write_text("same\n", encoding="utf-8")
            target_onboarding = root / "target" / "onboarding"
            target_onboarding.mkdir(parents=True, exist_ok=True)
            (target_onboarding / "same.md").write_text("same\n", encoding="utf-8")
            refs = carryover.CarryoverRefs(
                code_repository_root=root,
                official_ref="main",
                source_ref="leaf",
                old_base="old",
                target_memory=root / "target",
                source_memory=root,
            )
            with (
                mock.patch.object(carryover, "discover_route_overviews", return_value=[]),
                mock.patch.object(carryover, "memory_merge_base", return_value="base"),
            ):
                self.assertEqual(
                    carryover.memory_only_doc_candidates(
                        refs=refs,
                        existing=set(),
                    ),
                    [],
                )
            self.assertEqual(
                carryover.memory_only_doc_candidates(
                    refs=replace(refs, source_memory=root / "absent"),
                    existing=set(),
                ),
                [],
            )

        authority = SimpleNamespace(
            config_path=Path("/config"),
            coordination_root=Path("/coordination"),
        )
        request = SimpleNamespace(
            config_path=Path("/config"),
            code_repository_name="repo",
            code_repository_root=Path("/code"),
            target_memory=Path("/memory"),
            target_contract_path=Path("/contract"),
            official_code_ref="main",
        )
        configured = SimpleNamespace(path=Path("/code"), memory_root=Path("/memory"))
        contract = SimpleNamespace(
            coordination_root=Path("/foreign"),
            kind="leaf",
            repo_name="repo",
            memory_mode="external",
            memory_worktree=Path("/memory"),
            closeout_status="not-started",
            integration_status="not-started",
            code_base_commit="tip",
            code_worktree=Path("/code-worktree"),
        )
        common = (
            mock.patch.object(carryover, "require_repo", return_value=configured),
            mock.patch.object(carryover, "repository_identity", side_effect=lambda path: path),
            mock.patch.object(carryover, "load_contract", return_value=contract),
        )
        with (
            common[0],
            common[1],
            common[2],
            self.assertRaisesRegex(RuntimeError, "coordination authority"),
        ):
            carryover._require_carryover_authority(cast(Any, request), cast(Any, authority))

        contract.coordination_root = authority.coordination_root
        contract.kind = "series"
        with (
            mock.patch.object(carryover, "require_repo", return_value=configured),
            mock.patch.object(carryover, "repository_identity", side_effect=lambda path: path),
            mock.patch.object(carryover, "load_contract", return_value=contract),
            self.assertRaisesRegex(RuntimeError, "exact external-memory leaf"),
        ):
            carryover._require_carryover_authority(cast(Any, request), cast(Any, authority))

        contract.kind = "leaf"
        contract.closeout_status = "completed"
        with (
            mock.patch.object(carryover, "require_repo", return_value=configured),
            mock.patch.object(carryover, "repository_identity", side_effect=lambda path: path),
            mock.patch.object(carryover, "load_contract", return_value=contract),
            self.assertRaisesRegex(RuntimeError, "no longer open"),
        ):
            carryover._require_carryover_authority(cast(Any, request), cast(Any, authority))

        contract.closeout_status = "not-started"
        with (
            mock.patch.object(carryover, "require_repo", return_value=configured),
            mock.patch.object(carryover, "repository_identity", side_effect=lambda path: path),
            mock.patch.object(carryover, "load_contract", return_value=contract),
            mock.patch.object(carryover, "require_ordinary_worktree"),
            mock.patch.object(carryover, "head_commit", side_effect=["tip", "wrong"]),
            self.assertRaisesRegex(RuntimeError, "unchanged at the selected official tip"),
        ):
            carryover._require_carryover_authority(cast(Any, request), cast(Any, authority))


class ModelAndIdentityRemainderTests(unittest.TestCase):
    def test_standalone_atomic_document_has_master_altitude(self) -> None:
        resolved = SimpleNamespace(
            document=SimpleNamespace(kind="master", orchestrates=[], executionNature="atomic")
        )
        topology = cast(
            Any, SimpleNamespace(resolve=lambda _ref: resolved, _sprint_parents=lambda _: [])
        )
        self.assertEqual(
            document_refs.TaskDocumentTopology.altitude(
                topology, TaskDocumentRef(repository="r", path="m/task.json")
            ),
            "master",
        )

        confined = document_refs.TaskDocumentTopology(Path("/coordination"))
        with self.assertRaisesRegex(document_refs.TaskDocumentRefError, "outside tasks/r"):
            confined.canonical_ref("r", Path("/outside/task.json"))

    def test_organizational_master_projection_requires_a_super_branch(self) -> None:
        master = SimpleNamespace(
            ref=TaskDocumentRef(repository="repo", path="master/task.json"),
            path=Path("/coordination/tasks/repo/master/task.json"),
        )
        sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
        sprint = SimpleNamespace(document=SimpleNamespace(integrationBranch=None))
        topology = SimpleNamespace(
            parent=lambda _ref: sprint_ref,
            resolve=lambda _ref: sprint,
            validate_execution_topology=lambda _ref: (master,),
        )
        projection = source_lineage._organizational_master_projection(
            cast(Any, topology), cast(Any, master)
        )
        self.assertEqual(projection.state, "unavailable")


class SealEvidenceAndRecoveryRemainderTests(unittest.TestCase):
    def test_atomic_seal_refuses_wrong_missing_and_invalid_series(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "requires an atomic series"):
            atomic_series_seal.require_series_accepting_leaves(
                cast(Any, SimpleNamespace(kind="leaf")), operation="start"
            )
        with self.assertRaisesRegex(RuntimeError, "task-owned series contract"):
            atomic_series_seal.require_series_path_accepting_leaves(
                Path("/missing"), operation="start"
            )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "series-contract.md"
            path.write_text("invalid\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "cannot read"):
                atomic_series_seal.require_series_path_accepting_leaves(path, operation="start")

    def test_atomic_landing_authority_and_operation_record_fail_closed(self) -> None:
        sprint = SimpleNamespace(
            ref=TaskDocumentRef(repository="repo", path="sprint/task.json"),
            document=SimpleNamespace(integrationBranch=None),
        )
        config = SimpleNamespace(repositories={}, coordination_root=Path("/coordination"))
        with self.assertRaisesRegex(
            closeout_queue_candidate_evidence.CloseoutQueueError,
            "configured repository",
        ):
            closeout_queue_candidate_evidence.atomic_master_landing_authority(
                cast(Any, config), cast(Any, sprint)
            )

        sprint.document.integrationBranch = "super"
        configured = SimpleNamespace(path=Path("/code"), memory_root=Path("/memory"))
        config.repositories = {"repo": configured}
        with (
            mock.patch.object(
                closeout_queue_candidate_evidence,
                "memory_mode_for_repository",
                return_value="external",
            ),
            mock.patch.object(
                closeout_queue_candidate_evidence,
                "repository_identity",
                return_value=None,
            ),
            self.assertRaisesRegex(
                closeout_queue_candidate_evidence.CloseoutQueueError,
                "cannot resolve",
            ),
        ):
            closeout_queue_candidate_evidence.atomic_master_landing_authority(
                cast(Any, config), cast(Any, sprint)
            )

        contract = SimpleNamespace(worktree_group=Path("/group"))
        authority = SimpleNamespace()
        store = SimpleNamespace(read=lambda: None)
        with mock.patch.object(
            closeout_queue_candidate_evidence,
            "LifecycleOperationStore",
            return_value=store,
        ):
            self.assertFalse(
                closeout_queue_candidate_evidence._atomic_operation_landed(
                    cast(Any, contract), cast(Any, authority)
                )
            )

        store.read = lambda: SimpleNamespace(
            input=IntegrateOperationInput(configPath="/config", contractPath="/contract"),
            integrationAuthority=None,
            recoveryCommits=None,
            result=None,
        )
        with mock.patch.object(
            closeout_queue_candidate_evidence,
            "LifecycleOperationStore",
            return_value=store,
        ):
            self.assertFalse(
                closeout_queue_candidate_evidence._atomic_operation_landed(
                    cast(Any, contract), cast(Any, authority)
                )
            )

        internal_authority = closeout_queue_candidate_evidence.AtomicMasterLandingAuthority(
            coordination_root=Path("/coordination"),
            repo_name="repo",
            sprint_ref=TaskDocumentRef(repository="repo", path="sprint/task.json"),
            source_branch="super",
            code_repository=Path("/code"),
            memory_mode="internal",
            memory_repository=None,
        )
        self.assertTrue(
            closeout_queue_candidate_evidence._atomic_memory_authority_matches(
                cast(
                    Any,
                    SimpleNamespace(
                        memory_repo_path=None,
                        memory_source_branch="",
                        memory_work_branch="",
                    ),
                ),
                internal_authority,
                expected_work_branch="ar/master",
                memory_identity=None,
            )
        )

    def test_closeout_recovery_refuses_missing_memory_and_mismatched_series_tips(self) -> None:
        commits = SimpleNamespace(
            codeCommit="code", memoryContentCommit="memory", ledgerCommit="ledger"
        )
        with self.assertRaisesRegex(RuntimeError, "requires a memory repository"):
            closeout_recovery._prove_recovered_series_memory(
                cast(Any, SimpleNamespace(memory_repo_path=None)), cast(Any, commits)
            )

        contract = SimpleNamespace(memory_repo_path=Path("/memory"), memory_work_branch="ar/master")
        with (
            mock.patch.object(closeout_recovery, "branch_commit", return_value="wrong"),
            self.assertRaisesRegex(RuntimeError, "found series memory ref"),
        ):
            closeout_recovery._prove_recovered_series_memory(
                cast(Any, contract), cast(Any, commits)
            )

    def test_leaf_integration_quality_checkout_is_the_existing_worktree(self) -> None:
        contract = SimpleNamespace(kind="leaf", code_worktree=Path("/leaf"))
        with integration_quality_checkout.integration_quality_checkout(
            cast(Any, contract)
        ) as checkout:
            self.assertEqual(checkout, Path("/leaf"))

    def test_atomic_leaf_document_and_ledger_set_refusals(self) -> None:
        series = cast(
            Any,
            SimpleNamespace(
                coordination_root=Path("/coordination"),
                repo_name="repo",
                task_root=Path("/coordination/tasks/repo/master"),
            ),
        )
        master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
        master = SimpleNamespace(
            ref=master_ref,
            path=Path("/coordination/tasks/repo/master/task.json"),
            document=SimpleNamespace(subTasks=[SimpleNamespace(number="leaf", file="")]),
        )
        topology = SimpleNamespace(
            canonical_ref=lambda *_args: master_ref,
            resolve=lambda _ref: master,
            parent=lambda _ref: None,
        )
        with (
            mock.patch.object(series_closeout, "TaskDocumentTopology", return_value=topology),
            self.assertRaisesRegex(series_closeout.CloseoutQueueError, "unique subtask rows"),
        ):
            series_closeout._atomic_leaf_documents(series)

        rows = [
            SimpleNamespace(number="one", file="same.md"),
            SimpleNamespace(number="two", file="same.md"),
        ]
        master.document.subTasks = rows
        leaf_ref = TaskDocumentRef(repository="repo", path="master/same.json")
        topology.canonical_ref = lambda _repo, path: (
            master_ref if Path(path).name == "task.json" else leaf_ref
        )
        topology.resolve = lambda ref: (
            master
            if ref == master_ref
            else SimpleNamespace(document=SimpleNamespace(kind="subTask", id="one"))
        )
        topology.parent = lambda _ref: master_ref
        with (
            mock.patch.object(series_closeout, "TaskDocumentTopology", return_value=topology),
            self.assertRaisesRegex(series_closeout.CloseoutQueueError, "duplicate task document"),
        ):
            series_closeout._atomic_leaf_documents(series)

        master.document.subTasks = [SimpleNamespace(number="leaf", file="leaf.md")]
        leaf_ref = TaskDocumentRef(repository="repo", path="master/leaf.json")
        topology.resolve = lambda ref: (
            master
            if ref == master_ref
            else SimpleNamespace(document=SimpleNamespace(kind="master", id="leaf"))
        )
        with (
            mock.patch.object(series_closeout, "TaskDocumentTopology", return_value=topology),
            self.assertRaisesRegex(series_closeout.CloseoutQueueError, "one exact owned leaf"),
        ):
            series_closeout._atomic_leaf_documents(series)

        master.document.subTasks = []
        with (
            mock.patch.object(series_closeout, "TaskDocumentTopology", return_value=topology),
            self.assertRaisesRegex(series_closeout.CloseoutQueueError, "at least one"),
        ):
            series_closeout._atomic_leaf_documents(series)

        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            with (
                mock.patch.object(series_closeout, "_atomic_leaf_code_matches", return_value=True),
                mock.patch.object(
                    series_closeout, "_atomic_leaf_memory_matches", return_value=True
                ),
                mock.patch.object(series_closeout, "require_git", return_value="ledger"),
                mock.patch.object(series_closeout, "parse_ledger_text", return_value=object()),
                mock.patch.object(series_closeout, "find_mapping", return_value=None),
                self.assertRaisesRegex(
                    series_closeout.CloseoutQueueError, "code-to-memory mapping"
                ),
            ):
                series_closeout._require_atomic_leaf_landed(
                    fixture.master_contract,
                    fixture.leaf_contract,
                    cast(Any, SimpleNamespace()),
                )

    def test_configured_contract_candidate_and_external_worktree_identity_refusals(self) -> None:
        contract = SimpleNamespace(
            repo_name="repo",
            code_repo_path=Path("/code"),
            code_worktree=Path("/foreign"),
            memory_mode="disabled",
        )
        config = SimpleNamespace(
            coordination_root=Path("/coordination"),
            repositories={"repo": SimpleNamespace(path=Path("/code"), memory_root=None)},
        )
        with (
            mock.patch.object(lifecycle_operations, "load_config", return_value=config),
            mock.patch.object(
                lifecycle_operations, "require_repo", return_value=config.repositories["repo"]
            ),
            mock.patch.object(lifecycle_operations, "_require_configured_task_identity"),
            mock.patch.object(
                lifecycle_operations,
                "repository_identity",
                side_effect=[Path("/identity"), Path("/identity"), Path("/foreign")],
            ),
            self.assertRaisesRegex(RuntimeError, "candidate belongs to another repository"),
        ):
            lifecycle_operations.require_configured_contract_repositories(
                cast(Any, contract), "/config"
            )

        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            external = replace(fixture.leaf_contract, memory_worktree=Path(tmp) / "wrong")
            with self.assertRaisesRegex(RuntimeError, "memory worktree is not owned"):
                lifecycle_operations._require_configured_task_identity(
                    external, fixture.coordination
                )


class TerminalAndCloseoutRemainderTests(unittest.TestCase):
    def test_abandon_and_cleanup_recheck_contract_and_skip_series_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            contract = fixture.leaf_contract
            abandon_guard = mock.Mock()
            abandon_guard.preview.return_value = {}
            with (
                mock.patch.object(abandon, "contract_lifecycle_lease", return_value=nullcontext()),
                mock.patch.object(
                    abandon, "load_contract", return_value=replace(contract, cleanup="abandoned")
                ),
            ):
                result = abandon._abandon_with_guard(
                    args=WorktreeArgs(),
                    contract=contract,
                    preflight=cast(Any, SimpleNamespace()),
                    guard=cast(Any, abandon_guard),
                )
            self.assertEqual(result.returncode, 2)
            self.assertIn("changed before terminal mutation", str(result.payload))
            abandon_guard.preview.assert_called_once_with()
            self.assertEqual(
                abandon._abandon_worktrees(
                    fixture.master_contract,
                    dry_run=True,
                    force=False,
                    authority=cast(Any, SimpleNamespace()),
                ),
                {},
            )

            cleanup_guard = mock.Mock()
            cleanup_guard.preview.return_value = {}
            with (
                mock.patch.object(cleanup, "contract_lifecycle_lease", return_value=nullcontext()),
                mock.patch.object(
                    cleanup, "load_contract", return_value=replace(contract, cleanup="completed")
                ),
            ):
                result = cleanup._cleanup_with_guard(
                    args=WorktreeArgs(),
                    contract=contract,
                    preflight=cast(Any, SimpleNamespace()),
                    guard=cast(Any, cleanup_guard),
                )
            self.assertEqual(result.returncode, 2)
            self.assertIn("changed before terminal mutation", str(result.payload))
            cleanup_guard.preview.assert_called_once_with()
            self.assertEqual(
                cleanup._removed_worktrees(
                    fixture.master_contract,
                    True,
                    authority=cast(Any, SimpleNamespace()),
                ),
                {},
            )

    def test_terminal_capability_refuses_missing_identities_targets_and_wrong_force_owner(
        self,
    ) -> None:
        with (
            mock.patch.object(cleanup, "repository_identity", return_value=None),
            self.assertRaisesRegex(RuntimeError, "cannot resolve"),
        ):
            cleanup._required_repository_identity(Path("/repo"), "code")

        capability = cleanup._TERMINAL_MUTATION_CAPABILITY
        authority = cleanup._TerminalMutationAuthority(
            "worktree_cleanup",
            frozenset(),
            frozenset(),
            frozenset(),
            capability,
        )
        with (
            mock.patch.object(cleanup, "_repository_key", return_value=Path("/repo")),
            self.assertRaisesRegex(RuntimeError, "worktree removal target"),
        ):
            cleanup._require_worktree_target(authority, Path("/repo"), Path("/worktree"))
        with (
            mock.patch.object(cleanup, "_repository_key", return_value=Path("/repo")),
            self.assertRaisesRegex(RuntimeError, "branch deletion target"),
        ):
            cleanup._require_local_branch_target(authority, Path("/repo"), "leaf")

        branch_authority = replace(
            authority,
            branches=frozenset({(Path("/repo"), "leaf", "super")}),
        )
        with (
            mock.patch.object(cleanup, "_repository_key", return_value=Path("/repo")),
            self.assertRaisesRegex(RuntimeError, "deletion source"),
        ):
            cleanup._require_local_branch_target(
                branch_authority, Path("/repo"), "leaf", source_branch="other"
            )
        with (
            mock.patch.object(cleanup, "_repository_key", return_value=Path("/repo")),
            self.assertRaisesRegex(RuntimeError, "remote branch deletion target"),
        ):
            cleanup._require_remote_branch_target(authority, Path("/repo"), "leaf")
        with (
            mock.patch.object(cleanup, "_require_local_branch_target"),
            self.assertRaisesRegex(RuntimeError, "requires abandon authority"),
        ):
            cleanup.delete_branch_force(Path("/repo"), "leaf", True, authority=authority)

    def test_closeout_publication_rechecks_refuse_changed_contract_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            contract = fixture.leaf_contract
            commits = SimpleNamespace(codeCommit="code", memoryContentCommit="", ledgerCommit="")
            args = WorktreeArgs(
                approved=True,
                approval_note="approved",
                recovery_commits=cast(Any, commits),
            )
            with (
                mock.patch.object(
                    closeout, "load_contract", return_value=replace(contract, cleanup="completed")
                ),
                mock.patch.object(
                    closeout,
                    "publish_closeout_under_authority",
                    side_effect=lambda _contract, publication: publication(),
                ),
                self.assertRaisesRegex(RuntimeError, "changed before recovery finalization"),
            ):
                closeout._recover_closeout_finalization(contract, args)

            series = fixture.master_contract
            series_args = WorktreeArgs(
                approved=True,
                approval_note="approved",
                recovery_commits=cast(Any, commits),
            )
            with (
                mock.patch.object(closeout, "load_contract", return_value=series),
                mock.patch.object(
                    closeout,
                    "publish_closeout_under_authority",
                    side_effect=lambda _contract, publication: publication(),
                ),
                mock.patch.object(
                    closeout,
                    "require_series_contract_authority",
                    side_effect=RuntimeError("series authority checked"),
                ),
                self.assertRaisesRegex(RuntimeError, "series authority checked"),
            ):
                closeout._recover_closeout_finalization(series, series_args)

    def test_git_guidance_sync_terminal_preview_and_queue_store_remainders(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid local branch"):
            git.local_branch_ref("HEAD")

        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            missing = replace(fixture.leaf_contract, memory_worktree=None)
            with (
                mock.patch(
                    "agents_remember.worktrees.integration.integration_branch_authority.require_ordinary_worktree"
                ),
                self.assertRaisesRegex(RuntimeError, "missing its target path"),
            ):
                git.ensure_worktree(missing, side="memory", dry_run=False)
            with (
                mock.patch(
                    "agents_remember.worktrees.integration.integration_branch_authority.require_ordinary_worktree"
                ),
                self.assertRaisesRegex(RuntimeError, "requires an external-memory contract"),
            ):
                git.ensure_worktree(
                    replace(
                        fixture.leaf_contract,
                        memory_mode="disabled",
                        memory_repo_path=None,
                        memory_worktree=None,
                    ),
                    side="memory",
                    dry_run=False,
                )

            self.assertEqual(guidance.carryover_done(fixture.leaf_contract), (False, ""))

            fixture.leaf_contract.code_worktree.mkdir(parents=True, exist_ok=True)
            with (
                mock.patch.object(sync, "_fetch_source_upstreams", return_value={}),
                mock.patch.object(sync, "require_sync_worktree"),
                mock.patch.object(sync, "integration_authority_lock", return_value=nullcontext()),
                mock.patch.object(
                    sync,
                    "load_contract_from_args",
                    side_effect=[
                        fixture.leaf_contract,
                        replace(fixture.leaf_contract, cleanup="completed"),
                    ],
                ),
                self.assertRaisesRegex(RuntimeError, "changed before branch mutation"),
            ):
                sync.sync_result(WorktreeArgs(contract_path=fixture.leaf_contract.contract_path))

            with (
                mock.patch.object(sync, "head_commit", return_value="tip"),
                mock.patch.object(sync, "branch_commit", return_value="tip"),
            ):
                self.assertEqual(
                    sync._sync_code(fixture.leaf_contract, False), {"state": "already-current"}
                )

            child = replace(fixture.leaf_contract, cleanup="completed")
            with mock.patch.object(
                terminal_validation, "_child_contract_matches_series", return_value=False
            ):
                self.assertIn(
                    "foreign child contract",
                    terminal_validation._child_terminal_blocker(
                        fixture.master_contract, child.contract_path.parent
                    )
                    or "",
                )
            enclosure = child.contract_path.parent
            enclosure.mkdir(parents=True, exist_ok=True)
            child.contract_path.touch(exist_ok=True)
            with (
                mock.patch.object(terminal_validation, "load_contract", return_value=child),
                mock.patch.object(
                    terminal_validation, "_child_contract_matches_series", return_value=True
                ),
                mock.patch.object(
                    terminal_validation,
                    "_live_child_resources",
                    return_value=["memory worktree"],
                ),
            ):
                self.assertIn(
                    "retains memory worktree",
                    terminal_validation._child_terminal_blocker(fixture.master_contract, enclosure)
                    or "",
                )
            with mock.patch.object(terminal_validation, "_append_live_branch"):
                child.memory_worktree.mkdir(parents=True, exist_ok=True)
                self.assertIn("memory worktree", terminal_validation._live_child_resources(child))

            self.assertIn(
                "read-exact-series-memory-ref",
                closeout_preview.closeout_order(fixture.master_contract),
            )
            self.assertEqual(
                closeout_preview.closeout_order(
                    replace(fixture.master_contract, memory_mode="disabled")
                ),
                ["read-exact-series-code-ref", "record-existing-series-commits-in-contract"],
            )

        state = SimpleNamespace()
        store = cast(
            Any,
            SimpleNamespace(
                state_path=Path("/state"),
                _recover=lambda _initial: state,
                _request_was_applied=lambda _current, _event: True,
            ),
        )
        with (
            mock.patch.object(
                type(closeout_queue_store.QUEUE_OWNERSHIP), "check_declared_writer"
            ) as writer,
            mock.patch.object(closeout_queue_store, "exclusive_access", return_value=nullcontext()),
        ):
            self.assertIs(
                closeout_queue_store.CloseoutQueueStore.transact_with_publication(
                    store,
                    initial=cast(Any, state),
                    event=cast(Any, SimpleNamespace()),
                    transform=lambda _state: self.fail("transform should not run"),
                    publication=lambda: self.fail("publication should not run"),
                ),
                state,
            )
        writer.assert_called_once_with()

    def test_integration_publication_refuses_a_changed_contract_before_ref_movement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            contract = fixture.leaf_contract
            changed = replace(contract, cleanup="completed")
            with (
                mock.patch.object(
                    integrate,
                    "_prepare_integration_commits",
                    return_value=(IntegratedCommits("code", "", ""), {}, None, False),
                ),
                mock.patch.object(integrate, "load_contract", return_value=changed),
                mock.patch.object(
                    integrate,
                    "publish_queue_candidate_integration_result_under_authority",
                    side_effect=lambda _contract, publication, **_kwargs: publication(
                        SimpleNamespace(organizational_completion=None)
                    ),
                ),
                self.assertRaisesRegex(RuntimeError, "changed before protected-ref movement"),
            ):
                integrate._apply_integration(
                    contract,
                    WorktreeArgs(operation_key="a" * 64),
                    cast(Any, SimpleNamespace()),
                    handover_warning=None,
                )
