from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees import closeout_recovery as closeout_recovery_journal
from agents_remember.worktrees.modules import closeout as closeout_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from test_worktree_support import git, open_external_contract_fixture


def _patch_external_refresh(stack: ExitStack) -> None:
    services = SimpleNamespace(memory_quality=SimpleNamespace(check_groups=lambda: ([], [])))
    stack.enter_context(mock.patch.object(closeout_module, "_closeout_contract_context"))
    stack.enter_context(
        mock.patch.object(closeout_module, "refresh_onboarding_metadata", return_value=[])
    )
    stack.enter_context(
        mock.patch.object(
            closeout_module,
            "refresh_route_overview_metadata_for_context",
            return_value=[],
        )
    )
    stack.enter_context(
        mock.patch.object(
            closeout_module,
            "refresh_entity_fingerprints_for_context",
            return_value=[],
        )
    )
    stack.enter_context(
        mock.patch.object(closeout_module, "refresh_route_indexes_for_context", return_value={})
    )
    stack.enter_context(
        mock.patch.object(closeout_module, "worktree_services", return_value=services)
    )
    stack.enter_context(
        mock.patch.object(closeout_module, "run_memory_quality_phase", return_value={})
    )
    stack.enter_context(
        mock.patch.object(closeout_module, "combine_memory_quality", return_value={})
    )
    stack.enter_context(mock.patch.object(closeout_module, "worktree_dirty", return_value=False))
    stack.enter_context(mock.patch.object(closeout_module, "load_ledger"))


