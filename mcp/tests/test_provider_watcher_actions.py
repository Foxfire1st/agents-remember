"""Watcher-action naming guards (restart must never invalidate indexes).

`refresh` used to silently force-rebuild every index, which read like a harmless restart.
It is now split into `restart` (no index changes) and `invalidate-indexes` (full rebuild),
and the old name is rejected with guidance so the destructive path is never picked by mistake.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from agents_remember.application.provider_tools import provider_watchers_tool
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
)


def _config_without_providers() -> McpRuntimeConfig:
    # Action validation happens before any provider is touched, so an empty
    # provider set is enough to exercise the naming guards.
    return cast(McpRuntimeConfig, SimpleNamespace(providers={}))


def _disk_disabled_config(tmp: Path) -> McpRuntimeConfig:
    # Containment R1 (260707-HFX-L1): launch-capable actions re-read the on-disk
    # authority file, so the fake config needs a real one saying providers:{}.
    settings = tmp / "authority.json"
    settings.write_text(json.dumps({"version": 1, "providers": {}}), encoding="utf-8")
    return cast(
        McpRuntimeConfig,
        SimpleNamespace(
            providers={},
            config_path=settings,
            coordination_root=tmp / "coord",
            workspace_root=tmp / "ws",
        ),
    )


class WatcherActionNamingTests(unittest.TestCase):
    def test_refresh_is_rejected_with_guidance(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            provider_watchers_tool(_config_without_providers(), action="refresh")
        message = str(ctx.exception)
        self.assertIn("restart", message)
        self.assertIn("invalidate-indexes", message)

    def test_unknown_action_lists_invalidate_indexes(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            provider_watchers_tool(_config_without_providers(), action="bogus")
        self.assertIn("invalidate-indexes", str(ctx.exception))
        self.assertNotIn("refresh", str(ctx.exception))

    def test_invalidate_indexes_refused_when_disabled_on_disk(self) -> None:
        # Containment R1 (260707-HFX-L1): the rebuild launches indexers, so a
        # disk-disabled authority refuses it — the old behavior (dispatch with
        # empty steps) silently honored a stale boot snapshot.
        with tempfile.TemporaryDirectory() as tmp_dir, self.assertRaises(ConfigError) as ctx:
            provider_watchers_tool(
                _disk_disabled_config(Path(tmp_dir)),
                action="invalidate-indexes",
                dry_run=True,
            )
        message = str(ctx.exception)
        self.assertIn("containment R1", message)
        self.assertIn("disabled", message)

    def test_stop_still_allowed_when_disabled_on_disk(self) -> None:
        # Stopping is always legal: the gate must never block teardown.
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = provider_watchers_tool(
                _disk_disabled_config(Path(tmp_dir)), action="stop", dry_run=True
            )
        self.assertEqual(result["operation"], "provider_watchers")


if __name__ == "__main__":
    unittest.main()
