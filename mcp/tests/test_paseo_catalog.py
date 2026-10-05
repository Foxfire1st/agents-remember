from __future__ import annotations

import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from agents_remember.cli import paseo_catalog, role_launch_preparation, role_launch_routes
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_catalog import (
    LaunchSelectionRefused,
    forget_launcher_catalogs,
    launcher_options,
    resolve_agent_selection,
)
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleAgentOverride, RoleLauncherOptionsRequest
from fastapi import HTTPException

HARNESS_ORDER = ("claude", "codex", "pi", "eve")
CATALOG: dict[str, Any] = {
    "runtime": {"serverId": "srv_configured", "version": "0.11.0-beta.2"},
    "providers": [
        {
            "id": "codex",
            "label": "Codex",
            "models": [
                {
                    "id": "gpt-a",
                    "label": "GPT A",
                    "description": "first",
                    "isDefault": True,
                    "efforts": [{"id": "low", "label": "Low"}, {"id": "high", "label": "High"}],
                    "defaultEffort": "low",
                    "serviceTiers": [],
                },
                {"id": "gpt-b", "label": "GPT B", "efforts": [], "serviceTiers": []},
            ],
        },
        {"id": "eve", "label": "Eve", "models": []},
        {"id": "pi", "label": "Pi", "models": [], "listingError": "auth missing"},
    ],
}


def runtime_config(
    root: Path, *, listen: str | None = "127.0.0.1:6833", **overrides: str
) -> McpRuntimeConfig:
    settings = (
        parse_paseo_runtime_settings(
            {
                "installPrefix": (root / "prefix").as_posix(),
                "home": (root / "home").as_posix(),
                "listen": listen,
                "version": "0.11.0-beta.2",
                "providers": {},
                "embed": [],
                **overrides,
            }
        )
        if listen
        else None
    )
    return McpRuntimeConfig(
        config_path=root / "settings" / "mcp.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
        paseo_runtime=settings,
    )


def defaults(
    agent: str | None = "codex", model: str | None = None, effort: str | None = None
) -> dict[str, str | None]:
    return {"agent": agent, "model": model, "effort": effort, "serviceTier": None}


class FakeBridge:
    """Stands in for ``bridge_call``: answers from a queue and records every call."""

    def __init__(self, *answers: dict[str, Any] | PaseoBridgeFailure) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(
        self, _config: McpRuntimeConfig, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((command, payload))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, PaseoBridgeFailure):
            raise answer
        return copy.deepcopy(answer)


class PaseoCatalogTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = runtime_config(self.root)
        self.config.workspace_root.mkdir()
        self.config.coordination_root.mkdir()
        forget_launcher_catalogs()
        self.addCleanup(forget_launcher_catalogs)

    def bridge(self, *answers: dict[str, Any] | PaseoBridgeFailure) -> FakeBridge:
        fake = FakeBridge(*(answers or (CATALOG,)))
        patcher = patch.object(paseo_catalog, "bridge_call", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def options(
        self,
        role_defaults: dict[str, str | None],
        *,
        config: McpRuntimeConfig | None = None,
        **request: Any,
    ) -> dict[str, Any]:
        """One call of the options route, with task resolution and settings stubbed out."""

        request.setdefault("role", "architect")
        with (
            patch.object(role_launch_routes, "resolve_role_launch_context", return_value=object()),
            patch.object(
                role_launch_preparation,
                "_role_defaults",
                return_value=(dict(role_defaults), HARNESS_ORDER),
            ),
            patch.object(
                role_launch_routes, "_receipt_path", return_value=self.root / "receipt.json"
            ),
            patch.object(role_launch_routes, "_read_receipt", return_value=None),
        ):
            response = role_launch_routes._role_launch_options_endpoint(
                config or self.config, RoleLauncherOptionsRequest(**request)
            )
        return json.loads(bytes(response.body))


class LauncherOptionsTests(PaseoCatalogTestCase):
    def test_options_come_from_the_runtime_catalog(self) -> None:
        bridge = self.bridge()
        taskless = self.options(defaults("codex", "gpt-a", "high"))
        bound = self.options(defaults("codex", "gpt-a", "high"), role="manager")

        self.assertEqual(set(taskless), {"roleDefaults", "agents", "catalogOrigin", "executions"})
        self.assertEqual(set(bound), {"roleDefaults", "agents", "catalogOrigin", "execution"})
        self.assertEqual(taskless["executions"], [])
        self.assertIsNone(bound["execution"])
        self.assertEqual(
            taskless["roleDefaults"],
            {
                "agent": "codex",
                "model": "gpt-a",
                "effort": "high",
                "serviceTier": None,
                "available": True,
            },
        )
        self.assertRegex(taskless["catalogOrigin"], r"^paseo:[0-9a-f]{32}$")
        self.assertEqual(taskless["agents"], CATALOG["providers"])
        model_less, failed = taskless["agents"][1], taskless["agents"][2]
        self.assertEqual(model_less, {"id": "eve", "label": "Eve", "models": []})
        self.assertEqual(failed["listingError"], "auth missing")
        self.assertEqual(
            bridge.calls, [("catalog", {"cwd": self.config.workspace_root.as_posix()})]
        )

    def test_catalog_is_cached_per_runtime_and_only_a_refresh_rediscovers(self) -> None:
        bridge = self.bridge()
        first = self.options(defaults())
        other_role = self.options(defaults("eve"), role="manager")
        other_agent = self.options(defaults(), agentId="eve")
        self.assertEqual(
            bridge.calls, [("catalog", {"cwd": self.config.workspace_root.as_posix()})]
        )
        self.assertEqual(first["catalogOrigin"], other_role["catalogOrigin"])
        self.assertEqual(first["catalogOrigin"], other_agent["catalogOrigin"])
        self.assertEqual(other_agent["agents"], first["agents"])

        resolve_agent_selection(self.config, defaults(), HARNESS_ORDER, None)
        self.assertEqual(len(bridge.calls), 1, "a launch re-read a catalog that was cached")

        refreshed = self.options(defaults(), refreshCatalog=True)
        self.assertEqual(
            bridge.calls[1:],
            [("catalog", {"cwd": self.config.workspace_root.as_posix(), "refresh": True})],
        )
        self.assertNotEqual(refreshed["catalogOrigin"], first["catalogOrigin"])
        self.assertEqual(self.options(defaults())["catalogOrigin"], refreshed["catalogOrigin"])

        other_runtimes = {
            "listen": runtime_config(self.root, listen="127.0.0.1:6834"),
            "home": runtime_config(self.root, home=(self.root / "other-home").as_posix()),
            "installPrefix": runtime_config(
                self.root, installPrefix=(self.root / "other-prefix").as_posix()
            ),
            "version": runtime_config(self.root, version="0.11.0-beta.3"),
        }
        origins = {refreshed["catalogOrigin"]}
        for calls, (differs, other_runtime) in enumerate(other_runtimes.items(), start=3):
            with self.subTest(f"a runtime with another {differs} has its own catalog"):
                elsewhere = self.options(defaults(), config=other_runtime)
                self.assertEqual(len(bridge.calls), calls)
                self.assertNotIn(elsewhere["catalogOrigin"], origins)
                origins.add(elsewhere["catalogOrigin"])
        self.assertEqual(self.options(defaults())["catalogOrigin"], refreshed["catalogOrigin"])
        self.assertEqual(len(bridge.calls), 6)

        forget_launcher_catalogs()
        resolve_agent_selection(self.config, defaults(), HARNESS_ORDER, None)
        self.assertEqual(
            bridge.calls[6:],
            [("catalog", {"cwd": self.config.workspace_root.as_posix()})],
            "a launch must load an empty cache",
        )

    def test_an_unoffered_default_is_flagged_and_never_replaced(self) -> None:
        self.bridge()
        cases: dict[str, tuple[dict[str, str | None], bool]] = {
            "agent, model and effort offered": (defaults("codex", "gpt-a", "high"), True),
            "agent only": (defaults("codex"), True),
            "model-less provider": (defaults("eve"), True),
            "provider whose listing failed": (defaults("pi"), True),
            "agent not offered": (defaults("claude", "opus"), False),
            "model not offered": (defaults("codex", "gpt-z"), False),
            "effort not offered": (defaults("codex", "gpt-a", "max"), False),
            "model has no efforts": (defaults("codex", "gpt-b", "low"), False),
            "effort without a model": (defaults("codex", None, "low"), False),
            "model on a model-less provider": (defaults("eve", "eve-1"), False),
        }
        for label, (configured, available) in cases.items():
            with self.subTest(label):
                self.assertEqual(
                    self.options(configured)["roleDefaults"],
                    {**configured, "available": available},
                )
        with self.subTest("no agent configured: the first harness the catalog offers"):
            detected = launcher_options(self.config, defaults(None), HARNESS_ORDER)
            self.assertEqual(
                detected["roleDefaults"],
                {
                    "agent": "codex",
                    "model": None,
                    "effort": None,
                    "serviceTier": None,
                    "available": True,
                },
            )
        with self.subTest("no agent configured and none of the harnesses offered"):
            nothing = launcher_options(self.config, defaults(None), ("claude",))
            self.assertEqual(
                nothing["roleDefaults"],
                {
                    "agent": None,
                    "model": None,
                    "effort": None,
                    "serviceTier": None,
                    "available": False,
                },
            )

    def test_a_partial_catalog_is_kept_and_a_failed_discovery_caches_nothing(self) -> None:
        unreachable = PaseoBridgeFailure("paseo_daemon_unreachable", "cannot be reached")
        repaired = copy.deepcopy(CATALOG)
        repaired["providers"][2] = {
            "id": "pi",
            "label": "Pi",
            "models": [{"id": "pi-1", "label": "Pi 1", "efforts": []}],
        }
        bridge = self.bridge(unreachable, CATALOG, unreachable, repaired)

        with self.assertRaises(PaseoBridgeFailure):
            launcher_options(self.config, defaults(), HARNESS_ORDER)
        partial = launcher_options(self.config, defaults(), HARNESS_ORDER)
        self.assertEqual(len(bridge.calls), 2, "the failed discovery must not have been cached")
        self.assertEqual(partial["agents"][2]["listingError"], "auth missing")
        again = launcher_options(self.config, defaults("pi"), HARNESS_ORDER)
        self.assertEqual(len(bridge.calls), 2, "a failed listing is cached until a refresh")
        self.assertEqual(again["agents"][2], partial["agents"][2])
        self.assertTrue(again["roleDefaults"]["available"])

        with self.assertRaises(PaseoBridgeFailure):
            launcher_options(self.config, defaults(), HARNESS_ORDER, refresh=True)
        kept = launcher_options(self.config, defaults(), HARNESS_ORDER)
        self.assertEqual(kept["catalogOrigin"], partial["catalogOrigin"])
        self.assertEqual(len(bridge.calls), 3)

        refreshed = launcher_options(self.config, defaults(), HARNESS_ORDER, refresh=True)
        self.assertNotIn("listingError", refreshed["agents"][2])
        self.assertEqual(refreshed["agents"][2]["models"][0]["id"], "pi-1")

    def test_an_unreadable_catalog_is_a_named_failure_and_is_not_cached(self) -> None:
        unreadable: tuple[dict[str, Any], ...] = (
            {},
            {"providers": "codex"},
            {"providers": [{"label": "No id", "models": []}]},
            {"providers": [{"id": "codex", "models": None}]},
            {"providers": [{"id": "codex", "models": [{"label": "No id", "efforts": []}]}]},
            {"providers": [{"id": "codex", "models": [{"id": "gpt-a"}]}]},
            {"providers": [{"id": "codex", "models": [{"id": "gpt-a", "efforts": [{}]}]}]},
        )
        bridge = self.bridge(*unreadable, CATALOG)
        for reply in unreadable:
            with self.subTest(reply=reply), self.assertRaises(PaseoBridgeFailure) as raised:
                launcher_options(self.config, defaults(), HARNESS_ORDER)
            self.assertEqual(raised.exception.code, "paseo_bridge_invalid_reply")
        self.assertEqual(
            launcher_options(self.config, defaults(), HARNESS_ORDER)["agents"][0]["id"], "codex"
        )
        self.assertEqual(len(bridge.calls), len(unreadable) + 1)

    def test_route_errors_name_not_configured_and_unreachable_apart(self) -> None:
        failures = {
            "paseo_daemon_unreachable": (502, "The Paseo daemon at ws://127.0.0.1:6833/ws cannot"),
            "paseo_bridge_timeout": (504, "did not end within 60 seconds"),
            "paseo_bridge_invalid_reply": (502, "returned an unreadable reply"),
        }
        for code, (status, text) in failures.items():
            forget_launcher_catalogs()
            self.bridge(PaseoBridgeFailure(code, f"{text} (details)"))
            with self.subTest(code), self.assertRaises(HTTPException) as raised:
                self.options(defaults())
            self.assertEqual(raised.exception.status_code, status)
            self.assertIn(text, raised.exception.detail)

        with self.subTest("no Paseo runtime configured"):
            bridge = self.bridge()
            unconfigured = runtime_config(self.root, listen=None)
            with self.assertRaises(HTTPException) as raised:
                self.options(defaults(), config=unconfigured)
            self.assertEqual(raised.exception.status_code, 503)
            self.assertTrue(raised.exception.detail.startswith("no Paseo runtime configured: "))
            self.assertEqual(bridge.calls, [])

        with self.subTest("a cached catalog answers while the daemon is unreachable"):
            forget_launcher_catalogs()
            bridge = self.bridge(
                CATALOG, PaseoBridgeFailure("paseo_daemon_unreachable", "cannot be reached")
            )
            cached = self.options(defaults())
            self.assertEqual(self.options(defaults(), role="manager")["agents"], cached["agents"])
            self.assertEqual(len(bridge.calls), 1)
            with self.assertRaises(HTTPException) as raised:
                self.options(defaults(), refreshCatalog=True)
            self.assertEqual(raised.exception.status_code, 502)


class LaunchValidationTests(PaseoCatalogTestCase):
    def test_a_launch_is_validated_against_the_catalog_before_anything_is_created(self) -> None:
        self.bridge()
        role = defaults("codex", "gpt-a", "high")
        accepted: dict[str, tuple[RoleAgentOverride | None, tuple[str, dict[str, str]]]] = {
            "role defaults": (None, ("codex", {"model": "gpt-a", "effort": "high"})),
            "model override drops the role effort": (
                RoleAgentOverride(agentId="codex", modelId="gpt-b"),
                ("codex", {"model": "gpt-b"}),
            ),
            "effort override on the role model": (
                RoleAgentOverride(agentId="codex", effortId="low"),
                ("codex", {"model": "gpt-a", "effort": "low"}),
            ),
            "another agent drops the role model and effort": (
                RoleAgentOverride(agentId="eve"),
                ("eve", {}),
            ),
            "provider whose listing failed stays selectable": (
                RoleAgentOverride(agentId="pi"),
                ("pi", {}),
            ),
        }
        for label, (override, expected) in accepted.items():
            with self.subTest(label):
                self.assertEqual(
                    resolve_agent_selection(self.config, role, HARNESS_ORDER, override), expected
                )

        refused: dict[str, tuple[dict[str, str | None], RoleAgentOverride | None]] = {
            "agent_not_selected": (defaults(None), None),
            "agent_not_offered": (role, RoleAgentOverride(agentId="claude")),
            "model_not_offered": (role, RoleAgentOverride(agentId="codex", modelId="gpt-z")),
            "effort_not_offered": (
                role,
                RoleAgentOverride(agentId="codex", modelId="gpt-a", effortId="max"),
            ),
            "effort_requires_model": (role, RoleAgentOverride(agentId="eve", effortId="low")),
        }
        for code, (role_defaults, override) in refused.items():
            with self.subTest(code), self.assertRaises(LaunchSelectionRefused) as raised:
                resolve_agent_selection(self.config, role_defaults, ("claude",), override)
            self.assertEqual(raised.exception.code, code)
        with self.subTest("unoffered default model"), self.assertRaises(ValueError) as raised:
            resolve_agent_selection(self.config, defaults("codex", "gpt-z"), HARNESS_ORDER, None)
        self.assertIn("'gpt-z'", str(raised.exception))
        unoffered_default_efforts = (
            defaults("codex", "gpt-a", "max"),
            defaults("codex", "gpt-b", "low"),
        )
        for role_defaults in unoffered_default_efforts:
            with (
                self.subTest("unoffered default effort", defaults=role_defaults),
                self.assertRaises(LaunchSelectionRefused) as raised,
            ):
                resolve_agent_selection(self.config, role_defaults, HARNESS_ORDER, None)
            self.assertEqual(raised.exception.code, "effort_not_offered")
        with self.subTest("model on a model-less provider"):
            with self.assertRaises(LaunchSelectionRefused) as raised:
                resolve_agent_selection(
                    self.config, role, HARNESS_ORDER, RoleAgentOverride(agentId="pi", modelId="x")
                )
            self.assertEqual(raised.exception.code, "model_not_offered")

        with (
            patch.object(
                role_launch_preparation, "_role_defaults", return_value=(role, HARNESS_ORDER)
            ),
            patch.object(role_launch_preparation, "_resolve_workspace") as resolve_workspace,
            self.assertRaises(LaunchSelectionRefused),
        ):
            role_launch_preparation.prepare_role_handover(
                self.config,
                SimpleNamespace(role="architect"),  # type: ignore[arg-type]
                agent_override=RoleAgentOverride(agentId="codex", modelId="gpt-z"),
                request_id=uuid.uuid4(),
            )
        resolve_workspace.assert_not_called()


class ServiceTierCatalogTests(PaseoCatalogTestCase):
    def test_fast_is_independent_and_model_override_revalidates_capability(self) -> None:
        catalog = copy.deepcopy(CATALOG)
        model = catalog["providers"][0]["models"][0]
        model["id"] = "gpt-6.1-sol"
        model["efforts"] += [{"id": "xhigh", "label": "xhigh"}, {"id": "max", "label": "max"}]
        model["serviceTiers"] = [
            {"id": "default", "label": "Normal"},
            {"id": "priority", "label": "Fast"},
        ]
        self.bridge(catalog)
        for effort in ["xhigh", "max"]:
            role = {**defaults("codex", "gpt-6.1-sol", effort), "serviceTier": "fast"}
            agent, options = resolve_agent_selection(self.config, role, HARNESS_ORDER, None)
            self.assertEqual(
                (agent, options),
                ("codex", {"model": "gpt-6.1-sol", "effort": effort, "serviceTier": "priority"}),
            )
            shown = launcher_options(self.config, role, HARNESS_ORDER)
            self.assertEqual(shown["roleDefaults"]["serviceTier"], "fast")
            self.assertTrue(shown["roleDefaults"]["available"])
            with self.assertRaisesRegex(LaunchSelectionRefused, "service tier"):
                resolve_agent_selection(
                    self.config,
                    role,
                    HARNESS_ORDER,
                    RoleAgentOverride(agentId="codex", modelId="gpt-b"),
                )
            self.assertEqual(
                resolve_agent_selection(
                    self.config, role, HARNESS_ORDER, RoleAgentOverride(agentId="eve")
                ),
                ("eve", {}),
            )
        for tier in ["default", "priority"]:
            role = {**defaults("codex"), "serviceTier": tier}
            self.assertEqual(
                resolve_agent_selection(self.config, role, HARNESS_ORDER, None),
                ("codex", {"serviceTier": tier}),
            )
        self.assertEqual(
            resolve_agent_selection(self.config, defaults("codex"), HARNESS_ORDER, None),
            ("codex", {}),
        )

    def test_unsupported_tier_and_exact_unavailable_model_are_not_substituted(self) -> None:
        self.bridge()
        for tier in ["fast", "default", "unadvertised"]:
            with self.assertRaisesRegex(LaunchSelectionRefused, "service tier"):
                resolve_agent_selection(
                    self.config,
                    {**defaults("codex", "gpt-a"), "serviceTier": tier},
                    HARNESS_ORDER,
                    None,
                )
        with self.assertRaisesRegex(LaunchSelectionRefused, "gpt-6.1-sol"):
            resolve_agent_selection(
                self.config,
                {**defaults("codex", "gpt-6.1-sol", "xhigh"), "serviceTier": "fast"},
                HARNESS_ORDER,
                None,
            )


if __name__ == "__main__":
    unittest.main()
