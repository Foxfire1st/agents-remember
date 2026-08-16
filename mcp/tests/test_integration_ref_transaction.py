"""Crash-cut forcing for the protected named-ref transaction owner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.application.lifecycle_operation_worker import OperationRuntime
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.worktrees import integration_ref_transaction, lifecycle_operations
from agents_remember.worktrees.integration_ref_transaction import (
    CheckoutRefresh,
    IntegratedCommits,
    merge_integrated_commits,
    prepare_integration_ref_move,
    refresh_recovered_checkout,
)
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.integrate import (
    IntegrationSources,
    integrate_result,
)
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from test_integration_branch_authority import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
    _closed_leaf_worktree,
)
from test_source_lineage import _git


class IntegrationRefTransactionTests(unittest.TestCase):
    def test_prepare_ref_move_refuses_code_and_memory_tip_races(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            closed = _closed_leaf_worktree(fixture, root, candidate_commit=True)
            commits = IntegratedCommits(closed.code_commit, "", "")
            sources = IntegrationSources("accepted", "", False, False)
            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "require_authorized_integration_commits",
                ),
                mock.patch.object(
                    integration_ref_transaction,
                    "branch_commit",
                    return_value="moved",
                ),
                self.assertRaisesRegex(RuntimeError, "code integration source moved"),
            ):
                prepare_integration_ref_move(closed, commits, WorktreeArgs(), sources)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            commits = IntegratedCommits(
                closed.code_commit,
                closed.memory_content_commit,
                closed.ledger_commit,
            )
            sources = IntegrationSources("code-source", "memory-source", False, False)
            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "require_authorized_integration_commits",
                ),
                mock.patch.object(
                    integration_ref_transaction,
                    "branch_commit",
                    side_effect=["code-source", "moved"],
                ),
                mock.patch.object(
                    integration_ref_transaction,
                    "is_ancestor",
                    return_value=True,
                ),
                self.assertRaisesRegex(RuntimeError, "memory integration source moved"),
            ):
                prepare_integration_ref_move(closed, commits, WorktreeArgs(), sources)

    def test_code_cas_race_and_unreadable_ledger_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            closed = _closed_leaf_worktree(fixture, root, candidate_commit=True)
            commits = IntegratedCommits(closed.code_commit, "", "")
            snapshot = integration_ref_transaction.IntegrationRefSnapshot(
                code_branch=closed.code_source_branch,
                code_before=closed.code_base_commit,
                _authority=integration_ref_transaction._PREPARED_MOVE_AUTHORITY,
            )
            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "_compare_and_swap_ref",
                    return_value=False,
                ),
                self.assertRaisesRegex(
                    integration_ref_transaction.IntegrationRefRace,
                    "code integration ref moved",
                ) as caught,
            ):
                merge_integrated_commits(closed, commits, snapshot)
            self.assertTrue(caught.exception.safe_to_replace)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            commits = IntegratedCommits(
                closed.code_commit,
                closed.memory_content_commit,
                closed.ledger_commit,
            )
            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "run_git",
                    return_value=SimpleNamespace(returncode=1, stdout="", stderr="missing"),
                ),
                self.assertRaisesRegex(RuntimeError, "no readable memory.md"),
            ):
                integration_ref_transaction.require_integrated_ledger_mapping(closed, commits)

    def test_ref_and_checkout_recovery_cover_each_side_and_invalid_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            commits = IntegratedCommits(
                closed.code_commit,
                closed.memory_content_commit,
                closed.ledger_commit,
            )
            authority = SimpleNamespace(
                codeSourceBranch=closed.code_source_branch,
                codeSourceCommit=closed.code_base_commit,
                memorySourceBranch=closed.memory_source_branch,
                memorySourceCommit=closed.memory_base_commit,
            )
            record = SimpleNamespace(integrationAuthority=authority)
            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "require_authorized_integration_commits",
                    return_value=record,
                ),
                mock.patch.object(
                    integration_ref_transaction,
                    "_compare_and_swap_ref",
                    return_value=True,
                ) as compare,
            ):
                self.assertTrue(
                    integration_ref_transaction.recover_integration_ref(
                        closed, WorktreeArgs(), commits, side="code"
                    )
                )
                self.assertTrue(
                    integration_ref_transaction.recover_integration_ref(
                        closed, WorktreeArgs(), commits, side="memory"
                    )
                )
                self.assertEqual(compare.call_count, 2)
                with self.assertRaisesRegex(RuntimeError, "invalid integration recovery side"):
                    integration_ref_transaction.recover_integration_ref(
                        closed, WorktreeArgs(), commits, side="other"
                    )

            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "require_authorized_integration_commits",
                    return_value=record,
                ),
                mock.patch.object(
                    integration_ref_transaction,
                    "refresh_owned_checkout",
                ) as refresh,
            ):
                integration_ref_transaction.refresh_recovered_checkout(
                    closed,
                    WorktreeArgs(),
                    commits,
                    CheckoutRefresh("code", "old", "new"),
                )
                integration_ref_transaction.refresh_recovered_checkout(
                    closed,
                    WorktreeArgs(),
                    commits,
                    CheckoutRefresh("memory", "old", "new"),
                )
                self.assertEqual(refresh.call_count, 2)
                with self.assertRaisesRegex(
                    RuntimeError, "invalid integration checkout recovery side"
                ):
                    integration_ref_transaction.refresh_recovered_checkout(
                        closed,
                        WorktreeArgs(),
                        commits,
                        CheckoutRefresh("other", "old", "new"),
                    )

    def test_checkout_refresh_refuses_wrong_tip_untracked_and_unrelated_changes(self) -> None:
        repo = Path("/repo")
        checkout = Path("/checkout")
        authority = integration_ref_transaction._PREPARED_MOVE_AUTHORITY
        with (
            mock.patch.object(
                integration_ref_transaction,
                "branch_commit",
                return_value="wrong",
            ),
            self.assertRaisesRegex(RuntimeError, "named ref at the landed tip"),
        ):
            integration_ref_transaction.refresh_owned_checkout(
                repo, "super", "old", "new", authority=authority
            )

        with (
            mock.patch.object(
                integration_ref_transaction,
                "branch_commit",
                return_value="new",
            ),
            mock.patch.object(
                integration_ref_transaction,
                "branch_worktree_owners",
                return_value=(checkout,),
            ),
            mock.patch.object(
                integration_ref_transaction,
                "run_git",
                return_value=SimpleNamespace(returncode=1, stdout="", stderr="failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "untracked files"),
        ):
            integration_ref_transaction.refresh_owned_checkout(
                repo, "super", "old", "new", authority=authority
            )

        results = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        with (
            mock.patch.object(
                integration_ref_transaction,
                "branch_commit",
                return_value="new",
            ),
            mock.patch.object(
                integration_ref_transaction,
                "branch_worktree_owners",
                return_value=(checkout,),
            ),
            mock.patch.object(
                integration_ref_transaction,
                "run_git",
                side_effect=results,
            ),
            self.assertRaisesRegex(RuntimeError, "unrelated changes"),
        ):
            integration_ref_transaction.refresh_owned_checkout(
                repo, "super", "old", "new", authority=authority
            )

    def test_clean_checkout_preflight_refuses_wrong_head(self) -> None:
        checkout = Path("/checkout")
        with (
            mock.patch.object(
                integration_ref_transaction,
                "branch_worktree_owners",
                return_value=(checkout,),
            ),
            mock.patch.object(integration_ref_transaction, "require_clean"),
            mock.patch.object(
                integration_ref_transaction,
                "head_commit",
                return_value="wrong",
            ),
            self.assertRaisesRegex(RuntimeError, "not at its expected"),
        ):
            integration_ref_transaction._require_clean_branch_checkout(
                Path("/repo"), "super", "expected"
            )

    def test_post_cas_untracked_file_refuses_checkout_refresh_and_recovers_exactly(self) -> None:
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
            _git(fixture.code_repo, "switch", "main")
            owner = root / "super-owner"
            _git(fixture.code_repo, "worktree", "add", owner.as_posix(), "ar/master")
            commits = IntegratedCommits(code=closed.code_commit, memory_content="", ledger="")
            args = WorktreeArgs(operation_key=running.operationKey)
            snapshot = prepare_integration_ref_move(
                closed,
                commits,
                args,
                IntegrationSources(
                    current_code_source=authority.codeSourceCommit,
                    current_memory_source="",
                    code_replay_required=False,
                    memory_replay_required=False,
                ),
            )
            original = integration_ref_transaction._compare_and_swap_ref

            def create_untracked_after_cas(
                repo: Path,
                branch: str,
                expected: str,
                target: str,
                *,
                authority: object | None = None,
            ) -> bool:
                moved = original(
                    repo,
                    branch,
                    expected,
                    target,
                    authority=authority,
                )
                if moved:
                    (owner / "concurrent-untracked.txt").write_text("keep\n", encoding="utf-8")
                return moved

            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "_compare_and_swap_ref",
                    side_effect=create_untracked_after_cas,
                ),
                self.assertRaisesRegex(RuntimeError, "untracked files"),
            ):
                merge_integrated_commits(closed, commits, snapshot)

            self.assertEqual(_git(fixture.code_repo, "rev-parse", "ar/master"), commits.code)
            self.assertTrue((owner / "concurrent-untracked.txt").exists())
            (owner / "concurrent-untracked.txt").unlink()
            refresh_recovered_checkout(
                closed,
                args,
                commits,
                CheckoutRefresh(
                    side="code",
                    old=snapshot.code_before,
                    new=commits.code,
                ),
            )
            self.assertEqual(_git(owner, "rev-parse", "HEAD"), commits.code)

    def test_external_retry_accepts_code_checkout_already_refreshed_to_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            memory_repo = closed.memory_repo_path
            assert memory_repo is not None
            code_owner = root / "code-source-owner"
            memory_owner = root / "memory-source-owner"
            _git(fixture.code_repo, "worktree", "add", code_owner.as_posix(), "ar/master")
            _git(memory_repo, "worktree", "add", memory_owner.as_posix(), "ar/master")
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
            commits = IntegratedCommits(
                code=closed.code_commit,
                memory_content=closed.memory_content_commit,
                ledger=closed.ledger_commit,
            )
            args = WorktreeArgs(operation_key=running.operationKey)
            snapshot = prepare_integration_ref_move(
                closed,
                commits,
                args,
                IntegrationSources(
                    current_code_source=authority.codeSourceCommit,
                    current_memory_source=authority.memorySourceCommit,
                    code_replay_required=False,
                    memory_replay_required=False,
                ),
            )
            recovery = LifecycleOperationRecoveryCommits(
                codeCommit=commits.code,
                memoryContentCommit=commits.memory_content,
                ledgerCommit=commits.ledger,
            )
            OperationRuntime(store).progress(
                "source-merge",
                {
                    "current_command": "recover exact external integration pair",
                    "irreversible_boundary": True,
                    "recovery_commits": recovery.model_dump(mode="json"),
                },
            )
            refresh = integration_ref_transaction.refresh_owned_checkout

            def fail_memory_refresh(
                repo: Path,
                branch: str,
                old: str,
                new: str,
                *,
                authority: object | None = None,
            ) -> None:
                if repo == memory_repo:
                    raise RuntimeError("memory checkout refresh crash")
                refresh(repo, branch, old, new, authority=authority)

            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "refresh_owned_checkout",
                    side_effect=fail_memory_refresh,
                ),
                self.assertRaisesRegex(RuntimeError, "memory checkout refresh crash"),
            ):
                merge_integrated_commits(closed, commits, snapshot)

            self.assertTrue((code_owner / "candidate.txt").is_file())
            self.assertFalse((memory_owner / "candidate.md").exists())
            with (
                mock.patch(
                    "agents_remember.worktrees.modules.integrate."
                    "publish_queue_candidate_integration_under_authority",
                    side_effect=lambda _contract, publication, **_kwargs: publication(),
                ),
                mock.patch(
                    "agents_remember.worktrees.modules.integrate."
                    "complete_queue_candidate_integration"
                ),
            ):
                recovered = integrate_result(
                    WorktreeArgs(
                        contract_path=closed.contract_path,
                        approved=True,
                        operation_key=running.operationKey,
                        recovery_commits=recovery,
                    )
                )
            self.assertEqual(recovered.payload["state"], "integrated")
            completed = load_contract(closed.contract_path)
            self.assertEqual(
                (
                    completed.integrated_code_commit,
                    completed.integrated_memory_content_commit,
                    completed.integrated_ledger_commit,
                ),
                (
                    commits.code,
                    commits.memory_content,
                    commits.ledger,
                ),
            )
            self.assertTrue((code_owner / "candidate.txt").is_file())
            self.assertTrue((memory_owner / "candidate.md").is_file())


if __name__ == "__main__":
    unittest.main()
