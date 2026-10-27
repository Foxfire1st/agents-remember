"""Read the shared saved offers while the launcher's fresh question is in flight."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_catalog import saved_launcher_catalog
from agents_remember.cli.role_launch_request_rules import bridge_http_error
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig


def register_saved_catalog_route(app: FastAPI, config: McpRuntimeConfig) -> None:
    """Publish only saved offer data, without querying Paseo or deciding a start."""

    @app.get("/api/role-launch/saved-catalog")
    def saved_catalog() -> dict[str, Any]:
        try:
            catalog = saved_launcher_catalog(config)
        except PaseoBridgeFailure as error:
            raise bridge_http_error(error) from error
        return {
            "agents": [dict(agent) for agent in catalog.agents],
            "catalogDiscovery": catalog.discovery,
            "catalogOrigin": catalog.origin,
        }
