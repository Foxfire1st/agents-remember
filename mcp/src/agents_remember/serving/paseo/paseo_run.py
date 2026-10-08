"""The provision context and read-only admission for an install that must preserve sessions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.serving.paseo.paseo_command import PaseoCli
from agents_remember.serving.paseo.paseo_daemon import is_running
from agents_remember.serving.paseo.paseo_daemon_config import previous_config_path
from agents_remember.serving.paseo.paseo_node import node_executable_valid
from agents_remember.serving.paseo.paseo_packages import installed_lock_matches
from agents_remember.serving.paseo.paseo_process_record import ProcessReader, inspect_record
from agents_remember.serving.paseo.paseo_remedy import terminal_provision_remedy
from agents_remember.serving.paseo.paseo_settings import pending_settings, restart_reasons


class ProvisionIntent(Enum):
    EXPLICIT = "explicit"
    INSTALL = "install"


BindProbe = Callable[[str, int], OSError | None]


@dataclass
class _Run:
    settings: PaseoRuntimeSettings
    cli: PaseoCli
    plugin_source: Path
    probe: BindProbe
    reader: ProcessReader
    intent: ProvisionIntent = ProvisionIntent.EXPLICIT
    step: str = "install"
    changes: list[dict[str, Any]] = field(default_factory=list)
    restart_reasons: list[str] = field(default_factory=list)
    # Index of the first change applied to a daemon that this pass kept running.
    live_from: int | None = None
    # An undone provider write: a running daemon may have loaded the file that was undone.
    reload_owed: bool = False

    def change(self, step: str, action: str, **facts: Any) -> None:
        self.changes.append({"step": step, "action": action, **facts})


def _check_install_restart(run: _Run) -> None:
    """Read before any write: an install called by the host must keep its supervisor alive."""
    record = inspect_record(run.settings.home, "install", run.reader)
    if record.kind != "own":
        return
    facts = run.reader(record.pid) if record.pid is not None else None
    if (
        facts is not None
        and facts.node_executable is not None
        and facts.node_executable != product_node().node.as_posix()
    ):
        run.restart_reasons = ["node"]
        return
    if not node_executable_valid(product_node(), run.cli.runner):
        run.restart_reasons = ["node"]
        return
    if previous_config_path(run.settings.home).is_file():
        raise PaseoRuntimeFailure(
            "provider_recovery_required",
            "install",
            "An unfinished provider write must be recovered before the host can answer. "
            f"{terminal_provision_remedy(run.settings)} It restores the previous provider file first. "
            "runtime_install left the live host and recovery files untouched.",
        )
    status = run.cli.json("install", "daemon", "status", "--json")
    if not is_running(status) or not status.get("daemonVersion"):
        raise PaseoRuntimeFailure(
            "daemon_not_answering",
            "install",
            "the host supervisor is alive but not answering; "
            "the install left it untouched; retry runtime_install when it answers",
        )
    reasons = restart_reasons(run.settings, status, pending_settings(run.cli))
    if run.cli.installed_version() != run.settings.version and "version" not in reasons:
        reasons.append("version")
    if not installed_lock_matches(run.settings.install_prefix):
        reasons.append("packages")
    run.restart_reasons = reasons


def preserve_live_install(run: _Run, action: str) -> None:
    """Admission never authorizes replacing or stopping a subsequently observed live host."""
    if (
        run.intent is ProvisionIntent.INSTALL
        and inspect_record(run.settings.home, "install", run.reader).kind == "own"
    ):
        raise PaseoRuntimeFailure(
            "install_live_host_preserved",
            "install",
            f"Cannot {action} the live host during runtime_install; its sessions and install "
            "were preserved. Retry when its observations are stable, or use explicit terminal "
            "provision outside the host for a transition that ends agent sessions.",
        )
