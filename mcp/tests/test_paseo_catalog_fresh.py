"""R214: fresh discovery, durable per-part fallback, and direct Paseo names."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_paseo_catalog as fixtures
from agents_remember.cli import paseo_catalog, paseo_catalog_store
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, require_bridge_runtime
from agents_remember.cli.paseo_catalog_store import catalog_store_path, merge_catalog_parts
from agents_remember.models.role_launcher import RoleAgentOverride


def catalog() -> dict:
    result = copy.deepcopy(fixtures.CATALOG)
    result["readAt"] = "2026-10-26T15:00:00+00:00"
    provider = result["providers"][0]
    provider["acceptsToolServers"] = True
    provider["modes"] = [{"id": "auto", "label": "Automatic"}]
    model = provider["models"][0]
    model["serviceTiers"] = [{"id": "priority", "label": "Fast"}]
    model["features"] = [
        {"id": "service_tier", "label": "Speed", "type": "enum", "options": model["serviceTiers"]}
    ]
    return result


def sized_catalog(size: int) -> dict:
    seed = catalog()["providers"][0]
    providers = []
    for provider_id in range(size):
        provider = copy.deepcopy(seed)
        provider["id"] = f"provider-{provider_id}"
        provider["models"] = [
            {**copy.deepcopy(seed["models"][0]), "id": f"model-{i}"} for i in range(size)
        ]
        providers.append(provider)
    return {"readAt": "2026-10-26T15:00:00+00:00", "providers": providers}


def _ids(rows: list[dict] | None) -> list[str]:
    return [row["id"] for row in rows or []]


def _choices(agents) -> list[tuple]:
    """Every choice an answer offers: each provider's modes and tool-server support, and each
    model's efforts, speed tiers and features."""
    return [
        (
            agent["id"],
            _ids(agent.get("modes")),
            agent.get("acceptsToolServers"),
            [
                (
                    model["id"],
                    _ids(model.get("efforts")),
                    _ids(model.get("serviceTiers")),
                    _ids(model.get("features")),
                )
                for model in agent["models"]
            ],
        )
        for agent in agents
    ]


class FreshCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = fixtures.runtime_config(self.root)
        self.config.workspace_root.mkdir()
        self.config.coordination_root.mkdir()

    def read(self, *replies):
        with mock.patch.object(paseo_catalog, "bridge_call", fixtures.FakeBridge(*replies)):
            return paseo_catalog.launcher_catalog(self.config)

    def test_next_view_asks_fresh_and_replaces_changed_choices(self) -> None:
        first = catalog()
        second = catalog()
        second["providers"][0]["models"][0]["label"] = "Changed now"
        bridge = fixtures.FakeBridge(first, second)
        with mock.patch.object(paseo_catalog, "bridge_call", bridge):
            a = paseo_catalog.launcher_options(
                self.config, fixtures.defaults(), fixtures.HARNESS_ORDER
            )
            b = paseo_catalog.launcher_options(
                self.config, fixtures.defaults(), fixtures.HARNESS_ORDER
            )
        self.assertEqual(len(bridge.calls), 2)
        self.assertNotEqual(
            a["agents"][0]["models"][0]["label"], b["agents"][0]["models"][0]["label"]
        )
        self.assertEqual(b["catalogDiscovery"]["source"], "fresh")

    def test_whole_failure_survives_new_process_with_saved_timestamp(self) -> None:
        first = self.read(catalog())
        script = """import json,sys
from pathlib import Path
from unittest import mock
from test_paseo_catalog import runtime_config
from agents_remember.cli import paseo_catalog, paseo_catalog_store
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, require_bridge_runtime
config=runtime_config(Path(sys.argv[1]))
with mock.patch.object(paseo_catalog,'bridge_call',side_effect=PaseoBridgeFailure('paseo_bridge_timeout','timed out')):
    result=paseo_catalog.launcher_catalog(config)
print(json.dumps({'state':result.discovery,'agents':result.agents}))
"""
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                (str(Path(__file__).parent), os.environ.get("PYTHONPATH", ""))
            ),
        }
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.root)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        after = json.loads(result.stdout)
        self.assertEqual(after["state"]["source"], "saved")
        self.assertEqual(after["state"]["readAt"], first.discovery["readAt"])
        self.assertEqual(
            after["agents"][0]["models"][0]["serviceTiers"],
            first.agents[0]["models"][0]["serviceTiers"],
        )

    def test_partial_timeout_retains_only_saved_part_and_never_saves_empty(self) -> None:
        self.read(catalog())
        broken = catalog()
        broken["readAt"] = "2026-10-26T16:00:00+00:00"
        model = broken["providers"][0]["models"][0]
        model.update(
            serviceTiers=[], serviceTierError="timed out", features=[], featureError="timed out"
        )
        result = self.read(broken)
        state = result.agents[0]["models"][0]["discovery"]
        self.assertEqual(state["efforts"]["source"], "fresh")
        self.assertEqual(state["serviceTiers"]["source"], "saved")
        self.assertEqual(state["serviceTiers"]["readAt"], "2026-10-26T15:00:00+00:00")
        self.assertEqual(result.agents[0]["models"][0]["serviceTiers"][0]["id"], "priority")
        saved = json.loads(catalog_store_path(require_bridge_runtime(self.config).home).read_text())
        key = json.dumps(("provider", "codex", "model", "gpt-a", "serviceTiers"))
        self.assertEqual(saved["parts"][key]["value"][0]["id"], "priority")
        for field, error_key, on_model in (
            ("models", "listingError", False),
            ("modes", "modeError", False),
            ("acceptsToolServers", "toolServerError", False),
            ("efforts", "effortError", True),
        ):
            with self.subTest(unanswered=field):
                partial = catalog()
                row = partial["providers"][0]
                if on_model:
                    row = row["models"][0]
                row.pop(field)
                row[error_key] = "timed out"
                observed = self.read(partial).agents[0]
                if on_model:
                    observed = observed["models"][0]
                self.assertEqual(observed["discovery"][field]["source"], "saved")
                self.assertTrue(observed[field])

    def test_no_answer_leaves_all_configured_choices_unchecked(self) -> None:
        values = {
            **fixtures.defaults("custom", "unknown-model", "unknown-effort"),
            "mode": "unknown-mode",
            "serviceTier": "unknown-tier",
        }
        with mock.patch.object(
            paseo_catalog,
            "bridge_call",
            side_effect=PaseoBridgeFailure("paseo_bridge_timeout", "timed out"),
        ):
            agent, options = paseo_catalog.resolve_agent_selection(self.config, values, (), None)
            self.assertTrue(paseo_catalog.provider_accepts_tool_servers(self.config, agent))
        self.assertEqual(agent, "custom")
        self.assertEqual(
            {key: options[key] for key in ("model", "effort", "mode", "serviceTier")},
            {
                "model": "unknown-model",
                "effort": "unknown-effort",
                "mode": "unknown-mode",
                "serviceTier": "unknown-tier",
            },
        )
        partial = catalog()
        partial["providers"][0]["models"][0].pop("features")
        partial["providers"][0]["models"][0].pop("serviceTiers")
        with mock.patch.object(paseo_catalog, "bridge_call", return_value=partial):
            _agent, options = paseo_catalog.resolve_agent_selection(
                self.config,
                {**fixtures.defaults("codex", "gpt-a", "high"), "serviceTier": "new-tier"},
                (),
                None,
            )
        self.assertEqual(options["serviceTier"], "new-tier")

    def test_names_match_ids_or_labels_without_case_and_conflicts_use_ids(self) -> None:
        with mock.patch.object(paseo_catalog, "bridge_call", return_value=catalog()):
            values = {
                **fixtures.defaults("CODEX", "GPT A", "HIGH"),
                "mode": "automatic",
                "serviceTier": "fast",
                "featureValues": {"service_tier": "PRIORITY"},
            }
            agent, options = paseo_catalog.resolve_agent_selection(self.config, values, (), None)
            self.assertEqual(
                (
                    agent,
                    options["model"],
                    options["effort"],
                    options["mode"],
                    options["serviceTier"],
                ),
                ("codex", "gpt-a", "high", "auto", "priority"),
            )
            with self.assertRaisesRegex(paseo_catalog.LaunchSelectionRefused, "not offered"):
                paseo_catalog.resolve_agent_selection(
                    self.config,
                    {**values, "featureValues": {"service_tier": "not-a-tier"}},
                    (),
                    None,
                )
            unknown = copy.deepcopy(catalog())
            unknown["providers"][0]["models"][0]["serviceTiers"].append(
                {"id": "standard", "label": "Regular"}
            )
            unknown["providers"][0]["models"][0]["features"][0]["options"].append(
                {"id": "standard", "label": "Regular"}
            )
        with (
            mock.patch.object(paseo_catalog, "bridge_call", return_value=unknown),
            self.assertRaisesRegex(paseo_catalog.LaunchSelectionRefused, "different Paseo"),
        ):
            paseo_catalog.resolve_agent_selection(
                self.config, {**values, "featureValues": {"service_tier": "regular"}}, (), None
            )

    def test_request_scope_asks_once_then_discards_its_answer(self) -> None:
        bridge = fixtures.FakeBridge(catalog())
        with mock.patch.object(paseo_catalog, "bridge_call", bridge):
            for _ in range(2):
                with paseo_catalog.launcher_catalog_question():
                    first = paseo_catalog.launcher_catalog(self.config)
                    with paseo_catalog.launcher_catalog_question():
                        second = paseo_catalog.launcher_catalog(self.config)
                    self.assertIs(first, second)
            self.assertEqual(len(bridge.calls), 2)
            paseo_catalog.launcher_catalog(self.config)
            self.assertEqual(len(bridge.calls), 3)

    def test_successful_inventory_reclaims_parts_at_two_sizes_but_failure_retains_them(
        self,
    ) -> None:
        measured = []
        path = catalog_store_path(require_bridge_runtime(self.config).home)
        for size in (2, 5):
            with self.subTest(size=size):
                scope = ("reclamation-test", str(size))
                initial = sized_catalog(size)
                merge_catalog_parts(path, scope, initial, None)
                full = path.read_bytes()
                for _ in range(3):
                    merge_catalog_parts(path, scope, initial, None)
                    self.assertEqual(path.read_bytes(), full)
                merge_catalog_parts(path, scope, None, "whole inventory timed out")
                self.assertEqual(path.read_bytes(), full)
                before = json.loads(full)["parts"]
                saved_models = {
                    key: value for key, value in before.items() if "model" in json.loads(key)
                }
                failed = {
                    "readAt": initial["readAt"],
                    "providers": [
                        {"id": row["id"], "listingError": "model inventory timed out"}
                        for row in initial["providers"]
                    ],
                }
                merge_catalog_parts(path, scope, failed, None)
                partial = json.loads(path.read_bytes())["parts"]
                self.assertEqual({key: partial[key] for key in saved_models}, saved_models)
                smaller = sized_catalog(1)
                smaller["readAt"] = "2026-10-26T16:00:00+00:00"
                merge_catalog_parts(path, scope, smaller, None)
                shrunk = path.read_bytes()
                keys = [json.loads(key) for key in json.loads(shrunk)["parts"]]
                self.assertTrue(all(key[1] == "provider-0" for key in keys if key[0] == "provider"))
                self.assertTrue(all(key[3] == "model-0" for key in keys if "model" in key))
                self.assertLess(len(shrunk), len(full))
                for _ in range(3):
                    merge_catalog_parts(path, scope, smaller, None)
                    self.assertEqual(path.read_bytes(), shrunk)
                merge_catalog_parts(
                    path, scope, {"readAt": "2026-10-26T17:00:00+00:00", "providers": []}, None
                )
                empty = path.read_bytes()
                self.assertEqual(list(json.loads(empty)["parts"]), [json.dumps(("providers",))])
                self.assertLess(len(empty), len(shrunk))
                merge_catalog_parts(path, scope, None, "inventory unavailable")
                self.assertEqual(path.read_bytes(), empty)
                measured.append((len(full), len(shrunk), len(empty)))
        self.assertGreater(measured[1][0], measured[0][0])
        self.assertEqual(measured[1][1:], measured[0][1:])
        print(f"catalog reclamation bytes for 2 and 5 sized inventories: {measured}")

    def test_start_questions_only_selected_features_after_precedence_and_labels(self) -> None:
        full = catalog()
        metadata = copy.deepcopy(full)
        for row in metadata["providers"][0]["models"]:
            row.pop("features", None)
            row.pop("serviceTiers", None)
        metadata["providers"][0]["models"][1]["label"] = "Chosen Model"
        features = full["providers"][0]["models"][0]["features"]
        tiers = full["providers"][0]["models"][0]["serviceTiers"]
        values = {
            **fixtures.defaults("codex", "gpt-a"),
            "hostRole": {
                "profile": {
                    "provider": "CODEX",
                    "model": "Chosen Model",
                    "featureValues": {"service_tier": "FAST"},
                }
            },
        }
        for override, selected in ((None, "gpt-b"), (RoleAgentOverride(modelId="GPT A"), "gpt-a")):
            with self.subTest(selected=selected):
                calls = []

                def answer(_config, _command, payload, calls=calls, selected=selected):
                    calls.append(payload)
                    if payload["mode"] == "metadata":
                        return metadata
                    self.assertEqual(
                        (payload["selectedProvider"], payload["selectedModel"]), ("codex", selected)
                    )
                    return {
                        "readAt": full["readAt"],
                        "selectedFeatures": {
                            "provider": "codex",
                            "model": selected,
                            "features": features,
                            "serviceTiers": tiers,
                        },
                    }

                with (
                    mock.patch.object(paseo_catalog, "bridge_call", side_effect=answer),
                    paseo_catalog.launcher_catalog_question(),
                ):
                    agent, options = paseo_catalog.resolve_agent_selection(
                        self.config, values, (), override
                    )
                    self.assertTrue(paseo_catalog.provider_accepts_tool_servers(self.config, agent))
                    repeated = paseo_catalog.resolve_agent_selection(
                        self.config, values, (), override
                    )
                    self.assertEqual(repeated, (agent, options))
                self.assertEqual(options["model"], selected)
                self.assertEqual([row["mode"] for row in calls], ["metadata", "selected"])

    def test_selected_feature_failure_uses_saved_part_or_passes_unknown_value(self) -> None:
        full = catalog()
        metadata = copy.deepcopy(full)
        for row in metadata["providers"][0]["models"]:
            row.pop("features", None)
            row.pop("serviceTiers", None)
        failure = PaseoBridgeFailure("paseo_bridge_timeout", "selected feature timeout")
        for seeded, tier in ((False, "new-tier"), (True, "FAST")):
            with self.subTest(seeded=seeded):
                if seeded:
                    self.read(full)
                bridge = fixtures.FakeBridge(metadata, failure)
                with mock.patch.object(paseo_catalog, "bridge_call", bridge):
                    _, options = paseo_catalog.resolve_agent_selection(
                        self.config,
                        {**fixtures.defaults("codex", "gpt-a"), "serviceTier": tier},
                        (),
                        None,
                    )
                self.assertEqual(options["serviceTier"], "priority" if seeded else "new-tier")
                self.assertEqual(
                    [payload["mode"] for _, payload in bridge.calls], ["metadata", "selected"]
                )

    def test_a_narrow_start_question_leaves_the_saved_answer_full_and_the_fallback_offers_it(
        self,
    ) -> None:
        """MIK-R215 rule 4: a start's narrow question never replaces the saved full answer with
        less, and with Paseo silent the launcher offers every choice the full answer had."""
        full = catalog()
        with mock.patch.object(paseo_catalog, "bridge_call", fixtures.FakeBridge(full)):
            answered = paseo_catalog.launcher_options(
                self.config, fixtures.defaults(), fixtures.HARNESS_ORDER
            )
        metadata = copy.deepcopy(full)
        metadata["readAt"] = "2026-10-26T16:00:00+00:00"
        for row in metadata["providers"][0]["models"]:
            row.pop("features", None)
            row.pop("serviceTiers", None)
        model = full["providers"][0]["models"][0]
        narrow = {
            "readAt": metadata["readAt"],
            "selectedFeatures": {
                "provider": "codex",
                "model": model["id"],
                "features": model["features"],
                "serviceTiers": model["serviceTiers"],
            },
        }
        start = fixtures.FakeBridge(metadata, narrow)
        with (
            mock.patch.object(paseo_catalog, "bridge_call", start),
            paseo_catalog.launcher_catalog_question(),
        ):
            paseo_catalog.resolve_agent_selection(
                self.config, fixtures.defaults("codex", "gpt-a"), (), None
            )
        self.assertEqual([payload["mode"] for _, payload in start.calls], ["metadata", "selected"])
        silent = PaseoBridgeFailure("paseo_daemon_unreachable", "Paseo does not answer")
        with mock.patch.object(paseo_catalog, "bridge_call", side_effect=silent):
            fallback = paseo_catalog.launcher_options(
                self.config, fixtures.defaults(), fixtures.HARNESS_ORDER
            )
        offered = _choices(answered["agents"])
        self.assertTrue(any(model[3] for agent in offered for model in agent[3]))
        self.assertEqual(_choices(fallback["agents"]), offered)
        self.assertEqual(
            _choices(paseo_catalog.saved_launcher_catalog(self.config).agents), offered
        )
        self.assertEqual(fallback["catalogDiscovery"]["source"], "saved")
        self.assertIs(fallback["roleDefaults"]["available"], True)

    def test_saved_reader_never_queries_or_creates_or_writes_files(self) -> None:
        path = catalog_store_path(require_bridge_runtime(self.config).home)
        with (
            mock.patch.object(
                paseo_catalog, "bridge_call", side_effect=AssertionError("network query")
            ),
            mock.patch.object(
                paseo_catalog_store, "exclusive_file_lock", side_effect=AssertionError("lock write")
            ),
            mock.patch.object(
                paseo_catalog_store,
                "atomic_write_text",
                side_effect=AssertionError("snapshot write"),
            ),
        ):
            unknown = paseo_catalog.saved_launcher_catalog(self.config)
        self.assertEqual(unknown.discovery["source"], "unknown")
        self.assertEqual(unknown.agents, ())
        self.assertFalse(path.parent.exists())
        self.read(catalog())
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        with (
            mock.patch.object(
                paseo_catalog, "bridge_call", side_effect=AssertionError("network query")
            ),
            mock.patch.object(
                paseo_catalog_store, "exclusive_file_lock", side_effect=AssertionError("lock write")
            ),
            mock.patch.object(
                paseo_catalog_store,
                "atomic_write_text",
                side_effect=AssertionError("snapshot write"),
            ),
        ):
            saved = paseo_catalog.saved_launcher_catalog(self.config)
        self.assertEqual(saved.discovery["source"], "saved")
        self.assertEqual(saved.origin, "paseo:saved")
        self.assertEqual(saved.discovery["readAt"], catalog()["readAt"])
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        bridge = fixtures.FakeBridge(catalog())
        with mock.patch.object(paseo_catalog, "bridge_call", bridge):
            paseo_catalog.resolve_agent_selection(
                self.config, fixtures.defaults("codex", "gpt-a"), (), None, catalog=saved
            )
        self.assertEqual(
            len(bridge.calls),
            1,
            "a saved-only snapshot cannot admit a start without a fresh question",
        )

    def test_concurrent_processes_merge_disjoint_successful_parts(self) -> None:
        path = catalog_store_path(require_bridge_runtime(self.config).home)
        scope = paseo_catalog.paseo_catalog_scope(self.config)
        a = catalog()
        a["providers"][0].pop("modes")
        b = {
            "readAt": "2026-10-26T16:00:00+00:00",
            "providers": [
                {
                    "id": "codex",
                    "modes": [{"id": "auto", "label": "Automatic"}],
                    "listingError": "timed out",
                }
            ],
        }
        script = "import json,sys;from pathlib import Path;from agents_remember.cli.paseo_catalog_store import merge_catalog_parts;merge_catalog_parts(Path(sys.argv[1]),tuple(json.loads(sys.argv[2])),json.loads(sys.argv[3]),None)"
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(path), json.dumps(scope), json.dumps(reply)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for reply in (a, b)
        ]
        for process in processes:
            out, error = process.communicate(timeout=60)
            self.assertEqual(process.returncode, 0, (out, error))
        result = merge_catalog_parts(path, scope, None, "offline")
        provider = result["providers"][0]
        self.assertEqual(provider["modes"][0]["id"], "auto")
        self.assertEqual(provider["models"][0]["serviceTiers"][0]["id"], "priority")
        self.assertTrue(
            all(
                state["source"] == "saved"
                for state in (provider["discovery"]["models"], provider["discovery"]["modes"])
            )
        )
