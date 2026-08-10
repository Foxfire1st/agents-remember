from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.kernel.primitives.runtime_config import (
    load_config,
)
from agents_remember.models.providers import ProviderStatusResponse
from agents_remember.models.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.providers import current_state
from agents_remember.providers import status as provider_status
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_container_state_summary,
)
from test_config import settings_payload, write_json


class ProviderCurrentStateTests(unittest.TestCase):
    def test_docker_container_state_summary_reports_uptime(self) -> None:
        inspect_data = {
            "State": {
                "Status": "running",
                "Running": True,
                "StartedAt": "2026-05-28T09:30:00.123456789Z",
                "Health": {"Status": "healthy"},
            }
        }

        summary = docker_container_state_summary(inspect_data)

        self.assertEqual(summary["containerState"], "running")
        self.assertTrue(summary["running"])
        self.assertEqual(summary["health"], "healthy")
        self.assertIsInstance(summary["uptimeSeconds"], int)

    def test_current_state_is_current_truth_not_setup_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)

            written = current_state.write_current_provider_state(config, status)

            payload = written["state"]
            self.assertEqual(payload["state"], "ready")
            self.assertTrue(payload["ok"])
            self.assertNotIn("lastSetup", payload)
            self.assertEqual(
                written["path"],
                (
                    root
                    / "ar-coordination"
                    / "logs"
                    / "providers"
                    / "status"
                    / "workspace"
                    / "workspace"
                    / "current.json"
                ).as_posix(),
            )
            saved = json.loads(Path(written["path"]).read_text(encoding="utf-8"))
            memory_root = config.repositories["agents-remember"].memory_root
            assert memory_root is not None
            self.assertEqual(saved["providers"]["grepai-memory"]["watcherUp"], True)
            self.assertEqual(
                saved["providers"]["grepai-memory"]["targetRepos"],
                [
                    {
                        "repoId": "agents-remember",
                        "path": memory_root.as_posix(),
                    }
                ],
            )
            self.assertEqual(
                saved["providers"]["grepai-memory"]["resources"]["postgres"]["uptimeSeconds"],
                7200,
            )

    def test_current_state_reports_per_repo_cgc_degradation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            cgc = next(
                result for result in status["results"] if result["provider"] == "codegraphcontext"
            )
            cgc["ok"] = False
            cgc["results"][0]["ok"] = False
            cgc["results"][0]["process"]["alive"] = False
            cgc["results"][0]["process"]["containerState"] = {
                "containerState": "exited",
                "running": False,
                "startedAt": None,
                "uptimeSeconds": None,
                "health": None,
            }

            payload = current_state.build_current_provider_state(config, status)

            self.assertEqual(payload["state"], "degraded")
            cgc_state = payload["providers"]["codegraphcontext-code"]
            self.assertEqual(cgc_state["state"], "degraded")
            repo_state = cgc_state["resources"]["watchers"]["agents-remember"]
            self.assertFalse(repo_state["watcherUp"])
            self.assertEqual(repo_state["containerState"], "exited")
            self.assertEqual(repo_state["indexingState"], "unknown")

    def test_current_state_reports_grepai_no_workspace_as_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            grepai = next(result for result in status["results"] if result["provider"] == "grepai")
            # `grepai workspace status` exits 0 even with no workspace.
            grepai["watcher"]["workspaceStatus"] = {
                "returncode": 0,
                "stdout": "No workspaces configured.\n",
            }

            payload = current_state.build_current_provider_state(config, status)

            grepai_state = payload["providers"]["grepai-memory"]
            self.assertEqual(grepai_state["state"], "degraded")
            self.assertFalse(grepai_state["ok"])
            self.assertEqual(grepai_state["indexingState"], "noWorkspace")
            self.assertEqual(payload["state"], "degraded")

    def test_provider_status_reports_restart_recovery_for_grepai_no_workspace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            grepai = next(result for result in status["results"] if result["provider"] == "grepai")
            grepai["watcher"]["workspaceStatus"] = {
                "returncode": 0,
                "stdout": "No workspaces configured.\n",
            }

            with mock.patch.object(
                provider_status,
                "_watchers_status",
                return_value=status,
            ):
                packet = provider_status.provider_status_packet(config)
                diagnostics = provider_status.provider_diagnostics_packet(config)

            recovery = packet["providers"]["recoveryActions"][0]
            self.assertEqual(recovery["provider"], "grepai-memory")
            self.assertIn("provider_watchers(action='restart')", recovery["recoveryAction"])
            self.assertEqual(diagnostics["recoveryActions"], packet["providers"]["recoveryActions"])

    def test_current_state_ignores_disabled_providers_for_aggregate_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            status["enabled"]["grepai-memory"] = False
            status["results"] = [
                result for result in status["results"] if result["provider"] != "grepai"
            ]

            payload = current_state.build_current_provider_state(config, status)

            self.assertEqual(payload["state"], "ready")
            self.assertEqual(payload["providers"]["grepai-memory"]["state"], "disabled")
            self.assertEqual(
                payload["providers"]["grepai-memory"]["indexingState"],
                "disabled",
            )

    def test_current_state_uses_workflow_local_instance_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            payload = settings_payload(root)
            for provider in payload["providers"].values():
                provider["scope"] = "benchmark"
                provider["instanceId"] = "projects-benchmark"
            write_json(config_path, payload)
            config = load_config(config_path)

            written = current_state.write_current_provider_state(
                config,
                ready_status_payload(root),
            )

            self.assertEqual(
                written["path"],
                (
                    root
                    / "ar-coordination"
                    / "logs"
                    / "providers"
                    / "status"
                    / "benchmark"
                    / "projects-benchmark"
                    / "current.json"
                ).as_posix(),
            )
            self.assertEqual(written["state"]["instance"]["scope"], "benchmark")

    def test_provider_status_packet_writes_current_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)

            with mock.patch.object(
                provider_status,
                "_watchers_status",
                return_value=ready_status_payload(root),
            ):
                packet = provider_status.provider_status_packet(config)
                diagnostics = provider_status.provider_diagnostics_packet(config)

            self.assertEqual(packet["operation"], "provider_status")
            providers = packet["providers"]
            self.assertEqual(providers["state"], "ready")
            self.assertTrue(Path(providers["currentStateFile"]).exists())
            self.assertNotIn("currentState", providers)
            self.assertEqual(diagnostics["currentState"]["state"], "ready")
            self.assertNotIn("lastSetup", diagnostics["currentState"])

    def test_a_sampled_status_packet_still_validates_at_the_wire_boundary(self) -> None:
        """260731-EFA-L6 R10: metrics/indexState are MODEL FIELDS, not post-dump injections.

        Both keys used to be stamped onto the already-dumped ``ProviderStatusResponse``.
        That envelope is ``extra="forbid"`` and ``mcp/tools/base.py::_tool_payload``
        re-validates every tool payload against its model, so any run where the daemon HAD
        sampled a container row or recorded an index-state row raised ValidationError on
        the way to the wire -- `provider_status` returned an error instead of a status.

        The suite could not see it because the two halves were tested apart: the tests that
        call ``provider_status_packet`` never populated the metrics store, and the tests
        that populate the metrics store never go through the wire re-validation. This test
        is the intersection, which is where the bug lived.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)

            metrics_root = config.coordination_root / "logs" / "observer" / "providers"
            metrics_root.mkdir(parents=True, exist_ok=True)
            sample = {
                "schema": "ar-provider-metrics/v1",
                "sampledAt": "2026-07-31T09:00:00+00:00",
                "containers": [],
                "runningCount": 0,
            }
            index_row = {
                "schema": "ar-provider-index-state/v1",
                "sampledAt": "2026-07-31T09:00:01+00:00",
                "providerId": "codegraphcontext-code",
                "state": "staleIndex",
            }
            (metrics_root / "metrics-current.json").write_text(
                json.dumps(sample) + "\n", encoding="utf-8"
            )
            (metrics_root / "metrics.jsonl").write_text(
                json.dumps(sample) + "\n" + json.dumps(index_row) + "\n", encoding="utf-8"
            )

            with mock.patch.object(
                provider_status,
                "_watchers_status",
                return_value=ready_status_payload(root),
            ):
                packet = provider_status.provider_status_packet(config)

            # The sampled facts really did ride the packet ...
            self.assertEqual(packet["metrics"]["schema"], "ar-provider-metrics/v1")
            self.assertEqual(packet["indexState"][-1]["state"], "staleIndex")
            # ... and the packet is still inside its own contract. This is the assertion
            # that used to raise. The registry lookup is asserted separately from the
            # validation so the validated object keeps its concrete type: the registry is
            # a union over every tool response, and re-validating through it would say
            # nothing about `metrics`/`indexState` being real declared fields.
            self.assertIs(TOOL_RESPONSE_MODELS["provider_status"], ProviderStatusResponse)
            revalidated = ProviderStatusResponse.model_validate(packet)
            self.assertEqual(revalidated.metrics, packet["metrics"])
            self.assertEqual(revalidated.indexState, packet["indexState"])

    def test_an_unsampled_status_packet_omits_both_keys_entirely(self) -> None:
        # exclude_none keeps the empty case byte-identical to before the fields existed,
        # so declaring them changed no response anybody was already receiving.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            with mock.patch.object(
                provider_status,
                "_watchers_status",
                return_value=ready_status_payload(root),
            ):
                packet = provider_status.provider_status_packet(config)
            self.assertNotIn("metrics", packet)
            self.assertNotIn("indexState", packet)
            ProviderStatusResponse.model_validate(packet)

    def test_provider_status_summarizes_structured_cgc_last_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            cgc = next(
                result for result in status["results"] if result["provider"] == "codegraphcontext"
            )
            cgc["results"][0]["lastRefresh"] = {
                "returncode": 130,
                "durationSeconds": 397.447,
                "updatedAt": "2026-06-19T05:09:28Z",
            }

            with mock.patch.object(
                provider_status,
                "_watchers_status",
                return_value=status,
            ):
                packet = provider_status.provider_status_packet(config)

            cgc_item = next(
                item
                for item in packet["providers"]["items"]
                if item["id"] == "codegraphcontext-code"
            )
            watcher = cgc_item["watchers"][0]
            self.assertEqual(
                watcher["lastRefresh"],
                "2026-06-19T05:09:28Z returncode=130 durationSeconds=397.447",
            )

    def test_current_state_degrades_cgc_repo_with_empty_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            cgc = next(
                result for result in status["results"] if result["provider"] == "codegraphcontext"
            )
            # Containers and raw status stay green; only graph content is bad.
            cgc["results"][0]["indexingState"] = "empty"

            payload = current_state.build_current_provider_state(config, status)

            cgc_state = payload["providers"]["codegraphcontext-code"]
            repo_state = cgc_state["resources"]["watchers"]["agents-remember"]
            self.assertEqual(repo_state["state"], "degraded")
            self.assertFalse(repo_state["ok"])
            self.assertEqual(repo_state["indexingState"], "empty")
            self.assertEqual(cgc_state["state"], "degraded")
            self.assertEqual(payload["state"], "degraded")
            self.assertFalse(payload["ok"])

    def test_current_state_keeps_indexing_cgc_repo_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            cgc = next(
                result for result in status["results"] if result["provider"] == "codegraphcontext"
            )
            cgc["results"][0]["indexingState"] = "indexing"

            payload = current_state.build_current_provider_state(config, status)

            cgc_state = payload["providers"]["codegraphcontext-code"]
            repo_state = cgc_state["resources"]["watchers"]["agents-remember"]
            self.assertEqual(repo_state["state"], "ready")
            self.assertEqual(repo_state["indexingState"], "indexing")
            self.assertEqual(cgc_state["state"], "ready")
            self.assertEqual(payload["state"], "ready")
            self.assertTrue(payload["ok"])

    def test_provider_status_degrades_global_summary_for_empty_cgc_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            cgc = next(
                result for result in status["results"] if result["provider"] == "codegraphcontext"
            )
            cgc["results"][0]["indexingState"] = "empty"

            with mock.patch.object(
                provider_status,
                "_watchers_status",
                return_value=status,
            ):
                packet = provider_status.provider_status_packet(config)

            providers = packet["providers"]
            self.assertEqual(providers["state"], "degraded")
            self.assertFalse(providers["ok"])
            self.assertTrue(providers["partial"])
            self.assertEqual(providers["indexing"], [])
            recovery = [
                action
                for action in providers["recoveryActions"]
                if action.get("provider") == "codegraphcontext-code"
            ]
            self.assertEqual(recovery[0]["repoId"], "agents-remember")
            self.assertIn("provider_watchers(action='restart')", recovery[0]["recoveryAction"])

    def test_provider_summary_lists_indexing_targets_without_degrading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            cgc = next(
                result for result in status["results"] if result["provider"] == "codegraphcontext"
            )
            cgc["results"][0]["indexingState"] = "indexing"

            with mock.patch.object(
                provider_status,
                "_watchers_status",
                return_value=status,
            ):
                packet = provider_status.provider_status_packet(config)

            providers = packet["providers"]
            self.assertEqual(providers["state"], "ready")
            self.assertTrue(providers["ok"])
            self.assertEqual(
                providers["indexing"],
                ["codegraphcontext-code:agents-remember"],
            )
            self.assertEqual(
                [
                    action
                    for action in providers["recoveryActions"]
                    if action.get("provider") == "codegraphcontext-code"
                ],
                [],
            )

    def test_restarting_watcher_is_not_ready(self) -> None:
        """A crash-looping container reports Running=true between restarts but
        cannot serve; readiness must not count it as a live watcher."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            cgc = next(
                result for result in status["results"] if result["provider"] == "codegraphcontext"
            )
            cgc["results"][0]["process"]["containerState"]["containerState"] = "restarting"

            payload = current_state.build_current_provider_state(config, status)

            repo_state = payload["providers"]["codegraphcontext-code"]["resources"]["watchers"][
                "agents-remember"
            ]
            self.assertFalse(repo_state["watcherUp"])
            self.assertNotEqual(repo_state["state"], "ready")
            self.assertEqual(payload["providers"]["codegraphcontext-code"]["state"], "degraded")
            self.assertFalse(payload["ok"])

    def test_grepai_scan_markers_map_to_indexing_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)

            for scan_state, expected in (
                ("in-progress", "indexing"),
                ("complete", "indexed"),
                ("unknown", "unknown"),
            ):
                status = ready_status_payload(root)
                grepai = next(
                    result for result in status["results"] if result["provider"] == "grepai"
                )
                grepai["watcher"]["initialScan"] = {"state": scan_state}

                payload = current_state.build_current_provider_state(config, status)

                grepai_state = payload["providers"]["grepai-memory"]
                self.assertEqual(grepai_state["indexingState"], expected, msg=scan_state)
                # Busy-but-healthy never degrades readiness.
                self.assertEqual(grepai_state["state"], "ready")

    def test_grepai_indexing_feeds_summary_busy_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "mcp-settings.json"
            write_json(config_path, settings_payload(root))
            config = load_config(config_path)
            status = ready_status_payload(root)
            grepai = next(result for result in status["results"] if result["provider"] == "grepai")
            grepai["watcher"]["initialScan"] = {"state": "in-progress"}

            with mock.patch.object(
                provider_status,
                "_watchers_status",
                return_value=status,
            ):
                packet = provider_status.provider_status_packet(config)

            providers = packet["providers"]
            self.assertTrue(providers["ok"])
            self.assertIn("grepai-memory", providers["indexing"])


