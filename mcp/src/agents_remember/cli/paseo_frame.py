"""Where the dashboard's Chats pane frames the Paseo web UI.

The frame route answers one question: which frame base URL must a browser on *this* dashboard
origin use to reach the configured Paseo runtime? The answer comes from the embed list
(``paseoRuntime.embed``), never from a fixed loopback value, and carries no secret. The dashboard
builds the frame URL itself from the base URL, the daemon's server id and the host fields of the
execution it displays.

An unavailable frame names exactly one reason, checked in this order: no Paseo runtime is
configured, the request's dashboard origin is not in the embed list, or the daemon is unreachable.
The reason is the state; ``detail`` adds only what the reason does not say (which file, which
origin, what the bridge reported), so a reader that prints both prints nothing twice. A request
that a page of another site made the browser send is answered like an unlisted origin.

The runtime itself is reached only through the bridge. Whether its daemon answers, its server id
and the workspace of the Projects folder come from one function, :func:`host_frame_facts`, which
is the single place this module touches the host. Nothing is cached: the dashboard asks once when
the pane is first opened and once per Retry, and each ask reads the runtime as it is then.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.cli.paseo_launch import opened_workspace_id
from agents_remember.cli.role_launch_workspace import workspace_folder
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

REASON_NOT_CONFIGURED = "not-configured"
REASON_ORIGIN_NOT_LISTED = "origin-not-listed"
REASON_UNREACHABLE = "unreachable"
CROSS_SITE_DETAIL = "the request came from a page of another site (Sec-Fetch-Site: cross-site)"
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

    Two bridge calls. ``runtime-info`` answers with the daemon's server id; its failure, whatever
    the reason, means no frame can be offered and its message says why. ``workspace-open`` then
    reuses or creates the workspace of the Projects folder, the same folder and the same call a
    Projects-altitude launch uses. A failure of this second call does not fail the frame: the
    frame then opens the application without naming a workspace.
    """
    try:
        server_id = bridge_call(config, "runtime-info", {}).get("serverId")
    except PaseoBridgeFailure as error:
        return HostFrameFacts(reachable=False, detail=str(error))
    if not isinstance(server_id, str) or not server_id:
        return HostFrameFacts(reachable=True)
    try:
        folder = workspace_folder(config.workspace_root)["path"]
        workspace_id = opened_workspace_id(
            bridge_call(config, "workspace-open", {"cwd": folder}), folder
        )
    except (PaseoBridgeFailure, OSError) as error:
        return HostFrameFacts(reachable=True, server_id=server_id, detail=str(error))
    return HostFrameFacts(reachable=True, server_id=server_id, projects_workspace_id=workspace_id)


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


def frame_answer(
    config: McpRuntimeConfig,
    request: Request,
    *,
    host: HostFrameFactsCall | None = None,
) -> dict[str, Any]:
    """The frame route's answer for one request.

    A browser marks a request that a page of another site caused with ``Sec-Fetch-Site:
    cross-site``. Such a GET carries the dashboard's own ``Host`` and no ``Origin``, so it would
    look like the dashboard asking; it gets no frame and the runtime is not asked. Same-origin
    and same-site requests, and callers that send no such header, are judged by their origin.
    """
    if request.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        return _unavailable(REASON_ORIGIN_NOT_LISTED, CROSS_SITE_DETAIL)
    return frame_descriptor(config, request_dashboard_origin(request), host=host)


def frame_descriptor(
    config: McpRuntimeConfig,
    dashboard_origin: str | None,
    *,
    host: HostFrameFactsCall | None = None,
) -> dict[str, Any]:
    """The frame route's answer for one dashboard origin."""
    try:
        settings = config.paseo_runtime
    except ValueError as error:
        return _unavailable(REASON_UNREACHABLE, str(error))
    if settings is None:
        return _unavailable(
            REASON_NOT_CONFIGURED,
            f"{config.coordination_root / 'system/settings.json'} has no paseoRuntime block",
        )
    frame_base_url = frame_base_url_for(settings, dashboard_origin)
    if frame_base_url is None:
        return _unavailable(
            REASON_ORIGIN_NOT_LISTED,
            f"{dashboard_origin or 'the origin of this request'} is not a dashboard origin in "
            "paseoRuntime.embed",
        )
    # The runtime is asked only for a listed origin.
    try:
        facts = (host or host_frame_facts)(config)
    except (OSError, RuntimeError, ValueError) as error:
        facts = HostFrameFacts(reachable=False, detail=str(error))
    if not facts.reachable:
        remedy = "run runtime_install if not installed, then agents-remember dashboard --daemon"
        detail = facts.detail or "the daemon gave no answer"
        marker = detail.casefold().find("run runtime_install")
        detail = (detail[:marker] if marker >= 0 else detail).rstrip(" ;,.")
        return _unavailable(
            REASON_UNREACHABLE,
            remedy + "; " + detail[: max(0, _DETAIL_LIMIT - len(remedy) - 2)],
        )
    if not facts.server_id:
        return _unavailable(REASON_UNREACHABLE, "the daemon answered without naming its server id")
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
