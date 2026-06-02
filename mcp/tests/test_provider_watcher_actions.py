"""Watcher-action naming guards (restart must never invalidate indexes).

`refresh` used to silently force-rebuild every index, which read like a harmless restart.
It is now split into `restart` (no index changes) and `invalidate-indexes` (full rebuild),
and the old name is rejected with guidance so the destructive path is never picked by mistake.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import cast

from agents_remember.controllers.provider_tools import provider_watchers_tool
from agents_remember.mcp.config import McpRuntimeConfig


def _config_without_providers() -> McpRuntimeConfig:
    # Action validation happens before any provider is touched, so an empty
    # provider set is enough to exercise the naming guards.
    return cast(McpRuntimeConfig, SimpleNamespace(providers={}))


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

    def test_invalidate_indexes_dispatches_under_new_name(self) -> None:
        result = provider_watchers_tool(
            _config_without_providers(), action="invalidate-indexes", dry_run=True
        )
        self.assertEqual(result["operation"], "provider_watchers")
        self.assertEqual(result["action"], "invalidate-indexes")
        self.assertIn("steps", result)


if __name__ == "__main__":
    unittest.main()
