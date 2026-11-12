"""R214: bridge discovery distinguishes unavailable parts from empty answers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agents_remember.cli import paseo_bridge
from test_paseo_bridge_agents import FAKE_CLIENT_ROOT, AgentCommandScriptTests

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
                "partial": {
                    "models": [{"id": "unknown-effort"}, _model("empty"), _model("failed")]
                },
            },
            featureReplies={"partial/failed": {"error": "features unavailable"}},
            configError="configuration unavailable",
        )
        failed, partial = reply["providers"]
        self.assertNotIn("models", failed)
        self.assertEqual(failed["listingError"], "provider failed")
        self.assertEqual(failed["modes"], [])
        self.assertNotIn("modes", partial)
        self.assertIn("modeError", partial)
        unknown, empty, unavailable = partial["models"]
        self.assertNotIn("efforts", unknown)
        self.assertIn("effortError", unknown)
        self.assertEqual(empty["features"], [])
        self.assertEqual(empty["serviceTiers"], [])
        self.assertNotIn("features", unavailable)
        self.assertNotIn("serviceTiers", unavailable)
        self.assertEqual(unavailable["featureError"], "features unavailable")
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

    def test_model_and_feature_timeouts_do_not_claim_empty_offerings(self) -> None:
        with patch.object(paseo_bridge, "_SCRIPT_DEADLINE_MS", 1000):
            reply = self.fixture.call(
                "catalog",
                {},
                entries=[_provider("slow-models"), _provider("codex")],
                hangConfig=True,
                hangModels=["slow-models"],
                hangFeatures=["codex/slow"],
                modelReplies={"codex": {"models": [_model("slow"), _model("fast")]}},
            )
        slow_models, codex = reply["providers"]
        self.assertNotIn("acceptsToolServers", codex)
        self.assertIn("timed out", codex["toolServerError"])
        self.assertNotIn("models", slow_models)
        self.assertIn("listingError", slow_models)
        slow, fast = codex["models"]
        self.assertNotIn("features", slow)
        self.assertNotIn("serviceTiers", slow)
        self.assertIn("timed out", slow["featureError"])
        self.assertEqual(slow["efforts"], [])
        self.assertEqual(fast["features"], [])
        self.assertEqual(fast["serviceTiers"], [])

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
