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
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.control_vocabulary import (
    BRIDGE_REFUSED,
    BRIDGE_TIMEOUT,
    SEND_OUTCOME_UNKNOWN,
)
from agents_remember.kernel.primitives.paseo_authority import authority_settings_path
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    NO_PASEO_RUNTIME_CONFIGURED,
    PaseoRuntimeNotConfigured,
    PaseoRuntimeSettings,
)
from agents_remember.kernel.primitives.runtime_config import ConfigError, McpRuntimeConfig
from agents_remember.kernel.read_cancellation import (
    ReadCancelled,
    raise_if_read_cancelled,
    read_cancellation,
)
from agents_remember.serving.paseo.paseo_daemon_config import (
    DAEMON_CONFIG_COMMAND,
    DAEMON_CONFIG_UNVERIFIED,
)
from agents_remember.serving.paseo.paseo_node import node_status
from agents_remember.serving.paseo.paseo_process_record import (
    ProcessUnreadable,
    inspect_record,
    read_process,
)
from agents_remember.serving.paseo.paseo_remedy import terminal_provision_remedy
from agents_remember.serving.paseo.paseo_running_node import communication_node

# Every bridge call ends within this limit; a call that does not is stopped and reported as a
# timeout. An operation that has to wait longer is a sequence of calls.
__all__ = [
    "BRIDGE_REFUSED",
    "BRIDGE_TIMEOUT",
    "SEND_OUTCOME_UNKNOWN",
    "PaseoBridgeFailure",
    "bridge_call",
    "daemon_configuration",
]

PASEO_BRIDGE_TIMEOUT_SECONDS = 60
_BRIDGE_POLL_SECONDS = 0.05
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
BRIDGE_UNAVAILABLE = "paseo_bridge_unavailable"
BRIDGE_INVALID_REPLY = "paseo_bridge_invalid_reply"
# agent-send: the connection was lost while the message was being sent, so the host may
# have taken it. What the product does with that is MIK-R171 rule 3.

# agent-create: the agent exists without its first message and its closed session cannot be
# opened again, so no repeat of the call can deliver the message.
AGENT_WITHOUT_MESSAGE_LOST = "paseo_agent_without_message_lost"


class PaseoBridgeFailure(RuntimeError):
    """One bridge call failed; ``code`` names the reason and the message is short."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _DaemonConfigAnswer:
    document: dict[str, Any] | None
    error: str | None
    read_at: str | None


_OPERATION_DAEMON_CONFIG: ContextVar[dict[tuple[str, ...], _DaemonConfigAnswer] | None] = (
    ContextVar("paseo_operation_daemon_config", default=None)
)


def paseo_catalog_scope(config: McpRuntimeConfig) -> tuple[str, str, str, str]:
    """The identity of one Paseo runtime and its saved catalogue."""
    settings = require_bridge_runtime(config)
    return (
        settings.install_prefix.as_posix(),
        settings.home.as_posix(),
        settings.listen,
        settings.version,
    )


@contextmanager
def launcher_daemon_configuration() -> Iterator[None]:
    """Keep configuration answers within the enclosing catalog question."""
    token = _OPERATION_DAEMON_CONFIG.set({})
    try:
        yield
    finally:
        _OPERATION_DAEMON_CONFIG.reset(token)


def record_operation_daemon_config(
    scope: tuple[str, ...], reply: dict[str, Any] | None, error: str | None
) -> None:
    """Record the metadata call's configuration answer or its failure."""
    answers = _OPERATION_DAEMON_CONFIG.get()
    if answers is None:
        return
    data = reply or {}
    document = data.get("daemonConfig")
    error = error if error is not None else data.get("daemonConfigError")
    if isinstance(document, dict) or isinstance(error, str):
        answers[scope] = _DaemonConfigAnswer(
            document if isinstance(document, dict) else None,
            error if isinstance(error, str) else None,
            data.get("readAt"),
        )
    else:
        answers.pop(scope, None)


def _operation_daemon_answer(config: McpRuntimeConfig) -> _DaemonConfigAnswer | None:
    answers = _OPERATION_DAEMON_CONFIG.get()
    return answers.get(paseo_catalog_scope(config)) if answers else None


