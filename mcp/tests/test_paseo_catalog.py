from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
    resolve_role_launch_context,
)
from agents_remember.application.terminal_tools import _resolve_spawn_harness
from agents_remember.cli import (
    paseo_catalog,
    role_launch_preparation,
    role_launch_receipts,
    role_launch_routes,
)
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_catalog import (
    LaunchSelectionRefused,
    forget_launcher_catalogs,
    launcher_options,
    resolve_agent_selection,
)
from agents_remember.cli.paseo_launch import RoleLaunch, build_launch_call
from agents_remember.cli.role_instructions import compose_role_instructions
from agents_remember.cli.role_launch_preparation import (
    RoleHandoverRequest,
    _compile_handover,
    _prepare_role_folders,
)
from agents_remember.cli.role_launch_receipts import _observe_launch_choices, _public_execution
from agents_remember.cli.role_launch_workspace import workspace_folder
from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    load_agentic_settings,
    user_agentic_settings_path,
)
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.mcp.registration.core import _register_identity_tools
from agents_remember.models.core import ServingBuildPayload
from agents_remember.models.role_launcher import (
    LauncherRole,
    RoleAgentOverride,
    RoleLauncherOptionsRequest,
    RoleSelection,
)
from agents_remember.worktrees import role_reports
from agents_remember.worktrees.modules import cleanup as worktree_cleanup
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.terminal_validation import TerminalPreflight
from agents_remember.worktrees.role_reports import collect_group_reports, remove_group_role_folders
from agents_remember.worktrees.worktree_contract import WorktreeContract
from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from paseo_runtime_test_support import write_shared_runtime
from pydantic import ValidationError
from test_paseo_launch import ROLE_REFS, resolved_document


def _requested_launch_values(*args, **kwargs):
    # These preserved tests assert the original three choices; R71 tests below assert the
    # new full options, mode, feature and provenance contract independently.
    agent_id, options = resolve_agent_selection(*args, **kwargs)
    return agent_id, {
        key: value for key, value in options.items() if key in {"model", "effort", "serviceTier"}
    }


def _legacy_options_fields(*args, **kwargs):
    return _legacy_options_projection(launcher_options(*args, **kwargs))


def _legacy_options_projection(reply):
    reply = copy.deepcopy(reply)
    reply["roleDefaults"] = {
        key: value
        for key, value in reply["roleDefaults"].items()
        if key in {"agent", "model", "effort", "serviceTier", "available"}
    }
    for agent in reply["agents"]:
        for key in ("modes", "modesKnown", "defaultModeId", "fetchedAt"):
            agent.pop(key, None)
        for model in agent["models"]:
            model.pop("features", None)
    return reply


