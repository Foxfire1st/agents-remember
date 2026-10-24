"""The installation's one host authority: the MCP authority settings file.

MIK-R196 rule 3 requires a cold launch to establish the validated host configuration it needs.
That configuration is the ``paseoRuntime`` block, and it belongs to the product's own authority —
the MCP settings file the tool server was started with, at the documented discovery
convention — not to
the developer's user settings file, which MIK-R137 keeps as the user's own artifact.

So this module owns one file: the authority settings file named by ``configPath``. It reads that
file's ``paseoRuntime`` block on every use (host authority is per-use, so a block added by a cold
first run is visible without restarting the server), and it creates the block exactly once, when
the file or the block is absent. An existing block is never rewritten: a host that is configured
stays configured, and the developer decides when real host settings change.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.fresh_host import fresh_host_block
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeSettings,
    parse_paseo_runtime_settings,
)

BLOCK_KEY = "paseoRuntime"
# The key this product writes into an authority file it created itself, and the one value it writes
# there. It is how a later launch tells its own cold scaffold from a file a developer wrote.
AUTHORITY_INIT_KEY = "settingsInit"
AUTHORITY_INIT_STATE = "host-authority"


class HostAuthorityRefused(ValueError):
    """A host authority that must not be written, with its own recovery action."""

    def __init__(self, code: str, detail: str, next_action: str) -> None:
        super().__init__(f"{detail} Next: {next_action}")
        self.code = code
        self.detail = detail
        self.next_action = next_action


def authority_settings_path(config_path: str | Path) -> Path:
    """The MCP authority settings file this process was started with, unresolved.

    The path is made absolute without following links: a symlink at this address must still look
    like a symlink when the writer asks, or the guard below is dead code and the block is written
    through the link into whatever it names (L188-B04, reproduced on 221820b2).
    """

    return Path(os.path.abspath(os.path.expanduser(str(config_path))))


def _read_authority_raw(path: Path) -> bytes | None:
    """The authority file's bytes as this call reads them, or ``None`` when it is absent.

    A file this process may not read is a modeled refusal, not the operating system's own error:
    callers see ``HostAuthorityRefused`` and the consumer's ``ConfigError``, as they did before the
    reader was split (L188-B04 review of the freeze).
    """

    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise HostAuthorityRefused(
            "host-authority-unreadable",
            f"The authority settings file {path} cannot be read: {error}.",
            "Repair its mode or its owner; this surface never writes a file it cannot read.",
        ) from error


def _decode_authority(raw: bytes, path: Path) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise HostAuthorityRefused(
            "host-authority-unreadable",
            f"The authority settings file {path} cannot be read as JSON: {error}",
            "Repair that file by hand; this surface never replaces it.",
        ) from error
    if not isinstance(data, dict):
        raise HostAuthorityRefused(
            "host-authority-unreadable",
            f"The authority settings file {path} is not a JSON object.",
            "Repair that file by hand; this surface never replaces it.",
        )
    return data


def _read_authority(path: Path) -> dict[str, Any]:
    raw = _read_authority_raw(path)
    if raw is None:
        raise HostAuthorityRefused(
            "host-authority-unreadable",
            f"The authority settings file {path} is not there.",
            "Create it, or let this surface create it on a cold root.",
        )
    return _decode_authority(raw, path)


def load_authority_paseo_runtime(config_path: str | Path) -> PaseoRuntimeSettings | None:
    """The authority file's ``paseoRuntime`` block; an absent file or block selects no host."""

    path = authority_settings_path(config_path)
    if not path.exists() and not path.is_symlink():
        return None
    settings = parse_paseo_runtime_settings(_read_authority(path).get(BLOCK_KEY))
    return None if settings is None else _with_source(settings, path)


def _with_source(settings: PaseoRuntimeSettings, path: Path) -> PaseoRuntimeSettings:
    return replace(settings, source_path=path)


def authority_paseo_block(config: Any) -> dict[str, Any]:
    """The complete block a cold authority file receives (one owner: the host settings module)."""

    return fresh_host_block(config.dashboard.port)


def establish_authority_paseo_runtime(
    config: Any, block: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create the host block once during setup; leave an existing block byte-identical."""

    path = authority_settings_path(config.config_path)
    incoming = dict(block) if block is not None else authority_paseo_block(config)
    if path.is_symlink():
        raise HostAuthorityRefused(
            "host-authority-symlink",
            f"The authority settings file {path} is a symlink; this surface never writes through it.",
            "Replace the symlink with the real settings file, or move the host settings by hand.",
        )
    raw = _read_authority_raw(path)
    data = _decode_authority(raw, path) if raw is not None else {}
    existed = raw is not None
    if data.get(BLOCK_KEY) is not None:
        return {
            "state": "present",
            "path": path.as_posix(),
            "changed": False,
            "keys": sorted(data),
        }
    document = {
        **data,
        BLOCK_KEY: incoming,
        AUTHORITY_INIT_KEY: AUTHORITY_INIT_STATE,
    }
    if not document.get("coordinationRoot"):
        document["coordinationRoot"] = config.coordination_root.as_posix()
    if not document.get("workspaceRoot"):
        document["workspaceRoot"] = config.workspace_root.as_posix()
    _write_authority(path, document)
    return {
        "state": "written" if existed else "created",
        "path": path.as_posix(),
        "changed": True,
        "keys": sorted(document),
    }


def _write_authority(path: Path, document: dict[str, Any]) -> None:
    """Create a private temporary beside the destination, then rename it over the name."""

    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        handle, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".new", dir=path.parent.as_posix()
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            # Apply the mode through the descriptor, never through a sibling pathname.
            os.fchmod(stream.fileno(), mode)
            json.dump(document, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException as error:
        cleanup = ""
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                cleanup = f" Removing this call's temporary failed: {cleanup_error}."
        remaining = (
            f"Temporary {temporary} {_name_state(Path(temporary))}."
            if temporary is not None
            else f"In {path.parent}, no temporary file was created by this call."
        )
        detail = (
            f"Publication to the authority settings file {path} failed: {error}. "
            f"Authority {path} {_name_state(path)}. {remaining}{cleanup}"
        )
        if isinstance(error, OSError):
            raise HostAuthorityRefused(
                "host-authority-write-failed",
                detail,
                "Repair the named file or folder's permissions or free space, then run again; "
                "remove any retained temporary only after inspecting the named state.",
            ) from error
        error.add_note(detail)
        raise


def _name_state(path: Path) -> str:
    """Describe a remaining name without following a symlink or hiding a read failure."""

    try:
        if path.is_symlink():
            return f"is a symlink to {os.readlink(path)!r}"
        return _actual_state(path.read_bytes())
    except FileNotFoundError:
        return "is absent"
    except OSError as error:
        return f"cannot be read: {error}"


def _actual_state(raw: bytes) -> str:
    """What a remaining file actually holds, in the file's own terms."""

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "holds bytes that are not a JSON object"
    if not isinstance(data, dict):
        return "holds a JSON value that is not an object"
    block = data.get(BLOCK_KEY)
    if isinstance(block, dict):
        listen = block.get("listen")
        return f"holds a {BLOCK_KEY} block with listen {listen!r} and keys {sorted(data)}"
    return f"holds no {BLOCK_KEY} block and keys {sorted(data)}"
