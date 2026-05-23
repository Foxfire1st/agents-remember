from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp.config import ConfigError, load_config, require_config_path
from agents_remember.providers.settings import lifecycle_settings_from_config


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def settings_payload(root: Path) -> dict:
    coordination_root = root / "ar-coordination"
    workspace_root = root / "workspace"
    return {
        "version": 1,
        "coordinationRoot": str(coordination_root),
        "workspaceRoot": str(workspace_root),
        "repositories": {"agents-remember-md": {}},
        "providers": {
            "codegraphcontext-code": {},
            "grepai-memory": {},
        },
        "timeoutCaps": {
            "toolSeconds": 30,
            "providerSeconds": 120,
        },
    }


class McpConfigTests(unittest.TestCase):
    def test_config_path_must_be_absolute(self) -> None:
        with self.assertRaisesRegex(ConfigError, "absolute"):
            require_config_path(Path("relative-settings.json"))

    def test_config_path_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing = Path(tmp_dir) / "missing-settings.json"

            with self.assertRaisesRegex(ConfigError, "does not exist"):
                require_config_path(missing)

    def test_coordinator_system_settings_is_not_mcp_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "ar-coordination" / "system" / "settings.json"
            write_json(path, settings_payload(root))

            with self.assertRaisesRegex(ConfigError, "coordinator system/settings.json"):
                load_config(path)

    def test_config_must_not_live_inside_coordination_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "ar-coordination" / "mcp-settings.json"
            write_json(path, settings_payload(root))

            with self.assertRaisesRegex(ConfigError, "inside the coordinator root"):
                load_config(path)

    def test_loads_authority_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".agents" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))

            config = load_config(path)

            self.assertEqual(config.allowed_repo_ids, ("agents-remember-md",))
            self.assertEqual(
                config.allowed_provider_ids,
                ("codegraphcontext-code", "grepai-memory"),
            )
            self.assertEqual(config.timeout_caps["toolSeconds"], 30)
            self.assertEqual(
                config.transcript_root,
                root / "ar-coordination" / "providers" / "logs" / "mcp",
            )
            self.assertEqual(config.harness_skill_root, root / ".agents" / "skills")
            self.assertEqual(
                config.repositories["agents-remember-md"].path,
                root / "workspace" / "agents-remember-md",
            )
            self.assertEqual(
                config.repositories["agents-remember-md"].memory_root.name,
                "ar-agents-remember-md",
            )
            self.assertEqual(config.providers["grepai-memory"].log_root.name, "grepai")
            self.assertEqual(
                config.providers["codegraphcontext-code"].runtime_root,
                root / "ar-coordination" / "providers" / "runners" / "codegraphcontext",
            )

            lifecycle_settings = lifecycle_settings_from_config(config)
            providers = lifecycle_settings["contextProviders"]["providers"]
            self.assertEqual(
                providers["grepai-memory"]["runtimeRoot"],
                (root / "ar-coordination" / "providers" / "runners" / "grepai").as_posix(),
            )
            self.assertEqual(
                providers["grepai-memory"]["backend"]["runtimeRoot"],
                (
                    root / "ar-coordination" / "providers" / "data" / "grepai" / "postgres"
                ).as_posix(),
            )
            self.assertEqual(
                providers["codegraphcontext-code"]["backend"]["runtimeRoot"],
                (
                    root
                    / "ar-coordination"
                    / "providers"
                    / "data"
                    / "codegraphcontext"
                    / "falkordb"
                ).as_posix(),
            )

    def test_harness_skill_root_is_none_without_registration_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "mcp-settings.json"
            write_json(path, settings_payload(root))

            config = load_config(path)

            self.assertIsNone(config.harness_skill_root)

    def test_harness_skill_root_override_wins_over_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            payload["harnessSkillRoot"] = str(root / "custom" / "skills")
            path = root / ".agents" / "mcp" / "settings.json"
            write_json(path, payload)

            config = load_config(path)

            self.assertEqual(config.harness_skill_root, root / "custom" / "skills")

    def test_loads_repository_contract_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            payload["repositories"]["agents-remember-md"]["contractPath"] = (
                "tasks/agents-remember-md/task/contract.md"
            )
            path = root / "mcp-settings.json"
            write_json(path, payload)

            config = load_config(path)

            self.assertEqual(
                config.repositories["agents-remember-md"].contract_path.name,
                "contract.md",
            )

    def test_memory_settings_include_cannot_escape_repo_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            payload["repositories"]["agents-remember-md"]["memorySettingsIncludes"] = [
                str(
                    root
                    / "ar-coordination"
                    / "memory-repos"
                    / "ar-agents-remember-md"
                    / "system"
                    / "settings.json"
                ),
                str(root / "outside" / "settings.json"),
            ]
            path = root / "mcp-settings.json"
            write_json(path, payload)

            with self.assertRaisesRegex(ConfigError, "outside configured repo boundaries"):
                load_config(path)

    def test_harness_skill_root_must_be_absolute_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            payload["harnessSkillRoot"] = ".agents/skills"
            path = root / "mcp-settings.json"
            write_json(path, payload)

            with self.assertRaisesRegex(ConfigError, "harnessSkillRoot.*absolute"):
                load_config(path)

    def test_provider_settings_reject_derived_path_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            payload["providers"]["grepai-memory"]["runnerRoot"] = str(
                root / "provider-runners" / "grepai"
            )
            payload["providers"]["grepai-memory"]["logRoot"] = str(
                root / "ar-coordination" / "providers" / "logs" / "grepai"
            )
            path = root / "mcp-settings.json"
            write_json(path, payload)

            with self.assertRaisesRegex(ConfigError, "derived by the server"):
                load_config(path)

    def test_repository_contract_path_cannot_escape_coordination_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            payload["repositories"]["agents-remember-md"]["contractPath"] = str(
                root / "outside" / "contract.md"
            )
            path = root / "mcp-settings.json"
            write_json(path, payload)

            with self.assertRaisesRegex(ConfigError, "contractPath must be inside"):
                load_config(path)

    def test_timeout_caps_reject_boolean_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            payload["timeoutCaps"]["toolSeconds"] = True
            path = root / "mcp-settings.json"
            write_json(path, payload)

            with self.assertRaisesRegex(ConfigError, "positive integer"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
