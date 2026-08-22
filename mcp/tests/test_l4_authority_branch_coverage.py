"""Focused negative-branch coverage for L4 repository and operation authority."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

from agents_remember.application.lifecycle_operation_worker import OperationRuntime
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    LifecycleOperationRecord,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees import series_closeout, source_lineage
from agents_remember.worktrees.integration import (
    integration_branch_authority,
    integration_branch_repository,
    integration_operation_authority,
    lifecycle_operations,
)
from agents_remember.worktrees.integration.integration_branch_types import (
    IntegrationSurface,
    IntegrationSurfaceKind,
    _BranchScope,
    _RepositorySide,
)
from agents_remember.worktrees.integration.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules import integrate as integrate_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract
from closeout_input_test_support import closeout_operation_input
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
    _closed_leaf_worktree,
)
from test_source_lineage import _commit_on


def _git_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class IntegrationBranchRepositoryCoverageTests(unittest.TestCase):
    def test_canonical_local_branch_refuses_every_invalid_alias_shape(self) -> None:
        repository = Path("/repo")
        with self.assertRaisesRegex(RuntimeError, "blank"):
            integration_branch_repository.canonical_local_branch(repository, "refs/heads/")

        cases = (
            ([_git_result(stdout="refs/heads/loop\n")], "cycle"),
            ([_git_result(2, stderr="permission denied")], "permission denied"),
            ([_git_result(stdout="refs/tags/main\n")], "non-local target"),
            ([_git_result(stdout="refs/heads/\n")], "non-local target"),
        )
        for results, reason in cases:
            with (
                self.subTest(reason=reason),
                mock.patch.object(
                    integration_branch_repository,
                    "run_git",
                    side_effect=results,
                ),
                self.assertRaisesRegex(RuntimeError, reason),
            ):
                integration_branch_repository.canonical_local_branch(repository, "loop")

        targets = [_git_result(stdout=f"refs/heads/alias-{index + 1}\n") for index in range(32)]
        with (
            mock.patch.object(
                integration_branch_repository,
                "run_git",
                side_effect=targets,
            ),
            self.assertRaisesRegex(RuntimeError, "too deep"),
        ):
            integration_branch_repository.canonical_local_branch(repository, "alias-0")

    def test_default_branch_authority_refuses_missing_and_malformed_facts(self) -> None:
        repository = Path("/repo")
        with (
            mock.patch.object(
                integration_branch_repository,
                "_remote_repository_default_branch",
                return_value=None,
            ),
            self.assertRaisesRegex(RuntimeError, "unavailable"),
        ):
            integration_branch_repository.repository_default_branch(repository)

        cases = (
            (
                [_git_result(1), _git_result()],
                "not symbolic",
            ),
            (
                [_git_result(stdout="refs/tags/main\n")],
                "malformed",
            ),
            (
                [_git_result(stdout="refs/remotes/origin/main\n"), _git_result(1)],
                "does not exist",
            ),
        )
        for results, reason in cases:
            with (
                self.subTest(reason=reason),
                mock.patch.object(
                    integration_branch_repository,
                    "run_git",
                    side_effect=results,
                ),
                self.assertRaisesRegex(RuntimeError, reason),
            ):
                integration_branch_repository._remote_repository_default_branch(repository)

    def test_memory_default_branch_refuses_invalid_local_authority(self) -> None:
        repository = Path("/memory")
        cases = (
            (_git_result(1), "unavailable"),
            (_git_result(stdout="feature\n"), "does not match"),
        )
        for local, reason in cases:
            with (
                self.subTest(reason=reason),
                mock.patch.object(
                    integration_branch_repository,
                    "_remote_repository_default_branch",
                    return_value=None,
                ),
                mock.patch.object(
                    integration_branch_repository,
                    "run_git",
                    return_value=local,
                ),
                self.assertRaisesRegex(RuntimeError, reason),
            ):
                integration_branch_repository.memory_repository_default_branch(repository)

        with (
            mock.patch.object(
                integration_branch_repository,
                "_remote_repository_default_branch",
                return_value=None,
            ),
            mock.patch.object(
                integration_branch_repository,
                "run_git",
                side_effect=[_git_result(stdout="main\n"), _git_result(1)],
            ),
            self.assertRaisesRegex(RuntimeError, "target does not exist"),
        ):
            integration_branch_repository.memory_repository_default_branch(repository)

    def test_branch_owner_enumeration_refuses_git_failure_and_skips_detached_rows(self) -> None:
        repository = Path("/repo")
        with (
            mock.patch.object(
                integration_branch_repository,
                "run_git",
                return_value=_git_result(1, stderr="cannot list"),
            ),
            self.assertRaisesRegex(RuntimeError, "cannot list"),
        ):
            integration_branch_repository.branch_worktree_owners(repository, "main")

        porcelain = "worktree /tmp/detached\nHEAD abc\ndetached\n"
        with (
            mock.patch.object(
                integration_branch_repository,
                "run_git",
                side_effect=[_git_result(stdout=porcelain), _git_result(1)],
            ),
        ):
            self.assertEqual(
                integration_branch_repository.branch_worktree_owners(repository, "main"),
                (),
            )


class IntegrationOperationAuthorityCoverageTests(unittest.TestCase):
    def _running_record(
        self, root: Path, *, external: bool = False
    ) -> tuple[WorktreeContract, LifecycleOperationRecord]:
        fixture = _authority_fixture(root, external_memory=external)
        contract = (
            _closed_external_leaf_worktrees(fixture, root)
            if external
            else _closed_leaf_worktree(fixture, root, candidate_commit=True)
        )
        write_contract(contract.contract_path, contract)
        lifecycle_operations.start_or_observe_operation(
            IntegrateOperationInput(
                configPath=fixture.config_path.as_posix(),
                contractPath=contract.contract_path.as_posix(),
            ),
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        return contract, OperationRuntime(store).start()

    def test_plane_operation_refuses_every_mismatched_journal_fact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract, running = self._running_record(Path(tmp))
            valid = WorktreeArgs(operation_key=running.operationKey)
            with self.assertRaisesRegex(RuntimeError, "journaled integration operation"):
                integration_operation_authority.require_plane_integration_operation(
                    contract, WorktreeArgs()
                )

            cases = (
                (None, valid, "record is missing"),
                (running, WorktreeArgs(operation_key="wrong"), "key does not own"),
                (running.model_copy(update={"status": "queued"}), valid, "not the active"),
                (
                    running.model_copy(update={"contractPath": "/wrong/contract.md"}),
                    valid,
                    "contract identity changed",
                ),
                (
                    running.model_copy(
                        update={
                            "input": closeout_operation_input(
                                contract,
                                config_path="/settings.json",
                                code="candidate",
                                approval_note="test authority mismatch",
                            )
                        }
                    ),
                    valid,
                    "wrong durable input",
                ),
                (
                    running.model_copy(
                        update={"input": running.input.model_copy(update={"strategy": "replay"})}
                    ),
                    valid,
                    "strategy changed",
                ),
                (
                    running.model_copy(update={"integrationAuthority": None}),
                    valid,
                    "missing exact source",
                ),
            )
            for record, args, reason in cases:
                with (
                    self.subTest(reason=reason),
                    mock.patch.object(LifecycleOperationStore, "read", return_value=record),
                    self.assertRaisesRegex(RuntimeError, reason),
                ):
                    integration_operation_authority.require_plane_integration_operation(
                        contract, args
                    )

    def test_source_commit_and_closeout_candidate_mismatches_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract, running = self._running_record(Path(tmp), external=True)
            authority = running.integrationAuthority
            assert authority is not None
            args = WorktreeArgs(operation_key=running.operationKey)
            with (
                mock.patch.object(
                    integration_operation_authority,
                    "require_plane_integration_operation",
                    return_value=running,
                ),
                self.assertRaisesRegex(RuntimeError, "code integration source moved"),
            ):
                integration_operation_authority.require_current_integration_sources(
                    contract,
                    args,
                    code_source_commit="wrong",
                    memory_source_commit=authority.memorySourceCommit,
                )

            with (
                mock.patch.object(
                    integration_operation_authority,
                    "require_plane_integration_operation",
                    return_value=running,
                ),
                self.assertRaisesRegex(RuntimeError, "memory integration source moved"),
            ):
                integration_operation_authority.require_current_integration_sources(
                    contract,
                    args,
                    code_source_commit=authority.codeSourceCommit,
                    memory_source_commit="wrong",
                )

            with (
                mock.patch.object(
                    integration_operation_authority,
                    "require_plane_integration_operation",
                    return_value=running,
                ),
                self.assertRaisesRegex(RuntimeError, "not the exact journaled"),
            ):
                integration_operation_authority.require_authorized_integration_commits(
                    contract,
                    args,
                    code_commit="wrong",
                    memory_content_commit=authority.memoryContentCommit,
                    ledger_commit=authority.ledgerCommit,
                )

            for field, reason in (
                ("codeCandidateCommit", "code candidate"),
                ("memoryContentCommit", "memory candidate"),
                ("ledgerCommit", "ledger candidate"),
            ):
                changed = authority.model_copy(update={field: "wrong"})
                with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, reason):
                    integration_operation_authority._require_closed_candidate(contract, changed)

    def test_contract_authority_refuses_repository_and_memory_shape_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            internal, internal_record = self._running_record(root / "internal")
            internal_authority = internal_record.integrationAuthority
            assert internal_authority is not None
            with self.assertRaisesRegex(RuntimeError, "code repository or target ref"):
                integration_operation_authority._require_contract_authority(
                    internal,
                    internal_authority.model_copy(update={"codeSourceRef": "refs/heads/wrong"}),
                )

            with (
                mock.patch.object(
                    integration_operation_authority,
                    "repository_identity",
                    return_value=Path("/wrong"),
                ),
                self.assertRaisesRegex(RuntimeError, "code candidate worktree changed"),
            ):
                integration_operation_authority._require_contract_authority(
                    internal,
                    internal_authority,
                )

            with self.assertRaisesRegex(RuntimeError, "carries external-memory authority"):
                integration_operation_authority._require_contract_authority(
                    internal,
                    internal_authority.model_copy(update={"memoryRepository": "/memory"}),
                )

            external, external_record = self._running_record(root / "external", external=True)
            external_authority = external_record.integrationAuthority
            assert external_authority is not None
            with self.assertRaisesRegex(RuntimeError, "memory repository or target ref"):
                integration_operation_authority._require_contract_authority(
                    external,
                    external_authority.model_copy(update={"memorySourceRef": "refs/heads/wrong"}),
                )

            code_identity = Path(external_authority.codeRepository)

            def candidate_identity(path: Path) -> Path:
                if path == external.code_worktree:
                    return code_identity
                return Path("/wrong-memory")

            with (
                mock.patch.object(
                    integration_operation_authority,
                    "repository_identity",
                    side_effect=candidate_identity,
                ),
                self.assertRaisesRegex(RuntimeError, "memory candidate worktree changed"),
            ):
                integration_operation_authority._require_contract_authority(
                    external,
                    external_authority,
                )

    def test_final_preparation_race_refuses_before_irreversible_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            closed = _closed_leaf_worktree(fixture, root, candidate_commit=True)
            write_contract(closed.contract_path, closed)
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
            authority = running.integrationAuthority
            assert authority is not None
            progress: list[dict[str, object]] = []

            def raced_publication(_contract, publication, **_kwargs):
                _commit_on(fixture.code_repo, "ar/master", "pre-cas-race.txt")
                return publication(SimpleNamespace(organizational_completion=None))

            with (
                mock.patch.object(integrate_module, "claim_queue_candidate_for_integration"),
                mock.patch.object(
                    integrate_module,
                    "publish_queue_candidate_integration_result_under_authority",
                    side_effect=raced_publication,
                ),
                mock.patch.object(
                    integrate_module,
                    "report_operation_progress",
                    side_effect=lambda _args, _phase, **facts: progress.append(facts),
                ),
                mock.patch.object(integrate_module, "merge_integrated_commits") as merge,
            ):
                result = integrate_module._apply_integration(
                    closed,
                    WorktreeArgs(operation_key=running.operationKey),
                    integrate_module.IntegrationSources(
                        current_code_source=authority.codeSourceCommit,
                        current_memory_source="",
                        code_replay_required=False,
                        memory_replay_required=False,
                    ),
                    handover_warning=None,
                )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "source-moved-during-quality")
            merge.assert_not_called()
            self.assertFalse(any(item.get("irreversible_boundary") for item in progress))

    def test_configured_contract_identity_refuses_every_foreign_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            contract = _closed_leaf_worktree(fixture, root, candidate_commit=True)

            task_identity_cases = (
                (replace(contract, task_artifact=root / "wrong.md"), "task artifact"),
                (replace(contract, contract_path=root / "wrong.md"), "contract path"),
                (
                    replace(contract, worktree_group=root / "outside"),
                    "worktree group is outside",
                ),
                (
                    replace(contract, code_worktree=contract.worktree_group / "nested" / "code"),
                    "code worktree is not owned",
                ),
                (
                    replace(
                        fixture.master_contract,
                        worktree_group=root / "wrong-series-group",
                    ),
                    "series contract worktree group",
                ),
            )
            for changed, reason in task_identity_cases:
                with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, reason):
                    lifecycle_operations._require_configured_task_identity(
                        changed,
                        fixture.coordination,
                    )

            external_fixture = _authority_fixture(root / "external", external_memory=True)
            external = _closed_external_leaf_worktrees(external_fixture, root / "external")
            configured = lifecycle_operations.require_repo(
                lifecycle_operations.load_config(external_fixture.config_path),
                external.repo_name,
            )
            with self.assertRaisesRegex(RuntimeError, "does not match configured"):
                lifecycle_operations._require_external_memory_authority(
                    replace(external, memory_repo_path=None),
                    configured,
                    Path("/code"),
                )
            with (
                mock.patch.object(
                    lifecycle_operations,
                    "repository_identity",
                    side_effect=[Path("/memory"), Path("/other")],
                ),
                self.assertRaisesRegex(RuntimeError, "memory repository does not match"),
            ):
                lifecycle_operations._require_external_memory_authority(
                    external,
                    configured,
                    Path("/code"),
                )
            with (
                mock.patch.object(
                    lifecycle_operations,
                    "repository_identity",
                    side_effect=[Path("/same"), Path("/same")],
                ),
                self.assertRaisesRegex(RuntimeError, "must not share"),
            ):
                lifecycle_operations._require_external_memory_authority(
                    external,
                    configured,
                    Path("/same"),
                )
            with (
                mock.patch.object(
                    lifecycle_operations,
                    "repository_identity",
                    side_effect=[Path("/memory"), Path("/memory")],
                ),
                self.assertRaisesRegex(RuntimeError, "missing its candidate worktree"),
            ):
                lifecycle_operations._require_external_memory_authority(
                    replace(external, memory_worktree=None),
                    configured,
                    Path("/code"),
                )
            with (
                mock.patch.object(
                    lifecycle_operations,
                    "repository_identity",
                    side_effect=[Path("/memory"), Path("/memory"), Path("/foreign")],
                ),
                self.assertRaisesRegex(RuntimeError, "memory candidate belongs"),
            ):
                lifecycle_operations._require_external_memory_authority(
                    external,
                    configured,
                    Path("/code"),
                )

    def test_lifecycle_recovery_predicates_cover_process_and_terminal_states(self) -> None:
        now = datetime.now(UTC)
        with mock.patch.object(lifecycle_operations.os, "killpg", side_effect=ProcessLookupError):
            self.assertFalse(lifecycle_operations._worker_process_group_alive(42))
        with mock.patch.object(lifecycle_operations.os, "killpg", side_effect=PermissionError):
            self.assertTrue(lifecycle_operations._worker_process_group_alive(42))

        with tempfile.TemporaryDirectory() as tmp:
            _contract, running = self._running_record(Path(tmp))
            completed = running.model_copy(update={"status": "completed"})
            self.assertFalse(lifecycle_operations._recoverable_stale(completed, now))
            stale = running.model_copy(
                update={
                    "queuedAt": "2000-01-01T00:00:00+00:00",
                    "heartbeatAt": None,
                    "workerPid": None,
                }
            )
            self.assertTrue(lifecycle_operations._recoverable_stale(stale, now))
            with mock.patch.object(
                lifecycle_operations,
                "_worker_process_group_alive",
                return_value=True,
            ):
                self.assertFalse(
                    lifecycle_operations._recoverable_stale(
                        stale.model_copy(update={"workerPid": 42}),
                        now,
                    )
                )
            self.assertTrue(
                lifecycle_operations._should_recover(
                    running.model_copy(
                        update={
                            "status": "input-required",
                            "irreversibleBoundaryEntered": True,
                        }
                    ),
                    now,
                )
            )
            self.assertTrue(
                lifecycle_operations._should_recover(
                    running.model_copy(update={"status": "cancelled"}),
                    now,
                )
            )
            self.assertTrue(
                lifecycle_operations._should_recover(
                    running.model_copy(
                        update={
                            "status": "failed",
                            "irreversibleBoundaryEntered": True,
                            "result": {"safeToReplace": True},
                        }
                    ),
                    now,
                )
            )


class IntegrationBranchAuthorityCoverageTests(unittest.TestCase):
    @staticmethod
    def _surface(
        kind: str,
        *,
        branch: str = "ar/master",
        owner: str = "repo/master/task.json",
    ) -> IntegrationSurface:
        return IntegrationSurface(
            side="code",
            kind=cast(IntegrationSurfaceKind, kind),
            repository=Path("/repo"),
            branch=branch,
            owner=owner,
        )

    def test_surface_availability_and_owner_changes_fail_closed(self) -> None:
        scope = _BranchScope(
            Path("/coordination"),
            "repo",
            Path("/coordination/tasks/repo"),
            (_RepositorySide("code", Path("/repo"), Path("/repo"), "", ""),),
        )
        atomic = self._surface("atomic-integration")
        with (
            mock.patch.object(integration_branch_authority, "branch_exists", return_value=False),
            mock.patch.object(
                integration_branch_authority,
                "branch_worktree_owners",
                return_value=(Path("/checkout"),),
            ),
            mock.patch.object(
                integration_branch_authority,
                "_atomic_surface_has_series",
                return_value=False,
            ),
            self.assertRaisesRegex(RuntimeError, "already checked out"),
        ):
            integration_branch_authority._require_new_surface_availability(
                scope,
                (),
                (atomic,),
                allow_existing_super=False,
            )

        current = self._surface("sprint-super", owner="repo/sprint/task.json")
        with self.assertRaisesRegex(RuntimeError, "ownership would change"):
            integration_branch_authority._require_stable_surface_owners(
                (current,),
                (atomic,),
            )

    def test_atomic_surface_series_probe_covers_non_atomic_foreign_and_exact(self) -> None:
        scope = _BranchScope(
            Path("/coordination"),
            "repo",
            Path("/coordination/tasks/repo"),
            (_RepositorySide("code", Path("/repo"), Path("/repo"), "", ""),),
        )
        self.assertFalse(
            integration_branch_authority._atomic_surface_has_series(
                scope,
                self._surface("sprint-super"),
            )
        )
        self.assertFalse(
            integration_branch_authority._atomic_surface_has_series(
                scope,
                self._surface("atomic-integration", owner="foreign/master/task.json"),
            )
        )
        atomic = self._surface("atomic-integration")
        with mock.patch.object(Path, "is_file", return_value=False):
            self.assertFalse(integration_branch_authority._atomic_surface_has_series(scope, atomic))

        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            repository = integration_branch_authority.repository_identity(fixture.code_repo)
            assert repository is not None
            exact = IntegrationSurface(
                side="code",
                kind="atomic-integration",
                repository=repository,
                branch="ar/master",
                owner="repo/master/task.json",
            )
            self.assertTrue(
                integration_branch_authority._atomic_surface_has_series(
                    integration_branch_authority._scope(fixture.master_contract),
                    exact,
                )
            )

    def test_live_leaf_queue_identity_and_source_owner_must_remain_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            topology = TaskDocumentTopology(fixture.coordination)
            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
            master = topology.resolve(master_ref)
            leaf_ref = TaskDocumentRef(repository="repo", path="master/leaf-1.json")

            cases = (
                (
                    replace(
                        fixture.leaf_contract,
                        queue_candidate_task_document="repo/wrong.json",
                        queue_sprint_task_document=sprint_ref.key,
                    ),
                    "queue identity changed",
                ),
                (
                    replace(
                        fixture.leaf_contract,
                        queue_candidate_task_document=leaf_ref.key,
                        queue_sprint_task_document="",
                    ),
                    "queue binding is partial",
                ),
                (
                    replace(
                        fixture.leaf_contract,
                        queue_candidate_task_document=leaf_ref.key,
                        queue_sprint_task_document="repo/other/task.json",
                    ),
                    "queue owner changed",
                ),
            )
            for contract, reason in cases:
                with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, reason):
                    integration_branch_authority._require_live_leaf_task_identity(
                        topology,
                        contract,
                        master,
                        sprint_ref,
                        {},
                    )

            with self.assertRaisesRegex(RuntimeError, "source no longer matches"):
                integration_branch_authority._require_live_leaf_source_authority(
                    fixture.leaf_contract,
                    master,
                    sprint_ref,
                    (),
                    "atomic",
                )


class IntegrationValidationCoverageTests(unittest.TestCase):
    def test_validate_integration_refuses_stale_series_and_leaf_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            closed = _closed_leaf_worktree(fixture, root, candidate_commit=True)
            with self.assertRaisesRegex(RuntimeError, "worktree does not exist"):
                integrate_module.validate_integrate_contract(
                    replace(closed, code_worktree=root / "missing")
                )
            with (
                mock.patch.object(integrate_module, "current_branch", return_value="wrong"),
                self.assertRaisesRegex(RuntimeError, "must have"),
            ):
                integrate_module.validate_integrate_contract(closed)
            with (
                mock.patch.object(integrate_module, "current_branch", return_value="leaf"),
                mock.patch.object(integrate_module, "require_clean"),
                mock.patch.object(integrate_module, "head_commit", return_value="wrong"),
                self.assertRaisesRegex(RuntimeError, "HEAD does not match"),
            ):
                integrate_module.validate_integrate_contract(closed)

            series = replace(
                fixture.master_contract,
                closeout_status="completed",
                approved_for_commit=True,
                code_commit="a" * 40,
            )
            with (
                mock.patch.object(integrate_module, "branch_commit", return_value="b" * 40),
                self.assertRaisesRegex(RuntimeError, "atomic code ref"),
            ):
                integrate_module.validate_integrate_contract(series)

    def test_validate_external_memory_refuses_incomplete_and_stale_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            contract = fixture.leaf_contract
            with self.assertRaisesRegex(RuntimeError, "requires memory repo"):
                integrate_module.validate_integrate_memory_contract(
                    replace(contract, memory_repo_path=None, ledger_path=None)
                )
            with self.assertRaisesRegex(RuntimeError, "requires closeout"):
                integrate_module.validate_integrate_memory_contract(contract)
            complete = replace(
                contract,
                memory_content_commit="a" * 40,
                ledger_commit="b" * 40,
            )
            with self.assertRaisesRegex(RuntimeError, "requires a memory worktree"):
                integrate_module.validate_integrate_memory_contract(
                    replace(complete, memory_worktree=None)
                )
            series = replace(
                fixture.master_contract, memory_content_commit="a" * 40, ledger_commit="b" * 40
            )
            with (
                mock.patch.object(integrate_module, "branch_commit", return_value="c" * 40),
                self.assertRaisesRegex(RuntimeError, "atomic memory ref"),
            ):
                integrate_module.validate_integrate_memory_contract(series)

    def test_ref_recovery_refuses_mismatched_memory_shapes_and_torn_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = IntegrationOperationAuthorityCoverageTests()
            internal, internal_record = helper._running_record(root / "internal")
            internal_authority = internal_record.integrationAuthority
            assert internal_authority is not None
            external, external_record = helper._running_record(root / "external", external=True)
            external_authority = external_record.integrationAuthority
            assert external_authority is not None
            recovery = LifecycleOperationRecoveryCommits(
                codeCommit=external_authority.codeCandidateCommit,
                memoryContentCommit=external_authority.memoryContentCommit,
                ledgerCommit=external_authority.ledgerCommit,
            )
            with (
                mock.patch.object(
                    integrate_module,
                    "branch_commit",
                    return_value=internal_authority.codeSourceCommit,
                ),
                self.assertRaisesRegex(RuntimeError, "recorded external-memory commits"),
            ):
                integrate_module._recover_landed_refs(
                    internal,
                    WorktreeArgs(),
                    recovery,
                    internal_authority,
                )

            internal_recovery = LifecycleOperationRecoveryCommits(
                codeCommit=internal_authority.codeCandidateCommit,
            )
            with mock.patch.object(
                integrate_module,
                "branch_commit",
                return_value=internal_authority.codeSourceCommit,
            ):
                self.assertFalse(
                    integrate_module._recover_landed_refs(
                        internal,
                        WorktreeArgs(),
                        internal_recovery,
                        internal_authority,
                    )
                )
            with (
                mock.patch.object(integrate_module, "branch_commit", return_value="f" * 40),
                self.assertRaisesRegex(RuntimeError, "unowned code ref"),
            ):
                integrate_module._recover_landed_refs(
                    internal,
                    WorktreeArgs(),
                    internal_recovery,
                    internal_authority,
                )

            with (
                mock.patch.object(
                    integrate_module,
                    "branch_commit",
                    side_effect=[external_authority.codeCandidateCommit, "f" * 40],
                ),
                self.assertRaisesRegex(RuntimeError, "torn or concurrently changed"),
            ):
                integrate_module._recover_landed_refs(
                    external,
                    WorktreeArgs(),
                    recovery,
                    external_authority,
                )


class LineageAndSeriesCoverageTests(unittest.TestCase):
    def test_organizational_lineage_refuses_missing_graph_and_wrong_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            topology = TaskDocumentTopology(fixture.coordination)
            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            master = topology.resolve(master_ref)
            with mock.patch.object(topology, "parent", return_value=None):
                projection = source_lineage._organizational_master_projection(topology, master)
            self.assertEqual(projection.state, "unavailable")

            sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
            with (
                mock.patch.object(topology, "parent", return_value=sprint_ref),
                mock.patch.object(topology, "validate_execution_topology", return_value=[]),
            ):
                projection = source_lineage._organizational_master_projection(topology, master)
            self.assertEqual(projection.state, "unavailable")

            wrong = source_lineage._organizational_edge(
                source_lineage._EdgeInput(
                    "super-to-leaf",
                    "code",
                    fixture.code_repo,
                    "wrong",
                    "leaf",
                    fixture.leaf_contract.contract_path,
                    fixture.leaf_contract.code_base_commit,
                    True,
                ),
                expected_source_branch="super",
            )
            self.assertEqual(wrong.state, "unavailable")

    def test_organizational_source_resolution_refuses_parent_graph_and_branch_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp))
            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
            master = SimpleNamespace(document=SimpleNamespace(executionNature="organizational"))
            sprint = SimpleNamespace(document=SimpleNamespace(integrationBranch="super"))
            topology = SimpleNamespace(
                canonical_ref=lambda *_args: master_ref,
                resolve=lambda ref: master if ref == master_ref else sprint,
                parent=lambda _ref: sprint_ref,
                validate_execution_topology=lambda _ref: [SimpleNamespace(ref=master_ref)],
            )
            cases = (
                (
                    SimpleNamespace(**{**topology.__dict__, "parent": lambda _ref: None}),
                    "not commanded",
                ),
                (
                    SimpleNamespace(
                        **{
                            **topology.__dict__,
                            "validate_execution_topology": lambda _ref: [],
                        }
                    ),
                    "absent",
                ),
                (
                    SimpleNamespace(
                        **{
                            **topology.__dict__,
                            "resolve": lambda ref: (
                                master
                                if ref == master_ref
                                else SimpleNamespace(document=SimpleNamespace(integrationBranch=""))
                            ),
                        }
                    ),
                    "does not declare",
                ),
            )
            for mocked_topology, reason in cases:
                with (
                    self.subTest(reason=reason),
                    mock.patch.object(
                        source_lineage,
                        "TaskDocumentTopology",
                        return_value=mocked_topology,
                    ),
                ):
                    branch, detail = source_lineage._organizational_source_branch(
                        fixture.leaf_contract
                    )
                self.assertIsNone(branch)
                self.assertIn(reason, detail or "")

    def test_atomic_series_leaf_set_and_task_rows_must_be_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            empty_task = root / "empty-task"
            (empty_task / "enclosures").mkdir(parents=True)
            series = replace(
                fixture.master_contract,
                task_root=empty_task,
                worktree_group=empty_task / "enclosures",
            )
            leaf_ref = TaskDocumentRef(repository="repo", path="master/leaf.json")
            with (
                mock.patch.object(
                    series_closeout,
                    "_atomic_leaf_documents",
                    return_value=({"leaf": leaf_ref}, None),
                ),
                self.assertRaisesRegex(series_closeout.CloseoutQueueError, "one exact enclosure"),
            ):
                series_closeout._require_every_atomic_leaf_landed(series)

            master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
            master = SimpleNamespace(
                path=root / "master" / "task.json",
                document=SimpleNamespace(subTasks=[]),
            )
            topology = SimpleNamespace(
                canonical_ref=lambda *_args: master_ref,
                resolve=lambda _ref: master,
                parent=lambda _ref: None,
            )
            with (
                mock.patch.object(
                    series_closeout,
                    "TaskDocumentTopology",
                    return_value=topology,
                ),
                self.assertRaisesRegex(series_closeout.CloseoutQueueError, "at least one"),
            ):
                series_closeout._atomic_leaf_documents(series)
