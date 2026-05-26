from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.mcp.config import load_config
from agents_remember.providers import status as provider_status
from agents_remember.providers.integrity import (
    check_provider_runner_integrity,
    manifest_path_for_config,
    write_provider_runner_manifest,
)
from test_config import settings_payload, write_json


class ProviderIntegrityTests(unittest.TestCase):
    def test_missing_manifest_without_runner_files_is_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = write_and_load_config(root)

            result = check_provider_runner_integrity(config)

            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "notInstalled")

    def test_manifest_ignores_legacy_venv_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = write_and_load_config(root)
            runner = (
                root
                / "ar-coordination"
                / "providers"
                / "_venvs"
                / "codegraphcontext"
                / "bin"
                / "cgc"
            )
            runner.parent.mkdir(parents=True)
            runner.write_text("before\n", encoding="utf-8")

            write_result = write_provider_runner_manifest(config)
            clean = check_provider_runner_integrity(config)
            runner.write_text("after\n", encoding="utf-8")
            changed = check_provider_runner_integrity(config)

            self.assertTrue(write_result["ok"])
            self.assertEqual(write_result["fileCount"], 0)
            self.assertTrue(clean["ok"])
            self.assertTrue(changed["ok"])
            self.assertEqual(changed["fileCount"], 0)
            self.assertTrue(manifest_path_for_config(config).exists())

    def test_provider_status_does_not_short_circuit_on_legacy_venv_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = write_and_load_config(root)
            runner = (
                root
                / "ar-coordination"
                / "providers"
                / "_venvs"
                / "codegraphcontext"
                / "bin"
                / "cgc"
            )
            runner.parent.mkdir(parents=True)
            runner.write_text("unrecorded\n", encoding="utf-8")

            original = provider_status._watchers_status
            provider_status._watchers_status = lambda config: {
                "ok": True,
                "partial": False,
                "enabled": {"codegraphcontext-code": True, "grepai-memory": False},
                "results": [],
                "recoveryActions": [],
            }
            try:
                packet = provider_status.provider_status_packet(config)
            finally:
                provider_status._watchers_status = original

            self.assertTrue(packet["ok"])
            self.assertEqual(packet["state"], "checked")
            self.assertEqual(packet["integrity"]["state"], "notInstalled")


def write_and_load_config(root: Path):
    config_path = root / "mcp-settings.json"
    write_json(config_path, settings_payload(root))
    return load_config(config_path)


if __name__ == "__main__":
    unittest.main()
