"""Settings for the one dedicated Paseo runtime Agents Remember provisions.

The ``paseoRuntime`` authority block holds exactly six facts: where the pinned Paseo CLI is
installed, which daemon home it owns, where that daemon listens, the exact Paseo version, the
provider entries handed to the daemon unchanged, and the embed list. The block is optional as a
whole; when it is present every fact is required. Absence is the named state
:data:`NO_PASEO_RUNTIME_CONFIGURED`, which each consumer refuses in its own words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agents_remember.errors import AgentsRememberError

KNOWN_PASEO_RUNTIME_FIELDS = frozenset(
    {"installPrefix", "home", "listen", "version", "providers", "embed"}
)
KNOWN_PASEO_EMBED_FIELDS = frozenset({"dashboardOrigin", "frameBaseUrl"})
NO_PASEO_RUNTIME_CONFIGURED = "no Paseo runtime configured"

# One exact release: MAJOR.MINOR.PATCH with an optional prerelease. A range, a tag ("beta",
# "latest"), a partial version or build metadata is not a pin and is refused.
_EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")
_LISTEN_ADDRESS = re.compile(r"(?P<host>\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+):(?P<port>\d{1,5})")
_DEFAULT_PORTS = {"http": 80, "https": 443}


class PaseoRuntimeSettingsError(AgentsRememberError):
    """Raised when the paseoRuntime settings block is malformed."""


class PaseoRuntimeNotConfigured(AgentsRememberError):
    """The settings carry no paseoRuntime block; the message names that state."""

    code = "paseo_runtime_not_configured"


@dataclass(frozen=True)
class PaseoEmbedEntry:
    """A dashboard origin and the frame base URL its browser uses to reach the daemon."""

    dashboard_origin: str
    frame_base_url: str


@dataclass(frozen=True)
class PaseoRuntimeSettings:
    """The one Paseo daemon AR provisions: prefix, home, listen address and exact version."""

    install_prefix: Path
    home: Path
    listen: str
    version: str
    providers: dict[str, dict[str, Any]]
    embed: tuple[PaseoEmbedEntry, ...]

    @property
    def listen_host(self) -> str:
        return self.listen.rsplit(":", 1)[0].strip("[]")

    @property
    def listen_port(self) -> int:
        return int(self.listen.rsplit(":", 1)[1])

    def embed_payload(self) -> list[dict[str, str]]:
        """The embed list in the settings' own key names, for the plugin and for status."""
        return [
            {"dashboardOrigin": entry.dashboard_origin, "frameBaseUrl": entry.frame_base_url}
            for entry in self.embed
        ]


def parse_paseo_runtime_settings(raw: object) -> PaseoRuntimeSettings | None:
    """Parse the optional ``paseoRuntime`` authority block; ``None`` is the unconfigured state."""

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PaseoRuntimeSettingsError("paseoRuntime must be an object")
    unknown = sorted(set(raw) - KNOWN_PASEO_RUNTIME_FIELDS)
    if unknown:
        allowed = ", ".join(sorted(KNOWN_PASEO_RUNTIME_FIELDS))
        unknown_text = ", ".join(unknown)
        raise PaseoRuntimeSettingsError(
            f"unsupported paseoRuntime setting(s): {unknown_text}; allowed: {allowed}"
        )
    missing = sorted(KNOWN_PASEO_RUNTIME_FIELDS - set(raw))
    if missing:
        raise PaseoRuntimeSettingsError("paseoRuntime must define " + ", ".join(missing))
    return PaseoRuntimeSettings(
        install_prefix=_absolute_path(raw["installPrefix"], "installPrefix"),
        home=_absolute_path(raw["home"], "home"),
        listen=_listen_address(raw["listen"]),
        version=_exact_version(raw["version"]),
        providers=_provider_entries(raw["providers"]),
        embed=_embed_list(raw["embed"]),
    )


def require_paseo_runtime(
    settings: PaseoRuntimeSettings | None, *, source: Path | str
) -> PaseoRuntimeSettings:
    """Return the configured runtime or refuse naming the unconfigured state."""

    if settings is None:
        raise PaseoRuntimeNotConfigured(
            f"{NO_PASEO_RUNTIME_CONFIGURED}: {source} has no paseoRuntime block"
        )
    return settings


