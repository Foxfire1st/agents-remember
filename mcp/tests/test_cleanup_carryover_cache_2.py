from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agents_remember.errors import CitationCacheError
from agents_remember.memory_quality.style.citations import source_index, source_index_cache
from agents_remember.worktrees.modules.abandon import abandon_result
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import cleanup_result
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)
from test_cleanup_carryover import (
    CitationCacheLifecycleTests,
    _allow_terminal_archive_for_downstream_unit,
)


class CitationCacheLifecycleTests2(CitationCacheLifecycleTests):
    def setUp(self) -> None:
        super().setUp()
        _allow_terminal_archive_for_downstream_unit(self)

    def test_terminal_tombstone_reserves_capacity_until_failed_publication_restores(self) -> None:
        contracts = [self.contract(f"capacity-{index}") for index in range(4)]
        target = contracts[0]
        target_cache = self.cache(target)
        for contract in contracts[1:]:
            self.cache(contract)
        target_before = self.cache_bytes(target_cache)
        newcomer = self.contract("capacity-newcomer")
        newcomer_authority = source_index_cache.contract_cache_authority(newcomer)
        assert newcomer_authority is not None
        newcomer_trees = self.trees(newcomer, newcomer_authority)
        publication_started = threading.Event()
        release_publication = threading.Event()
        terminal_outcome: list[str] = []

        # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_cleanup_carryover_cache_2.py:41).
        def blocked_publication() -> None:  # pragma: no cover
            publication_started.set()
            if not release_publication.wait(5):
                raise RuntimeError("test did not release publication")
            raise RuntimeError("publication failed")

        def terminal_attempt() -> None:
            try:
                with source_index_cache.terminal_namespace_guard(
                    target, requested_contract_path=target.contract_path
                ) as guard:
                    guard.complete(
                        outcome="completed",
                        publish=blocked_publication,
                        rollback_publish=lambda: write_contract(target.contract_path, target),
                    )
            except Exception as error:
                terminal_outcome.append(str(error))

        thread = threading.Thread(target=terminal_attempt)
        thread.start()
        self.assertTrue(publication_started.wait(2))
        with self.assertRaisesRegex(source_index.SourceIndexError, "capacity is full"):
            source_index.open_repository_index(newcomer_trees)
        self.assertFalse(newcomer_authority.namespace.exists())
        release_publication.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(terminal_outcome, ["publication failed"])
        self.assertEqual(self.cache_bytes(target_cache), target_before)
        self.assertEqual(
            len(source_index_cache._namespace_ids(target_cache.parent)),
            source_index_cache.MANAGED_NAMESPACE_LIMIT,
        )

    def test_cleanup_state_publication_failure_rolls_back_contract_cache_and_fence(self) -> None:
        contract = self.contract("cleanup-publication-rollback")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        handle = source_index_cache.open_shared_namespace(authority, create=True)
        handle.close()
        before_cache = self.cache_bytes(cache)
        before_state = authority.control_state.read_bytes()
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
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches",
                return_value={
                    "code": {"deleted": True},
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
            patch.object(
                source_index_cache,
                "_write_control_state",
                side_effect=RuntimeError("state publication exploded"),
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
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)
        self.assertEqual(authority.control_state.read_bytes(), before_state)

    def test_cleanup_publisher_write_then_raise_rolls_back_exact_live_state(self) -> None:
        contract = self.contract("cleanup-write-then-raise")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        handle = source_index_cache.open_shared_namespace(authority, create=True)
        handle.close()
        before_cache = self.cache_bytes(cache)
        before_state = authority.control_state.read_bytes()
        before_contract = contract.contract_path.read_bytes()

        def write_then_raise(path: Path, candidate: WorktreeContract) -> None:
            write_contract(path, candidate)
            if candidate.cleanup == "completed":
                raise RuntimeError("publisher raised after terminal contract write")

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
                    "code": {"deleted": True},
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
            patch(
                "agents_remember.worktrees.modules.cleanup.write_contract",
                side_effect=write_then_raise,
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
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)
        self.assertEqual(authority.control_state.read_bytes(), before_state)
        self.assertEqual(load_contract(contract.contract_path).cleanup, "pending")

    def test_abandon_publisher_write_then_raise_rolls_back_exact_live_state(self) -> None:
        contract = self.contract("abandon-write-then-raise")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        handle = source_index_cache.open_shared_namespace(authority, create=True)
        handle.close()
        before_cache = self.cache_bytes(cache)
        before_state = authority.control_state.read_bytes()
        before_contract = contract.contract_path.read_bytes()

        def write_then_raise(path: Path, candidate: WorktreeContract) -> None:
            write_contract(path, candidate)
            if candidate.cleanup == "abandoned":
                raise RuntimeError("publisher raised after abandoned contract write")

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
                return_value={"state": "removed"},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_branches",
                return_value={
                    "code": {"deleted": True},
                    "memory": {"deleted": True},
                },
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_directories", return_value={}
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.write_contract",
                side_effect=write_then_raise,
            ),
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True, force=True)
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "abandon-blocked")
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)
        self.assertEqual(authority.control_state.read_bytes(), before_state)
        self.assertEqual(load_contract(contract.contract_path).cleanup, "pending")

    def test_partial_retired_cleanup_is_terminal_success_and_never_restores_live_bytes(
        self,
    ) -> None:
        contract = self.contract("partial-retired-cleanup")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None

        def partial_delete(path: Path) -> None:
            (path / "ready.json").unlink()
            raise CitationCacheError("deterministic partial recursive deletion")

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
                    "code": {"deleted": True},
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
            patch.object(source_index_cache, "_remove_tree", side_effect=partial_delete),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )

        citation_cache = result.payload["citation_source_index"]  # type: ignore[index]
        assert isinstance(citation_cache, dict)
        retired_cleanup = citation_cache["retired_cleanup"]
        assert isinstance(retired_cleanup, dict)
        retired = Path(str(retired_cleanup["path"]))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(citation_cache["removed"])
        self.assertFalse(retired_cleanup["removed"])
        self.assertIn("partial recursive deletion", retired_cleanup["reason"])
        self.assertFalse(cache.exists())
        self.assertTrue(retired.exists())
        self.assertFalse((retired / "ready.json").exists())
        self.assertTrue((retired / "index.sqlite3").exists())
        self.assertNotIn(
            authority.namespace_id,
            source_index_cache._namespace_ids(authority.managed_root),
        )
        self.assertEqual(load_contract(contract.contract_path).cleanup, "completed")
        self.assertEqual(
            json.loads(authority.control_state.read_text(encoding="utf-8"))["phase"],
            "terminal",
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_cleanup_carryover_cache_2.py:345).
    def test_empty_legacy_lifecycle_cannot_acquire_terminal_fence(self) -> None:  # pragma: no cover
        contract = replace(self.contract("empty-lifecycle"), lifecycle_id="")
        write_contract(contract.contract_path, contract)
        with (
            self.assertRaisesRegex(CitationCacheError, "nonempty lifecycle"),
            source_index_cache.terminal_namespace_guard(
                contract, requested_contract_path=contract.contract_path
            ),
        ):
            self.fail("empty lifecycle unexpectedly acquired terminal authority")

    def test_neighbor_build_completes_while_target_terminal_fence_is_held(self) -> None:
        target = self.contract("fenced-target")
        neighbor = self.contract("fenced-neighbor")
        neighbor_authority = source_index_cache.contract_cache_authority(neighbor)
        assert neighbor_authority is not None
        trees = self.trees(neighbor, neighbor_authority)
        outcome: list[str] = []
        started = threading.Event()
        with source_index_cache.terminal_namespace_guard(
            target, requested_contract_path=target.contract_path
        ):
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(trees, started, outcome),
            )
            thread.start()
            self.assertTrue(started.wait(2))
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(outcome, ["opened"])

    def test_old_authority_stays_refused_after_fresh_lifecycle_reopens(self) -> None:
        contract = self.contract("fresh-lifecycle")
        old_authority = source_index_cache.contract_cache_authority(contract)
        assert old_authority is not None
        old_trees = self.trees(contract, old_authority)
        with source_index.open_repository_index(old_trees):
            pass
        terminal = amend_contract(contract, ContractCells(cleanup="completed"))
        with source_index_cache.terminal_namespace_guard(
            contract, requested_contract_path=contract.contract_path
        ) as guard:
            guard.complete(
                outcome="completed",
                publish=lambda: write_contract(contract.contract_path, terminal),
                rollback_publish=lambda: write_contract(contract.contract_path, contract),
            )
        fresh = amend_contract(
            replace(terminal, lifecycle_id="LC-FRESH"),
            ContractCells(cleanup="pending"),
        )
        write_contract(fresh.contract_path, fresh)
        fresh_authority = source_index_cache.contract_cache_authority(fresh)
        assert fresh_authority is not None
        fresh_trees = self.trees(fresh, fresh_authority)
        with self.assertRaisesRegex(source_index.SourceIndexError, "stale|terminal"):
            source_index.open_repository_index(old_trees)
        with source_index.open_repository_index(fresh_trees):
            pass
        queued_started = threading.Event()
        queued_outcome: list[str] = []
        with source_index_cache.terminal_namespace_guard(
            fresh, requested_contract_path=fresh.contract_path
        ):
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(old_trees, queued_started, queued_outcome),
            )
            thread.start()
            self.assertTrue(queued_started.wait(2))
            time.sleep(0.05)
            self.assertTrue(thread.is_alive())
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(queued_outcome), 1)
        self.assertRegex(queued_outcome[0], "stale|terminal")
        self.assertLessEqual(fresh_authority.control_state.stat().st_size, 4096)
        self.assertEqual(
            sorted(path.name for path in fresh_authority.control_dir.iterdir()),
            ["lease.lock", "state.json"],
        )
