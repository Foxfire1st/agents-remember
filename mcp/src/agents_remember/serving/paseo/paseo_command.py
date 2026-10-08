"""The single command-line boundary to npm and the pinned Paseo CLI.

Every process the Paseo runtime commands start goes through one runner callable, so a test can
substitute a fake command. Every Paseo call names the configured home with ``--home`` and runs
without inherited ``PASEO_*`` variables, so no other Paseo home is ever selected. The Paseo client
package is not used here; calling the daemon's agent functions belongs to the bridge.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.host_environment import host_environment
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings

COMMAND_NOT_FOUND = 127
COMMAND_TIMED_OUT = 124
DEFAULT_TIMEOUT_SECONDS = 60.0
_ERROR_TEXT_LIMIT = 2000


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def missing(self) -> bool:
        """The executable does not exist: nothing is installed at that path."""
        return self.returncode == COMMAND_NOT_FOUND


CommandRunner = Callable[[Sequence[str], float], CommandResult]


def run_command(argv: Sequence[str], timeout_seconds: float) -> CommandResult:
    """Run one npm or Paseo command and capture its output."""
    env = host_environment(os.environ)
    if len(argv) > 1 and str(argv[1]).endswith("/npm/bin/npm-cli.js"):
        env["PATH"] = str(Path(argv[0]).parent) + os.pathsep + env.get("PATH", "")
    try:
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            env=env,
        )
    except FileNotFoundError as error:
        return CommandResult(COMMAND_NOT_FOUND, "", str(error))
    except subprocess.TimeoutExpired:
        return CommandResult(COMMAND_TIMED_OUT, "", f"timed out after {timeout_seconds:g} s")
    except OSError as error:
        return CommandResult(1, "", str(error))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class PaseoCli:
    """The Paseo CLI of one install root, always addressed to the configured home."""

    settings: PaseoRuntimeSettings
    runner: CommandRunner = run_command
    root: Path | None = None

    @property
    def executable(self) -> Path:
        return (self.root or self.settings.install_prefix) / "node_modules" / ".bin" / "paseo"

    @property
    def node(self) -> Path:
        return product_node().node

    def installed_version(self) -> str | None:
        """The version this install root's CLI reports; ``None`` when missing or broken."""
        if not self.node.is_file() or not self.executable.is_file():
            return None
        result = self.runner(
            [
                self.node.as_posix(),
                self.executable.as_posix(),
                "--version",
                "--home",
                self.settings.home.as_posix(),
            ],
            DEFAULT_TIMEOUT_SECONDS,
        )
        version = result.stdout.strip()
        return version if result.returncode == 0 and version else None

    def call(self, *args: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> CommandResult:
        if not self.node.is_file():
            raise PaseoRuntimeFailure(
                "node_not_installed",
                "node",
                f"The product Node at {self.node} is not installed; run runtime_install.",
            )
        argv = [
            self.node.as_posix(),
            self.executable.as_posix(),
            *args,
            "--home",
            self.settings.home.as_posix(),
        ]
        return self.runner(argv, timeout)

    def json(
        self,
        step: str,
        *args: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        expect: type = dict,
    ) -> Any:
        """Run a command that prints one JSON object (or ``expect``) on success.

        A failed command carries Paseo's error text; an answer of another shape is a failure too.
        """
        result = self.call(*args, timeout=timeout)
        if result.returncode != 0:
            raise command_failure(step, args, result)
        return parse_json_output(step, args, result, expect)


def parse_json_output(
    step: str, args: Sequence[str], result: CommandResult, expect: type = dict
) -> Any:
    """The command's JSON answer, refused unless it has the expected shape.

    The answer itself is never echoed into the failure: a configuration read can carry provider
    values that must not reach a report.
    """
    try:
        answer = json.loads(result.stdout)
    except json.JSONDecodeError:
        answer = None
    if not isinstance(answer, expect):
        wanted = "a JSON array" if expect is list else "a JSON object"
        raise PaseoRuntimeFailure(
            "paseo_invalid_response",
            step,
            f"paseo {' '.join(args[:3])} did not return {wanted}",
        )
    return answer


def command_failure(step: str, args: Sequence[str], result: CommandResult) -> PaseoRuntimeFailure:
    if result.missing:
        return PaseoRuntimeFailure(
            "paseo_cli_unavailable", step, "the Paseo CLI is not installed in the install prefix"
        )
    return PaseoRuntimeFailure(
        "paseo_command_failed",
        step,
        f"paseo {' '.join(args[:3])} failed with exit code {result.returncode}",
        paseo_error_text(result),
    )


def paseo_error_text(result: CommandResult) -> str:
    """Paseo's own error message: the JSON error on stderr when there is one, else the text."""
    start = result.stderr.find("{")
    if start >= 0:
        try:
            error = json.loads(result.stderr[start:]).get("error")
        except (json.JSONDecodeError, AttributeError):
            error = None
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:_ERROR_TEXT_LIMIT]
    return (result.stderr.strip() or result.stdout.strip())[:_ERROR_TEXT_LIMIT]
