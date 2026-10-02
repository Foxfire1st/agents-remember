"""Write the provider entries into the daemon's ``config.json`` without a command line.

``paseo daemon config set`` takes its value as an argument, and a provider entry may carry an
``env`` map. A value on a command line is readable in the process list, so this one setting is
edited in the file itself and then handed to Paseo: a running daemon reloads the file, a stopped
one has it read back, and either way Paseo validates the whole file.

A file Paseo refuses would make every later Paseo call on this home fail, so the edit is
write-ahead: the previous file is kept beside the new one until Paseo has accepted it, and a pass
that finds that copy still there puts it back before it does anything else.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents_remember.cli.paseo_command import PaseoRuntimeFailure
from agents_remember.cli.paseo_plugin_files import AR_HOME_DIRECTORY

CONFIG_FILE = "config.json"
PROVIDER_ENTRIES = "agents.providers"


def previous_config_path(home: Path) -> Path:
    return home / AR_HOME_DIRECTORY / "config-previous.json"


def write_provider_entries(home: Path, providers: dict[str, dict[str, Any]]) -> None:
    """Replace ``agents.providers`` in the daemon configuration, keeping the previous file."""
    path = home / CONFIG_FILE
    previous = path.read_bytes()
    try:
        document = json.loads(previous)
    except json.JSONDecodeError:
        document = None
    agents = document.setdefault("agents", {}) if isinstance(document, dict) else None
    if not isinstance(agents, dict):
        raise PaseoRuntimeFailure(
            "daemon_config_unreadable", "config", f"{path} does not hold a configuration object"
        )
    agents["providers"] = providers
    _write_private(previous_config_path(home), previous)
    _write_private(path, (json.dumps(document, indent=2) + "\n").encode("utf-8"))


def accept_provider_entries(home: Path) -> None:
    """Paseo accepted the new file: the previous one is no longer needed."""
    previous_config_path(home).unlink(missing_ok=True)


def restore_previous_config(home: Path) -> bool:
    """Put back the file an unfinished or refused provider write replaced; ``True`` if it did."""
    previous = previous_config_path(home)
    if not previous.is_file():
        return False
    os.replace(previous, home / CONFIG_FILE)
    return True


def _write_private(path: Path, payload: bytes) -> None:
    """Replace ``path`` atomically with a file only the daemon's user can read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
