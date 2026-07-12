"""Background landing-state refresh and exact-contract publication tests."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.observer.landing_state import LandingStateRefresher
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    default_contract,
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


def _contract(root: Path, index: int) -> WorktreeContract:
    contract = default_contract(
        task_name=f"landing-{index}",
        repo_name=f"repo-{index}",
        workflow_kind="light",
        memory_mode="disabled",
        coordination_root=root,
        code_repo_path=root / f"repo-{index}",
        code_source_branch=f"feat/{index}",
        code_work_branch=f"ar/{index}",
        code_base_commit=f"base-{index}",
        worktree_name=f"landing-{index}",
    )
    contract = replace(contract, closeout_status="completed")
    contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(contract.contract_path, contract)
    return contract


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
            with self.assertLogs("agents_remember.observer.landing_state", level="ERROR"):
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


if __name__ == "__main__":
    unittest.main()
