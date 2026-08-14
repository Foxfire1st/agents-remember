"""Resolve and mount the built dashboard bundle -- or say plainly that it is not there.

The cockpit is a Vite build (``dashboard/dist``) copied into ``package_data/dashboard`` by
the release job and shipped inside the wheel and sdist. It is **not** committed: a 28 MB
generated tree in version control is what made the pre-commit gate unpassable, so the
artifact now lives only in the package it ships in. Resolution goes through
``importlib.resources`` -- the same idiom as :mod:`agents_remember.install.assets` -- so it
works from an installed wheel and from the ``mcp/src`` source tree alike.

A source checkout that has never run a frontend build therefore legitimately has no bundle.
That is a diagnosable state, not a crash and not a silent 404: the API keeps serving, and
the static surface answers ``503`` naming the directory it expected and the command that
produces it. There is deliberately no placeholder and no fallback UI -- the slice-04
hand-authored placeholder is long gone, and a stand-in cockpit would misrepresent a broken
install as a working one. An honest error is the correct product here.
"""

from __future__ import annotations

import logging
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse, Response
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)

#: The command chain that produces the bundle from a source checkout. Named in the startup
#: log and in the 503 body so the missing-bundle state carries its own remedy.
BUILD_COMMAND = (
    "npm --prefix dashboard ci && npm --prefix dashboard run build "
    "&& python3 scripts/sync-dashboard.py"
)


class DashboardStaticFiles(StaticFiles):
    """Serve entry HTML as revalidated identity while retaining hashed-asset caching semantics."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200 and response.headers.get("content-type", "").startswith(
            "text/html"
        ):
            response.headers["Cache-Control"] = "no-cache"
        return response


class MissingDashboardBundle:
    """The static surface when no bundle was built: ``503`` plus what to run, on every path.

    Mounted in the same place and with the same greed as :class:`DashboardStaticFiles`, so
    the set of paths that would have been served is exactly the set that now explains itself.
    ``no-store`` keeps the diagnostic from outliving the build that fixes it.

    Only ``GET``/``HEAD`` get the explanation, because that is the method contract
    :class:`StaticFiles` itself enforces (it raises ``405`` on anything else). A greedy
    mount at ``/`` outranks an API route that matched the path but not the method, so
    without this a ``POST`` to a ``GET``-only ``/api`` route would answer ``503`` -- which
    would both contradict this body's "the API is unaffected" and silently change the
    API's method semantics depending on whether a frontend build happened to be present.
    """

    #: What :class:`StaticFiles` serves; everything else is a method error, not an outage.
    SERVED_METHODS = frozenset({"GET", "HEAD"})

    def __init__(self, expected: str) -> None:
        self._body = (
            "Agents Remember dashboard: no built cockpit bundle in this installation.\n"
            "\n"
            "The cockpit is a generated Vite build. It is shipped inside the wheel and sdist\n"
            "and is not committed to the repository, so a source checkout that has never run\n"
            "a frontend build has nothing to serve here.\n"
            "\n"
            f"Expected at: {expected}\n"
            f"Build it with: {BUILD_COMMAND}\n"
            "\n"
            "The HTTP API is unaffected and is serving normally under /api.\n"
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("method") not in self.SERVED_METHODS:
            raise HTTPException(status_code=405)  # exactly what StaticFiles raises here
        response = PlainTextResponse(
            self._body, status_code=503, headers={"Cache-Control": "no-store"}
        )
        await response(scope, receive, send)


def _bundle_root() -> Traversable:
    """The one place the shipped-bundle path is spelled."""
    return resources.files("agents_remember").joinpath("package_data", "dashboard")


def _bundle_location() -> str:
    """Where the bundle is expected, printable whether or not anything is there."""
    return str(_bundle_root())


def dashboard_static_dir() -> Path | None:
    """The built ``package_data/dashboard`` directory, or ``None`` when no build was placed."""
    root = _bundle_root()
    if isinstance(root, Path) and root.is_dir():
        return root
    return None


def mount_static(app: FastAPI) -> None:
    """Mount the static surface at ``/`` (SPA-style), built bundle or honest error.

    Registered after the ``/api`` routes so the greedy ``/`` mount only catches paths the
    API did not. A missing bundle is non-fatal -- the server starts, the API serves, and the
    static surface reports what is missing instead of returning a bare 404.
    """
    static_dir = dashboard_static_dir()
    if static_dir is None:
        expected = _bundle_location()
        logger.warning(
            "No dashboard bundle at %s; serving 503 on the static surface. Build it with: %s",
            expected,
            BUILD_COMMAND,
        )
        app.mount("/", MissingDashboardBundle(expected), name="dashboard")
        return
    app.mount("/", DashboardStaticFiles(directory=static_dir, html=True), name="dashboard")
