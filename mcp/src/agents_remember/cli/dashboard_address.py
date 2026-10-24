"""The dashboard address a start answer names (MIK-R196 rule 1) and the chat address.

The dashboard is the product's own UI: it listens on loopback at the port the settings hold
(``dashboard.port``), and every answer that says where a seat can be continued names that
address rather than a path the caller would have to spell. The chat address is the host's own
web route for one agent, and it is built here from the same embed list the frame route uses, so
a start never invents a loopback URL for the host.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

DASHBOARD_HOST = "127.0.0.1"
#: How long the answer waits for the dashboard's own port to answer after it is ensured.
DASHBOARD_PROBE_SECONDS = 2.0


def dashboard_address(config: McpRuntimeConfig) -> str:
    """The dashboard's own address, where the started seat is listed and can be opened."""

    return f"http://{DASHBOARD_HOST}:{config.dashboard.port}/"


def _origin_answer(host: str, port: int) -> tuple[bool, str]:
    """Whether the dashboard's own origin answers with a page, and what it answered with.

    A TCP accept is not an answer: a listener that greets every connection with an error, or a port
    some other process holds, would pass a connect check and fail the user. So this asks the origin
    for its front page and takes a status under 400 as the proof the promise needs (L188-B24,
    review of 221: a 503 "no cockpit bundle" page is the origin speaking, not the dashboard serving).
    """

    url = f"http://{host}:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=DASHBOARD_PROBE_SECONDS) as reply:
            return reply.status < 400, f"HTTP {reply.status}"
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except Exception as error:
        return False, type(error).__name__


def dashboard_address_for(config: McpRuntimeConfig) -> dict[str, Any]:
    """The dashboard address, after the existing owner has made something serve it.

    MIK-R196 rule 1 asks the one call to return the address at which the user continues setup, and
    an address nothing serves breaks that promise (ruling 7c1153ee, L188-B24). So this asks
    :func:`agents_remember.serving.daemon.ensure` -- the product's own dashboard owner, the same one
    the MCP boot hook calls -- to adopt or start it, then answers the address only when the port
    actually answers. When it does not, the answer carries the reason instead of a dead pointer.
    """

    port = config.dashboard.port
    try:
        from agents_remember.serving.daemon import DaemonEndpoint, ensure  # noqa: PLC0415

        outcome = ensure(config, DaemonEndpoint(host=DASHBOARD_HOST, port=port))
        action, detail = outcome.action, outcome.detail
    except Exception as error:
        action, detail = "failed", f"{type(error).__name__}: {error}"
    served, observed = _origin_answer(DASHBOARD_HOST, port)
    if served and action != "failed":
        return {"dashboardAddress": dashboard_address(config)}
    if not served:
        return {
            "dashboardAddress": None,
            "dashboardAddressDetail": (
                f"no dashboard answers http://{DASHBOARD_HOST}:{port}/; the origin gave {observed} "
                f"and the dashboard owner answered {action!r}: {detail}"
            ),
        }
    return {
        "dashboardAddress": None,
        "dashboardAddressDetail": (
            f"the dashboard owner answered {action!r}: {detail}, although the origin gave "
            f"{observed} at http://{DASHBOARD_HOST}:{port}/"
        ),
    }


def chat_address(
    settings: PaseoRuntimeSettings | None, server_id: str | None, agent_id: str | None
) -> tuple[str | None, str | None]:
    """The host's web route for one agent's chat, and why it cannot be given when it cannot.

    ``None`` names no configured host or no agent, which is the honest state rather than a
    guessed address: the embed list is what a browser may reach, and with no entry the host's
    web UI is not reachable from the dashboard at all.
    """

    if settings is None:
        return None, "no Paseo runtime is configured"
    if not agent_id:
        return None, "the start answer named no agent"
    if not server_id:
        return None, "the host did not name its server id"
    if not settings.embed:
        return None, "paseoRuntime.embed lists no dashboard origin, so no browser reaches the host"
    base = settings.embed[0].frame_base_url.rstrip("/")
    return f"{base}/h/{server_id}/agent/{agent_id}", None


def chat_address_for(config: McpRuntimeConfig, agent_id: str | None) -> dict[str, Any]:
    """The chat address of ``agent_id``, or the named reason it is not available."""

    try:
        settings = config.paseo_runtime
    except ValueError as error:
        return {"chatAddress": None, "chatAddressDetail": str(error)}
    server_id: str | None = None
    if settings is not None and agent_id:
        try:
            from agents_remember.cli.paseo_bridge import (  # noqa: PLC0415 - bridge stays lazy
                PaseoBridgeFailure,
                bridge_call,
            )

            reply = bridge_call(config, "runtime-info", {})
            named = reply.get("serverId")
            server_id = named if isinstance(named, str) and named else None
        except PaseoBridgeFailure as error:
            return {"chatAddress": None, "chatAddressDetail": str(error)}
    address, detail = chat_address(settings, server_id, agent_id)
    return {"chatAddress": address, "chatAddressDetail": detail}
