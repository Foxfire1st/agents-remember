"""Write the provider entries into the daemon's ``config.json`` without a command line.

``paseo daemon config set`` takes its value as an argument, and a provider entry may carry an
``env`` map. A value on a command line is readable in the process list, so this one setting is
edited in the file itself and then handed to Paseo: a running daemon reloads the file, a stopped
one has it read back, and either way Paseo validates the whole file.

A file Paseo refuses would make every later Paseo call on this home fail, so the edit is
write-ahead: the previous file is kept beside the new one, with the digest of the file this pass
wrote, until Paseo has accepted it. The file belongs to Paseo, which rewrites it for its own
settings, so a rollback undoes only what the pass changed: the kept file goes back whole only while
``config.json`` is still the file the pass wrote; otherwise only ``agents.providers`` is put back
into the file as it is now.

The module also names the one setting whose value in the file the runtime status reports
(``AGENT_TOOLS_SETTING``) and reads it from the file, and it reads the tool rules that a
provider's entry denies, which a role start repeats; neither read changes anything.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from agents_remember.serving.paseo.paseo_command import PaseoRuntimeFailure
from agents_remember.serving.paseo.paseo_plugin_files import AR_HOME_DIRECTORY

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

# The composition root's port for the running daemon's configuration: the bridge's
# ``daemon-config`` call, bound by the package that may call the bridge (MIK-R192 rule 8).
type DaemonConfigRead = Callable[["McpRuntimeConfig"], dict[str, Any]]

CONFIG_FILE = "config.json"
PROVIDER_ENTRIES = "agents.providers"
# The running daemon's own shape for the same map, and the command that reads it. Paseo's client
# reports the providers at ``config.providers`` (the catalog reads them there too); the file's
# ``agents.providers`` is the shape on disk, never the daemon's. A bridge with no such command
# answers a refusal, and a refused read refuses the start that would record it (MIK-R192 rule 8).
DAEMON_PROVIDER_ENTRIES = "providers"
DAEMON_CONFIG_COMMAND = "daemon-config"
DAEMON_CONFIG_UNVERIFIED = "daemon_config_unverified"
# Paseo's own tools for creating and messaging agents, which Paseo can add to every agent it
# runs. Role agents start and message each other through AR's role tools only, so provision
# writes this setting off instead of leaving it to Paseo's default.
AGENT_TOOLS_SETTING = "daemon.mcp.injectIntoAgents"
AGENT_TOOLS_VALUE = False

Restored = Literal["file", "providers"]


def previous_config_path(home: Path) -> Path:
    return home / AR_HOME_DIRECTORY / "config-previous.json"


def written_digest_path(home: Path) -> Path:
    return home / AR_HOME_DIRECTORY / "config-written.sha256"


def write_provider_entries(home: Path, providers: dict[str, dict[str, Any]]) -> None:
    """Replace ``agents.providers`` in the daemon configuration, keeping the previous file."""
    path = home / CONFIG_FILE
    previous = path.read_bytes()
    document = _configuration(previous)
    if document is None or not isinstance(document.setdefault("agents", {}), dict):
        raise PaseoRuntimeFailure(
            "daemon_config_unreadable", "config", f"{path} does not hold a configuration object"
        )
    document["agents"]["providers"] = providers
    payload = _serialised(document)
    _write_private(written_digest_path(home), hashlib.sha256(payload).hexdigest().encode("ascii"))
    _write_private(previous_config_path(home), previous)
    _write_private(path, payload)


def accept_provider_entries(home: Path) -> None:
    """Paseo accepted the new file: the previous one is no longer needed."""
    previous_config_path(home).unlink(missing_ok=True)
    written_digest_path(home).unlink(missing_ok=True)


def restore_previous_config(home: Path) -> Restored | None:
    """Undo an unfinished or refused provider write; say what was put back, ``None`` if nothing.

    ``"file"``: ``config.json`` was still the file the pass wrote (or is no configuration object
    at all), so the kept file went back whole. ``"providers"``: the file changed since, so only
    ``agents.providers`` was put back into it and every other key stays as it is now.
    """
    previous = previous_config_path(home)
    digest = written_digest_path(home)
    if not previous.is_file():
        digest.unlink(missing_ok=True)
        return None
    path = home / CONFIG_FILE
    current = path.read_bytes() if path.is_file() else b""
    written = digest.read_text(encoding="ascii").strip() if digest.is_file() else None
    merged = None if hashlib.sha256(current).hexdigest() == written else _configuration(current)
    kept = _configuration(previous.read_bytes()) or {}
    if merged is None or not isinstance(merged.setdefault("agents", {}), dict):
        os.replace(previous, path)
        restored: Restored = "file"
    else:
        _put_back_provider_entries(merged, kept)
        _write_private(path, _serialised(merged))
        previous.unlink()
        restored = "providers"
    digest.unlink(missing_ok=True)
    return restored


def agent_tools_setting(home: Path) -> dict[str, Any]:
    """What ``config.json`` holds for Paseo's own agent tools, and whether AR wrote that value.

    ``configured`` is ``None`` when the file, or the key in it, is missing or unreadable. Any
    value but the one provision writes ``differs``: with ``true`` every agent of this home gets
    Paseo's tools to create and message agents beside AR's.
    """
    path = home / CONFIG_FILE
    value: Any = _configuration(path.read_bytes()) if path.is_file() else None
    for key in AGENT_TOOLS_SETTING.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return {
        "path": AGENT_TOOLS_SETTING,
        "expected": AGENT_TOOLS_VALUE,
        "configured": value,
        "differs": value is not AGENT_TOOLS_VALUE,
    }


def provider_denied_tools(home: Path, provider: str) -> tuple[str, ...]:
    """The tool rules ``config.json`` denies in the options of one provider's entry.

    Paseo lays the provider options of a request to create an agent over these, and a list in
    the request takes the place of the configured list (``mergeProviderOptions``). A start that
    hands Paseo rules of its own therefore repeats these, or the user's rules were out of force
    for that agent. The options are ``options``, and ``params`` where the entry has none, as
    Paseo reads them. A missing or unreadable file, entry or list reads as no rule. The rules
    of the entry's own ``disallowedTools`` are not here: Paseo adds those to every agent itself.
    """
    rules = _provider_options(home, provider).get("disallowedTools")
    if not isinstance(rules, list):
        return ()
    return tuple(rule for rule in rules if isinstance(rule, str) and rule)


def provider_extra_args(home: Path, provider: str) -> tuple[str, ...]:
    """The extra command-line arguments ``config.json`` gives one provider's entries (MIK-R192).

    Paseo merges its configured provider options into every start and every resume, so these
    reach a confined seat that AR cannot check. Read as ``options``, and ``params`` where the
    entry has none, like the denied tools above; a missing or unreadable file or entry reads as
    none.
    """
    return extra_args_in_options(_provider_options(home, provider))


def extra_args_in_options(options: object) -> tuple[str, ...]:
    """The names of one provider's configured ``extraArgs``, read from its option record."""
    arguments = options.get("extraArgs") if isinstance(options, dict) else None
    if not isinstance(arguments, dict):
        return ()
    return tuple(name for name in arguments if isinstance(name, str) and name)


