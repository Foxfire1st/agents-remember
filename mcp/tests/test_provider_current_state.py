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

from agents_remember.mcp.config import load_config
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
            self.assertEqual(saved["providers"]["grepai-memory"]["watcherUp"], True)
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
            repo_state = cgc_state["resources"]["watchers"]["agents-remember-md"]
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
            grepai = next(
                result for result in status["results"] if result["provider"] == "grepai"
            )
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
                        "repoId": "agents-remember-md",
                        "indexingState": "unknown",
                        "process": {
                            "alive": True,
                            "containerName": "ar-cgc-watcher-workspace-agents-remember-md",
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
