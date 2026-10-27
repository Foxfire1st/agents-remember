"""The one user settings file holds one thing this product initializes: a harness per role.

MIK-R196 rule 11 gives this surface one write: the validated harness choice of each role in
``~/.config/agents-remember/settings.json`` under ``XDG_CONFIG_HOME``, with model and effort left
unset so that the harness picks its own defaults. The host authority is not here -- ``paseoRuntime``
belongs to the installed MCP settings file (the discovery convention in
:mod:`agents_remember.cli.discovery`, see
``cli.discovery.SETTINGS_CONVENTION``), which this module never writes.

The write is one exclusive creation (``O_CREAT | O_EXCL``, mode 0600) of a destination that does
not exist. **An existing destination is never written, merged, completed or re-moded**, whatever
it holds and whoever wrote it: no key inside a file is custody of that file, and no exception to
rule 11 is approved (L188-B03). Every existing file -- a migrated one, a hand-written one, a
symlink, an unreadable one -- is read and reported as it stands, with the recovery that belongs
to what was found, and that report needs no live host. A destination that appears between the
preview and the write is refused. There is no replacement step and no temporary sibling path, so
nothing outside the one destination is ever opened for writing (L188-B04).

Nothing here prints a value that is not the settings file's own role choices.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel._agentic_settings_core import KNOWN_ROLES
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.named_choices import named as match_choice
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.primitives.user_settings_location import (
    UserSettingsLocationError,
    require_user_settings_path,
    user_settings_path,
)

ORCHESTRATION_KEY = "orchestration"
ROLES_KEY = "roles"
HARNESS_KEY = "harness"
# What a refusal names as ``path`` when even the settings location cannot be resolved (a relative
# ``XDG_CONFIG_HOME``): the refusal's own detail carries the reason and the recovery.
UNRESOLVED_SETTINGS_PATH = (
    "unresolved: the user settings location needs an absolute XDG_CONFIG_HOME"
)


class SettingsInitRefused(ValueError):
    """A settings initialization that must not be carried out, with its own recovery action."""

    def __init__(self, code: str, detail: str, next_action: str) -> None:
        super().__init__(f"{detail} Next: {next_action}")
        self.code = code
        self.detail = detail
        self.next_action = next_action


@dataclass(frozen=True)
class SettingsInitRequest:
    """What one initialization is asked to write: a harness per role, nothing else."""

    harness: str | None = None
    roles: Mapping[str, str] | None = None
    dry_run: bool = False


def user_settings_destination(config: McpRuntimeConfig) -> Path:
    """The one user settings path; the removed location is refused by name, never migrated."""

    try:
        return require_user_settings_path(config.coordination_root)
    except UserSettingsLocationError as error:
        raise SettingsInitRefused(
            "settings-location-upgrade-required",
            str(error),
            "Run the upgrade command named in detail; this surface does not move settings.",
        ) from error


def settings_path_without_read(config: McpRuntimeConfig) -> str:
    """The destination a refusal names, resolved without reading the file that refused.

    A refusal is answered from the refusal itself (L188-B07): re-entering the reader that raised
    would raise a second time on an unreadable, misplaced or colliding file. The old-location
    check is therefore not re-run; its own refusal names both paths and the upgrade command.
    """

    with contextlib.suppress(OSError, ValueError):
        return user_settings_destination(config).as_posix()
    with contextlib.suppress(OSError, ValueError):
        return user_settings_path().as_posix()
    return UNRESOLVED_SETTINGS_PATH


def read_settings_file(path: Path) -> dict[str, Any] | None:
    """The file's JSON object, ``None`` when there is none; anything else is refused by shape."""

    if not path.exists() and not path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise SettingsInitRefused(
            "settings-unreadable",
            f"{path} cannot be read as JSON: {error}",
            "Repair that file by hand; this surface never replaces it.",
        ) from error
    if not isinstance(data, dict):
        raise SettingsInitRefused(
            "settings-unreadable",
            f"{path} is not a JSON object.",
            "Repair that file by hand; this surface never replaces it.",
        )
    return data


