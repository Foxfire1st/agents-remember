"""Where the dashboard's Chats pane frames the Paseo web UI.

The frame route answers one question: which frame base URL must a browser on *this* dashboard
origin use to reach the configured Paseo runtime? The answer comes from the embed list
(``paseoRuntime.embed``), never from a fixed loopback value, and carries no secret. The dashboard
builds the frame URL itself from the base URL, the daemon's server id and the host fields of the
execution it displays.

An unavailable frame names exactly one reason, checked in this order: no Paseo runtime is
configured, the request's dashboard origin is not in the embed list, or the daemon is unreachable.

The runtime itself is reached only through the bridge. Whether its daemon answers, its server id
and the workspace of the Projects folder come from one function, :func:`host_frame_facts`, which
is the single place this module touches the host.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request

from agents_remember.kernel.primitives.paseo_runtime_settings import (
    NO_PASEO_RUNTIME_CONFIGURED,
    PaseoRuntimeSettings,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

REASON_NOT_CONFIGURED = "not-configured"
REASON_ORIGIN_NOT_LISTED = "origin-not-listed"
REASON_UNREACHABLE = "unreachable"
ORIGIN_NOT_LISTED_TEXT = "embedded chat is not configured for this address"
BRIDGE_NOT_WIRED = "the frame route is not wired to the Paseo bridge in this build"
_DEFAULT_PORTS = {"http": 80, "https": 443}
_DETAIL_LIMIT = 300


@dataclass(frozen=True)
class HostFrameFacts:
    """What the runtime says for the frame: does its daemon answer, who is it, where is Projects.

    ``detail`` says why the daemon counts as unreachable, or why no Projects workspace is named.
    """

    reachable: bool
    server_id: str | None = None
    projects_workspace_id: str | None = None
    detail: str | None = None


HostFrameFactsCall = Callable[[McpRuntimeConfig], HostFrameFacts]


def host_frame_facts(config: McpRuntimeConfig) -> HostFrameFacts:
    """Ask the configured runtime, through the bridge, for the facts the frame needs.

    NOT WIRED YET: the bridge (``cli/paseo_bridge.py``, ``bridge_call``) is not in this leaf's
    base, so this answers "unreachable" until it is. This is the one function to fill in:

    1. Reachability and server id: one read-only bridge command that returns the daemon's server
       id. A bridge failure (daemon down, timeout) is ``HostFrameFacts(reachable=False,
       detail=<the failure's message>)``.
    2. The workspace of the Projects folder (``config.workspace_root``): the reuse-or-create
       bridge command the launch uses. Its failure does not fail the frame: return the server id
       with ``projects_workspace_id=None`` and the failure's message as ``detail``.
    """
    return HostFrameFacts(
        reachable=False, detail=f"{BRIDGE_NOT_WIRED} (settings: {config.config_path})"
    )


def normalise_origin(value: str | None) -> str | None:
    """``scheme://host[:port]`` in lower case without a default port; ``None`` when not an origin."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def request_dashboard_origin(request: Request) -> str | None:
    """The origin of the dashboard page that sent this request.

    A browser names the page's origin in ``Origin`` on cross-origin and non-GET requests; a
    same-origin GET carries none, and then the page's origin is the scheme and ``Host`` the
    request was sent to.
    """
    origin = request.headers.get("origin")
    if origin:
        return normalise_origin(origin)
    host = request.headers.get("host")
    return normalise_origin(f"{request.url.scheme}://{host}") if host else None


def frame_base_url_for(settings: PaseoRuntimeSettings, dashboard_origin: str | None) -> str | None:
    """The embed list's frame base URL for one dashboard origin; ``None`` when it is not listed."""
    if dashboard_origin is None:
        return None
    for entry in settings.embed:
        if normalise_origin(entry.dashboard_origin) == dashboard_origin:
            return entry.frame_base_url.rstrip("/")
    return None


def frame_descriptor(
    config: McpRuntimeConfig,
    dashboard_origin: str | None,
    *,
    host: HostFrameFactsCall | None = None,
) -> dict[str, Any]:
    """The frame route's answer for one dashboard origin."""
    settings = config.paseo_runtime
    if settings is None:
        return _unavailable(
            REASON_NOT_CONFIGURED,
            f"{NO_PASEO_RUNTIME_CONFIGURED}: {config.config_path} has no paseoRuntime block",
        )
    frame_base_url = frame_base_url_for(settings, dashboard_origin)
    if frame_base_url is None:
        return _unavailable(
            REASON_ORIGIN_NOT_LISTED,
            f"{ORIGIN_NOT_LISTED_TEXT}: {dashboard_origin or 'this request'} is not a dashboard "
            "origin in paseoRuntime.embed",
        )
    # The runtime is asked only for a listed origin.
    try:
        facts = (host or host_frame_facts)(config)
    except (OSError, RuntimeError, ValueError) as error:
        facts = HostFrameFacts(reachable=False, detail=str(error))
    if not facts.reachable:
        return _unavailable(
            REASON_UNREACHABLE,
            f"the Paseo daemon is unreachable: {(facts.detail or 'no answer')[:_DETAIL_LIMIT]}",
        )
    if not facts.server_id:
        return _unavailable(
            REASON_UNREACHABLE, "the Paseo daemon answered without naming its server id"
        )
    workspace_id = facts.projects_workspace_id or None
    return {
        "available": True,
        "frameBaseUrl": frame_base_url,
        "serverId": facts.server_id,
        "projectsWorkspaceId": workspace_id,
        "projectsWorkspaceDetail": None
        if workspace_id
        else (facts.detail or "the runtime named no workspace for the Projects folder")[
            :_DETAIL_LIMIT
        ],
    }


def _unavailable(reason: str, detail: str) -> dict[str, Any]:
    return {"available": False, "reason": reason, "detail": detail}
