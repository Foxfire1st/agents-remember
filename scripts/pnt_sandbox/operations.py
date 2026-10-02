"""Everything the sandbox commands run or start: the one seam a test replaces.

Every child process receives the sandbox environment (``environment.py``). Code of the PNT build
is never imported here; it runs in the checkout's own Python environment, through the checkout's
``agents_remember`` command line or through the two helper scripts next to this module.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .environment import child_environment, removed_names, sandbox_environment
from .layout import LOOPBACK, SandboxLayout, SandboxRefusal
from .lock import HeldLock
from .procfs import ProcessIdentity, environment, started_identity
from .record import verified_supervisor

HELPERS = Path(__file__).resolve().parent
TOOLING_CHECKOUT = HELPERS.parents[1]
# What makes a directory a PNT build: the AR package with the Paseo runtime commands of PNT-R01
# and the plugin they install, and the dashboard sources the bundle is built from.
PNT_BUILD_FILES = (
    "mcp/pyproject.toml",
    "mcp/src/agents_remember/cli/dashboard.py",
    "mcp/src/agents_remember/cli/paseo_runtime.py",
    "mcp/src/agents_remember/kernel/primitives/paseo_runtime_settings.py",
    "mcp/src/agents_remember/package_data/paseo_plugin/paseo-plugin.json",
    "dashboard/package.json",
    "scripts/sync-dashboard.py",
)
_ERROR_TAIL = 1500


class StepFailed(Exception):
    """One named step failed; ``log`` is the file that holds its output, when there is one."""

    def __init__(self, step: str, message: str, log: Path | None = None) -> None:
        super().__init__(message)
        self.step = step
        self.log = log


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str

    @property
    def tail(self) -> str:
        return (self.stderr.strip() or self.stdout.strip())[-_ERROR_TAIL:]


def checkout_refusal(checkout: Path) -> str | None:
    """Why ``checkout`` is not a PNT build, or ``None`` when it is one."""
    missing = [name for name in PNT_BUILD_FILES if not (checkout / name).is_file()]
    if missing:
        return f"{checkout} is not a PNT build checkout: it has no {', '.join(missing)}"
    return None


class Operations:
    """Runs and starts things for one sandbox, each with the sandbox environment."""

    # How long a started dashboard may take to answer before the start counts as failed.
    dashboard_wait_seconds = 90.0

    def __init__(self, layout: SandboxLayout, environ: Mapping[str, str] | None = None) -> None:
        self.layout = layout
        base = os.environ if environ is None else environ
        self.environment = sandbox_environment(base, layout)
        self.removed_variables = removed_names(base)
        # Set by the command that holds the sandbox's lock, for the provision run to share.
        self.held_lock: HeldLock | None = None

    def run(self, argv: Sequence[str], *, cwd: Path, timeout: float) -> Completed:
        try:
            done = subprocess.run(
                list(argv),
                cwd=cwd,
                env=child_environment(self.environment, cwd),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return Completed(124, "", f"timed out after {timeout:g} s")
        except OSError as error:
            return Completed(127, "", str(error))
        return Completed(done.returncode, done.stdout, done.stderr)

    def require(self, step: str, argv: Sequence[str], *, cwd: Path, timeout: float) -> Completed:
        done = self.run(argv, cwd=cwd, timeout=timeout)
        if done.returncode != 0:
            raise StepFailed(step, f"{' '.join(argv[:3])} failed ({done.returncode}): {done.tail}")
        return done

    # --- the checkout's own environment -----------------------------------------------------

    def build_python(self, checkout: Path) -> Path:
        return checkout / "mcp" / ".venv" / "bin" / "python"

    def prepare_python(self, checkout: Path) -> list[str]:
        """Create the checkout's Python environment when it is missing.

        Like the dashboard bundle below it is an ignored build product of the checkout; the
        checkout's tracked files are never written.
        """
        if self.build_python(checkout).is_file():
            return []
        self.require(
            "python environment", ["uv", "sync", "--all-extras"], cwd=checkout / "mcp", timeout=900
        )
        return ["created the checkout's Python environment (uv sync --all-extras)"]

    def prepare_bundle(self, checkout: Path) -> list[str]:
        """Build the dashboard bundle the checkout serves, when it is missing or stale.

        The checkout's own ``scripts/sync-dashboard.py --check`` decides: it compares the placed
        bundle with the fingerprint of the dashboard sources as they stand.
        """
        sync = [self.build_python(checkout).as_posix(), "scripts/sync-dashboard.py"]
        if self.run([*sync, "--check"], cwd=checkout, timeout=120).returncode == 0:
            return []
        dashboard = checkout / "dashboard"
        if not (dashboard / "node_modules").is_dir():
            self.require("dashboard bundle", ["npm", "ci"], cwd=dashboard, timeout=1200)
        self.require("dashboard bundle", ["npm", "run", "build"], cwd=dashboard, timeout=1200)
        self.require("dashboard bundle", sync, cwd=checkout, timeout=120)
        return ["built the dashboard bundle from the checkout's sources"]

    def helper(self, checkout: Path, script: str, args: Sequence[str], timeout: float) -> Completed:
        """Run one of this package's helper scripts inside the build's Python environment."""
        argv = [self.build_python(checkout).as_posix(), (HELPERS / script).as_posix(), *args]
        return self.run(argv, cwd=self.layout.root, timeout=timeout)

    def resolve_roots(self, checkout: Path, mode: str) -> dict[str, Any]:
        done = self.helper(
            checkout,
            "build_roots.py",
            ["--config", self.layout.settings_file.as_posix(), "--mode", mode],
            120,
        )
        if done.returncode != 0:
            raise StepFailed("safety check", f"the build's root resolver failed: {done.tail}")
        report = json.loads(done.stdout)
        if not isinstance(report, dict):
            raise StepFailed("safety check", "the build's root resolver returned no object")
        return report

    def tool_calls(self, checkout: Path, request: Path) -> dict[str, Any]:
        done = self.helper(checkout, "build_tool_calls.py", ["--request", request.as_posix()], 600)
        try:
            report = json.loads(done.stdout)
        except ValueError:
            report = None
        if not isinstance(report, dict):
            raise StepFailed(
                "sandbox corpus", f"the build's tool server did not answer: {done.tail}"
            )
        return report

    # --- the Paseo runtime, through the commands of PNT-R01 ---------------------------------

    def paseo_argv(self, checkout: Path, command: str) -> list[str]:
        return [
            self.build_python(checkout).as_posix(),
            "-m",
            "agents_remember.cli",
            "paseo",
            command,
            "--config",
            self.layout.settings_file.as_posix(),
        ]

    def paseo(self, checkout: Path, command: str, timeout: float = 1800) -> dict[str, Any]:
        """``agents-remember paseo <command>`` of the checkout, on the sandbox settings."""
        argv = self.paseo_argv(checkout, command)
        if command == "provision" and self.held_lock is not None:
            done = self._run_sharing_lock(argv, self.held_lock, f"paseo {command}", timeout)
        else:
            done = self.run(argv, cwd=self.layout.root, timeout=timeout)
        try:
            report = json.loads(done.stdout)
        except ValueError:
            report = None
        if not isinstance(report, dict):
            raise StepFailed(f"paseo {command}", f"no report from the command: {done.tail}")
        return report

    def _run_sharing_lock(
        self, argv: Sequence[str], held: HeldLock, name: str, timeout: float
    ) -> Completed:
        """Run a child that keeps the sandbox locked for as long as it runs.

        Provision installs and starts things for minutes. Were the command that started it
        killed, the lock would go with it and a second provision could run beside the first; the
        child inherits the locked file instead, and the holder record names it.
        """
        cwd = self.layout.root
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=child_environment(self.environment, cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                pass_fds=(held.fileno(),),
            )
        except OSError as error:
            return Completed(127, "", str(error))
        held.note_child(name, process.pid)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return Completed(124, "", f"timed out after {timeout:g} s")
        finally:
            held.note_child(name, None)
        return Completed(process.returncode, stdout, stderr)

    def paseo_supervisor(self) -> ProcessIdentity | None:
        """The running daemon of the sandbox's Paseo home, proven from its own process record."""
        return verified_supervisor(self.layout.paseo_home)

    def process_environment(self, pid: int) -> dict[str, str] | None:
        """The environment a running process was started with; ``None`` when not readable."""
        return environment(pid)

    # --- the dashboard ----------------------------------------------------------------------

    def dashboard_argv(self, checkout: Path) -> list[str]:
        return [
            self.build_python(checkout).as_posix(),
            "-m",
            "agents_remember.cli",
            "dashboard",
            "--config",
            self.layout.settings_file.as_posix(),
            "--host",
            LOOPBACK,
            "--port",
            str(self.layout.dashboard_port),
            "--no-access-log",
        ]

    def spawn_dashboard(self, checkout: Path) -> ProcessIdentity:
        """Start the dashboard detached, in a session of its own, logging to the sandbox."""
        self.layout.run_dir.mkdir(parents=True, exist_ok=True)
        argv = self.dashboard_argv(checkout)
        with open(self.layout.dashboard_log, "ab") as log:
            process = subprocess.Popen(
                argv,
                cwd=self.layout.root,
                env=child_environment(self.environment, self.layout.root),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        identity = started_identity(process.pid, argv)
        if identity is None:
            raise StepFailed("dashboard", "the dashboard exited at once", self.layout.dashboard_log)
        return identity

    def dashboard_answers(self) -> bool:
        try:
            with urllib.request.urlopen(self.layout.dashboard_url, timeout=3) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def stop_tmux_server(self) -> str | None:
        """Stop the sandbox's own terminal multiplexer server, when it has one."""
        socket_path = self.layout.tmux_dir / f"tmux-{os.getuid()}" / "default"
        if not socket_path.exists():
            return None
        done = self.run(
            ["tmux", "-S", socket_path.as_posix(), "kill-server"], cwd=self.layout.root, timeout=20
        )
        return "stopped" if done.returncode == 0 else "not running"


def require_checkout(checkout: Path) -> Path:
    """The absolute path of a PNT build checkout; anything else is refused."""
    resolved = checkout.expanduser().resolve()
    refusal = checkout_refusal(resolved)
    if refusal is not None:
        raise SandboxRefusal(refusal)
    return resolved