def role_settings_state(config: McpRuntimeConfig) -> dict[str, Any]:
    """The role harnesses the file holds, and whether an absent one can still be created."""

    path = user_settings_destination(config)
    data = read_settings_file(path)
    return {
        "path": path.as_posix(),
        "exists": data is not None,
        "roleHarnesses": {} if data is None else _role_rows(data),
        # An existing file is never completed (L188-B03), so absence is the only destination this
        # surface can still initialize.
        "initializable": data is None,
    }


def initialize_role_settings(
    config: McpRuntimeConfig,
    request: SettingsInitRequest,
    *,
    runnable_harnesses: Callable[[McpRuntimeConfig], list[dict[str, str]] | None] | None = None,
) -> dict[str, Any]:
    """Create the settings file with one harness per role, exclusively, when it is absent.

    An existing destination is reported before any choice is validated or any host is asked
    anything: the file's own choices are the answer, and no live catalog is needed to read them.
    Only an absent destination reaches validation and the one exclusive create. ``runnable_harnesses``
    is the host-catalog reader the caller supplies (the CLI layer owns that read); without it the
    fallback refuses rather than importing the CLI from this layer.
    """

    path = user_settings_destination(config)
    _refuse_symlink(path)
    data = read_settings_file(path)
    if data is not None:
        return _existing_role_report(path, data, request)
    choices = _validated_choices(config, request, runnable_harnesses)
    content = _role_settings_file(choices)
    answer: dict[str, Any] = {
        "ok": True,
        "changed": True,
        "state": "created",
        "path": path.as_posix(),
        "roleHarnesses": choices,
        "writtenKeys": sorted(content),
        "preservedKeys": [],
        "dryRun": request.dry_run,
        "detail": f"{path} did not exist; this call created it with the harness of each role and "
        "model and effort unset",
    }
    if request.dry_run:
        return answer
    _write_after_preview(path, content, existed=False)
    answer["readBack"] = _state_or_none(role_settings_state, config)
    answer["settingsSources"] = _settings_sources(config)
    return answer


def _existing_role_report(
    path: Path, data: Mapping[str, Any], request: SettingsInitRequest
) -> dict[str, Any]:
    """Report an existing file's role harnesses; it is never completed or merged (L188-B03)."""

    return {
        "ok": True,
        "changed": False,
        "state": "already-initialized",
        "path": path.as_posix(),
        "roleHarnesses": _role_rows(data),
        "preservedKeys": sorted(data),
        "dryRun": request.dry_run,
        "detail": "the user settings already exist and are reported as they stand; this surface "
        "never overwrites, merges, completes or re-modes an existing file. A role this file does "
        "not name starts on the calling agent's own harness; choose one per role by editing that "
        "file (the settings page), never through this call.",
    }


