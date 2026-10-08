"""A daemon must not give new agents the session identity of its starter."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents_remember.kernel.primitives.host_environment import (
    SESSION_VARIABLES,
    is_session_variable,
)
from agents_remember.serving.paseo.paseo_command import run_command
from agents_remember.serving.paseo.paseo_process_record import read_process


def test_daemon_runner_drops_session_identity_and_keeps_logins():
    inherited = {name: "session-value" for name in SESSION_VARIABLES}
    inherited.update(
        PASEO_AGENT_ID="agent",
        AR_SPAWN_ROLE="worker",
        AR_HOSTED_SESSION_ID="seat",
        CLAUDE_CODE_OAUTH_TOKEN="login",
        CODEX_HOME="/login/config",
        PATH="/usr/bin:/bin",
    )
    completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")
    with (
        patch.dict(os.environ, inherited, clear=True),
        patch(
            "agents_remember.serving.paseo.paseo_command.subprocess.run", return_value=completed
        ) as process,
    ):
        assert run_command(["/product/node", "daemon", "start"], 1).returncode == 0
    environment = process.call_args.kwargs["env"]
    assert environment == {
        "CLAUDE_CODE_OAUTH_TOKEN": "login",
        "CODEX_HOME": "/login/config",
        "PATH": "/usr/bin:/bin",
    }


def test_process_observation_reports_only_session_names():
    with (
        patch("agents_remember.serving.paseo.paseo_process_record.os.kill"),
        patch(
            "agents_remember.serving.paseo.paseo_process_record.Path.readlink",
            return_value=Path("/product/node"),
        ),
        patch(
            "agents_remember.serving.paseo.paseo_process_record.Path.read_bytes",
            side_effect=[
                b"Paseo Supervisor\0",
                b"PASEO_HOME=/home/host\0CODEX_THREAD_ID=secret\0"
                b"AR_SPAWN_ROLE=worker\0CLAUDE_CODE_OAUTH_TOKEN=login\0",
            ],
        ),
    ):
        facts = read_process(4242)
    assert facts is not None
    assert facts.session_variables == ("AR_SPAWN_ROLE", "CODEX_THREAD_ID")
    assert "secret" not in repr(facts)
    assert facts.paseo_home == "/home/host"


def test_session_filter_uses_exact_harness_names():
    assert all(is_session_variable(name) for name in SESSION_VARIABLES)
    assert is_session_variable("PASEO_AGENT_CWD")
    assert is_session_variable("AR_SPAWN_OPERATION")
    assert not is_session_variable("CLAUDE_CODE_OAUTH_TOKEN")
    assert not is_session_variable("CODEX_HOME")
    assert not is_session_variable("AR_DAGGER_AUTHORITY_ROOT")


def test_only_npm_child_gets_product_node_path_without_losing_logins():
    inherited = {
        "PATH": "/usr/bin:/bin",
        "CLAUDE_CODE_OAUTH_TOKEN": "login",
        "CODEX_THREAD_ID": "session",
    }
    completed = SimpleNamespace(returncode=0, stdout="{}", stderr="")
    with (
        patch.dict(os.environ, inherited, clear=True),
        patch(
            "agents_remember.serving.paseo.paseo_command.subprocess.run", return_value=completed
        ) as process,
    ):
        run_command(
            ["/product/node/bin/node", "/product/node/lib/node_modules/npm/bin/npm-cli.js", "ci"], 1
        )
        npm = process.call_args.kwargs["env"]
        run_command(
            ["/product/node/bin/node", "/prefix/node_modules/.bin/paseo", "daemon", "start"], 1
        )
        host = process.call_args.kwargs["env"]
    assert npm["PATH"] == "/product/node/bin:/usr/bin:/bin"
    assert host["PATH"] == inherited["PATH"]
    assert npm["CLAUDE_CODE_OAUTH_TOKEN"] == host["CLAUDE_CODE_OAUTH_TOKEN"] == "login"
    assert "CODEX_THREAD_ID" not in npm and "CODEX_THREAD_ID" not in host