class CloseoutRecoveryTests(unittest.TestCase):
    def test_code_commit_recovery_proves_head_and_candidate_tree(self) -> None:
        contract = SimpleNamespace(kind="leaf", code_worktree=Path("/code"))
        args = WorktreeArgs(
            candidate_tree="b" * 40,
            recovery_commits=LifecycleOperationRecoveryCommits(codeCommit="a" * 40),
        )
        with (
            mock.patch.object(closeout_recovery_journal, "require_clean"),
            mock.patch.object(closeout_recovery_journal, "head_commit", return_value="c" * 40),
            self.assertRaisesRegex(RuntimeError, "does not match task HEAD"),
        ):
            closeout_recovery_journal.accepted_code_commit(
                contract,
                args,
                strict_code_quality_required=True,
                resuming=True,
            )

        with (
            mock.patch.object(closeout_recovery_journal, "require_clean"),
            mock.patch.object(closeout_recovery_journal, "head_commit", return_value="a" * 40),
            mock.patch.object(closeout_recovery_journal, "require_git", return_value="c" * 40),
            self.assertRaisesRegex(RuntimeError, "accepted candidate tree"),
        ):
            closeout_recovery_journal.accepted_code_commit(
                contract,
                args,
                strict_code_quality_required=True,
                resuming=True,
            )

    def test_clean_claimed_code_commit_is_journaled_without_recommit(self) -> None:
        contract = SimpleNamespace(kind="leaf", code_worktree=Path("/code"))
        evidence: dict[str, object] = {}
        args = WorktreeArgs(
            candidate_tree="b" * 40,
            operation_progress=lambda _phase, found: evidence.update(found),
        )
        with (
            mock.patch.object(closeout_recovery_journal, "worktree_dirty", return_value=False),
            mock.patch.object(closeout_recovery_journal, "head_commit", return_value="a" * 40),
            mock.patch.object(closeout_recovery_journal, "require_git", return_value="b" * 40),
        ):
            found = closeout_recovery_journal.accepted_code_commit(
                contract,
                args,
                strict_code_quality_required=True,
                resuming=True,
            )
        self.assertEqual(found, "a" * 40)
        self.assertEqual(
            evidence["recovery_commits"],
            {
                "codeCommit": "a" * 40,
                "memoryContentCommit": "",
                "ledgerCommit": "",
            },
        )

    def test_series_closeout_reuses_the_clean_accepted_head_without_committing(self) -> None:
        contract = SimpleNamespace(kind="series", code_worktree=Path("/code"))
        args = WorktreeArgs(candidate_tree="b" * 40)
        with (
            mock.patch.object(closeout_recovery_journal, "require_clean") as require_clean,
            mock.patch.object(closeout_recovery_journal, "head_commit", return_value="a" * 40),
            mock.patch.object(closeout_recovery_journal, "require_git", return_value="b" * 40),
            mock.patch.object(
                closeout_recovery_journal, "commit_verified_staged"
            ) as commit_verified,
            mock.patch.object(closeout_recovery_journal, "commit_if_dirty") as commit_dirty,
        ):
            found = closeout_recovery_journal.accepted_code_commit(
                contract,
                args,
                strict_code_quality_required=False,
                resuming=False,
            )

        self.assertEqual(found, "a" * 40)
        require_clean.assert_called_once_with(
            contract.code_worktree, "recording series/master closeout code"
        )
        commit_verified.assert_not_called()
        commit_dirty.assert_not_called()

    def test_external_resume_rejects_conflict_missing_head_and_unreachable_content(self) -> None:
        contract = SimpleNamespace(
            memory_worktree=Path("/memory"),
            ledger_path=Path("/memory/memory.md"),
            task_id="TASK",
        )
        args = WorktreeArgs()
        mapping = SimpleNamespace(memory_commit="b" * 40)
        with (
            mock.patch.object(closeout_recovery_journal, "require_clean"),
            mock.patch.object(closeout_recovery_journal, "load_ledger", return_value=[]),
            mock.patch.object(closeout_recovery_journal, "head_commit", return_value="c" * 40),
            mock.patch.object(closeout_recovery_journal, "find_mapping", return_value=mapping),
            self.assertRaisesRegex(RuntimeError, "conflicting"),
        ):
            closeout_recovery_journal.resume_external_commits(
                contract, args, code_commit="a" * 40, memory_commit="d" * 40
            )
        with (
            mock.patch.object(closeout_recovery_journal, "require_clean"),
            mock.patch.object(closeout_recovery_journal, "load_ledger", return_value=[]),
            mock.patch.object(closeout_recovery_journal, "head_commit", return_value="c" * 40),
            mock.patch.object(closeout_recovery_journal, "find_mapping", return_value=None),
            self.assertRaisesRegex(RuntimeError, "memory HEAD"),
        ):
            closeout_recovery_journal.resume_external_commits(
                contract, args, code_commit="a" * 40, memory_commit="b" * 40
            )
        with (
            mock.patch.object(closeout_recovery_journal, "require_clean"),
            mock.patch.object(closeout_recovery_journal, "load_ledger", return_value=[]),
            mock.patch.object(closeout_recovery_journal, "head_commit", return_value="c" * 40),
            mock.patch.object(closeout_recovery_journal, "find_mapping", return_value=mapping),
            mock.patch.object(closeout_recovery_journal, "is_ancestor", return_value=False),
            self.assertRaisesRegex(RuntimeError, "not reachable"),
        ):
            closeout_recovery_journal.resume_external_commits(
                contract, args, code_commit="a" * 40, memory_commit="b" * 40
            )
        evidence: dict[str, object] = {}
        with (
            mock.patch.object(closeout_recovery_journal, "require_clean"),
            mock.patch.object(closeout_recovery_journal, "load_ledger", return_value=[]),
            mock.patch.object(closeout_recovery_journal, "head_commit", return_value="c" * 40),
            mock.patch.object(closeout_recovery_journal, "find_mapping", return_value=mapping),
            mock.patch.object(closeout_recovery_journal, "is_ancestor", return_value=True),
        ):
            resumed = closeout_recovery_journal.resume_external_commits(
                contract,
                replace(
                    args,
                    operation_progress=lambda _phase, found: evidence.update(found),
                ),
                code_commit="a" * 40,
                memory_commit="b" * 40,
            )
        self.assertEqual(resumed, ("b" * 40, "c" * 40))
        self.assertEqual(
            evidence["recovery_commits"],
            {
                "codeCommit": "a" * 40,
                "memoryContentCommit": "b" * 40,
                "ledgerCommit": "c" * 40,
            },
        )

    def test_recovery_rejects_code_and_contract_memory_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = open_external_contract_fixture(Path(tmp))
            code_head = git(contract.code_worktree, "rev-parse", "HEAD")
            no_memory = LifecycleOperationRecoveryCommits(codeCommit=code_head)
            with self.assertRaisesRegex(RuntimeError, "recorded code commit"):
                closeout_recovery_journal.prove_closeout_recovery_commits(
                    contract, LifecycleOperationRecoveryCommits(codeCommit="a" * 40)
                )

            internal = replace(
                contract,
                memory_mode="internal",
                memory_repo_path=None,
                memory_worktree=None,
                ledger_path=None,
            )
            self.assertEqual(
                closeout_recovery_journal.prove_closeout_recovery_commits(internal, no_memory),
                closeout_recovery_journal.MemoryCloseoutOutcome(),
            )
            with self.assertRaisesRegex(RuntimeError, "recorded external-memory commits"):
                closeout_recovery_journal.prove_closeout_recovery_commits(
                    internal,
                    LifecycleOperationRecoveryCommits(
                        codeCommit=code_head,
                        memoryContentCommit="b" * 40,
                        ledgerCommit="c" * 40,
                    ),
                )
            with self.assertRaisesRegex(RuntimeError, "requires memory worktree and ledger"):
                closeout_recovery_journal.prove_closeout_recovery_commits(
                    replace(contract, memory_worktree=None, ledger_path=None), no_memory
                )

    def test_recovery_rejects_unproven_memory_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = open_external_contract_fixture(Path(tmp))
            assert contract.memory_worktree is not None
            assert contract.ledger_path is not None
            code_head = git(contract.code_worktree, "rev-parse", "HEAD")
            mapping = closeout_recovery_journal.find_mapping(
                closeout_recovery_journal.load_ledger(contract.ledger_path), code_head
            )
            assert mapping is not None
            proven = LifecycleOperationRecoveryCommits(
                codeCommit=code_head,
                memoryContentCommit=mapping.memory_commit,
                ledgerCommit=git(contract.memory_worktree, "rev-parse", "HEAD"),
            )
            self.assertEqual(
                closeout_recovery_journal.prove_closeout_recovery_commits(contract, proven),
                closeout_recovery_journal.MemoryCloseoutOutcome(
                    memory_commit=proven.memoryContentCommit,
                    ledger_commit=proven.ledgerCommit,
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "found memory HEAD"):
                closeout_recovery_journal.prove_closeout_recovery_commits(
                    contract, proven.model_copy(update={"ledgerCommit": "d" * 40})
                )
            for observed in (None, SimpleNamespace(memory_commit="e" * 40)):
                with (
                    self.subTest(observed=observed),
                    mock.patch.object(
                        closeout_recovery_journal, "find_mapping", return_value=observed
                    ),
                    self.assertRaisesRegex(RuntimeError, "ledger mapping"),
                ):
                    closeout_recovery_journal.prove_closeout_recovery_commits(contract, proven)
            with (
                mock.patch.object(closeout_recovery_journal, "is_ancestor", return_value=False),
                self.assertRaisesRegex(RuntimeError, "not reachable"),
            ):
                closeout_recovery_journal.prove_closeout_recovery_commits(contract, proven)

    def test_completed_recovery_must_match_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = open_external_contract_fixture(Path(tmp))
            code_head = git(contract.code_worktree, "rev-parse", "HEAD")
            internal = replace(
                contract,
                memory_mode="internal",
                memory_repo_path=None,
                memory_worktree=None,
                ledger_path=None,
                closeout_status="completed",
                code_commit=code_head,
            )
            args = WorktreeArgs(
                recovery_commits=LifecycleOperationRecoveryCommits(codeCommit=code_head)
            )
            with mock.patch.object(closeout_module, "status_payload", return_value={}):
                recovered = closeout_module._recover_closeout_finalization(internal, args)
            assert recovered is not None
            self.assertEqual(recovered.payload["state"], "already-closed")
            self.assertTrue(recovered.payload["recovered"])
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                closeout_module._recover_closeout_finalization(
                    replace(internal, code_commit="f" * 40), args
                )
            self.assertIsNone(
                closeout_module._recover_closeout_finalization(internal, WorktreeArgs())
            )

    def test_external_closeout_refuses_an_unreachable_existing_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            contract = open_external_contract_fixture(Path(tmp))
            _patch_external_refresh(stack)
            stack.enter_context(
                mock.patch.object(
                    closeout_module,
                    "find_mapping",
                    return_value=SimpleNamespace(memory_commit="b" * 40),
                )
            )
            stack.enter_context(
                mock.patch.object(closeout_module, "head_commit", return_value="c" * 40)
            )
            stack.enter_context(
                mock.patch.object(closeout_module, "is_ancestor", return_value=False)
            )
            stack.enter_context(self.assertRaisesRegex(RuntimeError, "not reachable"))
            closeout_module._external_closeout_commits(
                contract,
                WorktreeArgs(),
                closeout_module.VerifiedChange("a" * 40, "2026-08-14", ["feature.py"]),
                {},
            )

    def test_external_closeout_uses_clean_memory_head_when_no_mapping_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            contract = replace(
                open_external_contract_fixture(Path(tmp)),
                memory_content_commit="d" * 40,
            )
            _patch_external_refresh(stack)
            stack.enter_context(
                mock.patch.object(closeout_module, "find_mapping", return_value=None)
            )
            stack.enter_context(
                mock.patch.object(closeout_module, "head_commit", return_value="b" * 40)
            )
            stack.enter_context(mock.patch.object(closeout_module, "prepend_mapping"))
            stack.enter_context(mock.patch.object(closeout_module, "write_ledger"))
            stack.enter_context(mock.patch.object(closeout_module, "require_git"))
            stack.enter_context(
                mock.patch.object(closeout_module, "commit_if_dirty", return_value="c" * 40)
            )
            result = closeout_module._external_closeout_commits(
                contract,
                WorktreeArgs(approval_claimed=True),
                closeout_module.VerifiedChange("a" * 40, "2026-08-14", ["feature.py"]),
                {},
            )

            self.assertEqual(result.memory_commit, "b" * 40)
            self.assertEqual(result.ledger_commit, "c" * 40)
