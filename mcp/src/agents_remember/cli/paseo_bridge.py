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
import re
import subprocess
from pathlib import Path
from typing import Any

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_authority import paseo_runtime_path
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    NO_PASEO_RUNTIME_CONFIGURED,
    PaseoRuntimeNotConfigured,
    PaseoRuntimeSettings,
)
from agents_remember.kernel.primitives.runtime_config import ConfigError, McpRuntimeConfig
from agents_remember.serving.paseo.paseo_process_record import inspect_record, read_process
from agents_remember.serving.paseo.paseo_remedy import terminal_provision_remedy

# Every bridge call ends within this limit; a call that does not is stopped and reported as a
# timeout. An operation that has to wait longer is a sequence of calls.
PASEO_BRIDGE_TIMEOUT_SECONDS = 60
# The script's own budget, so it can answer with a named timeout before the hard stop.
_SCRIPT_DEADLINE_MS = 55_000
_SERVER_ID_FILE = "server-id"
_CODE_LIMIT = 100
_MESSAGE_LIMIT = 800
_STDERR_TAIL = 200
# A Node crash report is: source line, caret, the error line, stack frames, the version line.
# A process Node ends itself (out of memory) says why in a line that begins with FATAL ERROR.
_ERROR_LINE = re.compile(r"^(?:\S*Error\b|FATAL ERROR\b).*", re.MULTILINE)
_REPORT_NOISE = re.compile(r"\s+at .*|\s*|Node\.js v\S+")

RUNTIME_NOT_CONFIGURED = PaseoRuntimeNotConfigured.code
DAEMON_UNREACHABLE = "paseo_daemon_unreachable"
BRIDGE_TIMEOUT = "paseo_bridge_timeout"
BRIDGE_UNAVAILABLE = "paseo_bridge_unavailable"
BRIDGE_INVALID_REPLY = "paseo_bridge_invalid_reply"
BRIDGE_REFUSED = "paseo_call_failed"
# agent-create: the agent exists without its first message and its closed session cannot be
# opened again, so no repeat of the call can deliver the message.
AGENT_WITHOUT_MESSAGE_LOST = "paseo_agent_without_message_lost"


class PaseoBridgeFailure(RuntimeError):
    """One bridge call failed; ``code`` names the reason and the message is short."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def bridge_call(
    config: McpRuntimeConfig,
    command: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run one bridge command against the configured Paseo runtime and return its result."""

    if timeout_seconds is None:
        timeout_seconds = PASEO_BRIDGE_TIMEOUT_SECONDS
    settings = require_bridge_runtime(config)
    try:
        node = product_node().node
    except PaseoRuntimeFailure as error:
        raise PaseoBridgeFailure(BRIDGE_UNAVAILABLE, str(error)) from error
    script = Path(__file__).with_name("paseo_bridge.mjs")
    if not node.is_file() or not script.is_file():
        remedy = (
            terminal_provision_remedy(settings)
            if inspect_record(settings.home, "bridge", read_process).kind == "own"
            else "Run runtime_install, then the one dashboard start."
        )
        raise PaseoBridgeFailure(
            BRIDGE_UNAVAILABLE,
            f"The Paseo bridge needs this build's product Node and packaged script. {remedy}",
        )
    try:
        completed = subprocess.run(
            [node.as_posix(), script.as_posix(), command],
            input=json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
            # Output that is not UTF-8 is read with replacement characters: it then fails as an
            # unreadable reply, a named failure, instead of leaving this call as a decoding error.
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env={
                **_bridge_environment(settings),
                "AR_PASEO_DEADLINE_MS": str(
                    min(_SCRIPT_DEADLINE_MS, max(1, int(timeout_seconds * 1000) - 100))
                ),
            },
        )
    except subprocess.TimeoutExpired as error:
        raise PaseoBridgeFailure(
            BRIDGE_TIMEOUT,
            f"The Paseo bridge call {command!r} did not end within "
            f"{timeout_seconds} seconds and was stopped.",
        ) from error
    except OSError as error:
        raise PaseoBridgeFailure(
            BRIDGE_UNAVAILABLE, f"The Paseo bridge could not be started: {error}"
        ) from error
    return _bridge_reply(command, completed)


def require_bridge_runtime(config: McpRuntimeConfig) -> PaseoRuntimeSettings:
    """Return the configured Paseo runtime or refuse naming the unconfigured state."""

    try:
        settings = config.paseo_runtime
    except ConfigError as error:
        raise PaseoBridgeFailure(RUNTIME_NOT_CONFIGURED, str(error)) from error
    if settings is None:
        raise PaseoBridgeFailure(
            RUNTIME_NOT_CONFIGURED,
            f"{NO_PASEO_RUNTIME_CONFIGURED}: {paseo_runtime_path(config.coordination_root)} has no paseoRuntime block",
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
            "identity yet; run runtime_install, then agents-remember dashboard --daemon.",
        )
    return server_id


def _bridge_reply(command: str, completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        reply = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise _unreadable_reply(command, completed) from error
    if not isinstance(reply, dict):
        raise _unreadable_reply(command, completed)
    if completed.returncode == 0 and reply.get("ok") is not False:
        return reply
    error = reply.get("error")
    if isinstance(error, dict):
        raise PaseoBridgeFailure(
            str(error.get("code") or BRIDGE_REFUSED)[:_CODE_LIMIT],
            str(error.get("message") or "The Paseo runtime refused the call.")[:_MESSAGE_LIMIT],
        )
    raise PaseoBridgeFailure(BRIDGE_REFUSED, "The Paseo runtime refused the call.")


def _unreadable_reply(
    command: str, completed: subprocess.CompletedProcess[str]
) -> PaseoBridgeFailure:
    """The script printed no JSON object; its standard error says why it died."""

    message = f"The Paseo bridge call {command!r} returned an unreadable reply."
    cause = _crash_cause(completed.stderr or "")
    if cause:
        message += f" Its standard error says: {cause}"
    return PaseoBridgeFailure(BRIDGE_INVALID_REPLY, message[:_MESSAGE_LIMIT])


def _crash_cause(stderr: str) -> str:
    """The last line that names an error; without one, the end of the text minus the stack."""

    errors = _ERROR_LINE.findall(stderr)
    if errors:
        return errors[-1].strip()[:_STDERR_TAIL]
    kept = [line for line in stderr.splitlines() if not _REPORT_NOISE.fullmatch(line)]
    return "\n".join(kept).strip()[-_STDERR_TAIL:]
