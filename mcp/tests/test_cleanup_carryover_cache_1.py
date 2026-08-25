from __future__ import annotations

import fcntl
import threading
import time
from dataclasses import replace
from unittest.mock import patch

from agents_remember.memory_quality.style.citations import source_index, source_index_cache
from agents_remember.worktrees.modules.abandon import abandon_result
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import LocalBranchPresence, cleanup_result
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    amend_contract,
    write_contract,
)
from test_cleanup_carryover import CitationCacheLifecycleTests


class CitationCacheLifecycleTests1(CitationCacheLifecycleTests):
    def test_cleanup_dirty_refusal_leaves_cache_intact(self) -> None:
        contract = self.contract("dirty-cleanup")
        cache = self.cache(contract)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.application.provider_runtime.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight({"worktree": "code", "reason": "dirty"}),
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertTrue(cache.exists())
        self.assertNotIn("citation_source_index", result.payload)

    def test_cleanup_integration_refusal_precedes_cache_reclamation(self) -> None:
        contract = replace(self.contract("pending"), integration_status="not-started")
        write_contract(contract.contract_path, contract)
        cache = self.cache(contract)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            self.assertRaisesRegex(RuntimeError, "integration.status completed"),
        ):
            cleanup_result(WorktreeArgs(contract_path=contract.contract_path, approved=True))
        self.assertTrue(cache.exists())

    def test_successful_cleanup_removes_only_exact_namespace(self) -> None:
        contract = self.contract("cleanup-target")
        neighbor = self.contract("cleanup-neighbor")
        target_cache = self.cache(contract)
        neighbor_cache = self.cache(neighbor)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.application.provider_runtime.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches",
                return_value={"code": {"deleted": True}, "memory": {"deleted": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories",
                return_value={},
            ),
            patch("agents_remember.worktrees.modules.cleanup.write_contract"),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.payload["citation_source_index"]["removed"])  # type: ignore[index]
        self.assertFalse(target_cache.exists())
        self.assertTrue(neighbor_cache.exists())

    def test_cleanup_live_lease_blocks_before_worktree_removal(self) -> None:
        contract = self.contract("live-cleanup")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        handle = authority.control_lock.open("r+b")
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            with (
                patch(
                    "agents_remember.worktrees.modules.cleanup.load_contract",
                    return_value=contract,
                ),
                patch(
                    "agents_remember.worktrees.modules.cleanup.carryover_done",
                    return_value=(True, "now"),
                ),
                patch(
                    "agents_remember.application.provider_runtime.provider_setup_running",
                    return_value=False,
                ),
                patch(
                    "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                    return_value=self.preflight(),
                ),
                patch.object(source_index_cache, "LOCK_TIMEOUT_SECONDS", 0.02),
                patch("agents_remember.worktrees.modules.cleanup._removed_worktrees") as removed,
            ):
                result = cleanup_result(
                    WorktreeArgs(contract_path=contract.contract_path, approved=True)
                )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.payload["citation_source_index"]["reason"],  # type: ignore[index]
                "live-lease-timeout",
            )
            removed.assert_not_called()
            self.assertTrue(cache.exists())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def test_abandon_refusal_leaves_cache_intact(self) -> None:
        contract = self.contract("dirty-abandon")
        cache = self.cache(contract)
        with (
            patch("agents_remember.worktrees.modules.abandon.load_contract", return_value=contract),
            patch(
                "agents_remember.application.provider_runtime.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.terminal_preflight",
                return_value=self.preflight({"worktree": "memory", "reason": "dirty"}),
            ),
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertTrue(cache.exists())
        self.assertNotIn("citation_source_index", result.payload)

    def test_abandon_approval_refusal_precedes_cache_reclamation(self) -> None:
        contract = self.contract("unapproved-abandon")
        cache = self.cache(contract)
        with self.assertRaisesRegex(RuntimeError, "requires --approved"):
            abandon_result(WorktreeArgs(contract_path=contract.contract_path))
        self.assertTrue(cache.exists())

    def test_successful_force_abandon_removes_only_exact_namespace(self) -> None:
        contract = self.contract("abandon-target")
        neighbor = self.contract("abandon-neighbor")
        target_cache = self.cache(contract)
        neighbor_cache = self.cache(neighbor)
        with (
            patch("agents_remember.worktrees.modules.abandon.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.abandon.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_branches",
                return_value={
                    "code": {"deleted": True},
                    "memory": {"deleted": True},
                },
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_directories",
                return_value={},
            ),
            patch(
                "agents_remember.application.provider_runtime.teardown_worktree_providers",
                return_value={"state": "removed"},
            ),
            patch("agents_remember.worktrees.modules.abandon.write_contract"),
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True, force=True)
            )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.payload["citation_source_index"]["removed"])  # type: ignore[index]
        self.assertFalse(target_cache.exists())
        self.assertTrue(neighbor_cache.exists())

    def test_cleanup_worktree_failure_after_preflight_preserves_contract_and_cache(self) -> None:
        contract = self.contract("cleanup-worktree-failure")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        before_contract = contract.contract_path.read_bytes()
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.application.provider_runtime.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": False, "reason": "remove refused"}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches"
            ) as deleted_branches,
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ) as remove_drift,
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories"
            ) as remove_directories,
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "blocked")
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)
        self.assertTrue(result.payload["citation_source_index"]["preserved"])  # type: ignore[index]
        deleted_branches.assert_not_called()
        remove_drift.assert_not_called()
        remove_directories.assert_not_called()

    def test_cleanup_provider_failure_stops_before_worktree_and_branch_mutation(self) -> None:
        contract = self.contract("cleanup-provider-failure")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        providers = {
            "state": "blocked",
            "containers": [{"removed": False, "reason": "container refused"}],
            "networks": [],
            "providerRuntime": {"removed": False, "reason": "already-absent"},
        }
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.application.provider_runtime.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.application.provider_runtime.teardown_worktree_providers",
                return_value=providers,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees"
            ) as removed_worktrees,
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches"
            ) as deleted_branches,
        ):
            result = cleanup_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["providers"], providers)
        self.assertEqual(result.payload["removed_worktrees"], {})
        self.assertEqual(result.payload["branches"], {})
        self.assertEqual(self.cache_bytes(cache), before_cache)
        removed_worktrees.assert_not_called()
        deleted_branches.assert_not_called()

    def test_abandon_branch_refusal_after_passing_preview_is_nonzero_and_preserves_cache(
        self,
    ) -> None:
        contract = self.contract("abandon-branch-refusal")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        before_contract = contract.contract_path.read_bytes()
        providers = {
            "state": "torn-down",
            "containers": [],
            "networks": [],
            "providerRuntime": {"removed": False, "reason": "already-absent"},
        }
        with (
            patch("agents_remember.worktrees.modules.abandon.load_contract", return_value=contract),
            patch(
                "agents_remember.application.provider_runtime.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.application.provider_runtime.teardown_worktree_providers",
                return_value=providers,
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_branches",
                return_value={"code": {"deleted": False, "reason": "git branch -d refused"}},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_directories", return_value={}
            ),
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "abandon-blocked")
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)

    def test_cleanup_late_remote_branch_failure_is_nonzero_and_preserves_cache(self) -> None:
        contract = self.contract("cleanup-remote-refusal")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.application.provider_runtime.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches",
                return_value={
                    "code": {
                        "deleted": True,
                        "remote": {"remote_deleted": False, "reason": "remote refused"},
                    },
                    "memory": {"deleted": True},
                },
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories", return_value={}
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["blockers"][0]["reason"], "remote refused")  # type: ignore[index]
        self.assertEqual(self.cache_bytes(cache), before_cache)

    def test_post_preflight_ref_query_error_is_not_already_absent(self) -> None:
        contract = self.contract("post-preview-query-error")
        assert contract.memory_repo_path is not None
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.application.provider_runtime.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.local_branch_presence",
                return_value=LocalBranchPresence("error", "ref query exploded"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories", return_value={}
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        branches = result.payload["branches"]  # type: ignore[index]
        assert isinstance(branches, dict)
        self.assertTrue(
            all(
                isinstance(item, dict) and item["reason"] == "ref query exploded"
                for item in branches.values()
            )
        )
        self.assertEqual(self.cache_bytes(cache), before_cache)

    def test_unexpected_terminal_helper_exception_is_a_blocked_result(self) -> None:
        contract = self.contract("terminal-helper-exception")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.application.provider_runtime.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                side_effect=RuntimeError("helper exploded"),
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "blocked")
        self.assertIn("helper exploded", result.payload["blockers"][0]["reason"])  # type: ignore[index]
        self.assertEqual(self.cache_bytes(cache), before_cache)

    def test_absent_namespace_concurrent_first_builder_refuses_after_terminal_success(
        self,
    ) -> None:
        contract = self.contract("absent-concurrent-builder")
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        trees = self.trees(contract, authority)
        started = threading.Event()
        outcome: list[str] = []
        updated = amend_contract(contract, ContractCells(cleanup="completed"))
        with source_index_cache.terminal_namespace_guard(
            contract, requested_contract_path=contract.contract_path
        ) as guard:
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(trees, started, outcome),
            )
            thread.start()
            self.assertTrue(started.wait(2))
            time.sleep(0.05)
            self.assertTrue(thread.is_alive())
            result = guard.complete(
                outcome="completed",
                publish=lambda: write_contract(contract.contract_path, updated),
                rollback_publish=lambda: write_contract(contract.contract_path, contract),
            )
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["reason"], "already-absent")
        self.assertEqual(len(outcome), 1)
        self.assertIn("terminal", outcome[0])
        self.assertFalse(authority.namespace.exists())

    def test_existing_namespace_queued_builder_refuses_after_terminal_success(self) -> None:
        contract = self.contract("existing-concurrent-builder")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        trees = self.trees(contract, authority)
        started = threading.Event()
        outcome: list[str] = []
        updated = amend_contract(contract, ContractCells(cleanup="completed"))
        with source_index_cache.terminal_namespace_guard(
            contract, requested_contract_path=contract.contract_path
        ) as guard:
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(trees, started, outcome),
            )
            thread.start()
            self.assertTrue(started.wait(2))
            time.sleep(0.05)
            self.assertTrue(thread.is_alive())
            result = guard.complete(
                outcome="completed",
                publish=lambda: write_contract(contract.contract_path, updated),
                rollback_publish=lambda: write_contract(contract.contract_path, contract),
            )
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result["removed"])
        self.assertEqual(len(outcome), 1)
        self.assertIn("terminal", outcome[0])
        self.assertFalse(cache.exists())

    def test_terminal_publication_failure_restores_generation_and_releases_waiter(self) -> None:
        contract = self.contract("failed-terminal-publication")
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        trees = self.trees(contract, authority)
        with source_index.open_repository_index(trees):
            pass
        before_cache = self.cache_bytes(authority.namespace)
        before_state = authority.control_state.read_bytes()
        before_contract = contract.contract_path.read_bytes()
        started = threading.Event()
        outcome: list[str] = []
        with (
            self.assertRaisesRegex(RuntimeError, "publication exploded"),
            source_index_cache.terminal_namespace_guard(
                contract, requested_contract_path=contract.contract_path
            ) as guard,
        ):
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(trees, started, outcome),
            )
            thread.start()
            self.assertTrue(started.wait(2))
            time.sleep(0.05)
            self.assertTrue(thread.is_alive())
            guard.complete(
                outcome="completed",
                publish=self.fail_publication,
                rollback_publish=lambda: write_contract(contract.contract_path, contract),
            )
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, ["opened"])
        self.assertEqual(self.cache_bytes(authority.namespace), before_cache)
        self.assertEqual(authority.control_state.read_bytes(), before_state)
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
