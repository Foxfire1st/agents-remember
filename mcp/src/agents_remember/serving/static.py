"""Resolve and mount the shipped dashboard static bundle.

The built cockpit ships as package data under ``package_data/dashboard/`` (slice 05
replaces the slice-04 placeholder). It is resolved through ``importlib.resources`` -- the
same idiom as :mod:`agents_remember.install.assets` -- so it works from an installed wheel
and from the ``mcp/src`` source tree alike.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope


class DashboardStaticFiles(StaticFiles):
    """Serve entry HTML as revalidated identity while retaining hashed-asset caching semantics."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200 and response.headers.get("content-type", "").startswith(
            "text/html"
        ):
            response.headers["Cache-Control"] = "no-cache"
        return response


def dashboard_static_dir() -> Path | None:
    """The shipped ``package_data/dashboard`` directory, or ``None`` if unavailable."""
    root = resources.files("agents_remember").joinpath("package_data", "dashboard")
    if isinstance(root, Path) and root.is_dir():
        return root
    return None


def mount_static(app: FastAPI) -> None:
    """Mount the static bundle at ``/`` (SPA-style) when it is present.

    Registered after the ``/api`` routes so the greedy ``/`` mount only catches paths the
    API did not. A missing bundle is non-fatal -- the API still serves.
    """
    static_dir = dashboard_static_dir()
    if static_dir is None:
        return
    app.mount("/", DashboardStaticFiles(directory=static_dir, html=True), name="dashboard")
