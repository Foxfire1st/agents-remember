from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.mcp.config import load_config  # noqa: E402
from agents_remember.providers.integrity import (  # noqa: E402
    check_provider_runner_integrity,
    manifest_path_for_config,
    write_provider_runner_manifest,
)
from agents_remember.providers.status import provider_status_packet  # noqa: E402
from test_config import settings_payload, write_json  # noqa: E402


class ProviderIntegrityTests(unittest.TestCase):
    def test_missing_manifest_without_runner_files_is_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = write_and_load_config(root)

            result = check_provider_runner_integrity(config)

            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "notInstalled")

    def test_records_and_detects_runner_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = write_and_load_config(root)
            binary = root / "ar-coordination" / "providers" / "_bin" / "grepai.exe"
            binary.parent.mkdir(parents=True)
            binary.write_text("before\n", encoding="utf-8")

            write_result = write_provider_runner_manifest(config)
            clean = check_provider_runner_integrity(config)
            binary.write_text("after\n", encoding="utf-8")
            changed = check_provider_runner_integrity(config)

            self.assertTrue(write_result["ok"])
            self.assertTrue(clean["ok"])
            self.assertFalse(changed["ok"])
            self.assertEqual(changed["changed"], ["providers/_bin/grepai.exe"])
            self.assertTrue(manifest_path_for_config(config).exists())

    def test_provider_status_short_circuits_on_unrecorded_runner_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = write_and_load_config(root)
            binary = root / "ar-coordination" / "providers" / "_bin" / "grepai.exe"
            binary.parent.mkdir(parents=True)
            binary.write_text("unrecorded\n", encoding="utf-8")

            packet = provider_status_packet(config)

            self.assertFalse(packet["ok"])
            self.assertEqual(packet["state"], "runnerIntegrityFailed")
            self.assertEqual(packet["integrity"]["state"], "manifestMissing")
            self.assertEqual(packet["recoveryActions"][0]["action"], "runtime_install")


def write_and_load_config(root: Path):
    config_path = root / "mcp-settings.json"
    write_json(config_path, settings_payload(root))
    return load_config(config_path)


if __name__ == "__main__":
    unittest.main()
