"""Background landing-state refresh and exact-contract publication tests."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.observer.projection import LandingRefNode
from agents_remember.serving.projections.landing_state import (
    _LANDING_ROW_KEYS,
    LandingStateRefresher,
    _load_final,
)
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.reopen import _clear_frozen_landing, reopen_task
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    load_contract,
    write_contract,
)

NOW = datetime(2026, 7, 12, 16, 0, tzinfo=UTC)


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _stamp_contract(contract: WorktreeContract, when: datetime) -> None:
    """Put the contract file's mtime on the tests' fake clock.

    The freeze only trusts a landing-final.json whose ``frozenAt`` is at or after the contract's
    last write. These tests drive the refresher with a fake ``now`` in the past, so a
    contract file carrying the real wall-clock mtime would look "rewritten after every freeze"
    and nothing would ever stay frozen. Stamping keeps the fixture's ordering — contract written,
    then observed, then frozen — the same as production's.
    """
    stamp = when.timestamp()
    os.utime(contract.contract_path, (stamp, stamp))


def _contract(root: Path, index: int) -> WorktreeContract:
    contract = default_contract(
        ContractTask(
            name=f"landing-{index}",
            repo_name=f"repo-{index}",
            coordination_root=root,
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        leaf=LeafIdentity(worktree_name=f"landing-{index}"),
        code=RepoBranchPlan(
            repo_path=root / f"repo-{index}",
            source_branch=f"feat/{index}",
            work_branch=f"ar/{index}",
            base_commit=f"base-{index}",
        ),
    )
    contract = replace(contract, closeout_status="completed")
    contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(contract.contract_path, contract)
    _stamp_contract(contract, NOW - timedelta(hours=1))
    return contract


def _landed_contract(root: Path, index: int) -> WorktreeContract:
    """A fully landed leaf: frozen-eligible for the sweep AND reopen-eligible for task_reopen."""
    contract = replace(_contract(root, index), integration_status="completed", cleanup="completed")
    write_contract(contract.contract_path, contract)
    _stamp_contract(contract, NOW - timedelta(hours=1))
    return contract


def _write_leaf_task_doc(contract: WorktreeContract) -> None:
    """Give a landed enclosure the canonical identity required for a real reopen."""
    write_task_doc(
        contract.task_root,
        TaskDocument.model_validate(
            {
                "id": contract.leaf_id,
                "slug": contract.code_worktree.name,
                "title": f"Leaf {contract.leaf_id}",
                "kind": "subTask",
                "status": "Completed",
                "repo": contract.repo_name,
                "createdAt": "2026-07-12T15:00",
                "lifecycleId": contract.lifecycle_id,
                "steps": [{"id": "S1", "title": "land the leaf", "status": "done"}],
            }
        ),
    )


def _observed(contract: WorktreeContract) -> list[dict[str, object]]:
    return [
        {
            "kind": "origin-feat",
            "label": f"origin/{contract.code_source_branch}",
            "state": "pushed",
            "factState": "observed",
            "detail": contract.repo_name,
        }
    ]


class LandingStateRefresherTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_is_bounded_and_isolated_by_exact_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contracts = [_contract(root, index) for index in range(6)]
            lock = threading.Lock()
            active = 0
            maximum = 0

            def slow_observer(contract: WorktreeContract) -> list[dict[str, object]]:
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.04)
                with lock:
                    active -= 1
                return _observed(contract)

            refresher = LandingStateRefresher(
                _config(root), max_concurrency=2, observe=slow_observer
            )
            await refresher.refresh_once(now=NOW)

            self.assertEqual(maximum, 2)
            for contract in contracts:
                rows = refresher.current(contract, now=NOW + timedelta(seconds=5))
                assert rows is not None
                self.assertEqual(rows[0]["detail"], contract.repo_name)
                self.assertEqual(rows[0]["label"], f"origin/{contract.code_source_branch}")

    async def test_failed_refresh_keeps_last_truth_as_explicit_stale_fact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = _contract(root, 1)
            attempts = 0

            def observer(current: WorktreeContract) -> list[dict[str, object]]:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    return _observed(current)
                raise OSError("remote unavailable")

            refresher = LandingStateRefresher(_config(root), observe=observer)
            await refresher.refresh_once(now=NOW)
            second = NOW + timedelta(seconds=60)
            await refresher.refresh_once(now=second)
            rows = refresher.current(contract, now=second)

            assert rows is not None
            self.assertEqual(rows[0]["factState"], "stale")
            self.assertEqual(rows[0]["detail"], contract.repo_name)
            self.assertEqual(rows[0]["observedAt"], NOW.isoformat())
            self.assertEqual(rows[0]["lastAttemptAt"], second.isoformat())
            self.assertEqual(rows[0]["staleSeconds"], 60.0)

    async def test_startup_and_rewritten_contract_are_explicitly_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = _contract(root, 2)
            refresher = LandingStateRefresher(_config(root), observe=_observed)

            startup = refresher.current(contract, now=NOW)
            assert startup is not None
            self.assertTrue(all(row["factState"] == "missing" for row in startup))

            await refresher.refresh_once(now=NOW)
            rewritten = replace(contract, code_source_branch="feat/rewritten")
            rows = refresher.current(rewritten, now=NOW)
            assert rows is not None
            self.assertTrue(all(row["factState"] == "missing" for row in rows))
            self.assertEqual(rows[0]["label"], "origin/feat/rewritten")

            aged = refresher.current(contract, now=NOW + timedelta(seconds=120))
            assert aged is not None
            self.assertTrue(all(row["factState"] == "stale" for row in aged))


class LandingStateLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_failure_logs_then_recovers_on_normal_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            refresher = LandingStateRefresher(_config(Path(tmp)), interval_seconds=0)
            recovered = asyncio.Event()
            calls = 0

            async def refresh_once() -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("bad sweep")
                recovered.set()
                await asyncio.Event().wait()

            refresher.refresh_once = refresh_once  # type: ignore[method-assign]
            with self.assertLogs(
                "agents_remember.serving.projections.landing_state", level="ERROR"
            ):
                task = asyncio.create_task(refresher.run())
                await asyncio.wait_for(recovered.wait(), timeout=1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(calls, 2)

    async def test_run_cancellation_leaves_no_refresh_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _contract(root, 3)
            probe_started = threading.Event()

            def slow_observer(contract: WorktreeContract) -> list[dict[str, object]]:
                probe_started.set()
                time.sleep(0.1)
                return _observed(contract)

            refresher = LandingStateRefresher(
                _config(root), interval_seconds=100, observe=slow_observer
            )
            task = asyncio.create_task(refresher.run())
            await asyncio.to_thread(probe_started.wait, 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(task.done())


class LandingFreezeTests(unittest.IsolatedAsyncioTestCase):
    """A finished, fully-observed contract freezes and leaves the sweep."""

    @staticmethod
    def _finished_contract(root: Path, index: int) -> WorktreeContract:
        contract = _contract(root, index)
        contract = replace(contract, cleanup="completed")
        write_contract(contract.contract_path, contract)
        _stamp_contract(contract, NOW - timedelta(hours=1))
        return contract

    async def test_finished_contract_freezes_once_and_leaves_the_sweep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._finished_contract(root, 0)
            calls = 0

            def counting_observer(target: WorktreeContract) -> list[dict[str, object]]:
                nonlocal calls
                calls += 1
                return _observed(target)

            refresher = LandingStateRefresher(_config(root), observe=counting_observer)
            await refresher.refresh_once(now=NOW)
            final = contract.contract_path.parent / "landing-final.json"
            self.assertTrue(final.exists())
            self.assertEqual(calls, 1)

            await refresher.refresh_once(now=NOW + timedelta(seconds=30))
            self.assertEqual(calls, 1)

            rows = refresher.current(contract, now=NOW + timedelta(days=30))
            assert rows is not None
            self.assertEqual(rows[0]["state"], "pushed")
            self.assertEqual(rows[0]["factState"], "observed")
            self.assertIsNone(rows[0]["staleSeconds"])
            # No stray key may reach the reducer's extra="forbid" LandingRefNode.
            self.assertNotIn("frozen", rows[0])
            self.assertLessEqual(set(rows[0]), _LANDING_ROW_KEYS)

    async def test_unobserved_facts_do_not_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._finished_contract(root, 0)
            calls = 0

            def missing_observer(target: WorktreeContract) -> list[dict[str, object]]:
                nonlocal calls
                calls += 1
                rows = _observed(target)
                rows[0]["factState"] = "missing"
                return rows

            refresher = LandingStateRefresher(_config(root), observe=missing_observer)
            await refresher.refresh_once(now=NOW)
            await refresher.refresh_once(now=NOW + timedelta(seconds=30))
            self.assertFalse((contract.contract_path.parent / "landing-final.json").exists())
            self.assertEqual(calls, 2)

    async def test_pending_cleanup_keeps_probing_without_freezing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = _contract(root, 0)  # closeout completed, cleanup still pending
            calls = 0

            def counting_observer(target: WorktreeContract) -> list[dict[str, object]]:
                nonlocal calls
                calls += 1
                return _observed(target)

            refresher = LandingStateRefresher(_config(root), observe=counting_observer)
            await refresher.refresh_once(now=NOW)
            await refresher.refresh_once(now=NOW + timedelta(seconds=30))
            self.assertFalse((contract.contract_path.parent / "landing-final.json").exists())
            self.assertEqual(calls, 2)

    async def test_frozen_facts_survive_a_fresh_refresher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._finished_contract(root, 0)
            first = LandingStateRefresher(_config(root), observe=_observed)
            await first.refresh_once(now=NOW)

            def failing_observer(target: WorktreeContract) -> list[dict[str, object]]:
                raise AssertionError("a frozen contract must never be re-observed")

            restarted = LandingStateRefresher(_config(root), observe=failing_observer)
            await restarted.refresh_once(now=NOW + timedelta(hours=1))
            rows = restarted.current(contract, now=NOW + timedelta(hours=2))
            assert rows is not None
            self.assertEqual(rows[0]["label"], f"origin/{contract.code_source_branch}")
            self.assertNotIn("frozen", rows[0])


class LandingFreezeReopenTests(unittest.IsolatedAsyncioTestCase):
    """The freeze must not poison reopen/corrupt/reducer."""

    @staticmethod
    def _finished_contract(root: Path, index: int) -> WorktreeContract:
        contract = _contract(root, index)
        contract = replace(contract, cleanup="completed")
        write_contract(contract.contract_path, contract)
        _stamp_contract(contract, NOW - timedelta(hours=1))
        return contract

    async def test_frozen_rows_carry_only_reducer_known_keys(self) -> None:
        # LandingRefNode is extra="forbid": a served frozen row must be a strict subset of the
        # reducer's fields or project_and_write raises ValidationError on every tick.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._finished_contract(root, 0)
            refresher = LandingStateRefresher(_config(root), observe=_observed)
            await refresher.refresh_once(now=NOW)
            rows = refresher.current(contract, now=NOW + timedelta(days=30))
            assert rows is not None
            for row in rows:
                self.assertLessEqual(set(row), _LANDING_ROW_KEYS)
                LandingRefNode.model_validate(row)  # must not raise (reducer's extra="forbid")

    async def test_legacy_frozen_key_on_disk_is_stripped_on_read(self) -> None:
        # 85 landing-final.json files already exist on the live root with a "frozen": true key.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._finished_contract(root, 0)
            final = contract.contract_path.parent / "landing-final.json"
            final.write_text(
                json.dumps(
                    {
                        "frozenAt": NOW.isoformat(),
                        "facts": [
                            {
                                "kind": "origin-feat",
                                "label": "origin/feat/0",
                                "state": "pushed",
                                "factState": "observed",
                                "frozen": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            loaded = _load_final(final)
            assert loaded is not None
            _frozen_at, rows = loaded
            self.assertNotIn("frozen", rows[0])
            refresher = LandingStateRefresher(_config(root), observe=_observed)
            served = refresher.current(contract, now=NOW + timedelta(days=1))
            assert served is not None
            self.assertEqual(served[0]["state"], "pushed")  # served FROM the file, not pending
            self.assertNotIn("frozen", served[0])

    async def test_reopen_clears_frozen_file_and_refinish_refreezes(self) -> None:
        # Reopen deletes landing-final.json; during the reopen window fresh live
        # observations are served (not shadowed); a second finish re-freezes with fresh facts.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._finished_contract(root, 0)
            final = contract.contract_path.parent / "landing-final.json"

            first = LandingStateRefresher(_config(root), observe=_observed)
            await first.refresh_once(now=NOW)
            self.assertTrue(final.exists())

            # Reopen: cleanup flips away from "completed" and the frozen file is deleted.
            self.assertEqual(_clear_frozen_landing(contract, dry_run=False), "deleted")
            self.assertFalse(final.exists())
            reopened = replace(contract, cleanup="reopened")
            write_contract(reopened.contract_path, reopened)
            _stamp_contract(reopened, NOW + timedelta(minutes=30))

            def merged_observer(target: WorktreeContract) -> list[dict[str, object]]:
                rows = _observed(target)
                rows[0]["state"] = "merged"
                return rows

            # Fresh run reaches the reopened contract (in the sweep) and its live facts win.
            second = LandingStateRefresher(_config(root), observe=merged_observer)
            await second.refresh_once(now=NOW + timedelta(hours=1))
            live = second.current(reopened, now=NOW + timedelta(hours=1))
            assert live is not None
            self.assertEqual(live[0]["state"], "merged")
            self.assertFalse(final.exists())  # not re-frozen while non-completed

            # Second finish: cleanup completed again -> re-freezes with the fresh "merged" facts.
            refinished = replace(reopened, cleanup="completed")
            write_contract(refinished.contract_path, refinished)
            _stamp_contract(refinished, NOW + timedelta(minutes=90))
            await second.refresh_once(now=NOW + timedelta(hours=2))
            self.assertTrue(final.exists())
            # A brand-new refresher (no in-memory observation) can only answer from the frozen
            # file: proves the re-freeze landed a trusted file, not just a live cache hit.
            third = LandingStateRefresher(
                _config(root),
                observe=lambda _t: (_ for _ in ()).throw(
                    AssertionError("a re-frozen contract must not be re-probed")
                ),
            )
            frozen = third.current(refinished, now=NOW + timedelta(hours=3))
            assert frozen is not None
            self.assertEqual(frozen[0]["state"], "merged")

    async def test_corrupt_final_file_keeps_contract_in_sweep_and_selfheals(self) -> None:
        # An unparseable landing-final.json must be treated as absent — the contract stays
        # in the sweep (probed, live rows served) and its next full observation rewrites the file.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._finished_contract(root, 0)
            final = contract.contract_path.parent / "landing-final.json"
            final.write_text("{ this is not valid json", encoding="utf-8")

            calls = 0

            def counting_observer(target: WorktreeContract) -> list[dict[str, object]]:
                nonlocal calls
                calls += 1
                return _observed(target)

            refresher = LandingStateRefresher(_config(root), observe=counting_observer)
            # Corrupt file != frozen -> contract IS probed (not silently dropped from the sweep).
            await refresher.refresh_once(now=NOW)
            self.assertEqual(calls, 1)
            # Self-heal: the full observation atomically rewrote the corrupt file with a valid one.
            self.assertIsNotNone(_load_final(final))
            served = refresher.current(contract, now=NOW + timedelta(seconds=1))
            assert served is not None
            self.assertEqual(served[0]["state"], "pushed")
            # Now valid -> leaves the sweep.
            await refresher.refresh_once(now=NOW + timedelta(seconds=30))
            self.assertEqual(calls, 1)


class LandingFreezeResurrectionTests(unittest.IsolatedAsyncioTestCase):
    """A freeze racing a reopen must not resurrect the old arc's file."""

    @staticmethod
    def _reopened_contract(root: Path, index: int) -> WorktreeContract:
        """A leaf that has just been reopened: contract rewritten NOW, still frozen-eligible.

        cleanup is left "completed" (the reopen→refinish narrative's *second* finish) so the
        contract is freeze-eligible; the point under test is provenance, not the cleanup gate.
        """
        contract = replace(_contract(root, index), cleanup="completed")
        write_contract(contract.contract_path, contract)
        _stamp_contract(contract, NOW)
        return contract

    async def test_frozen_file_predating_the_contract_is_never_served_or_kept_out_of_sweep(
        self,
    ) -> None:
        # End to end: task_reopen deleted landing-final.json, then an in-flight freeze whose
        # frozenAt was stamped at the seconds-earlier sweep start re-wrote it. That resurrected
        # file describes run 1 (a past arc). It must not be served (else the leaf reports run 1's
        # SHA as factState "observed" forever) and must not pull the leaf out of the sweep (else
        # it never re-probes run 2).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._reopened_contract(root, 0)
            final = contract.contract_path.parent / "landing-final.json"
            # The racing freeze: run 1's facts, frozenAt from BEFORE the reopen's contract write.
            final.write_text(
                json.dumps(
                    {
                        "frozenAt": (NOW - timedelta(seconds=5)).isoformat(),
                        "facts": [
                            {
                                "kind": "origin-feat",
                                "label": "origin/feat/0",
                                "state": "pushed",
                                "factState": "observed",
                                "detail": "run1sha000",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            # (1) Pure serve, before any sweep: the stale file must NOT shadow honest pending truth.
            refresher = LandingStateRefresher(_config(root), observe=_observed)
            pre = refresher.current(contract, now=NOW)
            assert pre is not None
            self.assertNotEqual(pre[0]["factState"], "observed")  # would be "observed"/run1 unfixed
            self.assertNotEqual(pre[0].get("detail"), "run1sha000")

            # (2) The stale file must NOT drop the leaf from the sweep: it gets re-probed for run 2.
            calls = 0

            def run2_observer(target: WorktreeContract) -> list[dict[str, object]]:
                nonlocal calls
                calls += 1
                rows = _observed(target)
                rows[0]["detail"] = "run2sha000"
                return rows

            reprober = LandingStateRefresher(_config(root), observe=run2_observer)
            await reprober.refresh_once(now=NOW + timedelta(minutes=1))
            self.assertEqual(calls, 1)  # unfixed: 0 (stale file kept it out of the sweep)
            served = reprober.current(contract, now=NOW + timedelta(minutes=1))
            assert served is not None
            self.assertEqual(served[0]["detail"], "run2sha000")

    async def test_reopen_task_entry_point_deletes_the_frozen_landing_file(self) -> None:
        # The ONLY existing coverage of the freeze-clear calls the private
        # _clear_frozen_landing directly; the production reopen_task path — which loads the
        # contract, derives the final path, and threads the result into its payload — had none.
        # Drive the real tool end to end so that path stays pinned (a deleted or misdirected
        # clear leaves the file on disk and the "deleted" receipt absent).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = _landed_contract(root, 0)
            _write_leaf_task_doc(contract)
            final = contract.contract_path.parent / "landing-final.json"

            # A genuine freeze, produced by the real sweep — the exact file a reopen must clear.
            frozen = LandingStateRefresher(_config(root), observe=_observed)
            await frozen.refresh_once(now=NOW)
            self.assertTrue(final.exists())

            with (
                mock.patch(
                    "agents_remember.worktrees.reopen.parent_source_lineage",
                    return_value=None,
                ),
                mock.patch(
                    "agents_remember.worktrees.reopen.require_parent_series_accepting_leaves",
                    return_value=None,
                ),
            ):
                result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "reopened")
            self.assertEqual(result.payload["frozenLanding"], "deleted")
            self.assertFalse(final.exists())
            # The reopened contract is off the terminal state, so a fresh sweep re-probes it and
            # nothing serves the deleted first-finish facts.
            self.assertEqual(load_contract(contract.contract_path).cleanup, "reopened")


if __name__ == "__main__":
    unittest.main()