def operation_daemon_config(
    config: McpRuntimeConfig,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Return whether this operation recorded a configuration, and its document or error."""
    answer = _operation_daemon_answer(config)
    return (True, answer.document, answer.error) if answer is not None else (False, None, None)


def operation_role_profiles(config: McpRuntimeConfig) -> dict[str, Any] | None:
    """Project role profiles from the operation's configuration with its original read time."""
    answer = _operation_daemon_answer(config)
    if answer is None:
        return None
    if answer.error is not None:
        raise PaseoBridgeFailure(DAEMON_CONFIG_UNVERIFIED, answer.error)
    assert answer.document is not None
    return {"profiles": answer.document.get("agentProfiles", []), "readAt": answer.read_at}


def bridge_call(
    config: McpRuntimeConfig,
    command: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run one bridge command against the configured Paseo runtime and return its result."""

    raise_if_read_cancelled()
    if timeout_seconds is None:
        timeout_seconds = PASEO_BRIDGE_TIMEOUT_SECONDS
    settings = require_bridge_runtime(config)
    try:
        node = communication_node(settings, read_process)
    except PaseoRuntimeFailure as error:
        code = error.code if error.code == "node_not_installed" else BRIDGE_UNAVAILABLE
        raise PaseoBridgeFailure(code, str(error)) from error
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
        completed = _run_bridge(
            [node.as_posix(), script.as_posix(), command],
            json.dumps(payload, ensure_ascii=False),
            timeout_seconds,
            env={
                **_bridge_environment(settings),
                "AR_PASEO_DEADLINE_MS": str(
                    min(_SCRIPT_DEADLINE_MS, max(1, int(timeout_seconds * 1000) - 100))
                ),
            },
        )
    except subprocess.TimeoutExpired as error:
        raise_if_read_cancelled()
        raise PaseoBridgeFailure(
            BRIDGE_TIMEOUT,
            f"The Paseo bridge call {command!r} did not end within "
            f"{timeout_seconds} seconds and was stopped.",
        ) from error
    except OSError as error:
        raise_if_read_cancelled()
        raise PaseoBridgeFailure(
            BRIDGE_UNAVAILABLE, f"The Paseo bridge could not be started: {error}"
        ) from error
    reply = _bridge_reply(command, completed)
    try:
        record = inspect_record(settings.home, "bridge", read_process)
        facts = (
            read_process(record.pid) if record.kind == "own" and record.pid is not None else None
        )
    except (PaseoRuntimeFailure, ProcessUnreadable):
        # The host can exit or become unreadable after acknowledging the command. That
        # observation must never change the acknowledged result into an uncertain failure.
        return reply
    if facts is None:
        return reply
    state = node_status(facts.node_executable if facts else None)
    if not state["restartRequired"]:
        return reply
    return {
        **reply,
        **state,
        "restartRemedy": terminal_provision_remedy(settings) if state["restartRequired"] else None,
    }


def _run_bridge(
    argv: list[str], input_text: str | None, timeout: float, *, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    # Let malformed UTF-8 reach the named unreadable-reply path in either runner.
    if read_cancellation() is None:
        return subprocess.run(
            argv,
            input=input_text,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout,
            env=env,
        )

    raise_if_read_cancelled()
    with subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=True,
    ) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                raise_if_read_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    stdout, stderr = process.communicate(
                        input=input_text, timeout=min(_BRIDGE_POLL_SECONDS, remaining)
                    )
                except subprocess.TimeoutExpired:
                    input_text = None  # communicate resumes draining from its first call
                    continue
                raise_if_read_cancelled()
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        except (OSError, ReadCancelled, subprocess.TimeoutExpired):
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise


def daemon_configuration(config: McpRuntimeConfig) -> dict[str, Any]:
    """The running daemon's configuration document, as its own ``daemon-config`` command reads it.

    This is the composition root's port for that read (``serving/paseo/paseo_daemon_config.py``
    declares it): the bridge's caller ranks above the serving layer, so the serving module takes
    the reader rather than importing this one. A failed call, and a reply that holds no
    configuration object, both raise: a map that was not read is not a clean one (MIK-R192 rule 8).
    """

    recorded, document, error = operation_daemon_config(config)
    try:
        if error is not None:
            raise PaseoBridgeFailure(DAEMON_CONFIG_UNVERIFIED, error)
        if not recorded:
            document = bridge_call(config, DAEMON_CONFIG_COMMAND, {}).get("config")
    except PaseoBridgeFailure as failure:
        raise PaseoRuntimeFailure(
            DAEMON_CONFIG_UNVERIFIED,
            "config",
            f"the running daemon's configuration could not be read ({failure.code}): {failure}",
        ) from failure
    if not isinstance(document, dict):
        raise PaseoRuntimeFailure(
            DAEMON_CONFIG_UNVERIFIED,
            "config",
            "the running daemon answered no configuration document, so its provider map was "
            "not read",
        )
    return document


def require_bridge_runtime(config: McpRuntimeConfig) -> PaseoRuntimeSettings:
    """Return the configured Paseo runtime or refuse naming the unconfigured state."""

    try:
        settings = config.paseo_runtime
    except ConfigError as error:
        raise PaseoBridgeFailure(RUNTIME_NOT_CONFIGURED, str(error)) from error
    if settings is None:
        raise PaseoBridgeFailure(
            RUNTIME_NOT_CONFIGURED,
            f"{NO_PASEO_RUNTIME_CONFIGURED}: {authority_settings_path(config.config_path)} has no paseoRuntime block",
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