def _absolute_path(value: object, key: str) -> Path:
    if not isinstance(value, str) or not value:
        raise PaseoRuntimeSettingsError(f"paseoRuntime.{key} must be a non-empty string")
    path = Path(value)
    if not path.is_absolute():
        raise PaseoRuntimeSettingsError(f"paseoRuntime.{key} must be an absolute path")
    return path.resolve()


def _listen_address(value: object) -> str:
    match = _LISTEN_ADDRESS.fullmatch(value) if isinstance(value, str) else None
    if match is None or not 0 < int(match["port"]) < 65536:
        raise PaseoRuntimeSettingsError(
            "paseoRuntime.listen must be host:port with a port in 1..65535"
        )
    return match[0]


def _exact_version(value: object) -> str:
    if not isinstance(value, str) or _EXACT_VERSION.fullmatch(value) is None:
        raise PaseoRuntimeSettingsError(
            "paseoRuntime.version must be one exact Paseo version such as 0.11.0-beta.2; "
            "a range, a tag or a partial version is not a pin"
        )
    return value


def _provider_entries(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise PaseoRuntimeSettingsError(
            "paseoRuntime.providers must be an object keyed by provider id"
        )
    for provider_id, entry in value.items():
        if not isinstance(provider_id, str) or not provider_id or not isinstance(entry, dict):
            raise PaseoRuntimeSettingsError(
                "paseoRuntime.providers must map each non-empty provider id to an object"
            )
    return dict(value)


def _embed_list(value: object) -> tuple[PaseoEmbedEntry, ...]:
    if not isinstance(value, list):
        raise PaseoRuntimeSettingsError("paseoRuntime.embed must be a list")
    entries = tuple(_embed_entry(item, index) for index, item in enumerate(value))
    origins = [entry.dashboard_origin for entry in entries]
    repeated = sorted({origin for origin in origins if origins.count(origin) > 1})
    if repeated:
        raise PaseoRuntimeSettingsError(
            "paseoRuntime.embed lists a dashboard origin more than once: " + ", ".join(repeated)
        )
    return entries


def _embed_entry(value: object, index: int) -> PaseoEmbedEntry:
    label = f"paseoRuntime.embed[{index}]"
    if not isinstance(value, dict) or set(value) != KNOWN_PASEO_EMBED_FIELDS:
        raise PaseoRuntimeSettingsError(
            f"{label} must be an object with exactly dashboardOrigin and frameBaseUrl"
        )
    origin = value["dashboardOrigin"]
    if not _is_web_url(origin, origin_only=True):
        raise PaseoRuntimeSettingsError(
            f"{label}.dashboardOrigin must be an http(s) origin: scheme, host and optional "
            "port, without a path, query or credentials"
        )
    canonical = _canonical_origin(origin)
    if origin != canonical or not origin.isascii():
        hint = f"; write {canonical}" if canonical.isascii() else ""
        raise PaseoRuntimeSettingsError(
            f"{label}.dashboardOrigin must be the origin exactly as a browser reports it: "
            f"lower-case ASCII scheme and host, no default port{hint}"
        )
    frame_base_url = value["frameBaseUrl"]
    if not _is_web_url(frame_base_url, origin_only=False):
        raise PaseoRuntimeSettingsError(
            f"{label}.frameBaseUrl must be an http(s) URL without credentials, query or fragment"
        )
    return PaseoEmbedEntry(dashboard_origin=origin, frame_base_url=frame_base_url)


def _is_web_url(value: object, *, origin_only: bool) -> bool:
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        parts = urlsplit(value)
        port_is_valid = parts.port is None or 0 < parts.port < 65536
    except ValueError:
        return False
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname or not port_is_valid:
        return False
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return False
    return not (origin_only and parts.path)


def _canonical_origin(origin: str) -> str:
    """A validated origin as a browser serialises it, which is what later code compares with."""
    parts = urlsplit(origin)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = "" if parts.port in (None, _DEFAULT_PORTS[parts.scheme]) else f":{parts.port}"
    return f"{parts.scheme}://{host}{port}"
