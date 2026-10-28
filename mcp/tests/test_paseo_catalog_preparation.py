"""Startup discovery and a first launch share Paseo's catalog only inside one operation's scope."""

from __future__ import annotations

from typing import Any

from agents_remember.cli.paseo_catalog import (
    launcher_catalog,
    launcher_catalog_question,
    resolve_agent_selection,
)
from test_paseo_catalog import HARNESS_ORDER, FakeBridge, PaseoCatalogTestCase, defaults

# The cross-thread claim, that a startup discovery and a first launch on another thread join one
# in-flight Paseo question, is retired by R213: the landed design shares the catalog through
# launcher_catalog_question(), a contextvar scope, so one operation's questions share one bridge
# call and a different operation asks again. What is pinned here is that boundary. A launch still
# asks its selected model's features as a question of its own, after the shared metadata one.


def _modes(bridge: FakeBridge) -> list[str | None]:
    return [payload.get("mode") for command, payload in bridge.calls if command == "catalog"]


class CatalogPreparationScopeTests(PaseoCatalogTestCase):
    def _prepare_then_launch(self) -> tuple[str, dict[str, Any]]:
        launcher_catalog(self.config)
        return resolve_agent_selection(
            self.config, defaults("codex", "gpt-a", "high"), HARNESS_ORDER, None
        )

    def test_one_scope_asks_the_metadata_once_for_its_preparation_and_its_launch(self) -> None:
        bridge = self.bridge()
        with launcher_catalog_question():
            agent, options = self._prepare_then_launch()
        self.assertEqual((agent, options["model"], options["effort"]), ("codex", "gpt-a", "high"))
        self.assertEqual(_modes(bridge), ["metadata", "selected"])
        self.assertEqual(len(bridge.calls), 2)

    def test_two_scopes_ask_the_metadata_once_each(self) -> None:
        bridge = self.bridge()
        for _ in range(2):
            with launcher_catalog_question():
                self._prepare_then_launch()
        self.assertEqual(_modes(bridge), ["metadata", "selected"] * 2)
        self.assertEqual(len(bridge.calls), 4)