HARNESS_ORDER = ("claude", "codex", "pi", "eve")
CATALOG: dict[str, Any] = {
    "runtime": {"serverId": "srv_configured", "version": PASEO_VERSION},
    "providers": [
        {
            "id": "codex",
            "label": "Codex",
            "modes": [],
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
        {"id": "eve", "label": "Eve", "models": [], "modes": []},
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
                "version": PASEO_VERSION,
                "providers": {},
                "embed": [],
                **overrides,
            }
        )
        if listen
        else None
    )
    write_shared_runtime(root, settings)
    return McpRuntimeConfig(
        config_path=root / "settings" / "mcp.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
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
        self.config.coordination_root.mkdir(exist_ok=True)
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
        return _legacy_options_projection(json.loads(bytes(response.body)))


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
        self.assertEqual(
            taskless["agents"],
            [{k: v for k, v in row.items() if k != "modes"} for row in CATALOG["providers"]],
        )
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

        _requested_launch_values(self.config, defaults(), HARNESS_ORDER, None)
        self.assertEqual(len(bridge.calls), 1, "a launch re-read a catalog that was cached")

        refreshed = self.options(defaults(), refreshCatalog=True)
        self.assertEqual(
            bridge.calls[1:],
            [("catalog", {"cwd": self.config.workspace_root.as_posix(), "refresh": True})],
        )
        self.assertNotEqual(refreshed["catalogOrigin"], first["catalogOrigin"])
        self.assertEqual(self.options(defaults())["catalogOrigin"], refreshed["catalogOrigin"])

        # Runtime settings are user-owned live input, not a detached field of each config.
        original_runtime = user_agentic_settings_path().read_bytes()
        other_runtimes = {
            "listen": (self.root / "listen-runtime", {"listen": "127.0.0.1:6834"}),
            "home": (self.root / "home-runtime", {"home": (self.root / "other-home").as_posix()}),
            "installPrefix": (
                self.root / "prefix-runtime",
                {"installPrefix": (self.root / "other-prefix").as_posix()},
            ),
        }
        origins = {refreshed["catalogOrigin"]}
        for calls, (differs, (folder, overrides)) in enumerate(other_runtimes.items(), start=3):
            other_runtime = runtime_config(folder, **overrides)
            with self.subTest(f"a runtime with another {differs} has its own catalog"):
                elsewhere = self.options(defaults(), config=other_runtime)
                self.assertEqual(len(bridge.calls), calls)
                self.assertNotIn(elsewhere["catalogOrigin"], origins)
                origins.add(elsewhere["catalogOrigin"])
        user_agentic_settings_path().write_bytes(original_runtime)
        self.assertEqual(self.options(defaults())["catalogOrigin"], refreshed["catalogOrigin"])
        self.assertEqual(len(bridge.calls), 5)

        forget_launcher_catalogs()
        _requested_launch_values(self.config, defaults(), HARNESS_ORDER, None)
        self.assertEqual(
            bridge.calls[5:],
            [("catalog", {"cwd": self.config.workspace_root.as_posix()})],
            "a launch must load an empty cache",
        )

    def test_an_unoffered_default_is_flagged_and_never_replaced(self) -> None:
        self.bridge()
        cases: dict[str, tuple[dict[str, str | None], bool]] = {
            "agent, model and effort offered": (defaults("codex", "gpt-a", "high"), True),
            "agent only": (defaults("codex"), True),
            "model-less provider": (defaults("eve"), True),
            "provider whose listing failed": (defaults("pi"), False),
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
            detected = _legacy_options_fields(self.config, defaults(None), HARNESS_ORDER)
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
            nothing = _legacy_options_fields(self.config, defaults(None), ("claude",))
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
            "modes": [],
            "models": [{"id": "pi-1", "label": "Pi 1", "efforts": []}],
        }
        bridge = self.bridge(unreachable, CATALOG, unreachable, repaired)

        with self.assertRaises(PaseoBridgeFailure):
            _legacy_options_fields(self.config, defaults(), HARNESS_ORDER)
        partial = _legacy_options_fields(self.config, defaults(), HARNESS_ORDER)
        self.assertEqual(len(bridge.calls), 2, "the failed discovery must not have been cached")
        self.assertEqual(partial["agents"][2]["listingError"], "auth missing")
        again = _legacy_options_fields(self.config, defaults("pi"), HARNESS_ORDER)
        self.assertEqual(len(bridge.calls), 2, "a failed listing is cached until a refresh")
        self.assertEqual(again["agents"][2], partial["agents"][2])
        self.assertFalse(again["roleDefaults"]["available"])

        with self.assertRaises(PaseoBridgeFailure):
            _legacy_options_fields(self.config, defaults(), HARNESS_ORDER, refresh=True)
        kept = _legacy_options_fields(self.config, defaults(), HARNESS_ORDER)
        self.assertEqual(kept["catalogOrigin"], partial["catalogOrigin"])
        self.assertEqual(len(bridge.calls), 3)

        refreshed = _legacy_options_fields(self.config, defaults(), HARNESS_ORDER, refresh=True)
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
                _legacy_options_fields(self.config, defaults(), HARNESS_ORDER)
            self.assertEqual(raised.exception.code, "paseo_bridge_invalid_reply")
        self.assertEqual(
            _legacy_options_fields(self.config, defaults(), HARNESS_ORDER)["agents"][0]["id"],
            "codex",
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

        original_runtime = user_agentic_settings_path().read_bytes()
        with self.subTest("no Paseo runtime configured"):
            bridge = self.bridge()
            unconfigured = runtime_config(self.root / "unconfigured", listen=None)
            with self.assertRaises(HTTPException) as raised:
                self.options(defaults(), config=unconfigured)
            self.assertEqual(raised.exception.status_code, 503)
            self.assertTrue(raised.exception.detail.startswith("no Paseo runtime configured: "))
            self.assertEqual(bridge.calls, [])

        user_agentic_settings_path().parent.mkdir(parents=True, exist_ok=True)
        user_agentic_settings_path().write_bytes(original_runtime)
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
        }
        for label, (override, expected) in accepted.items():
            with self.subTest(label):
                self.assertEqual(
                    _requested_launch_values(self.config, role, HARNESS_ORDER, override), expected
                )

        refused: dict[str, tuple[dict[str, str | None], RoleAgentOverride | None]] = {
            "modes_unknown": (role, RoleAgentOverride(agentId="pi")),
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
                _requested_launch_values(self.config, role_defaults, ("claude",), override)
            self.assertEqual(raised.exception.code, code)
        with self.subTest("unoffered default model"), self.assertRaises(ValueError) as raised:
            _requested_launch_values(self.config, defaults("codex", "gpt-z"), HARNESS_ORDER, None)
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
                _requested_launch_values(self.config, role_defaults, HARNESS_ORDER, None)
            self.assertEqual(raised.exception.code, "effort_not_offered")
        with self.subTest("model on a model-less provider"):
            with self.assertRaises(LaunchSelectionRefused) as raised:
                _requested_launch_values(
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
        model["features"] = [
            {
                "id": "service_tier",
                "type": "select",
                "label": "Speed",
                "value": "default",
                "options": model["serviceTiers"],
            }
        ]
        self.bridge(catalog)
        for effort in ["xhigh", "max"]:
            role = {**defaults("codex", "gpt-6.1-sol", effort), "serviceTier": "fast"}
            agent, options = _requested_launch_values(self.config, role, HARNESS_ORDER, None)
            self.assertEqual(
                (agent, options),
                ("codex", {"model": "gpt-6.1-sol", "effort": effort, "serviceTier": "priority"}),
            )
            shown = _legacy_options_fields(self.config, role, HARNESS_ORDER)
            self.assertEqual(shown["roleDefaults"]["serviceTier"], "fast")
            self.assertTrue(shown["roleDefaults"]["available"])
            with self.assertRaisesRegex(LaunchSelectionRefused, "service tier"):
                _requested_launch_values(
                    self.config,
                    role,
                    HARNESS_ORDER,
                    RoleAgentOverride(agentId="codex", modelId="gpt-b"),
                )
            self.assertEqual(
                _requested_launch_values(
                    self.config, role, HARNESS_ORDER, RoleAgentOverride(agentId="eve")
                ),
                ("eve", {}),
            )
        for tier in ["default", "priority"]:
            role = {**defaults("codex"), "serviceTier": tier}
            self.assertEqual(
                _requested_launch_values(self.config, role, HARNESS_ORDER, None),
                ("codex", {"serviceTier": tier}),
            )
        self.assertEqual(
            _requested_launch_values(self.config, defaults("codex"), HARNESS_ORDER, None),
            ("codex", {}),
        )

    def test_unsupported_tier_and_exact_unavailable_model_are_not_substituted(self) -> None:
        self.bridge()
        for tier in ["fast", "default", "unadvertised"]:
            with self.assertRaisesRegex(LaunchSelectionRefused, "service tier"):
                _requested_launch_values(
                    self.config,
                    {**defaults("codex", "gpt-a"), "serviceTier": tier},
                    HARNESS_ORDER,
                    None,
                )
        with self.assertRaisesRegex(LaunchSelectionRefused, "gpt-6.1-sol"):
            _requested_launch_values(
                self.config,
                {**defaults("codex", "gpt-6.1-sol", "xhigh"), "serviceTier": "fast"},
                HARNESS_ORDER,
                None,
            )


if __name__ == "__main__":
    unittest.main()


class R71RoleChoicesTests(PaseoCatalogTestCase):
    def write_user_settings(self, orchestration: dict[str, Any]) -> None:
        """Change the tested family in the fixture's one user file; retain its host."""
        path = user_agentic_settings_path()
        document = json.loads(path.read_text())
        document["orchestration"] = orchestration
        path.write_text(json.dumps(document))

    def setUp(self) -> None:
        super().setUp()
        catalog = copy.deepcopy(CATALOG)
        provider = catalog["providers"][0]
        provider["modes"] = [
            {"id": "auto", "label": "Default Permissions"},
            {"id": "full-access", "label": "Full Access"},
        ]
        provider["defaultModeId"] = "full-access"
        provider["models"][0]["features"] = [
            {
                "id": "service_tier",
                "label": "Speed",
                "type": "select",
                "value": "default",
                "options": [
                    {"id": "default", "label": "Default"},
                    {"id": "priority", "label": "Fast"},
                ],
            },
            {"id": "plan_mode", "label": "Plan", "type": "toggle", "value": False},
        ]
        provider["models"][0]["serviceTiers"] = [
            {"id": "default", "label": "Default"},
            {"id": "priority", "label": "Fast"},
        ]
        catalog["providers"].append(
            {
                "id": "claude",
                "label": "Claude",
                "modes": [
                    {"id": "default", "label": "Always Ask"},
                    {"id": "auto", "label": "Auto"},
                ],
                "models": [
                    {
                        "id": "opus",
                        "label": "Opus",
                        "isDefault": True,
                        "efforts": [],
                        "features": [],
                    }
                ],
            }
        )
        self.bridge(catalog)
        self.role = {
            **defaults("codex", "gpt-a", "high"),
            "role": "worker",
            "mode": None,
            "notes": None,
            "featureValues": {},
        }

    def select(self, role=None, override=None):
        return resolve_agent_selection(self.config, role or self.role, HARNESS_ORDER, override)

    def test_r71_host_fields_win_and_keep_each_source(self) -> None:
        role = {
            **self.role,
            "serviceTier": "fast",
            "hostRole": {
                "profile": {
                    "id": "first",
                    "name": "worker",
                    "provider": "codex",
                    "model": "gpt-a",
                    "modeId": "full-access",
                    "featureValues": {"plan_mode": True},
                    "notes": "German findings",
                }
            },
        }
        agent_id, applied = self.select(
            role, RoleAgentOverride(featureValues={"service_tier": "default"})
        )
        self.assertEqual(
            (
                agent_id,
                applied["model"],
                applied["effort"],
                applied["mode"],
                applied["featureValues"],
            ),
            (
                "codex",
                "gpt-a",
                "high",
                "full-access",
                {"service_tier": "default", "plan_mode": True},
            ),
        )
        choices = applied["choices"]
        self.assertEqual(
            [choices[key]["source"] for key in ("agent", "model", "effort", "mode", "notes")],
            ["host role", "host role", "our settings", "host role", "host role"],
        )
        self.assertEqual(choices["featureValues"]["service_tier"]["source"], "start call")
        self.assertEqual(choices["featureValues"]["plan_mode"]["source"], "host role")

    def test_r71_explicit_safe_mode_is_not_catalog_default_or_inherited_user_policy(self) -> None:
        self.assertEqual(self.select()[1]["mode"], "auto")
        self.assertEqual(self.select()[1]["choices"]["mode"]["source"], "least permissive mode")
        self.assertEqual(
            self.select(override=RoleAgentOverride(agentId="claude"))[1]["mode"], "default"
        )
        self.assertNotIn("mode", self.select(override=RoleAgentOverride(agentId="eve"))[1])

    def test_r71_provider_and_model_overrides_do_not_inherit_foreign_permission_choices(
        self,
    ) -> None:
        role = {**self.role, "mode": "full-access", "featureValues": {"plan_mode": True}}
        result = self.select(role, RoleAgentOverride(agentId="claude"))[1]
        self.assertEqual(result["mode"], "default")
        self.assertEqual(result["featureValues"], {})
        self.assertEqual(result["choices"]["omitted"][0]["key"], "featureValues.plan_mode")
        result = self.select(role, RoleAgentOverride(agentId="codex", modelId="gpt-b"))[1]
        self.assertEqual(result["featureValues"], {})
        self.assertNotIn("effort", result)

    def test_r71_unoffered_mode_feature_and_value_refuse_then_corrected_selection_succeeds(
        self,
    ) -> None:
        cases = [
            {"mode": "typo"},
            {"featureValues": {"missing": True}},
            {"featureValues": {"service_tier": "missing"}},
            {"featureValues": {"plan_mode": "true"}},
        ]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(LaunchSelectionRefused):
                self.select({**self.role, **bad})
        self.assertEqual(
            self.select({**self.role, "featureValues": {"plan_mode": False}})[1]["featureValues"],
            {"plan_mode": False},
        )
        self.assertFalse((self.config.coordination_root / "notes").exists())

    def test_r71_start_calls_cannot_supply_permission_authority(self) -> None:
        for key in ("plan_mode", "auto_accept", "unknown"):
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(LaunchSelectionRefused, "user's settings"),
            ):
                self.select(override=RoleAgentOverride(featureValues={key: False}))
        with self.assertRaises(ValidationError):
            RoleAgentOverride.model_validate({"mode": "full-access"})

    def test_r71_repository_authority_refusal_both_families_and_alias_merge(self) -> None:
        user = user_agentic_settings_path()
        repository = self.root / "code"
        local = repository / "system" / "settings.json"
        user.parent.mkdir(parents=True, exist_ok=True)
        local.parent.mkdir(parents=True)
        self.write_user_settings(
            {
                "roles": {
                    "worker": {
                        "harness": "codex",
                        "serviceTier": "fast",
                        "mode": "auto",
                        "notes": "User text",
                    }
                }
            }
        )
        for family in ("roles", "rolesPerLevel"):
            for forbidden in (
                {"mode": "auto"},
                {"notes": "repo instruction"},
                {"featureValues": {"plan_mode": False}},
                {"featureValues": {"unknown": True}},
            ):
                roles = {"worker": forbidden}
                raw = roles if family == "roles" else {"leaf": roles}
                local.write_text(json.dumps({"orchestration": {family: raw}}))
                with (
                    self.subTest(family=family, key=forbidden),
                    self.assertRaisesRegex(AgenticSettingsError, "user-layer only") as raised,
                ):
                    load_agentic_settings(self.config.coordination_root, repository)
                self.assertIn(str(local), str(raised.exception))
        local.write_text(
            json.dumps(
                {
                    "orchestration": {
                        "roles": {
                            "worker": {
                                "model": "gpt-a",
                                "effort": "high",
                                "featureValues": {"service_tier": "priority"},
                            }
                        }
                    }
                }
            )
        )
        knobs = load_agentic_settings(
            self.config.coordination_root, repository
        ).resolved_role_knobs("worker")
        self.assertEqual(
            (knobs.model, knobs.effort, knobs.mode, knobs.notes),
            ("gpt-a", "high", "auto", "User text"),
        )
        local.write_text(
            json.dumps(
                {
                    "orchestration": {
                        "roles": {"worker": {"featureValues": {"service_tier": "default"}}}
                    }
                }
            )
        )
        with self.assertRaisesRegex(AgenticSettingsError, "conflict"):
            load_agentic_settings(self.config.coordination_root, repository)

    def test_r71_profile_exact_name_first_order_and_unavailable_reason(self) -> None:
        profiles = [
            {"id": "wrong", "name": "Worker"},
            {"id": "one", "name": "worker", "provider": "codex"},
            {"id": "two", "name": "worker", "provider": "claude"},
        ]
        with patch.object(
            paseo_catalog, "bridge_call", return_value={"profiles": profiles, "readAt": "now"}
        ):
            read = paseo_catalog.read_host_role(self.config, "worker")
        self.assertEqual(
            (read["profile"]["id"], read["passedOver"], read["readAt"]), ("one", ["two"], "now")
        )
        with patch.object(
            paseo_catalog, "bridge_call", side_effect=PaseoBridgeFailure("no-answer", "offline")
        ):
            read = paseo_catalog.read_host_role(self.config, "worker")
        self.assertEqual(read["state"], "unavailable")
        self.assertEqual(self.select({**self.role, "hostRole": read})[0], "codex")

    def test_r71_plain_text_overwrite_blocks_raw_digest_and_no_file_loading(self) -> None:
        named_file = self.root / "instructions.md"
        named_file.write_text("secret file text must not be loaded")
        text = f"Read {named_file} and use a missing skill."
        appended, record = compose_role_instructions(
            "BASELINE",
            "baseline-digest",
            {"value": text, "source": "our settings", "key": "roles.worker.notes"},
        )
        self.assertIn("BASELINE", appended)
        self.assertNotIn(named_file.read_text(), appended)
        self.assertFalse(record["baselineReplaced"])
        replacement, replaced = compose_role_instructions(
            "BASELINE",
            "baseline-digest",
            {"value": "\n  <overwrite>  \nNew instructions", "source": "host role", "key": "notes"},
        )
        self.assertNotIn("BASELINE", replacement)
        self.assertNotIn("<overwrite>", replacement)
        self.assertTrue(replaced["baselineReplaced"])
        self.assertEqual(replaced["baselineDigest"], "baseline-digest")
        self.assertNotEqual(record["semanticDigest"], replaced["semanticDigest"])
        self.assertEqual(
            compose_role_instructions("BASELINE", "baseline-digest", {})[1]["semanticDigest"],
            "baseline-digest",
        )
        self.assertIn(
            "<overwrite>",
            compose_role_instructions(
                "BASELINE", "baseline-digest", {"value": "Hello\n<overwrite>"}
            )[0],
        )
        with self.assertRaisesRegex(LaunchSelectionRefused, "without instructions"):
            self.select({**self.role, "notes": "<overwrite>\n "})

    def test_r71_reports_collect_and_survive_removal_without_following_legacy_alias(self) -> None:
        for count in (2, 9):
            group = self.root / f"group-{count}"
            local = group / "role-reports" / "role-launch"
            canonical = self.root / f"task-{count}" / "notes" / "reports"
            local.mkdir(parents=True)
            canonical.mkdir(parents=True)
            for index in range(count):
                (local / f"{index}.md").write_text(f"report-{index}")
            (group / "scratch").mkdir()
            (group / "scratch" / "temporary").write_text("discard")
            (group / "task-reports").symlink_to(canonical, target_is_directory=True)
            contract = cast(
                WorktreeContract,
                SimpleNamespace(worktree_group=group, task_root=canonical.parent.parent),
            )
            self.assertEqual(collect_group_reports(contract, dry_run=True)["files"], count)
            self.assertFalse((canonical / "role-launch").exists())
            remove_group_role_folders(contract, dry_run=False)
            self.assertEqual(len(list((canonical / "role-launch").glob("*.md"))), count)
            self.assertFalse((group / "role-reports").exists())
            self.assertFalse((group / "task-reports").is_symlink())
            self.assertFalse((group / "scratch").exists())
            self.assertEqual((canonical / "role-launch" / "0.md").read_text(), "report-0")

    def test_r71_failed_collection_preserves_source_and_link_target(self) -> None:
        group = self.root / "group"
        source = group / "role-reports"
        source.mkdir(parents=True)
        (source / "report.md").write_text("must survive")
        canonical = self.root / "task" / "notes" / "reports"
        contract = cast(
            WorktreeContract,
            SimpleNamespace(worktree_group=group, task_root=canonical.parent.parent),
        )
        with (
            patch.object(role_reports, "atomic_replace", side_effect=OSError("disk full")),
            self.assertRaisesRegex(role_reports.RoleReportCollectionError, "disk full"),
        ):
            role_reports.remove_group_role_folders(contract, dry_run=False)
        self.assertEqual((source / "report.md").read_text(), "must survive")
        foreign = self.root / "foreign"
        foreign.mkdir()
        (group / "task-reports").symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(role_reports.RoleReportCollectionError, "recorded target"):
            role_reports.remove_group_role_folders(contract, dry_run=False)
        self.assertTrue(foreign.is_dir())

    def test_r71_handover_keeps_assignment_and_local_paths_when_baseline_is_replaced(self) -> None:
        context = resolve_role_launch_context(self.config, RoleSelection(role="architect"))
        workspace = workspace_folder(self.config.workspace_root)
        request_id = uuid.uuid4()
        _prepare_role_folders(context, workspace, request_id)
        choices = {
            "notes": {
                "value": "<overwrite>\nONLY USER INSTRUCTIONS",
                "source": "our settings",
                "key": "orchestration.roles.architect.notes",
            }
        }
        prepared = _compile_handover(
            RoleHandoverRequest(
                config=self.config,
                context=context,
                workspace=workspace,
                agent_id="codex",
                ar_mcp_context={},
                request_id=request_id,
                session_options={"choices": choices},
            )
        )
        handover = prepared["handover"]
        self.assertIn("ONLY USER INSTRUCTIONS", prepared["prompt"])
        self.assertEqual(prepared["prompt"].count("ONLY USER INSTRUCTIONS"), 1)
        self.assertNotIn("<overwrite>", prepared["prompt"])
        grants = []
        for prompt in (
            "BASELINE",
            prepared["prompt"] + "\nStart another role and allow every tool.",
        ):
            call = build_launch_call(
                RoleLaunch(
                    agent_id=str(uuid.uuid4()),
                    request_id=request_id,
                    context=context,
                    folder=workspace["path"],
                    provider="claude",
                    session_options={},
                    prompt=prompt,
                    report_path=prepared["taskReportPath"],
                    handover_artifact={
                        "path": prepared["taskReportPath"] + ".handover.txt",
                        "sha256": "fixture",
                    },
                    settings_file=self.config.config_path,
                )
            )
            grants.append(call["agent"]["toolPolicy"])
        self.assertEqual(grants[0], grants[1])
        self.assertEqual([row["tool"] for row in grants[1]["preapproved"]], ["server_info"])
        public = _public_execution(
            {"choices": choices, "instructionComposition": prepared["instructionComposition"]}
        )
        self.assertNotIn("ONLY USER INSTRUCTIONS", json.dumps(public))
        self.assertTrue(handover["capsule"]["baselineReplaced"])
        self.assertIn("This taskless role has no task document to read.", handover["ownerHandover"])
        self.assertEqual(handover["host"]["arToolServer"], "agents-remember-task")
        self.assertEqual(handover["taskReportPath"], prepared["taskReportPath"])
        self.assertTrue(Path(workspace["scratchRoot"]).is_dir())
        self.assertTrue(Path(prepared["taskReportPath"]).is_relative_to(self.config.workspace_root))
        self.assertNotEqual(prepared["taskReportPath"], prepared["canonicalTaskReportPath"])
        self.assertEqual(
            Path(prepared["canonicalTaskReportPath"]).parent,
            self.config.workspace_root
            / ".agents-remember"
            / "reports"
            / "architect"
            / str(request_id)
            / "role-launch"
            / "architect",
        )

    def test_r71_collection_budget_and_nonleaf_retirement_preserve_durable_reports(self) -> None:
        execution = self.root / ".agents-remember" / "role-executions" / "request"
        source = execution / "role-reports"
        source.mkdir(parents=True)
        (execution / "scratch").mkdir()
        canonical = self.root / "durable"
        for index in range(2):
            (source / f"{index}.md").write_text("report")
        receipt = {
            "requestId": "request",
            "workspace": {
                "path": str(self.root),
                "taskReportAccessRoot": str(source),
                "taskReportRoot": str(canonical),
                "scratchRoot": str(execution / "scratch"),
            },
        }
        with (
            patch.object(role_reports, "MAX_COLLECTION_FILES", 1),
            self.assertRaisesRegex(role_reports.RoleReportCollectionError, "budget"),
        ):
            role_reports.close_execution_role_folders(receipt)
        self.assertTrue(source.is_dir())
        role_reports.close_execution_role_folders(receipt)
        self.assertFalse(execution.exists())
        self.assertEqual((canonical / "1.md").read_text(), "report")
        self.assertEqual(receipt["reportCollection"]["state"], "collected-closed")
        self.assertNotIn("reportCollectionFiles", receipt)
        # A recreated source is new evidence, even after an earlier terminal collection.
        source.mkdir(parents=True)
        (source / "later.md").write_text("later evidence")
        role_reports.collect_execution_reports(receipt)
        self.assertEqual((canonical / "later.md").read_text(), "later evidence")

    def test_r71_unknown_modes_refuse_and_fully_listed_empty_modes_remain_distinct(self) -> None:
        row = {"id": "codex", "models": [], "listingError": "still loading"}
        for stated in (None, "auto"):
            with (
                self.subTest(stated=stated),
                self.assertRaisesRegex(LaunchSelectionRefused, "codex.*still loading"),
            ):
                paseo_catalog._resolve_mode(
                    stated, {"source": "our settings"}, paseo_catalog._agent_row(row), "worker"
                )
        mode, choice = paseo_catalog._resolve_mode(
            None, {}, paseo_catalog._agent_row({"id": "codex", "models": [], "modes": []}), "worker"
        )
        self.assertIsNone(mode)
        self.assertIn("no named", choice["reason"])

    def test_r71_collection_reuses_unchanged_file_metadata_for_two_evidence_sizes(self) -> None:
        for size in (1024, 64 * 1024**2):
            local = self.root / f"reports-{size}"
            local.mkdir()
            with (local / "evidence.bin").open("wb") as stream:
                stream.truncate(size)
            receipt = {
                "workspace": {
                    "taskReportAccessRoot": str(local),
                    "taskReportRoot": str(self.root / f"canonical-{size}"),
                }
            }
            self.assertEqual(role_reports.collect_execution_reports(receipt)["copiedFiles"], 1)
            with patch.object(
                role_reports,
                "_file_digest",
                side_effect=AssertionError("unchanged bytes must not be read"),
            ):
                self.assertEqual(role_reports.collect_execution_reports(receipt)["copiedFiles"], 0)
            with patch.object(
                role_launch_receipts,
                "collect_execution_reports",
                side_effect=AssertionError("read must not collect"),
            ):
                self.assertEqual(
                    _public_execution(receipt)["reportCollection"]["state"], "collected"
                )

    def test_r71_refusals_name_exact_settings_and_host_profile_sources(self) -> None:
        for extra, words in [
            (
                {
                    "mode": "bad",
                    "fieldOrigins": {
                        "mode": {
                            "source": "user.json",
                            "key": "orchestration.roles.worker.mode",
                            "provider": "codex",
                        }
                    },
                },
                ("user.json", "orchestration.roles.worker.mode"),
            ),
            (
                {
                    "hostRole": {
                        "profile": {
                            "id": "profile-one",
                            "name": "worker",
                            "provider": "codex",
                            "modeId": "bad",
                        }
                    }
                },
                ("profile-one", "worker", "modeId"),
            ),
        ]:
            with self.subTest(extra=extra), self.assertRaises(LaunchSelectionRefused) as error:
                self.select({**self.role, **extra})
            for word in words:
                self.assertIn(word, str(error.exception))

    def test_r71_observed_mode_and_feature_mismatch_is_publicly_warned(self) -> None:
        receipt = {
            "choices": {
                "mode": {"value": "auto", "source": "our settings"},
                "featureValues": {"plan_mode": {"value": True, "source": "host role"}},
            }
        }
        _observe_launch_choices(receipt, {"mode": "auto", "featureValues": {"plan_mode": True}})
        self.assertNotIn("warning", _public_execution(receipt))
        _observe_launch_choices(
            receipt, {"mode": "full-access", "featureValues": {"plan_mode": False}}
        )
        self.assertIn("mode stated 'auto'", _public_execution(receipt)["warning"])
        self.assertIn("featureValues.plan_mode", receipt["warning"])

    def test_r71_each_role_grant_excludes_a_second_configured_ar_server(self) -> None:
        foreign = "agents-remember-foreign"
        harness_config = self.root / "harness-config.json"
        configured = {"mcpServers": {foreign: {"command": "never-started", "args": []}}}
        harness_config.write_text(json.dumps(configured))
        before = harness_config.read_bytes()
        # ROLE_REFS is keyed by plain str; the grant is built per LauncherRole,
        # in the catalogue's own role order.
        roles: tuple[LauncherRole, ...] = (
            "architect",
            "investigator",
            "orchestrator",
            "manager",
            "worker",
            "reviewer",
            "curator",
        )
        for role in roles:
            references = ROLE_REFS[role]
            documents = {
                key: resolved_document(self.config, ref, key) for key, ref in references.items()
            }
            context = RoleLaunchContext(
                role,
                documents.get("sprintDocumentRef"),
                documents.get("masterDocumentRef"),
                documents.get("taskDocumentRef"),
                documents.get("taskDocumentRef")
                or documents.get("masterDocumentRef")
                or documents.get("sprintDocumentRef"),
            )
            for provider in ("claude", "codex", "pi"):
                with self.subTest(role=role, provider=provider):
                    call = build_launch_call(
                        RoleLaunch(
                            agent_id=str(uuid.uuid4()),
                            request_id=uuid.uuid4(),
                            context=context,
                            folder=str(self.config.workspace_root),
                            provider=provider,
                            session_options={},
                            prompt="Read your own task file.",
                            report_path=str(self.root / "report.md"),
                            handover_artifact={
                                "path": str(self.root / "handover"),
                                "sha256": "fixture",
                            },
                            settings_file=self.config.config_path,
                        )
                    )
                    agent = call["agent"]
                    self.assertEqual(set(agent["mcpServers"]), {"agents-remember-task"})
                    grants = agent.get("toolPolicy", {}).get("preapproved", [])
                    expected = (
                        [{"kind": "mcp", "server": "agents-remember-task", "tool": "server_info"}]
                        if provider == "claude"
                        else []
                    )
                    self.assertEqual(grants, expected)
                    self.assertNotIn(foreign, json.dumps(grants))
                    self.assertNotIn("*", json.dumps(grants))
        self.assertEqual(harness_config.read_bytes(), before)
        self.assertIn(foreign, json.loads(before)["mcpServers"])

    def test_r71_completed_cleanup_collects_before_the_same_role_folder_removal(self) -> None:
        group = self.root / "completed-group"
        local = group / "role-reports" / "worker" / "request"
        local.mkdir(parents=True)
        (local / "report.md").write_text("Completed report with retained evidence")
        canonical = self.root / "task" / "notes" / "reports" / "worker" / "request" / "report.md"
        contract = cast(
            WorktreeContract,
            SimpleNamespace(
                kind="leaf",
                worktree_group=group,
                task_root=self.root / "task",
                coordination_root=self.config.coordination_root,
                code_worktree=group / "code",
                code_work_branch="ar/fixture",
            ),
        )
        events = []

        def collect(contract, *, dry_run):
            events.append("collect")
            return collect_group_reports(contract, dry_run=dry_run)

        def remove(contract, *, dry_run):
            self.assertEqual(events, ["collect"])
            self.assertEqual(canonical.read_text(), "Completed report with retained evidence")
            events.append("remove")
            return remove_group_role_folders(contract, dry_run=dry_run)

        services = SimpleNamespace(
            provider_lifecycle=SimpleNamespace(
                remove_tree=lambda path, **_kwargs: {"path": str(path), "removed": False}
            )
        )
        with (
            patch.object(worktree_cleanup, "collect_group_reports", side_effect=collect),
            patch.object(worktree_cleanup, "remove_group_role_folders", side_effect=remove),
            patch.object(worktree_cleanup, "_terminal_mutation_authority", return_value=object()),
            patch.object(worktree_cleanup, "_removed_worktrees", return_value={}),
            patch.object(worktree_cleanup, "_deleted_branches", return_value={}),
            patch.object(worktree_cleanup, "remove_drift_snapshot", return_value={"removed": True}),
            patch.object(worktree_cleanup, "worktree_services", return_value=services),
        ):
            worktree_cleanup._cleanup_terminal_outputs(
                WorktreeArgs(dry_run=False, teardown_providers=False),
                contract,
                cast(TerminalPreflight, SimpleNamespace(branches={})),
            )
        self.assertEqual(events, ["collect", "remove"])
        self.assertFalse((group / "role-reports").exists())
        self.assertEqual(canonical.read_text(), "Completed report with retained evidence")

    def test_r71_public_reader_has_no_caller_path_or_mutating_operation(self) -> None:
        server = FastMCP("fixture")
        _register_identity_tools(
            server,
            self.config,
            ServingBuildPayload(version="fixture", bootedAt="2026-10-06T00:00:00Z"),
        )
        listed = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        for name in ("server_info",):
            tool = listed[name]
            self.assertEqual(tool.inputSchema.get("properties", {}), {})
            assert tool.annotations is not None
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
        self.assertEqual(set(listed), {"server_info"})

    def test_r71_layer_binding_keeps_codex_auto_from_becoming_claude_auto(self) -> None:
        user = user_agentic_settings_path()
        code = self.config.workspace_root / "demo"
        local = code / "system" / "settings.json"
        user.parent.mkdir(parents=True, exist_ok=True)
        local.parent.mkdir(parents=True)
        self.write_user_settings(
            {
                "roles": {
                    "worker": {
                        "harness": "codex",
                        "mode": "auto",
                        "model": "gpt-a",
                        "effort": "high",
                        "featureValues": {"plan_mode": True},
                        "notes": "User-role instructions",
                    }
                }
            }
        )
        local.write_text(
            json.dumps({"orchestration": {"roles": {"worker": {"harness": "claude"}}}})
        )
        settings_path = self.root / "layer-reader.json"
        settings_path.write_text(
            json.dumps(
                {
                    "coordinationRoot": str(self.config.coordination_root),
                    "workspaceRoot": str(self.config.workspace_root),
                    "repositories": {"demo": {"path": str(code)}},
                }
            )
        )
        config = load_config(settings_path)
        context = cast(
            Any,
            SimpleNamespace(
                role="worker",
                effective_task=SimpleNamespace(ref=SimpleNamespace(repository="demo")),
            ),
        )
        role, order = role_launch_preparation._role_defaults(config, context)
        # Discovery remains the test's offline host catalog, never a live process.
        agent_id, options = resolve_agent_selection(self.config, role, order, None)
        self.assertEqual((agent_id, options["mode"]), ("claude", "default"))
        self.assertNotIn("model", options)
        self.assertNotIn("effort", options)
        self.assertEqual(options["featureValues"], {})
        self.assertEqual(options["choices"]["notes"]["value"], "User-role instructions")
        ignored = [
            row
            for row in options["choices"]["omitted"]
            if row.get("key") == "mode" and row.get("origin")
        ]
        self.assertEqual(ignored[0]["origin"]["provider"], "codex")
        self.assertEqual(ignored[0]["origin"]["source"], str(user))
        self.assertEqual(ignored[0]["origin"]["key"], "orchestration.roles.worker.mode")
        self.assertEqual(options["choices"]["mode"]["source"], "least permissive mode")
        # A higher user-level choice binds to its own effective provider, without a new key.
        raw = json.loads(user.read_text())
        raw["orchestration"]["rolesPerLevel"] = {
            "leaf": {"worker": {"harness": "claude", "mode": "auto"}}
        }
        user.write_text(json.dumps(raw))
        role, order = role_launch_preparation._role_defaults(config, context)
        self.assertEqual(resolve_agent_selection(self.config, role, order, None)[1]["mode"], "auto")

    def test_r71_per_level_origins_bind_at_the_authored_layer_and_keep_provider_controls(
        self,
    ) -> None:
        user = user_agentic_settings_path()
        code = self.config.workspace_root / "demo"
        local = code / "system/settings.json"
        user.parent.mkdir(parents=True, exist_ok=True)
        local.parent.mkdir(parents=True, exist_ok=True)
        settings_path = self.root / "level-origin-runtime.json"
        settings_path.write_text(
            json.dumps(
                {
                    "coordinationRoot": str(self.config.coordination_root),
                    "workspaceRoot": str(self.config.workspace_root),
                    "repositories": {"demo": {}},
                }
            )
        )
        config = load_config(settings_path)
        context = cast(
            Any,
            SimpleNamespace(
                role="worker",
                effective_task=SimpleNamespace(ref=SimpleNamespace(repository="demo")),
            ),
        )
        cases = [
            (
                "spawn-cross-provider",
                {"spawn": {"harness": "codex"}},
                {"spawn": {"harness": "claude"}},
                {},
                "claude",
                "default",
            ),
            (
                "spawn-same-provider",
                {"spawn": {"harness": "codex"}},
                {"spawn": {"harness": "codex"}},
                {},
                "codex",
                "auto",
            ),
            (
                "role-inheritance",
                {"roles": {"worker": {"harness": "codex"}}},
                {"roles": {"worker": {"harness": "claude"}}},
                {},
                "claude",
                "default",
            ),
            (
                "explicit-level-provider",
                {"roles": {"worker": {"harness": "claude"}}},
                {"spawn": {"harness": "claude"}},
                {"harness": "codex"},
                "codex",
                "auto",
            ),
            (
                "later-level-model",
                {"roles": {"worker": {"harness": "codex"}}},
                {
                    "roles": {"worker": {"harness": "claude"}},
                    "rolesPerLevel": {"leaf": {"worker": {"model": "opus"}}},
                },
                {},
                "claude",
                "default",
            ),
        ]
        for name, global_values, repo_values, own_values, expected_agent, expected_mode in cases:
            with self.subTest(case=name):
                authored_values = {
                    **global_values,
                    "rolesPerLevel": {"leaf": {"worker": {"mode": "auto", **own_values}}},
                }
                self.write_user_settings(authored_values)
                local.write_text(json.dumps({"orchestration": repo_values}))
                with patch.object(
                    role_launch_preparation,
                    "read_host_role",
                    return_value={"profile": {}, "state": "no-match"},
                ):
                    role, order = role_launch_preparation._role_defaults(config, context)
                agent, options = resolve_agent_selection(self.config, role, order, None)
                self.assertEqual((agent, options["mode"]), (expected_agent, expected_mode))
                self.assertEqual(role["fieldOrigins"]["mode"]["provider"], "codex")
                self.assertEqual(role["fieldOrigins"]["mode"]["source"], str(user))
                self.assertEqual(
                    role["fieldOrigins"]["mode"]["key"],
                    "orchestration.rolesPerLevel.leaf.worker.mode",
                )
                if name == "later-level-model":
                    self.assertEqual(role["fieldOrigins"]["model"]["provider"], "claude")
                    self.assertEqual(options["model"], "opus")
                if expected_agent == "claude":
                    self.assertEqual(options["choices"]["mode"]["source"], "least permissive mode")
                    self.assertTrue(
                        any(
                            row.get("origin", {}).get("provider") == "codex"
                            and row.get("key") == "mode"
                            for row in options["choices"]["omitted"]
                        )
                    )

    def test_r71_collection_cache_churn_reaches_a_bounded_fixed_point_and_retires(self) -> None:
        for turns in (4, 12):
            with self.subTest(turns=turns):
                source = self.root / f"churn-{turns}"
                source.mkdir()
                canonical = self.root / f"collected-{turns}"
                receipt = {
                    "workspace": {
                        "taskReportAccessRoot": str(source),
                        "taskReportRoot": str(canonical),
                    }
                }
                with patch.object(role_reports, "MAX_COLLECTION_FILES", 4):
                    for index in range(turns):
                        path = source / f"{index}.md"
                        path.write_text("report")
                        self.assertEqual(
                            role_reports.collect_execution_reports(receipt)["files"], 1
                        )
                        self.assertEqual(len(receipt["reportCollectionFiles"]), 1)
                        with patch.object(
                            role_reports,
                            "_file_digest",
                            side_effect=AssertionError("reuse must skip unchanged bytes"),
                        ):
                            self.assertEqual(
                                role_reports.collect_execution_reports(receipt)["copiedFiles"], 0
                            )
                        path.unlink()
                    self.assertEqual(role_reports.collect_execution_reports(receipt)["files"], 0)
                self.assertEqual(receipt["reportCollectionFiles"], {})
                self.assertEqual(len(list(canonical.iterdir())), turns)
                source.rmdir()
                role_reports.collect_execution_reports(receipt)
                self.assertNotIn("reportCollectionFiles", receipt)
                self.assertTrue((canonical / "0.md").is_file())
                stored = {
                    "schema": role_launch_receipts.RECEIPT_SCHEMA,
                    "requestId": f"churn-{turns}",
                    "reportCollectionFiles": {"obsolete.md": {}},
                    "reportCollection": {"state": "collected-closed"},
                }
                path = self.root / f"receipt-{turns}" / "current.json"
                role_launch_receipts._write_receipt(path, stored)
                role_launch_receipts._archive_receipt(path, stored)
                archived = json.loads((path.parent / "history" / f"churn-{turns}.json").read_text())
                self.assertNotIn("reportCollectionFiles", archived)
                self.assertEqual(archived["reportCollection"]["state"], "collected-closed")

    def test_r71_host_custom_provider_requires_no_ar_terminal_declaration(self) -> None:
        user = user_agentic_settings_path()
        user.parent.mkdir(parents=True, exist_ok=True)
        self.write_user_settings({"roles": {"worker": {"harness": "hermes"}}})
        settings = load_agentic_settings(self.config.coordination_root)
        self.assertEqual(settings.role_knobs("worker").harness, "hermes")
        self.assertIsNone(settings.find_harness("hermes"))
        adapter, refusal = _resolve_spawn_harness(settings, "hermes", lambda _command: None)
        self.assertIsNone(adapter)
        assert refusal is not None
        self.assertEqual(refusal["status"], "harness-unknown")
        catalog = copy.deepcopy(CATALOG)
        catalog["providers"].append({"id": "hermes", "label": "Hermes", "models": [], "modes": []})
        forget_launcher_catalogs()
        self.bridge(catalog)
        self.assertEqual(self.select({**defaults("hermes"), "role": "worker"})[0], "hermes")
        forget_launcher_catalogs()
        self.bridge(CATALOG)
        with self.assertRaisesRegex(LaunchSelectionRefused, "hermes"):
            self.select({**defaults("hermes"), "role": "worker"})
