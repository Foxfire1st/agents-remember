"""Pre-session daemon capability catalog cache and public envelope tests."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from collections import deque
from collections.abc import Mapping
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    EffortOption,
    ModelCapability,
)
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_models import LaunchSpec
from agents_remember.serving.harnesses import HARNESSES


def _snapshot(model: str = "model-a") -> CapabilitySnapshot:
    return CapabilitySnapshot(
        models=(
            ModelCapability(
                key=model,
                display_name="Model A",
                supports_effort=True,
                effort_options=(EffortOption("high", "High"),),
                default_effort="high",
                is_default=True,
            ),
        ),
        selected_model_key=model,
        selected_effort="high",
    )


class _Discoverer:
    def __init__(self, owner: _Factory, outcome: CapabilitySnapshot | Exception) -> None:
        self._owner = owner
        self._outcome = outcome

    async def discover(self, launch: LaunchSpec) -> CapabilitySnapshot:
        self._owner.launches.append(launch)
        await asyncio.sleep(0.01)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _Factory:
    def __init__(self, *outcomes: CapabilitySnapshot | Exception) -> None:
        self.outcomes = deque(outcomes)
        self.launches: list[LaunchSpec] = []
        self.environments: list[dict[str, str]] = []

    def __call__(self, harness_id: str, *, env: Mapping[str, str]) -> _Discoverer:
        del harness_id
        self.environments.append(dict(env))
        return _Discoverer(self, self.outcomes.popleft())


class HarnessCapabilityCatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.executable = self.tmp / "claude"
        self.executable.write_bytes(b"version-one")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _catalog(self, factory: _Factory) -> HarnessCapabilityCatalog:
        return HarnessCapabilityCatalog(
            self.tmp,
            which=lambda command: str(self.executable) if command == "claude" else None,
            adapter_factory=factory,
            environment=lambda: {"AUTH_ACCOUNT": "current"},
        )

    async def test_cache_hit_is_discover_only_and_preserves_model_gating(self) -> None:
        factory = _Factory(_snapshot())
        catalog = self._catalog(factory)

        first = await catalog.get("claude", registry=HARNESSES)
        second = await catalog.get("claude", registry=HARNESSES)

        self.assertEqual((first.cache_status, second.cache_status), ("miss", "hit"))
        self.assertEqual(len(factory.launches), 1)
        self.assertEqual(factory.environments, [{"AUTH_ACCOUNT": "current"}])
        self.assertEqual(factory.launches[0].argv[0], str(self.executable.resolve()))
        envelope = second.to_json()
        self.assertEqual(envelope["schema"], "ar-harness-capabilities/v1")
        capabilities = envelope["capabilities"]
        assert isinstance(capabilities, dict)
        model = capabilities["models"][0]
        self.assertEqual(model["effortOptions"][0]["key"], "high")
        self.assertEqual(capabilities["configOptions"][1]["category"], "thought_level")

    async def test_refresh_reenumerates_and_replaces_the_bounded_entry(self) -> None:
        factory = _Factory(_snapshot(), _snapshot("model-b"))
        catalog = self._catalog(factory)
        await catalog.get("claude", registry=HARNESSES)

        refreshed = await catalog.get("claude", registry=HARNESSES, refresh=True)

        self.assertEqual(refreshed.cache_status, "refreshed")
        self.assertEqual(refreshed.snapshot.models[0].key, "model-b")
        self.assertEqual(len(factory.launches), 2)
        self.assertEqual(catalog.retained_entry_count, 1)

    async def test_executable_change_invalidates_without_growing_the_cache(self) -> None:
        factory = _Factory(_snapshot(), _snapshot("model-b"))
        catalog = self._catalog(factory)
        first = await catalog.get("claude", registry=HARNESSES)
        self.executable.write_bytes(b"version-two-is-different")
        os.utime(self.executable, ns=(2_000_000_000, 2_000_000_000))

        changed = await catalog.get("claude", registry=HARNESSES)

        self.assertEqual(changed.cache_status, "miss")
        self.assertNotEqual(first.install_fingerprint, changed.install_fingerprint)
        self.assertEqual(len(factory.launches), 2)
        self.assertEqual(catalog.retained_entry_count, 1)

    async def test_failed_refresh_quarantines_stale_entry_until_ordinary_recovery(self) -> None:
        factory = _Factory(
            _snapshot(),
            HarnessControlError("auth refresh failed"),
            _snapshot("model-b"),
        )
        catalog = self._catalog(factory)
        await catalog.get("claude", registry=HARNESSES)

        with self.assertRaisesRegex(HarnessControlError, "auth refresh failed"):
            await catalog.get("claude", registry=HARNESSES, refresh=True)

        recovered = await catalog.get("claude", registry=HARNESSES)
        cached = await catalog.get("claude", registry=HARNESSES)
        self.assertEqual(recovered.cache_status, "miss")
        self.assertEqual(recovered.snapshot.models[0].key, "model-b")
        self.assertEqual(cached.cache_status, "hit")
        self.assertEqual(len(factory.launches), 3)

    async def test_failed_refresh_does_not_delete_a_later_concurrent_success(self) -> None:
        factory = _Factory(
            _snapshot(),
            HarnessControlError("auth refresh failed"),
            _snapshot("model-b"),
        )
        catalog = self._catalog(factory)
        await catalog.get("claude", registry=HARNESSES)

        outcomes = await asyncio.gather(
            catalog.get("claude", registry=HARNESSES, refresh=True),
            catalog.get("claude", registry=HARNESSES, refresh=True),
            return_exceptions=True,
        )

        self.assertEqual(sum(isinstance(item, HarnessControlError) for item in outcomes), 1)
        successes = [item for item in outcomes if not isinstance(item, BaseException)]
        self.assertEqual(len(successes), 1)
        success = successes[0]
        self.assertEqual(success.snapshot.models[0].key, "model-b")
        current = await catalog.get("claude", registry=HARNESSES)
        self.assertEqual(current.cache_status, "hit")
        self.assertEqual(current.snapshot.models[0].key, "model-b")
        self.assertEqual(catalog.retained_entry_count, 1)

    async def test_same_fingerprint_requests_share_one_inflight_discovery(self) -> None:
        factory = _Factory(_snapshot())
        catalog = self._catalog(factory)

        first, second = await asyncio.gather(
            catalog.get("claude", registry=HARNESSES),
            catalog.get("claude", registry=HARNESSES),
        )

        self.assertEqual({first.cache_status, second.cache_status}, {"miss", "hit"})
        self.assertEqual(len(factory.launches), 1)


if __name__ == "__main__":
    unittest.main()
