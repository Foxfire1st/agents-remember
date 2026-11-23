"""R214: bridge discovery distinguishes unavailable parts from empty answers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agents_remember.cli import paseo_bridge
from test_paseo_bridge_agents import AGENT_ID, FAKE_CLIENT_ROOT, AgentCommandScriptTests

_MODEL_COUNTS = {
    "hermes": 232,
    "opencode": 44,
    "antigravity": 23,
    "antigravity-acp": 23,
    "claude": 18,
    "codex": 10,
}

_PROVIDER_STUB = """    providers: {
      waitForReady: async () => ({ entries: scenario.entries ?? [] }),
      listFeatures: async () => ({ features: [] }),
      listModels: async () => ({ models: [] })
    },"""
_PROVIDER_DISCOVERY = """    providers: {
      refresh: async () => { record({ via: 'refresh' }) },
      waitForReady: async () => {
        record({ via: 'providers' })
        if (scenario.discoveryError) throw new Error(scenario.discoveryError)
        if (scenario.hangDiscovery) return new Promise(() => {})
        return scenario.providerReply ?? { entries: scenario.entries ?? [] }
      },
      snapshot: async () => scenario.hangSnapshot
        ? new Promise(() => {}) : scenario.providerReply ?? { entries: scenario.entries ?? [] },
      listModels: async (provider) => {
        record({ via: 'models', provider })
        if (scenario.hangModels?.includes(provider)) return new Promise(() => {})
        return scenario.modelReplies?.[provider] ?? { models: [] }
      },
      listFeatures: async ({ provider }) => {
        record({ via: 'features', provider })
        if (scenario.hangFeatures?.includes(provider)) return new Promise(() => {})
        return scenario.featureReplies?.[provider] ?? { features: [] }
      }
    },"""


def _provider(name: str = "codex", **extra: object) -> dict[str, object]:
    return {"provider": name, "enabled": True, "status": "ready", "modes": [], **extra}


def _model(name: str = "gpt") -> dict[str, object]:
    return {"id": name, "thinkingOptions": []}


def _six_provider_scenario() -> dict[str, object]:
    return {
        "entries": [_provider(provider) for provider in _MODEL_COUNTS],
        "modelReplies": {
            provider: {"models": [_model(f"{provider}-{index}") for index in range(count)]}
            for provider, count in _MODEL_COUNTS.items()
        },
    }


class CatalogFreshnessBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = AgentCommandScriptTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        package = self.fixture.root / "prefix/node_modules/@getpaseo/client/dist/index.js"
        self.assertEqual(FAKE_CLIENT_ROOT.count(_PROVIDER_STUB), 1)
        stub = FAKE_CLIENT_ROOT.replace(_PROVIDER_STUB, _PROVIDER_DISCOVERY)
        stub = stub.replace(
            "if (scenario.configError)",
            "if (scenario.hangConfig) return new Promise(() => {}); if (scenario.configError)",
        )
        package.write_text(stub)

    def test_successful_empty_is_explicit_and_every_question_reaches_paseo(self) -> None:
        first = self.fixture.call("catalog", {}, entries=[_provider()])
        self.assertEqual(first["providers"][0]["models"], [])
        self.assertEqual(first["providers"][0]["modes"], [])
        self.assertTrue(first["providers"][0]["acceptsToolServers"])
        self.assertIn("readAt", first)
        self.assertFalse(any(row["via"] == "refresh" for row in self.fixture.recorded()))
        second = self.fixture.call("catalog", {"refresh": True}, entries=[])
        self.assertEqual(second["providers"], [])
        self.assertEqual([row["via"] for row in self.fixture.recorded()], ["refresh", "providers"])

    def test_failed_parts_are_omitted_while_the_other_parts_survive(self) -> None:
        reply = self.fixture.call(
            "catalog",
            {},
            entries=[_provider("failed-models"), _provider("partial", modes=None)],
            modelReplies={
                "failed-models": {"error": "provider failed"},
                "partial": {"models": [{"id": "unknown-effort"}, _model("known-effort")]},
            },
            configError="configuration unavailable",
        )
        failed, partial = reply["providers"]
        self.assertNotIn("models", failed)
        self.assertEqual(failed["listingError"], "provider failed")
        self.assertEqual(failed["modes"], [])
        self.assertNotIn("modes", partial)
        self.assertIn("modeError", partial)
        unknown, known = partial["models"]
        self.assertNotIn("efforts", unknown)
        self.assertIn("effortError", unknown)
        self.assertEqual(known["efforts"], [])
        for provider in reply["providers"]:
            self.assertNotIn("acceptsToolServers", provider)
            self.assertEqual(provider["toolServerError"], "configuration unavailable")

    def test_whole_discovery_failure_is_never_an_empty_success(self) -> None:
        for scenario in (
            {"discoveryError": "offline"},
            {"providerReply": {}},
            {"providerReply": {"entries": [{}]}},
        ):
            with self.subTest(scenario=scenario):
                refused = self.fixture.failure("catalog", {}, **scenario)
                self.assertTrue(str(refused))
        with patch.object(paseo_bridge, "_SCRIPT_DEADLINE_MS", 1000):
            refused = self.fixture.failure("catalog", {}, hangDiscovery=True, hangSnapshot=True)
            self.assertIn("time", str(refused))
            partial = self.fixture.call("catalog", {}, entries=[_provider()], hangDiscovery=True)
        self.assertEqual(partial["providers"][0]["modes"], [])
        self.assertEqual(partial["providers"][0]["models"], [])
        # Snapshot answered with a ready provider, so the subsequent empty listModels is valid.

    def test_model_timeout_leaves_other_providers_available(self) -> None:
        with patch.object(paseo_bridge, "_SCRIPT_DEADLINE_MS", 1000):
            reply = self.fixture.call(
                "catalog",
                {},
                entries=[_provider("slow-models"), _provider("codex")],
                hangConfig=True,
                hangModels=["slow-models"],
                modelReplies={"codex": {"models": [_model("gpt")]}},
            )
        slow_models, codex = reply["providers"]
        self.assertNotIn("acceptsToolServers", codex)
        self.assertIn("timed out", codex["toolServerError"])
        self.assertNotIn("models", slow_models)
        self.assertIn("listingError", slow_models)
        self.assertEqual([(row["id"], row["efforts"]) for row in codex["models"]], [("gpt", [])])

    def test_metadata_reads_no_model_features(self) -> None:
        reply = self.fixture.call(
            "catalog",
            {"mode": "metadata"},
            entries=[_provider("codex"), _provider("another")],
            modelReplies={
                "codex": {"models": [_model("chosen"), _model("other")]},
                "another": {"models": [_model("unrelated")]},
            },
        )
        self.assertEqual(len(reply["providers"]), 2)
        self.assertFalse(any(row["via"] == "features" for row in self.fixture.recorded()))
        for provider in reply["providers"]:
            for model in provider["models"]:
                self.assertNotIn("features", model)
                self.assertNotIn("serviceTiers", model)
                self.assertNotIn("featureError", model)
                self.assertEqual(model["efforts"], [])

    def test_default_catalog_and_refresh_list_all_350_models_without_feature_queries(self) -> None:
        for request, first_calls in (
            ({}, ["providers"]),
            ({"refresh": True}, ["refresh", "providers"]),
        ):
            with self.subTest(request=request):
                reply = self.fixture.call("catalog", request, **_six_provider_scenario())
                counts = {row["id"]: len(row["models"]) for row in reply["providers"]}
                self.assertEqual(counts, _MODEL_COUNTS)
                calls = self.fixture.recorded()
                self.assertEqual(
                    [call["via"] for call in calls],
                    first_calls + ["models"] * len(_MODEL_COUNTS),
                )
                self.assertCountEqual(
                    (call["provider"] for call in calls if call["via"] == "models"),
                    _MODEL_COUNTS.keys(),
                )

    def test_selected_creation_keeps_other_models_out_of_feature_discovery(self) -> None:
        scenario = _six_provider_scenario()
        provider, model = "codex", "codex-9"
        features = [
            {
                "id": "service_tier",
                "type": "select",
                "options": [{"id": "priority", "label": "Fast"}],
            }
        ]
        selected = {"mode": "selected", "selectedProvider": provider, "selectedModel": model}
        catalog = self.fixture.call(
            "catalog",
            selected,
            **scenario,
            featureReplies={f"{provider}/{model}": {"features": features}},
        )
        self.assertEqual(catalog["selectedFeatures"]["features"], features)
        self.assertEqual(
            self.fixture.recorded(), [{"via": "features", "provider": f"{provider}/{model}"}]
        )

        launched = self.fixture.call(
            "agent-create",
            {
                "agentId": AGENT_ID,
                "idempotencyKey": "ar-role-launch:chosen-model",
                "workspaceId": "wks_fake",
                "provider": provider,
                "model": model,
                "title": "Worker",
                "labels": {},
                "featureValues": {"service_tier": "Fast"},
            },
            **scenario,
            features=features,
        )
        feature_request, creation = self.fixture.recorded()
        self.assertEqual(
            feature_request,
            {
                "via": "features",
                "options": {"provider": provider, "model": model, "cwd": "/work/folder"},
            },
        )
        self.assertEqual(creation["via"], "public")
        self.assertEqual(creation["options"]["config"]["provider"], f"{provider}/{model}")
        self.assertEqual(
            creation["options"]["config"]["featureValues"], {"service_tier": "priority"}
        )
        self.assertEqual(launched["agent"]["id"], AGENT_ID)

    def test_selected_reads_only_target_features_even_when_metadata_is_unavailable(self) -> None:
        request = {"mode": "selected", "selectedProvider": "codex", "selectedModel": "chosen"}
        offered = [
            {
                "id": "service_tier",
                "type": "select",
                "options": [{"id": "host-turbo", "label": "Fast"}],
            }
        ]
        reply = self.fixture.call(
            "catalog",
            request,
            discoveryError="metadata must not be requested",
            featureReplies={"codex/chosen": {"features": offered}},
        )
        self.assertNotIn("providers", reply)
        self.assertIn("readAt", reply)
        self.assertEqual(
            reply["selectedFeatures"],
            {
                "provider": "codex",
                "model": "chosen",
                "features": offered,
                "serviceTiers": [{"id": "host-turbo", "label": "Fast"}],
            },
        )
        self.assertEqual(self.fixture.recorded(), [{"via": "features", "provider": "codex/chosen"}])
        empty = self.fixture.call("catalog", request)["selectedFeatures"]
        self.assertEqual((empty["features"], empty["serviceTiers"]), ([], []))
        for scenario in (
            {"featureReplies": {"codex/chosen": {"error": "unavailable"}}},
            {"hangFeatures": ["codex/chosen"]},
        ):
            with (
                self.subTest(scenario=scenario),
                patch.object(paseo_bridge, "_SCRIPT_DEADLINE_MS", 1000),
            ):
                failed = self.fixture.call("catalog", request, **scenario)["selectedFeatures"]
                self.assertNotIn("features", failed)
                self.assertNotIn("serviceTiers", failed)
                self.assertIn("featureError", failed)
                self.assertEqual(
                    self.fixture.recorded(), [{"via": "features", "provider": "codex/chosen"}]
                )
