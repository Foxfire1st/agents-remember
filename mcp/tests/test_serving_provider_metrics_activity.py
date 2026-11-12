"""Provider safety sampling follows labelled fleets, never launch authority or a page."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    reload_provider_authority,
    require_provider_launch_authority,
)
from agents_remember.providers.metrics import (
    PROVIDER_METRICS_SCHEMA,
    ContainerSample,
    MetricsSnapshot,
    ProviderMetricsStore,
)
from agents_remember.serving import _app_lifespan as lifespan


class ProviderMetricsActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_launch_authority_does_not_prove_fleet_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = McpRuntimeConfig(root / "settings.json", root, root, root / "logs")
            config.config_path.write_text(json.dumps({"providers": {}}))
            store = Mock(spec=ProviderMetricsStore)
            sample = Mock()

            async def end_interval(_seconds: float) -> None:
                raise asyncio.CancelledError

            with (
                patch.object(lifespan, "sample_provider_containers", sample),
                patch.object(lifespan, "evaluate_provider_degradation") as evaluate,
                patch.object(lifespan.asyncio, "sleep", end_interval),
                self.assertRaises(asyncio.CancelledError),
            ):
                await lifespan._metrics_loop(config, store)
            sample.assert_called_once_with(cwd=root)
            store.record.assert_called_once_with(sample.return_value)
            evaluate.assert_called_once()
            store.compact.assert_called_once()

    async def test_live_admission_between_ticks_and_restart_keep_leftovers_observable(self) -> None:
        for scenario in ("live-revocation", "between-ticks", "restart"):
            with self.subTest(scenario=scenario):
                await self._assert_admission_scenario(scenario)

    async def _assert_admission_scenario(self, scenario: str) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = McpRuntimeConfig(root / "settings.json", root, root, root / "logs")
            config.config_path.write_text(json.dumps({"providers": {}}))
            store = Mock(spec=ProviderMetricsStore)
            intervals = 0
            admitted = scenario == "restart"

            def snapshot(*, cwd: Path) -> MetricsSnapshot:
                self.assertEqual(cwd, root)
                containers = (
                    [ContainerSample("leftover", "grepai-memory", "synthetic", True, 0)]
                    if admitted
                    else []
                )
                return MetricsSnapshot(PROVIDER_METRICS_SCHEMA, "2026-01-01T00:00:00Z", containers)

            sample = Mock(side_effect=snapshot)

            async def advance(_seconds: float) -> None:
                nonlocal intervals, admitted
                intervals += 1
                if intervals == 3:
                    raise asyncio.CancelledError
                if scenario == "live-revocation":
                    providers = {"grepai-memory": {}} if intervals == 1 else {}
                    config.config_path.write_text(json.dumps({"providers": providers}))
                    admitted = True
                elif scenario == "between-ticks":
                    config.config_path.write_text(json.dumps({"providers": {"grepai-memory": {}}}))
                    admitted = True
                    config.config_path.write_text(json.dumps({"providers": {}}))

            with (
                patch.object(lifespan, "sample_provider_containers", sample),
                patch.object(lifespan, "evaluate_provider_degradation") as evaluate,
                patch.object(lifespan.asyncio, "sleep", advance),
                self.assertRaises(asyncio.CancelledError),
            ):
                await lifespan._metrics_loop(config, store)
            self.assertEqual(sample.call_count, 3)
            sample.assert_called_with(cwd=root)
            self.assertEqual(evaluate.call_count, 3)
            self.assertEqual(store.compact.call_count, 3)
            self.assertEqual(
                [bool(call.args[0].containers) for call in store.record.call_args_list],
                [scenario == "restart", True, True],
            )
            with self.assertRaises(ConfigError):
                require_provider_launch_authority(config, operation="test launch refusal")

    async def test_malformed_authority_does_not_stop_safety_or_reclaim(self) -> None:
        for boot_enabled in (False, True):
            for authority in (b"\xff", b"unreadable JSON"):
                with (
                    self.subTest(boot_enabled=boot_enabled, authority=authority),
                    tempfile.TemporaryDirectory() as folder,
                ):
                    root = Path(folder)
                    config = McpRuntimeConfig(root / "settings.json", root, root, root / "logs")
                    providers = {"grepai-memory": {}} if boot_enabled else {}
                    config.config_path.write_text(json.dumps({"providers": providers}))
                    config = reload_provider_authority(config).apply(config)
                    config.config_path.write_bytes(authority)
                    store = Mock(spec=ProviderMetricsStore)
                    sample = Mock()

                    async def end_interval(_seconds: float) -> None:
                        raise asyncio.CancelledError

                    with (
                        patch.object(lifespan, "sample_provider_containers", sample),
                        patch.object(lifespan, "evaluate_provider_degradation") as evaluate,
                        patch.object(lifespan.asyncio, "sleep", end_interval),
                        self.assertRaises(asyncio.CancelledError),
                    ):
                        await lifespan._metrics_loop(config, store)
                    sample.assert_called_once_with(cwd=root)
                    store.record.assert_called_once_with(sample.return_value)
                    evaluate.assert_called_once()
                    store.compact.assert_called_once()
                    with self.assertRaises((ConfigError, UnicodeDecodeError)):
                        require_provider_launch_authority(config, operation="test launch refusal")