def host_provider_extra_args(
    config: McpRuntimeConfig,
    provider: str,
    *,
    read_config: DaemonConfigRead | None = None,
) -> tuple[str, ...]:
    """The extra arguments the host's provider map gives ``provider``, in both places it is read.

    The file is read as the denied tools are; the running daemon's map is read through
    ``read_config``, the port the composition root binds to the bridge's ``daemon-config``
    command as it reads the profiles. This module ranks below the bridge's caller and never
    imports it (rule 19). A read that fails refuses: the file's map alone would present a
    drifted daemon as clean (rule 8).
    """
    runtime = config.paseo_runtime
    if runtime is None:
        return ()
    names = list(provider_extra_args(runtime.home, provider))
    live = daemon_provider_extra_args(config, provider, read_config=read_config)
    names.extend(name for name in live if name not in names)
    return tuple(names)


def daemon_provider_extra_args(
    config: McpRuntimeConfig,
    provider: str,
    *,
    read_config: DaemonConfigRead | None = None,
) -> tuple[str, ...]:
    """The running daemon's map for one provider, read from the document the daemon reports.

    That document holds the providers at ``providers`` -- Paseo's own client reports them as
    ``config.providers`` -- so the file's ``agents.providers`` is not its shape. A provider the
    daemon's map does not name, or one whose entry names no list, gives no argument: that is a
    map that was read. A read that fails, or a caller with no port, raises instead of answering
    nothing, so a failed read is never the clean answer (rule 8).
    """
    if read_config is None:
        raise PaseoRuntimeFailure(
            DAEMON_CONFIG_UNVERIFIED,
            "config",
            f"the running daemon's provider map for {provider!r} was not read: no reader was "
            "supplied, and a map that was not read is not a clean one",
        )
    document = read_config(config)
    return extra_args_in_options(
        _entry_options(_named(document, DAEMON_PROVIDER_ENTRIES), provider)
    )


def _provider_options(home: Path, provider: str) -> dict[str, Any]:
    path = home / CONFIG_FILE
    value: Any = _configuration(path.read_bytes()) if path.is_file() else None
    return _entry_options(_named(value, PROVIDER_ENTRIES), provider)


def _named(document: object, dotted: str) -> object:
    """Walk a dotted key into a parsed document or a daemon reply; anything missing is ``None``."""

    for key in dotted.split("."):
        document = document.get(key) if isinstance(document, dict) else None
    return document


def _entry_options(entries: object, provider: str) -> dict[str, Any]:
    """One provider's option record, ``options``, and ``params`` where the entry has none."""

    entry = entries.get(provider) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return {}
    options = entry.get("options") if entry.get("options") is not None else entry.get("params")
    return options if isinstance(options, dict) else {}


def remove_stale_temporaries(home: Path) -> list[Path]:
    """Delete temporary files a killed provider write left behind; they can hold provider values."""
    stale = [*home.glob(f".{CONFIG_FILE}.*.tmp"), *(home / AR_HOME_DIRECTORY).glob(".config-*.tmp")]
    for path in stale:
        path.unlink(missing_ok=True)
    return stale


def _put_back_provider_entries(document: dict[str, Any], kept: dict[str, Any]) -> None:
    kept_agents = kept.get("agents")
    if isinstance(kept_agents, dict) and "providers" in kept_agents:
        document["agents"]["providers"] = kept_agents["providers"]
        return
    document["agents"].pop("providers", None)
    if not document["agents"] and "agents" not in kept:
        del document["agents"]


def _configuration(payload: bytes) -> dict[str, Any] | None:
    try:
        document = json.loads(payload)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _serialised(document: dict[str, Any]) -> bytes:
    """The way Paseo writes the file: two-space indent, text kept as UTF-8, a final newline."""
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


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
