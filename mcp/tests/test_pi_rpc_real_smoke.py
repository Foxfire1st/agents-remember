"""Opt-in isolated smoke against the pinned real Pi RPC npm package."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agents_remember.serving.harness_control_models import ControlIdentity, LaunchSpec
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from agents_remember.serving.pi_rpc_protocol import PI_RPC_PACKAGE

PI_RPC_VERSION = "0.80.6"


@unittest.skipUnless(
    os.environ.get("AR_RUN_PI_RPC_SMOKE") == "1",
    "set AR_RUN_PI_RPC_SMOKE=1 to install and run the pinned Pi RPC smoke",
)
class PiRpcRealSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_isolated_install_reaches_get_state_ready(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ar-pi-rpc-smoke-") as temp:
            root = Path(temp)
            home = root / "home"
            project = root / "project"
            prefix = root / "pi"
            for path in (home, project, prefix):
                path.mkdir()
            install_env = {
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "npm_config_cache": str(root / "npm-cache"),
                "npm_config_audit": "false",
                "npm_config_fund": "false",
                "npm_config_loglevel": "error",
            }
            subprocess.run(
                [
                    "npm",
                    "install",
                    "--prefix",
                    str(prefix),
                    "--no-save",
                    f"{PI_RPC_PACKAGE}@{PI_RPC_VERSION}",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=install_env,
                timeout=240,
            )
            executable = prefix / "node_modules" / ".bin" / "pi"
            version = subprocess.run(
                [str(executable), "--version"],
                check=True,
                capture_output=True,
                text=True,
                env=install_env,
                timeout=30,
            ).stdout.strip()
            self.assertEqual(version, PI_RPC_VERSION)

            launch_env = {
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(root / "xdg"),
                "NO_COLOR": "1",
            }
            launch = LaunchSpec(
                identity=ControlIdentity(
                    ar_session_id="real-pi-smoke",
                    tmux_name="real-pi-smoke",
                    created_at="2026-07-14T09:00:00+00:00",
                ),
                harness_id="pi",
                cwd=project,
                argv=(
                    str(executable),
                    "--no-session",
                    "--offline",
                    "--no-extensions",
                    "--no-skills",
                    "--no-prompt-templates",
                    "--no-themes",
                    "--no-context-files",
                ),
                env=launch_env,
            )
            adapter = PiRpcAdapter(version=PI_RPC_VERSION)
            handshake = await adapter.start(launch)
            try:
                state = await adapter.snapshot()
                self.assertEqual(handshake.adapter_id, f"pi-rpc:{PI_RPC_VERSION}")
                self.assertEqual(state.control, "ready")
                self.assertEqual(state.activity, "idle")
                self.assertTrue(state.vendor_session_id)
                self.assertEqual(state.raw["piVersion"], PI_RPC_VERSION)
            finally:
                await adapter.stop("graceful")
