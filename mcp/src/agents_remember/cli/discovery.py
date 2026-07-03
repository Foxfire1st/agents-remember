"""Trusted-settings discovery for the umbrella CLI (L1 of 260703).

``agents-remember dashboard`` (and future subcommands) should run without
``--config`` from anywhere under the workspace. Discovery walks the current
directory upward; at each level it probes, in order:

1. the settings convention ``.claude/mcp/agents-remember-settings.json``, and
2. an ``.mcp.json`` whose ``mcpServers``/``agents-remember`` entry records a
   ``--config`` argument — the harness's own registration, reused verbatim so
   the CLI and the harness always boot from the same settings file.

The nearest directory wins and the first USABLE file ends the walk — usable
meaning it parses as JSON and its ``coordinationRoot`` is an existing absolute
directory. That semantic probe matters because the repository itself ships a
placeholder template at the convention path (``<PATH/TO/YOUR/...>``), which
must never shadow the real settings above it. A malformed or foreign
``.mcp.json`` is skipped silently: discovery must never crash on someone
else's file. An explicit ``--config`` always bypasses this.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS_CONVENTION = Path(".claude") / "mcp" / "agents-remember-settings.json"
MCP_REGISTRATION = ".mcp.json"
_SERVER_NAME = "agents-remember"


class ConfigDiscoveryError(Exception):
    """No trusted settings file was discoverable from the starting directory."""


def discover_config(start: Path | None = None) -> Path:
    """The nearest trusted settings JSON at or above ``start`` (default: cwd)."""
    origin = (start or Path.cwd()).resolve()
    for directory in (origin, *origin.parents):
        convention = directory / SETTINGS_CONVENTION
        if _is_usable_settings(convention):
            return convention
        registered = _config_from_mcp_registration(directory / MCP_REGISTRATION)
        if registered is not None and _is_usable_settings(registered):
            return registered
    raise ConfigDiscoveryError(
        "no trusted settings found: pass --config, or run from a directory at or below "
        f"one containing {SETTINGS_CONVENTION.as_posix()} or an {MCP_REGISTRATION} whose "
        f"{_SERVER_NAME!r} entry records a --config path (walked up from {origin})"
    )


def _config_from_mcp_registration(mcp_json: Path) -> Path | None:
    """The ``--config`` path recorded for the agents-remember server, if any."""
    if not mcp_json.is_file():
        return None
    try:
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    entry = servers.get(_SERVER_NAME)
    if not isinstance(entry, dict):
        return None
    arguments = entry.get("args")
    if not isinstance(arguments, list):
        return None
    for index, argument in enumerate(arguments):
        if argument == "--config" and index + 1 < len(arguments):
            candidate = str(arguments[index + 1])
            if candidate:
                return Path(candidate)
    return None


def _is_usable_settings(path: Path) -> bool:
    """True for a real trusted settings file — not the shipped placeholder template.

    The probe is semantic, not syntactic: the file must parse as a JSON object whose
    ``coordinationRoot`` is an existing absolute directory. The repository's tracked
    template carries ``<PATH/TO/YOUR/...>`` placeholders and fails this, so a source
    checkout never shadows the workspace's real settings.
    """
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    root = data.get("coordinationRoot")
    if not isinstance(root, str) or not root:
        return False
    candidate = Path(root)
    return candidate.is_absolute() and candidate.is_dir()
