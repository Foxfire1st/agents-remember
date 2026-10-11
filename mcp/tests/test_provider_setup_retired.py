"""MIK-R112 F02: retired providers perform zero install/start/watch (spies).

The two retired provider ids never count as enabled, however the settings
read, so every shared install/prepare/watcher entry point returns before any
lifecycle command. The spies below fail loudly if a command is ever issued;
the old-watcher stop (install/provider_watchers.py) is explicitly retained
and only asserted present here, never gated.
"""

from __future__ import annotations

import argparse
import copy
import unittest
from unittest.mock import patch

from agents_remember.install import provider_watchers as install_provider_watchers
from agents_remember.providers import provider_setup
from agents_remember.providers.cgc import setup as cgc_setup
from agents_remember.providers.grepai import setup as grepai_setup

RETIRED_IDS = ("codegraphcontext-code", "grepai-memory")

_LIFECYCLE_SPY_TARGETS = (
    "agents_remember.providers.provider_setup.run_lifecycle",
    "agents_remember.providers.cgc.setup.run_lifecycle",
    "agents_remember.providers.grepai.setup.run_lifecycle",
)


def _enabled_settings() -> dict:
    return {
        "contextProviders": {
            "enabled": True,
            "providers": {
                "codegraphcontext-code": {"enabled": True},
                "grepai-memory": {"enabled": True},
            },
        }
    }


def _args() -> argparse.Namespace:
    return argparse.Namespace(skip_grepai=False, skip_watchers=False, dry_run=True)


class RetiredProviderSetupTests(unittest.TestCase):
    def test_retired_ids_never_count_as_enabled(self) -> None:
        settings = _enabled_settings()
        for provider_id in RETIRED_IDS:
            with self.subTest(provider=provider_id):
                self.assertFalse(provider_setup.provider_enabled(settings, provider_id))
                self.assertFalse(
                    provider_setup.selected_provider_enabled(_args(), settings, provider_id)
                )
        # The gate is id-specific, not a blanket disable: any other enabled id counts.
        settings["contextProviders"]["providers"]["other-provider"] = {"enabled": True}
        self.assertTrue(provider_setup.provider_enabled(settings, "other-provider"))

    def test_install_prepare_and_watchers_issue_zero_lifecycle_commands(self) -> None:
        settings = _enabled_settings()
        before = copy.deepcopy(settings)
        spies = [
            patch(target, side_effect=AssertionError(f"lifecycle command issued: {target}"))
            for target in _LIFECYCLE_SPY_TARGETS
        ]
        with spies[0] as spy0, spies[1] as spy1, spies[2] as spy2:
            self.assertEqual(provider_setup.install_enabled_providers(_args(), settings), [])
            self.assertEqual(provider_setup.prepare_enabled_providers(_args(), settings), [])
            self.assertEqual(cgc_setup.install_enabled_provider(_args(), settings), [])
            self.assertEqual(cgc_setup.prepare_enabled_provider(_args(), settings), [])
            self.assertEqual(grepai_setup.install_enabled_provider(_args(), settings), [])
            self.assertEqual(grepai_setup.prepare_enabled_provider(_args(), settings), [])
            self.assertEqual(provider_setup._watcher_results(_args(), settings), [])
        for spy in (spy0, spy1, spy2):
            spy.assert_not_called()
        # Early return touches neither settings nor args beyond reads.
        self.assertEqual(settings, before)

    def test_old_watcher_stop_stays_available(self) -> None:
        self.assertTrue(callable(install_provider_watchers.stop_provider_watchers_before_refresh))
