"""MIK-R192 rule 8: the live provider-map guard is real, reads the daemon's own envelope, closes.

Three things have to hold together. The bridge dispatches ``daemon-config`` at all; the serving
adapter reads the providers where the running daemon reports them (``config.providers``, not the
file's ``agents.providers``); and a read that fails refuses instead of passing as "no live
arguments". The serving module reaches none of that itself: the read is the composition root's
port, so ``serving`` adds no import at or above its own rank (rule 19).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from _user_settings_guard import user_settings_path_for_write
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.paseo.paseo_daemon_config import (
    DAEMON_CONFIG_UNVERIFIED,
    daemon_provider_extra_args,
    host_provider_extra_args,
)
from agents_remember_test_support.code_quality import layering
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "mcp/src/agents_remember"
BRIDGE_SCRIPT = SOURCE_ROOT / "cli/paseo_bridge.mjs"
SERVED_MODULE = SOURCE_ROOT / "serving/paseo/paseo_daemon_config.py"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="the bridge script needs Node.js")

# One reply of the pinned host's own `getDaemonConfig()`, as the catalog reads it: the providers
# are the daemon's map, at `config.providers`.
DAEMON_REPLY = {
    "config": {
        "agentProfiles": [],
        "providers": {
            "claude": {"options": {"extraArgs": {"chrome": None, "trace": "verbose"}}},
            "acp": {"extends": "acp", "params": {"extraArgs": {"idle": ""}}},
        },
    }
}


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated user settings folder and coordination root for one case."""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "coordination").mkdir()
    return tmp_path


def served_config(root: Path) -> McpRuntimeConfig:
    """A runtime whose settings name a Paseo home; the file itself holds no provider entry."""

    home = root / "home"
    home.mkdir(exist_ok=True)
    (home / "config.json").write_text(json.dumps({"agents": {"providers": {}}}), encoding="utf-8")
    path = user_settings_path_for_write()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "orchestration": {},
                "paseoRuntime": {
                    "installPrefix": (root / "prefix").as_posix(),
                    "home": home.as_posix(),
                    "listen": "127.0.0.1:6831",
                    "providers": {},
                    "embed": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return McpRuntimeConfig(
        config_path=root / "mcp.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
    )


@needs_node
def test_the_bridge_dispatches_the_daemon_config_command() -> None:
    """The command is in the bridge's table: the run gets past dispatch and asks for its runtime.

    A bridge without the command answers ``unsupported_bridge_command`` and nothing else happens,
    which is exactly the inert guard the finding names. With it, the first missing input is the
    daemon's address -- the command exists and would have read the daemon (L184-R1-F21).
    """

    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("PASEO_", "AR_PASEO_"))
    }
    completed = subprocess.run(
        [NODE or "node", BRIDGE_SCRIPT.as_posix(), "daemon-config"],
        capture_output=True,
        check=False,
        text=True,
        timeout=HANG_GUARD_SECONDS,
        env=env,
    )

    reply = json.loads(completed.stdout)
    error = reply.get("error") or {}
    assert error.get("code") != "unsupported_bridge_command", completed.stdout
    assert "AR_PASEO_URL" in error.get("message", ""), completed.stdout


@needs_node
def test_the_bridge_reports_the_daemons_own_configuration_document() -> None:
    """The reply carries the document under ``config``, and no document is a failed read."""

    probe = (
        "import(process.env.BRIDGE_URL).then((m) => {"
        " const reply = JSON.parse(process.env.DAEMON_REPLY);"
        " const read = m.daemonConfiguration(reply);"
        " let refused = 'no-throw';"
        " try { m.daemonConfiguration({}); } catch (error) { refused = (error && error.code) || String(error); }"
        " process.stdout.write(JSON.stringify({"
        "  config: read.config, readAt: typeof read.readAt, refused,"
        "  empty: (() => { try { m.daemonConfiguration({config: []}); return 'no-throw'; }"
        "   catch (error) { return (error && error.code) || String(error); } })()}));"
        "}).catch((error) => { process.stdout.write('IMPORT-FAIL ' + error.message); process.exit(1); });"
    )
    completed = subprocess.run(
        [NODE or "node", "--input-type=module", "-e", probe],
        capture_output=True,
        check=False,
        text=True,
        timeout=HANG_GUARD_SECONDS,
        env={
            **os.environ,
            "BRIDGE_URL": BRIDGE_SCRIPT.resolve().as_uri(),
            "DAEMON_REPLY": json.dumps(DAEMON_REPLY),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert not completed.stdout.startswith("IMPORT-FAIL"), completed.stdout
    answer = json.loads(completed.stdout)
    assert answer["config"] == DAEMON_REPLY["config"]
    assert answer["readAt"] == "string"
    assert answer["refused"] == "daemon_config_unreadable"
    assert answer["empty"] == "daemon_config_unreadable"


def test_the_adapter_reads_the_providers_where_the_daemon_reports_them(root: Path) -> None:
    """``config.providers``, and the entry's ``params`` where it has no ``options``."""

    config = served_config(root)

    def live(_config: McpRuntimeConfig) -> dict[str, Any]:
        return DAEMON_REPLY["config"]

    assert daemon_provider_extra_args(config, "claude", read_config=live) == ("chrome", "trace")
    assert daemon_provider_extra_args(config, "acp", read_config=live) == ("idle",)
    assert daemon_provider_extra_args(config, "codex", read_config=live) == ()


def test_the_files_own_shape_is_not_the_daemons(root: Path) -> None:
    """A document shaped like ``config.json`` is not what the daemon reports, so it gives nothing."""

    config = served_config(root)
    file_shaped = {
        "agents": {"providers": {"claude": {"options": {"extraArgs": {"chrome": None}}}}}
    }

    assert daemon_provider_extra_args(config, "claude", read_config=lambda _c: file_shaped) == ()


def test_a_failed_read_refuses_instead_of_answering_no_arguments(root: Path) -> None:
    """A map that was not read is not a clean one: the start that would record it refuses."""

    config = served_config(root)

    def unread(_config: McpRuntimeConfig) -> dict[str, Any]:
        raise PaseoRuntimeFailure(
            DAEMON_CONFIG_UNVERIFIED, "config", "the bridge could not reach the daemon"
        )

    with pytest.raises(PaseoRuntimeFailure) as refused:
        host_provider_extra_args(config, "claude", read_config=unread)

    assert refused.value.code == DAEMON_CONFIG_UNVERIFIED


def test_a_caller_without_the_ports_refuses(root: Path) -> None:
    """No port is no read: the adapter never falls back to the file and calls it clean."""

    config = served_config(root)

    with pytest.raises(PaseoRuntimeFailure) as refused:
        daemon_provider_extra_args(config, "claude")

    assert refused.value.code == DAEMON_CONFIG_UNVERIFIED
    assert "no reader was supplied" in str(refused.value)


def test_the_served_module_adds_no_import_at_or_above_its_own_rank() -> None:
    """Rule 19: the port is the caller's, so this module imports nothing above ``serving``.

    The comparison is against the declared order, not a baseline: the module that reads a file's
    own keys may not also reach for the bridge's caller (L184-R1-F14).
    """

    contract = layering.load_contract(REPO_ROOT / "layers.toml")
    serving = contract.ranks["serving"]

    above = [
        module
        for _lineno, module, _names in layering.imports_of(SERVED_MODULE)
        if (target := layering.resolve_import_target(module, contract)) is not None
        and contract.ranks[target] > serving
    ]

    assert above == []