def ready_status_payload(root: Path) -> dict:
    return {
        "provider": "watchers",
        "action": "status",
        "ok": True,
        "partial": False,
        "settingsFile": (root / "provider-settings.json").as_posix(),
        "processNamespace": {"durableForDaemons": True, "warning": None},
        "enabled": {
            "grepai-memory": True,
            "codegraphcontext-code": True,
        },
        "results": [
            {
                "provider": "grepai",
                "action": "status",
                "ok": True,
                "runtimeRoot": (
                    root / "ar-coordination" / "providers" / "runners" / "grepai"
                ).as_posix(),
                "watcherRunning": True,
                "backend": container_payload("ar-grepai-postgres-workspace"),
                "embedder": container_payload("ar-grepai-ollama-workspace"),
                "watcher": {
                    **container_payload("ar-grepai-watcher-workspace"),
                    "workspaceStatus": {
                        "returncode": 0,
                        "stdout": "Workspaces (1):\n\n  agents-remember-memory-projects\n",
                    },
                },
            },
            {
                "provider": "codegraphcontext",
                "action": "status-all",
                "ok": True,
                "backend": container_payload("ar-cgc-falkordb-workspace"),
                "results": [
                    {
                        "provider": "codegraphcontext",
                        "action": "status",
                        "ok": True,
                        "repoId": "agents-remember",
                        "indexingState": "unknown",
                        "process": {
                            "alive": True,
                            "containerName": "ar-cgc-watcher-workspace-agents-remember",
                            "containerState": running_container_state(),
                        },
                    }
                ],
            },
        ],
    }


def container_payload(name: str) -> dict:
    return {
        "ok": True,
        "containerName": name,
        "image": "example:latest",
        "running": True,
        "containerState": running_container_state(),
    }


def running_container_state() -> dict:
    return {
        "containerState": "running",
        "running": True,
        "startedAt": "2026-05-28T09:30:00+00:00",
        "uptimeSeconds": 7200,
        "health": None,
    }


if __name__ == "__main__":
    unittest.main()
