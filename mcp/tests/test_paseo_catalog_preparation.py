"""Startup discovery and a first launch share Paseo's catalog only inside one operation's scope."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.cli import (
    paseo_bridge,
    paseo_catalog,
    role_launch_preparation,
    role_launch_routes,
)
from agents_remember.cli.paseo_catalog import (
    launcher_catalog,
    launcher_catalog_question,
    resolve_agent_selection,
)
from agents_remember.kernel._agentic_settings_core import EXTRA_ARGS_HARNESSES
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_paseo_catalog import CATALOG, HARNESS_ORDER, FakeBridge, PaseoCatalogTestCase, defaults

_READ_AT = "2026-11-24T11:00:00Z"
_OFFERED_ROWS = [
    {"id": "antigravity", "label": "Antigravity", "models": [], "modes": []},
    {"id": "antigravity-acp", "label": "Antigravity ACP", "models": [], "modes": []},
]

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

    def test_options_metadata_shares_the_profile_and_extra_args_answers(self) -> None:
        profile = {"id": "host-architect", "name": "architect", "provider": "eve"}
        document = {
            "agentProfiles": [profile],
            "providers": {"claude": {"options": {"extraArgs": {"--host-arg": True}}}},
        }
        for answer in (
            {"daemonConfig": document},
            {"daemonConfigError": "configuration unread"},
        ):
            with self.subTest(answer=answer):
                self._assert_options_configuration_answer(answer, document, profile)

    def _assert_options_configuration_answer(
        self, answer: dict[str, Any], document: dict[str, Any], profile: dict[str, str]
    ) -> None:
        app = FastAPI()
        role_launch_routes.register_role_launch_routes(app, self.config)
        calls: list[tuple[str, dict[str, Any]]] = []

        def bridge(_config, command, payload):
            calls.append((command, payload))
            if command == "role-profiles":
                return {"profiles": [profile], "readAt": _READ_AT}
            if command == "daemon-config":
                return {"config": document}
            return {**CATALOG, **answer, "readAt": _READ_AT}

        observed: list[dict[str, Any]] = []
        original = role_launch_preparation._role_defaults

        def capture(config, context):
            role, order = original(config, context)
            observed.append(role)
            return role, order

        with (
            patch.object(paseo_catalog, "bridge_call", bridge),
            patch.object(paseo_bridge, "bridge_call", bridge),
            patch.object(role_launch_preparation, "_role_defaults", capture),
            patch.object(
                role_launch_routes,
                "resolve_role_launch_context",
                return_value=RoleLaunchContext("architect", None, None, None, None),
            ),
            TestClient(app) as client,
        ):
            response = client.post("/api/role-launch/options", json={"role": "architect"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            calls,
            [
                (
                    "catalog",
                    {"mode": "metadata", "cwd": self.config.workspace_root.as_posix()},
                )
            ],
        )
        (role,) = observed
        if "daemonConfig" in answer:
            self.assertEqual(role["hostRole"]["profile"], profile)
            self.assertEqual(role["hostRole"]["readAt"], _READ_AT)
            self.assertEqual(role["extraArgs"]["hostConfiguration"]["claude"], ["--host-arg"])
            self.assertNotIn("hostConfigurationUnread", role["extraArgs"])
            self.assertEqual(response.json()["roleDefaults"]["agent"], "eve")
        else:
            self.assertEqual(role["hostRole"]["state"], "unavailable")
            self.assertEqual(role["hostRole"]["reason"], "configuration unread")
            self.assertEqual(
                role["extraArgs"]["hostConfigurationUnread"], sorted(EXTRA_ARGS_HARNESSES)
            )

    def test_options_and_saved_catalog_exclude_only_the_refused_provider(self) -> None:
        bridge = self.bridge({**CATALOG, "providers": _OFFERED_ROWS})
        app = FastAPI()
        role_launch_routes.register_role_launch_routes(app, self.config)
        with (
            patch.object(
                role_launch_routes,
                "resolve_role_launch_context",
                return_value=RoleLaunchContext("architect", None, None, None, None),
            ),
            patch.object(
                role_launch_preparation,
                "_role_defaults",
                return_value=(defaults("Antigravity"), ("antigravity", "antigravity-acp")),
            ) as role_defaults,
            TestClient(app) as client,
        ):
            fresh_response = client.post("/api/role-launch/options", json={"role": "architect"})
            saved_response = client.get("/api/role-launch/saved-catalog")
            role_defaults.return_value = (defaults(None), ("antigravity", "antigravity-acp"))
            implicit_response = client.post("/api/role-launch/options", json={"role": "architect"})
        for response in (fresh_response, saved_response, implicit_response):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                [agent["id"] for agent in response.json()["agents"]], ["antigravity-acp"]
            )
        unavailable = fresh_response.json()["roleDefaults"]
        self.assertFalse(unavailable["available"])
        self.assertIn("Choose antigravity-acp", unavailable["refusal"])
        self.assertEqual(implicit_response.json()["roleDefaults"]["agent"], "antigravity-acp")
        self.assertEqual(_modes(bridge), ["metadata", "metadata"])
