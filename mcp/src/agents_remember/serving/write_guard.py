"""Refuse the requests a generated page or another site must never get through (MIK-R104 rules 4, 15).

The dashboard is local and unauthenticated, which is a *deployment* posture, not a write
authorization. Absence of CORS permission does not stop a page from *sending* a request that changes
something, so :class:`RefuseCrossOriginWrites` sits in front of every route and refuses a
state-changing request that a browser made from another origin: a request's ``Sec-Fetch-Site`` decides when the browser sends one (only ``same-origin`` is this page;
``none``, ``same-site`` and ``cross-site`` are refused), and ``Origin`` is consulted only when that
header is absent, where ``null`` and any foreign origin are refused. A request with neither header
(a script, ``curl``, the CLI) is passed through: the boundary is about browser-reachable write
surfaces, and those carry an origin or fetch metadata.

There is deliberately no navigation guard. The frame navigating itself -- a link, a script, a form
target, a meta refresh -- is an accepted channel under MIK-R104 v2 (D331): the browser is the
execution environment, no stronger container is built, and no safeguard is engineered around that
channel. What a navigation reaches is still bounded by the browser, not by this module: the frame
keeps its sandbox flags across the navigation, so the document it lands on is another opaque origin
and any write it attempted would be refused here as ``null-origin``. The browser proof observes and
names the navigation; it is not counted as a failed boundary.

:class:`RefuseForeignHosts` adds rule 15's rebinding check (D332) on *every* route, reads included:
the ``Host`` a request names must be this server's own host and port -- a loopback name or address,
or the address the server itself bound. A DNS-rebinding page reaches loopback with its own name in
``Host`` and is refused before a route sees it.

Each refusal is a typed ``403`` with its reason, so a refused effect is diagnosable rather than a
silent no-op.
"""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = [
    "RefuseCrossOriginWrites",
    "RefuseForeignHosts",
    "host_refusal",
    "refusal_reason",
]

PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SAME_ORIGIN = frozenset({"same-origin"})
#: The names a browser may use to reach a dashboard that runs on this machine.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_PORTS = {"http": 80, "https": 443}


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", ()):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _host_origin(scope: Scope) -> str:
    scheme = scope.get("scheme", "http")
    host = _header(scope, b"host") or "127.0.0.1"
    return f"{scheme}://{host}"


def _split_host(value: str) -> tuple[str, int | None]:
    """The hostname and optional port one ``Host`` header names, IPv6 brackets kept off the name."""

    if value.startswith("["):
        name, _, rest = value.partition("]")
        port = rest[1:] if rest.startswith(":") else ""
        return name[1:], int(port) if port.isdigit() else None
    name, separator, port = value.partition(":")
    return name, int(port) if separator and port.isdigit() else None


def host_refusal(scope: Scope) -> tuple[str, str] | None:
    """Why this request names a host that is not this dashboard's, or ``None`` when it admits it."""

    if scope.get("type") != "http":
        return None
    header = _header(scope, b"host")
    if header is None:
        return None
    server = scope.get("server") or ("127.0.0.1", 80)
    bound_host = str(server[0])
    # ASGI transports can omit the scheme's default port (for example ("test", None)).
    # Resolve that omission on both sides; an explicit bound port still has to match.
    default_port = DEFAULT_PORTS.get(str(scope.get("scheme")), 80)
    bound_port = default_port if server[1] is None else int(server[1])
    name, port = _split_host(header)
    admitted_name = name in LOOPBACK_HOSTS or name == bound_host
    if not admitted_name:
        return (
            "foreign-host",
            f"the request names the host {name!r}; this dashboard answers only its own name or "
            "address on loopback (DNS rebinding is refused)",
        )
    expected = port if port is not None else DEFAULT_PORTS.get(str(scope.get("scheme")), 80)
    if expected != bound_port:
        return (
            "foreign-host",
            f"the request names port {expected}; this dashboard answers only the port it bound "
            f"({bound_port})",
        )
    return None


def _origin_refusal(scope: Scope) -> tuple[str, str] | None:
    """The Origin fallback: consulted only when the browser sent no Fetch Metadata at all."""

    origin = _header(scope, b"origin")
    if origin is None:
        # Neither header: this is not a browser page (the CLI, a tool server), so no page to refuse.
        return None
    if origin == "null":
        return "null-origin", "an opaque-origin document may not write to this service"
    if origin != _host_origin(scope):
        return "foreign-origin", f"the request came from {origin}, not this dashboard"
    return None


def refusal_reason(scope: Scope) -> tuple[str, str] | None:
    """Why this request is a foreign browser write, or ``None`` when it is admitted.

    Fetch Metadata decides when present, and it decides alone (D332 literal): reading ``Origin``
    first would let a fallback override the header that is actually authoritative, and admitting
    ``none`` would admit a browser context that is not this page.
    """

    if scope.get("type") != "http" or scope.get("method") not in PROTECTED_METHODS:
        return None
    site = _header(scope, b"sec-fetch-site")
    if site is None:
        return _origin_refusal(scope)
    if site not in SAME_ORIGIN:
        return "cross-site", f"the browser marks this request {site!r}, not same-origin"
    return None


async def _refuse(scope: Scope, receive: Receive, send: Send, refusal: tuple[str, str]) -> None:
    """Answer the typed refusal without reaching a route."""

    reason, detail = refusal
    body: dict[str, Any] = {"state": "refused", "reason": reason, "detail": detail}
    response = JSONResponse(body, status_code=403)
    await response(scope, receive, send)


class RefuseCrossOriginWrites:
    """The ASGI middleware. A refusal answers ``403`` and never reaches a route."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        refusal = refusal_reason(scope)
        if refusal is None:
            await self._app(scope, receive, send)
            return
        await _refuse(scope, receive, send, refusal)


class RefuseForeignHosts:
    """Rule 15's rebinding guard: every route, reads included, answers only its own host and port."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        refusal = host_refusal(scope)
        if refusal is None:
            await self._app(scope, receive, send)
            return
        await _refuse(scope, receive, send, refusal)
