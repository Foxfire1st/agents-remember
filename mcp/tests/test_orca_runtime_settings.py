from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents_remember.cli.orca_runtime import OrcaRuntimeFailure, orca_catalog_scope, runtime_call
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    OrcaRuntimeSettings,
)


class OrcaRuntimeSettingsTests(unittest.TestCase):
    def test_missing_settings_refuse_without_searching_path(self) -> None:
        config = self._config(None)
        with (
            patch("agents_remember.cli.orca_runtime.shutil.which") as which,
            self.assertRaises(OrcaRuntimeFailure) as raised,
        ):
            runtime_call(config, "catalog", {})
        self.assertEqual(raised.exception.code, "native_runtime_configuration_missing")
        self.assertIn("orcaRuntime.runtimeRoot", str(raised.exception))
        self.assertIn("orcaRuntime.userDataPath", str(raised.exception))
        which.assert_not_called()

    def test_runtimeclient_receives_configured_paths_and_keeps_orca_auth_precedence(self) -> None:
        runtime_root = Path("/trusted/orca-source")
        user_data_path = Path("/trusted/orca-dev-profile")
        config = self._config(OrcaRuntimeSettings(runtime_root, user_data_path))
        inherited = {
            "AR_ORCA_RUNTIME_ROOT": "/wrong/inherited/source",
            "AR_ORCA_CLI": "/usr/bin/orca",
            "ORCA_USER_DATA_PATH": "/wrong/inherited/profile",
            "ORCA_REMOTE_PAIRING": "test-profile-pairing-value",
            "ORCA_ENVIRONMENT": "test-environment",
        }
        completed = SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True}), stderr="")
        with (
            patch.dict(os.environ, inherited, clear=True),
            patch("agents_remember.cli.orca_runtime.shutil.which", return_value="/usr/bin/node"),
            patch("agents_remember.cli.orca_runtime.subprocess.run", return_value=completed) as run,
        ):
            self.assertEqual(
                runtime_call(config, "catalog", {"workspaceSelector": "id:test"}), {"ok": True}
            )

        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["AR_ORCA_RUNTIME_ROOT"], runtime_root.as_posix())
        self.assertEqual(child_env["ORCA_USER_DATA_PATH"], user_data_path.as_posix())
        self.assertNotIn("AR_ORCA_CLI", child_env)
        self.assertEqual(child_env["ORCA_REMOTE_PAIRING"], inherited["ORCA_REMOTE_PAIRING"])
        self.assertEqual(child_env["ORCA_ENVIRONMENT"], inherited["ORCA_ENVIRONMENT"])

        other = self._config(OrcaRuntimeSettings(runtime_root, Path("/trusted/orca-other-profile")))
        self.assertNotEqual(orca_catalog_scope(config)[0], orca_catalog_scope(other)[0])

    @staticmethod
    def _config(orca_runtime: OrcaRuntimeSettings | None) -> McpRuntimeConfig:
        return McpRuntimeConfig(
            config_path=Path("/trusted/settings/mcp.json"),
            coordination_root=Path("/trusted/coordination"),
            workspace_root=Path("/trusted/projects"),
            transcript_root=Path("/trusted/coordination/logs/mcp"),
            orca_runtime=orca_runtime,
        )
