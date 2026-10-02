"""The single path from Agents Remember to the Paseo runtime's agent, workspace and catalog functions.

``bridge_call(config, command, payload)`` runs ``paseo_bridge.mjs <command>`` with the JSON payload
on standard input and returns the one JSON object the script prints. The script owns everything
Paseo-shaped: it imports Paseo's client package from the configured install prefix and lists its
commands in its header. This module owns the process: which runtime the script is pointed at, the
60-second limit, and the mapping of every failure onto :class:`PaseoBridgeFailure`.

No other module starts the script, a Paseo program or a Paseo package. The provision, status and
stop commands (``paseo_command.py``) use Paseo's own command line and are the one other boundary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.paseo_runtime_settings import (
    NO_PASEO_RUNTIME_CONFIGURED,
    PaseoRuntimeNotConfigured,
    PaseoRuntimeSettings,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

# Every bridge call ends within this limit; a call that does not is stopped and reported as a
# timeout. An operation that has to wait longer is a sequence of calls.
PASEO_BRIDGE_TIMEOUT_SECONDS = 60
# The script's own budget, so it can answer with a named timeout before the hard stop.
_SCRIPT_DEADLINE_MS = 55_000
_SERVER_ID_FILE = "server-id"
_CODE_LIMIT = 100
_MESSAGE_LIMIT = 800

RUNTIME_NOT_CONFIGURED = PaseoRuntimeNotConfigured.code
DAEMON_UNREACHABLE = "paseo_daemon_unreachable"
BRIDGE_TIMEOUT = "paseo_bridge_timeout"
BRIDGE_UNAVAILABLE = "paseo_bridge_unavailable"
BRIDGE_INVALID_REPLY = "paseo_bridge_invalid_reply"
BRIDGE_REFUSED = "paseo_call_failed"


class PaseoBridgeFailure(RuntimeError):
    """One bridge call failed; ``code`` names the reason and the message is short."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def bridge_call(
    config: McpRuntimeConfig,
    command: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Run one bridge command against the configured Paseo runtime and return its result."""

    settings = require_bridge_runtime(config)
    node = shutil.which("node")
    script = Path(__file__).with_name("paseo_bridge.mjs")
    if not node or not script.is_file():
        raise PaseoBridgeFailure(
            BRIDGE_UNAVAILABLE, "The Paseo bridge needs Node.js on PATH and its packaged script."
        )
    try:
        completed = subprocess.run(
            [node, script.as_posix(), command],
            input=json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
            capture_output=True,
            check=False,
            timeout=PASEO_BRIDGE_TIMEOUT_SECONDS,
            env=_bridge_environment(settings),
        )
    except subprocess.TimeoutExpired as error:
        raise PaseoBridgeFailure(
            BRIDGE_TIMEOUT,
            f"The Paseo bridge call {command!r} did not end within "
            f"{PASEO_BRIDGE_TIMEOUT_SECONDS} seconds and was stopped.",
        ) from error
    except OSError as error:
        raise PaseoBridgeFailure(
            BRIDGE_UNAVAILABLE, f"The Paseo bridge could not be started: {error}"
        ) from error
    return _bridge_reply(command, completed.returncode, completed.stdout)


def require_bridge_runtime(config: McpRuntimeConfig) -> PaseoRuntimeSettings:
    """Return the configured Paseo runtime or refuse naming the unconfigured state."""

    settings = config.paseo_runtime
    if settings is None:
        raise PaseoBridgeFailure(
            RUNTIME_NOT_CONFIGURED,
            f"{NO_PASEO_RUNTIME_CONFIGURED}: {config.config_path} has no paseoRuntime block",
        )
    return settings


def _bridge_environment(settings: PaseoRuntimeSettings) -> dict[str, str]:
    """Point the script at the configured runtime and at no other.

    Inherited ``PASEO_*`` and ``AR_PASEO_*`` variables are dropped, and the script is told the
    server id of the configured daemon home so that it refuses any other daemon it finds at the
    listen address.
    """

    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("PASEO_", "AR_PASEO_"))
    }
    env["AR_PASEO_INSTALL_PREFIX"] = settings.install_prefix.as_posix()
    env["AR_PASEO_URL"] = f"ws://{settings.listen}/ws"
    env["AR_PASEO_SERVER_ID"] = _configured_server_id(settings)
    env["AR_PASEO_VERSION"] = settings.version
    env["AR_PASEO_DEADLINE_MS"] = str(_SCRIPT_DEADLINE_MS)
    return env


def _configured_server_id(settings: PaseoRuntimeSettings) -> str:
    """The identity Paseo keeps in the daemon home; absent until that home's daemon first ran."""

    try:
        server_id = (settings.home / _SERVER_ID_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        server_id = ""
    if not server_id:
        raise PaseoBridgeFailure(
            DAEMON_UNREACHABLE,
            f"The Paseo daemon of {settings.home} cannot be reached: that home has no daemon "
            "identity yet; provision the Paseo runtime first.",
        )
    return server_id


def _bridge_reply(command: str, returncode: int, stdout: str) -> dict[str, Any]:
    try:
        reply = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise PaseoBridgeFailure(
            BRIDGE_INVALID_REPLY, f"The Paseo bridge call {command!r} returned an unreadable reply."
        ) from error
    if not isinstance(reply, dict):
        raise PaseoBridgeFailure(
            BRIDGE_INVALID_REPLY, f"The Paseo bridge call {command!r} returned an unreadable reply."
        )
    if returncode == 0 and reply.get("ok") is not False:
        return reply
    error = reply.get("error")
    if isinstance(error, dict):
        raise PaseoBridgeFailure(
            str(error.get("code") or BRIDGE_REFUSED)[:_CODE_LIMIT],
            str(error.get("message") or "The Paseo runtime refused the call.")[:_MESSAGE_LIMIT],
        )
    raise PaseoBridgeFailure(BRIDGE_REFUSED, "The Paseo runtime refused the call.")