def _role_rows(data: Mapping[str, Any]) -> dict[str, str]:
    """The ``orchestration.roles.<role>.harness`` map of one settings object, as it stands."""

    block = data.get(ORCHESTRATION_KEY)
    rows = block.get(ROLES_KEY) if isinstance(block, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {
        role: row[HARNESS_KEY]
        for role, row in rows.items()
        if isinstance(row, dict) and isinstance(row.get(HARNESS_KEY), str)
    }


def _validated_choices(
    config: McpRuntimeConfig,
    request: SettingsInitRequest,
    runnable_harnesses: Callable[[McpRuntimeConfig], list[dict[str, str]] | None] | None = None,
) -> dict[str, str]:
    """The per-role harness map, refused when it names no role or a harness nobody can run."""

    named = dict(request.roles or {})
    if request.harness:
        for role in sorted(KNOWN_ROLES - set(named)):
            named[role] = request.harness
    if not named:
        raise SettingsInitRefused(
            "no-harness-chosen",
            "No harness was chosen for any role; at least one is required.",
            "Choose one harness for every role, or a harness per role, and call again.",
        )
    unknown_roles = sorted(set(named) - KNOWN_ROLES)
    if unknown_roles:
        raise SettingsInitRefused(
            "unknown-role",
            f"The settings have no role named {', '.join(unknown_roles)}.",
            "Name roles this build publishes; see server_info.",
        )
    allowed = (runnable_harnesses or _runnable_harnesses)(config)
    if allowed is None:
        return dict(sorted(named.items()))
    resolved = {role: match_choice(allowed, harness) for role, harness in named.items()}
    refused = sorted({named[role] for role, choice in resolved.items() if choice is None})
    if refused:
        offered = ", ".join(row["id"] for row in allowed)
        raise SettingsInitRefused(
            "harness-not-hosted",
            f"The host cannot run {', '.join(refused)}. It can run: {offered}.",
            "Choose one of the harnesses the host runs, or install its CLI first.",
        )
    return {role: choice["id"] for role, choice in sorted(resolved.items()) if choice is not None}


def _runnable_harnesses(_config: McpRuntimeConfig) -> list[dict[str, str]] | None:
    """The fallback reader: this layer owns no host-catalog read (the caller supplies one).

    The CLI layer owns the catalog and passes its reader through the MCP tool handler, so the
    application layer never imports the CLI. A caller that reaches this fallback has not supplied
    the dependency and is refused rather than reading a host it cannot name.
    """

    raise SettingsInitRefused(
        "host-unreadable",
        "The host's providers cannot be read by this call path.",
        "Call the tool through the MCP server, which supplies the host catalog.",
    )


def _role_settings_file(choices: Mapping[str, str]) -> dict[str, Any]:
    """The one file a create writes: one harness row per role, and no other key."""

    return {
        ORCHESTRATION_KEY: {
            ROLES_KEY: {role: {HARNESS_KEY: harness} for role, harness in choices.items()}
        }
    }


def _settings_bytes(content: Mapping[str, Any]) -> bytes:
    return (json.dumps(content, indent=2, sort_keys=False) + "\n").encode("utf-8")


def _settings_sources(config: McpRuntimeConfig) -> list[str]:
    """The settings files this build resolves, for provenance; an unreadable set is reported empty."""

    with contextlib.suppress(OSError, ValueError):
        return [
            entry.as_posix() for entry in load_agentic_settings(config.coordination_root).sources
        ]
    return []


def _state_or_none(
    read: Callable[[McpRuntimeConfig], dict[str, Any]], config: McpRuntimeConfig
) -> dict[str, Any] | None:
    """A read-back that cannot turn a completed create into a raised failure (L188-B03)."""

    try:
        return read(config)
    except SettingsInitRefused:
        return None


def _refuse_symlink(path: Path) -> None:
    """A symlink is not an absent destination, and it is not a file this surface writes through."""

    if path.is_symlink():
        raise SettingsInitRefused(
            "destination-is-symlink",
            f"{path} is a symlink; a symlink is not a destination this surface writes.",
            "Ask the developer to point the settings at a real file and call again.",
        )


def _write_after_preview(path: Path, content: Mapping[str, Any], *, existed: bool) -> None:
    """Write only the destination the preview described as absent, and nothing else.

    ``existed`` is what the preview saw. A destination the preview saw, and one that appeared
    after it, are both refused as a destination that is present when the write would happen:
    this surface writes only a destination it found absent, so nothing it writes can replace,
    truncate, follow or re-mode a file that was there. There is no temporary sibling path and no
    replacement step; :func:`_create_exclusively` is the only writer.
    """

    if existed or path.is_symlink() or path.exists():
        raise SettingsInitRefused(
            "destination-appeared",
            f"{path} is present at write time; the preview described an absent destination.",
            "Read it and report it; the developer's own settings are never overwritten.",
        )
    _create_exclusively(path, content)


def _create_exclusively(path: Path, content: Mapping[str, Any]) -> None:
    """Create the file and its folder; an existing path of any kind is refused, never written.

    ``O_CREAT | O_EXCL`` is the guard: the kernel refuses a destination that appears between the
    preview and this create, and it refuses a symlink at that path, so no write ever follows a
    link or lands on a file this call did not create. The creation mode is 0600, so the bytes are
    never readable by another account, not even for the instant before a later ``chmod`` would
    have narrowed them.
    """

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as error:
        raise SettingsInitRefused(
            "settings-not-writable",
            f"{path} cannot be created: {error}",
            "Give the developer the folder that must be writable; do not change modes yourself.",
        ) from error
    with os.fdopen(handle, "wb") as stream:
        stream.write(_settings_bytes(content))
