"""Crash-cut forcing for the protected named-ref transaction owner."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.application.lifecycle.lifecycle_operation_worker import OperationRuntime
from agents_remember.kernel.memory_ledger import (
    LedgerRow,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationPublicationIntent,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.worktrees.integration import integration_ref_transaction
from agents_remember.worktrees.integration.integration_ref_transaction import (
    CheckoutRefresh,
    IntegratedCommits,
    merge_integrated_commits,
    prepare_integration_ref_move,
    refresh_recovered_checkout,
)
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules import integrate as integrate_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.integrate import (
    IntegrationSources,
    integrate_result,
)
from agents_remember.worktrees.series_closeout import atomic_series_ledger_prefix
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from integration_branch_authority_test_support import (
    _land_two_external_atomic_leaves,
    _publish_completed_closeout_fixture,
)
from test_integration_branch_authority import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
    _closed_leaf_worktree,
)
from test_source_lineage import _git


class IntegrationRefTransactionTests(unittest.TestCase):
    @staticmethod
    def _land_external_recovery_pair(fixture, contract):
        lifecycle_operations.start_or_observe_operation(
            IntegrateOperationInput(
                configPath=fixture.config_path.as_posix(),
                contractPath=contract.contract_path.as_posix(),
            ),
            contract,
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        runtime = OperationRuntime(store)
        running = runtime.start()
        authority = running.integrationAuthority
        assert authority is not None and contract.memory_repo_path is not None
        recovery = LifecycleOperationRecoveryCommits(
            codeCommit=contract.code_commit,
            memoryContentCommit=contract.memory_content_commit,
            ledgerCommit=contract.ledger_commit,
        )
        running = runtime.progress(
            "source-merge",
            {
                "current_command": "recover exact external integration pair",
                "irreversible_boundary": True,
                "recovery_commits": recovery.model_dump(mode="json"),
                "integration_publication": IntegrationPublicationIntent(
                    operationKey=running.operationKey,
                    generation=running.generation,
                    preparedAt="2026-08-22T00:00:00+00:00",
                    claimState="not-applicable",
                ).model_dump(mode="json"),
            },
        )
        _git(
            fixture.code_repo,
            "update-ref",
            f"refs/heads/{authority.codeSourceBranch}",
            recovery.codeCommit,
            authority.codeSourceCommit,
        )
        _git(
            contract.memory_repo_path,
            "update-ref",
            f"refs/heads/{authority.memorySourceBranch}",
            recovery.ledgerCommit,
            authority.memorySourceCommit,
        )
        durable = store.read()
        assert durable is not None
        return durable, recovery

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
            self.assertEqual(
                caught.exception.expected,
                {
                    "before": {"codeRef": closed.code_base_commit},
                    "intended": {"codeRef": closed.code_commit},
                },
            )
            # The raw CAS exception deliberately carries no live ref snapshot.  The
            # protected/public owner immediately rereads through IntegrationRefState.
            self.assertEqual(caught.exception.observed, {})

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
                integration_ref_transaction.require_integrated_ledger_mapping(
                    closed,
                    commits,
                    memory_source_commit=closed.memory_base_commit,
                )

    def test_prepared_move_refuses_mapped_content_outside_the_exact_memory_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            assert closed.memory_repo_path is not None
            assert closed.memory_worktree is not None
            assert closed.ledger_path is not None
            content_tree = _git(
                closed.memory_repo_path,
                "rev-parse",
                f"{closed.memory_content_commit}^{{tree}}",
            )
            unrelated_content = _git(
                closed.memory_repo_path,
                "commit-tree",
                content_tree,
                "-m",
                "foreign content",
            )
            ledger = load_ledger(closed.ledger_path)
            rows = [
                LedgerRow(closed.code_commit, unrelated_content),
                *(row for row in ledger.rows if row.code_commit != closed.code_commit),
            ]
            write_ledger(
                closed.ledger_path,
                replace(
                    ledger,
                    last_verified_code_commit=closed.code_commit,
                    last_memory_content_commit=unrelated_content,
                    rows=rows,
                ),
            )
            _git(closed.memory_worktree, "add", "memory.md")
            ledger_tree = _git(closed.memory_worktree, "write-tree")
            forged_ledger = _git(
                closed.memory_repo_path,
                "commit-tree",
                ledger_tree,
                "-p",
                closed.memory_base_commit,
                "-p",
                unrelated_content,
                "-m",
                "merge foreign mapped content",
            )
            self.assertFalse(
                integration_ref_transaction.is_ancestor(
                    closed.memory_repo_path,
                    closed.memory_base_commit,
                    unrelated_content,
                )
            )
            commits = IntegratedCommits(
                closed.code_commit,
                unrelated_content,
                forged_ledger,
            )
            sources = IntegrationSources(
                closed.code_base_commit,
                closed.memory_base_commit,
                False,
                False,
            )
            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "require_authorized_integration_commits",
                ),
                self.assertRaisesRegex(RuntimeError, "not based on the exact memory source"),
            ):
                prepare_integration_ref_move(closed, commits, WorktreeArgs(), sources)

    def test_integrated_ledger_accepts_newest_settings_only_mapping_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            assert closed.memory_repo_path is not None
            assert closed.memory_worktree is not None
            assert closed.ledger_path is not None
            source_ledger = load_ledger(closed.ledger_path)
            source_mapping = source_ledger.rows[0]
            settings = closed.memory_worktree / "system" / "settings.md"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text("setting: updated\n", encoding="utf-8")
            _git(closed.memory_worktree, "add", "system/settings.md")
            _git(closed.memory_worktree, "commit", "-m", "update memory settings")
            settings_memory = _git(closed.memory_worktree, "rev-parse", "HEAD")
            write_ledger(
                closed.ledger_path,
                prepend_mapping(
                    source_ledger,
                    closed.code_commit,
                    settings_memory,
                ),
            )
            _git(closed.memory_worktree, "add", "memory.md")
            _git(closed.memory_worktree, "commit", "-m", "map settings-only memory")
            settings_ledger = _git(closed.memory_worktree, "rev-parse", "HEAD")

            integration_ref_transaction.require_integrated_ledger_mapping(
                closed,
                IntegratedCommits(
                    closed.code_commit,
                    settings_memory,
                    settings_ledger,
                ),
                memory_source_commit=closed.ledger_commit,
            )
            resolved = load_ledger(closed.ledger_path)
            self.assertEqual(resolved.rows[0].code_commit, source_mapping.code_commit)
            self.assertEqual(resolved.rows[0].memory_commit, settings_memory)
            self.assertEqual(resolved.rows[1], source_mapping)

    def test_integrated_ledger_refuses_a_code_commit_with_no_landed_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            # A foreign code commit the ledger never maps resolves to None, and the
            # mismatch refusal names the exact requirement.
            with self.assertRaisesRegex(
                RuntimeError, "does not map landed code commit to landed memory content"
            ):
                integration_ref_transaction.require_integrated_ledger_mapping(
                    closed,
                    IntegratedCommits(
                        "d" * 40,
                        closed.memory_content_commit,
                        closed.ledger_commit,
                    ),
                    memory_source_commit=closed.memory_base_commit,
                )

    def test_integrated_ledger_refuses_unreachable_memory_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            assert closed.memory_repo_path is not None
            # The real landed ledger content (which maps the code to the landed memory
            # content) re-committed under a parent chain that excludes the memory
            # content commit: the content is right, the ancestry is not.
            tree = _git(closed.memory_repo_path, "rev-parse", f"{closed.ledger_commit}^{{tree}}")
            forged_ledger = _git(
                closed.memory_repo_path,
                "commit-tree",
                tree,
                "-p",
                closed.memory_base_commit,
                "-m",
                "forged ledger without the memory content ancestor",
            )
            with self.assertRaisesRegex(
                RuntimeError, "not reachable from the landed ledger commit"
            ):
                integration_ref_transaction.require_integrated_ledger_mapping(
                    closed,
                    IntegratedCommits(
                        closed.code_commit,
                        closed.memory_content_commit,
                        forged_ledger,
                    ),
                    memory_source_commit=closed.memory_base_commit,
                )

    def test_atomic_series_ledger_requires_exact_leaf_prefix_and_source_suffix(self) -> None:
        for mutation in (
            "drop-leaf-prefix",
            "reorder-leaf-prefix",
            "drop-source-row",
            "rewrite-source-row",
            "inject-prefix-row",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                fixture = _authority_fixture(Path(tmp), external_memory=True)
                _first, final = _land_two_external_atomic_leaves(fixture)
                series = fixture.master_contract
                memory_repo = series.memory_repo_path
                assert memory_repo is not None
                ledger_path = memory_repo / "memory.md"
                ledger = load_ledger(ledger_path)
                prefix = ledger.rows[:2]
                source = ledger.rows[2:]
                self.assertEqual(
                    tuple(prefix),
                    atomic_series_ledger_prefix(series),
                )
                self.assertTrue(source)
                if mutation == "drop-leaf-prefix":
                    rows = [prefix[0], *source]
                elif mutation == "reorder-leaf-prefix":
                    rows = [prefix[1], prefix[0], *source]
                elif mutation == "drop-source-row":
                    rows = prefix
                elif mutation == "rewrite-source-row":
                    rows = [
                        *prefix,
                        LedgerRow(source[0].code_commit, "e" * 40),
                        *source[1:],
                    ]
                else:
                    rows = [
                        prefix[0],
                        LedgerRow("f" * 40, "e" * 40),
                        prefix[1],
                        *source,
                    ]
                write_ledger(
                    ledger_path,
                    replace(
                        ledger,
                        last_verified_code_commit=rows[0].code_commit,
                        last_memory_content_commit=rows[0].memory_commit,
                        rows=rows,
                    ),
                )
                _git(memory_repo, "add", "memory.md")
                tree = _git(memory_repo, "write-tree")
                forged_ledger = _git(
                    memory_repo,
                    "commit-tree",
                    tree,
                    "-p",
                    final.integrated_ledger_commit,
                    "-m",
                    mutation,
                )
                with self.assertRaisesRegex(RuntimeError, "exact ordered leaf landing prefix"):
                    integration_ref_transaction.require_integrated_ledger_mapping(
                        series,
                        IntegratedCommits(
                            final.integrated_code_commit,
                            final.integrated_memory_content_commit,
                            forged_ledger,
                        ),
                        memory_source_commit=series.memory_base_commit,
                        expected_series_prefix=atomic_series_ledger_prefix(series),
                    )

    def test_atomic_series_publication_refuses_injected_prefix_before_super_movement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            _first, final = _land_two_external_atomic_leaves(fixture)
            series = fixture.master_contract
            memory_repo = series.memory_repo_path
            assert memory_repo is not None
            ledger_path = memory_repo / "memory.md"
            ledger = load_ledger(ledger_path)
            write_ledger(
                ledger_path,
                replace(
                    ledger,
                    rows=[
                        ledger.rows[0],
                        LedgerRow("f" * 40, "e" * 40),
                        *ledger.rows[1:],
                    ],
                ),
            )
            _git(memory_repo, "add", "memory.md")
            forged_tree = _git(memory_repo, "write-tree")
            forged_ledger = _git(
                memory_repo,
                "commit-tree",
                forged_tree,
                "-p",
                final.integrated_ledger_commit,
                "-m",
                "inject unrelated series prefix row",
            )
            _git(memory_repo, "reset", "--hard", forged_ledger)
            forged_final = replace(
                final,
                ledger_commit=forged_ledger,
                integrated_ledger_commit=forged_ledger,
            )
            write_contract(forged_final.contract_path, forged_final)
            forged = replace(
                series,
                closeout_status="completed",
                approved_for_commit=True,
                human_review_status="approved",
                code_commit=final.integrated_code_commit,
                memory_content_commit=final.integrated_memory_content_commit,
                ledger_commit=forged_ledger,
            )
            write_contract(forged.contract_path, forged)
            forged = _publish_completed_closeout_fixture(fixture, forged)
            lifecycle_operations.start_or_observe_operation(
                IntegrateOperationInput(
                    configPath=fixture.config_path.as_posix(),
                    contractPath=forged.contract_path.as_posix(),
                ),
                forged,
                launcher=lambda *_: None,
            )
            store = LifecycleOperationStore(
                operation_record_path(forged.worktree_group, "integrate")
            )
            running = OperationRuntime(store).start()
            code_super = _git(fixture.code_repo, "rev-parse", "super")
            memory_super = _git(memory_repo, "rev-parse", "super")
            contract_bytes = forged.contract_path.read_bytes()

            with (
                mock.patch.object(
                    integrate_module,
                    "_run_integration_quality_gate",
                    return_value=({"passed": True}, None),
                ),
                self.assertRaisesRegex(RuntimeError, "exact ordered leaf landing prefix"),
            ):
                integrate_result(
                    WorktreeArgs(
                        contract_path=forged.contract_path,
                        approved=True,
                        operation_key=running.operationKey,
                        operation_generation=running.generation,
                    ),
                    forged,
                )
            self.assertEqual(_git(fixture.code_repo, "rev-parse", "super"), code_super)
            self.assertEqual(_git(memory_repo, "rev-parse", "super"), memory_super)
            self.assertEqual(forged.contract_path.read_bytes(), contract_bytes)

    def test_integrated_ledger_refuses_dropped_source_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            assert closed.memory_repo_path is not None
            assert closed.memory_worktree is not None
            assert closed.ledger_path is not None
            ledger = load_ledger(closed.ledger_path)
            write_ledger(closed.ledger_path, replace(ledger, rows=[ledger.rows[0]]))
            _git(closed.memory_worktree, "add", "memory.md")
            _git(closed.memory_worktree, "commit", "-m", "drop source ledger history")
            truncated_ledger = _git(closed.memory_worktree, "rev-parse", "HEAD")
            with self.assertRaisesRegex(RuntimeError, "complete source ledger history"):
                integration_ref_transaction.require_integrated_ledger_mapping(
                    closed,
                    IntegratedCommits(
                        closed.code_commit,
                        closed.memory_content_commit,
                        truncated_ledger,
                    ),
                    memory_source_commit=closed.memory_base_commit,
                )

    def test_both_landed_recovery_refuses_two_new_rows_before_finalization(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(
                fixture,
                root,
                publish_closeout_evidence=False,
            )
            assert closed.memory_worktree is not None
            assert closed.ledger_path is not None
            write_ledger(
                closed.ledger_path,
                prepend_mapping(
                    load_ledger(closed.ledger_path),
                    closed.code_commit,
                    closed.memory_content_commit,
                ),
            )
            _git(closed.memory_worktree, "add", "memory.md")
            _git(closed.memory_worktree, "commit", "-m", "extra recovery mapping")
            forged = replace(
                closed,
                ledger_commit=_git(closed.memory_worktree, "rev-parse", "HEAD"),
            )
            write_contract(forged.contract_path, forged)
            forged = _publish_completed_closeout_fixture(fixture, forged)
            running, recovery = self._land_external_recovery_pair(fixture, forged)
            contract_bytes = forged.contract_path.read_bytes()

            with self.assertRaisesRegex(RuntimeError, "does not prepend exactly one mapping"):
                integrate_result(
                    WorktreeArgs(
                        contract_path=forged.contract_path,
                        approved=True,
                        operation_key=running.operationKey,
                        operation_generation=running.generation,
                        recovery_commits=recovery,
                        integration_publication=running.integrationPublication,
                    ),
                    forged,
                )
            self.assertEqual(forged.contract_path.read_bytes(), contract_bytes)

    def test_both_landed_recovery_refuses_content_outside_the_exact_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(
                fixture,
                root,
                publish_closeout_evidence=False,
            )
            assert closed.memory_repo_path is not None
            assert closed.memory_worktree is not None
            assert closed.ledger_path is not None
            content_tree = _git(
                closed.memory_repo_path,
                "rev-parse",
                f"{closed.memory_content_commit}^{{tree}}",
            )
            unrelated_content = _git(
                closed.memory_repo_path,
                "commit-tree",
                content_tree,
                "-m",
                "foreign recovery content",
            )
            ledger = load_ledger(closed.ledger_path)
            rows = [
                LedgerRow(closed.code_commit, unrelated_content),
                *(row for row in ledger.rows if row.code_commit != closed.code_commit),
            ]
            write_ledger(
                closed.ledger_path,
                replace(
                    ledger,
                    last_verified_code_commit=closed.code_commit,
                    last_memory_content_commit=unrelated_content,
                    rows=rows,
                ),
            )
            _git(closed.memory_worktree, "add", "memory.md")
            ledger_tree = _git(closed.memory_worktree, "write-tree")
            forged_ledger = _git(
                closed.memory_repo_path,
                "commit-tree",
                ledger_tree,
                "-p",
                closed.memory_base_commit,
                "-p",
                unrelated_content,
                "-m",
                "merge foreign recovery content",
            )
            forged = replace(
                closed,
                memory_content_commit=unrelated_content,
                ledger_commit=forged_ledger,
            )
            write_contract(forged.contract_path, forged)
            forged = _publish_completed_closeout_fixture(fixture, forged)
            running, recovery = self._land_external_recovery_pair(fixture, forged)
            contract_bytes = forged.contract_path.read_bytes()

            with self.assertRaisesRegex(RuntimeError, "not based on the exact memory source"):
                integrate_result(
                    WorktreeArgs(
                        contract_path=forged.contract_path,
                        approved=True,
                        operation_key=running.operationKey,
                        operation_generation=running.generation,
                        recovery_commits=recovery,
                        integration_publication=running.integrationPublication,
                    ),
                    forged,
                )
            self.assertEqual(forged.contract_path.read_bytes(), contract_bytes)

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
                closed,
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
                if moved:  # pragma: no cover
                    (owner / "concurrent-untracked.txt").write_text(
                        "keep\n", encoding="utf-8"
                    )  # pragma: no cover
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
                closed,
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
            runtime = OperationRuntime(store)
            runtime.progress(
                "source-merge",
                {
                    "current_command": "recover exact external integration pair",
                    "irreversible_boundary": True,
                    "recovery_commits": recovery.model_dump(mode="json"),
                    "integration_publication": IntegrationPublicationIntent(
                        operationKey=running.operationKey,
                        generation=running.generation,
                        preparedAt="2026-08-22T00:00:00+00:00",
                        claimState="not-applicable",
                    ).model_dump(mode="json"),
                },
            )
            running = store.read()
            assert running is not None
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
            recovered = integrate_result(
                WorktreeArgs(
                    contract_path=closed.contract_path,
                    approved=True,
                    operation_key=running.operationKey,
                    operation_generation=running.generation,
                    recovery_commits=recovery,
                    integration_publication=running.integrationPublication,
                ),
                closed,
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
