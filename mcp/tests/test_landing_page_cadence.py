"""MIK-R165: the composed server probes landing only for actual SSE subscribers."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.app import (
    INFERRED_LIVE_INPUTS,
    OWNED_SERVING_COLLABORATORS,
    _build_serving_runtime,
)
from agents_remember.serving.projections import landing_state as landing
from agents_remember.serving.projector import DEFAULT_PROJECTION_CADENCE, LIVE_PROJECTION_CLOCK
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def _live(root: Path, leaf: str = "live"):
    contract = replace(
        default_contract(
            ContractTask("task", "repo", root, "light-task", "external"),
            leaf=LeafIdentity(leaf),
            code=RepoBranchPlan(root / "code", "main", "work", "a" * 40),
            memory=RepoBranchPlan(root / "memory", "main", "work", "a" * 40),
        ),
        closeout_status="completed",
    )
    write_contract(contract.contract_path, contract)
    return contract


def _runtime(root: Path):
    config = McpRuntimeConfig(root / "settings.json", root, root, root / "logs")
    return _build_serving_runtime(
        config,
        DEFAULT_PROJECTION_CADENCE,
        LIVE_PROJECTION_CLOCK,
        INFERRED_LIVE_INPUTS,
        OWNED_SERVING_COLLABORATORS,
    )[0]


class LandingPageCadenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_rewrites_reopen_new_and_removed_contracts_reset_bounded_schedule(self) -> None:
        for size in (2, 6):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                contracts = [_live(root, str(i)) for i in range(size)]
                paths = [c.contract_path for c in contracts]
                refresher = landing.LandingStateRefresher(
                    McpRuntimeConfig(root / "settings.json", root, root, root / "logs")
                )
                refresher.bind_interest(lambda: True)
                observe = mock.Mock(side_effect=landing.unobserved_landing_refs)
                with (
                    mock.patch.object(refresher, "_observe", observe),
                    mock.patch.object(landing, "iter_leaf_enclosure_contracts", return_value=paths),
                ):
                    await refresher.refresh_once(now=NOW)
                    self.assertEqual(observe.call_count, size)
                    await refresher.refresh_once(now=NOW + timedelta(seconds=120))
                    self.assertEqual(observe.call_count, 2 * size)
                    rewritten = replace(contracts[0], code_base_commit="b" * 40)
                    write_contract(rewritten.contract_path, rewritten)
                    fresh = _live(root, "new")
                    paths.append(fresh.contract_path)
                    await refresher.refresh_once(now=NOW + timedelta(seconds=121))
                    self.assertEqual(observe.call_count, 2 * size + 2)
                    reopened = replace(
                        contracts[1], cleanup="reopened", closeout_status="not-started"
                    )
                    write_contract(reopened.contract_path, reopened)
                    await refresher.refresh_once(now=NOW + timedelta(seconds=122))
                    self.assertNotIn(
                        landing.LandingContractKey.from_contract(reopened),
                        refresher._probe_schedule._entries,
                    )
                    ready = replace(reopened, closeout_status="completed")
                    write_contract(ready.contract_path, ready)
                    await refresher.refresh_once(now=NOW + timedelta(seconds=123))
                    self.assertEqual(observe.call_count, 2 * size + 3)
                    finished = replace(ready, cleanup="completed")
                    write_contract(finished.contract_path, finished)
                    await refresher.refresh_once(now=NOW + timedelta(seconds=124))
                    self.assertEqual(observe.call_count, 2 * size + 4)
                    await refresher.refresh_once(now=NOW + timedelta(seconds=125))
                    self.assertEqual(observe.call_count, 2 * size + 4)
                    self.assertNotIn(
                        landing.LandingContractKey.from_contract(finished),
                        refresher._probe_schedule._entries,
                    )
                    self.assertLessEqual(len(refresher._probe_schedule._entries), size)
                    paths.clear()
                    await refresher.refresh_once(now=NOW + timedelta(seconds=126))
                    self.assertEqual(refresher._probe_schedule._entries, {})
                    self.assertEqual(refresher._observations, {})

    async def test_disconnect_during_discovery_admits_no_probe(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            contract = _live(root)
            refresher = landing.LandingStateRefresher(
                McpRuntimeConfig(root / "settings.json", root, root, root / "logs")
            )
            interested = True
            refresher.bind_interest(lambda: interested)

            def discover():
                nonlocal interested
                interested = False
                return [contract]

            with (
                mock.patch.object(refresher, "_landing_contracts", discover),
                mock.patch.object(refresher, "_observe") as observe,
            ):
                await refresher.refresh_once(now=NOW)
                observe.assert_not_called()

    async def test_unchanged_answers_use_all_age_bands_and_changed_answer_resets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            contract = _live(root)
            refresher = landing.LandingStateRefresher(
                McpRuntimeConfig(root / "settings.json", root, root, root / "logs")
            )
            refresher.bind_interest(lambda: True)
            rows = landing.unobserved_landing_refs(contract)
            assert rows is not None
            observe = mock.Mock(return_value=rows)
            checkpoints = [
                (0, 1),
                (29, 1),
                (30, 2),
                (60, 3),
                (90, 4),
                (120, 5),
                (239, 5),
                (240, 6),
                (360, 7),
                (480, 8),
                (600, 9),
                (1199, 9),
                (1200, 10),
                (1800, 11),
                (2400, 12),
                (3000, 13),
                (3600, 14),
                (7199, 14),
            ]
            with (
                mock.patch.object(refresher, "_observe", observe),
                mock.patch.object(
                    landing, "iter_leaf_enclosure_contracts", return_value=[contract.contract_path]
                ),
            ):
                for seconds, expected in checkpoints:
                    with self.subTest(seconds=seconds):
                        await refresher.refresh_once(now=NOW + timedelta(seconds=seconds))
                        self.assertEqual(observe.call_count, expected)
                observe.return_value = [{**row, "detail": "changed answer"} for row in rows]
                await refresher.refresh_once(now=NOW + timedelta(seconds=7200))
                self.assertEqual(observe.call_count, 15)
                await refresher.refresh_once(now=NOW + timedelta(seconds=7229))
                self.assertEqual(observe.call_count, 15)
                await refresher.refresh_once(now=NOW + timedelta(seconds=7230))
                self.assertEqual(observe.call_count, 16)

    async def test_composed_runtime_probes_only_until_the_last_subscriber_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            contract = _live(root)
            runtime = _runtime(root)
            refresher = cast(landing.LandingStateRefresher, runtime.projector._landing_refresher)
            observe = mock.Mock(return_value=[])
            with (
                mock.patch.object(refresher, "_observe", observe),
                mock.patch.object(
                    landing, "iter_leaf_enclosure_contracts", return_value=[contract.contract_path]
                ),
            ):
                await refresher.refresh_once(now=NOW)
                observe.assert_not_called()
                streams = [runtime.projector.subscribe(), runtime.projector.subscribe()]
                waiting = [asyncio.create_task(anext(stream)) for stream in streams]
                try:
                    await asyncio.sleep(0)
                    await refresher.refresh_once(now=NOW)
                    self.assertEqual(observe.call_count, 1)
                    waiting[0].cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await waiting[0]
                    await refresher.refresh_once(now=NOW + timedelta(seconds=30))
                    self.assertEqual(observe.call_count, 2)
                    waiting[1].cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await waiting[1]
                    await refresher.refresh_once(now=NOW + timedelta(seconds=60))
                    self.assertEqual(observe.call_count, 2)
                finally:
                    for task in waiting:
                        task.cancel()
                    for task in waiting:
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                    for stream in streams:
                        await stream.aclose()
