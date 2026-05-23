"""Stdio MCP server wiring for Agents Remember."""

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import ConfigError, McpRuntimeConfig, load_config
from .tools import (
    context_packet_payload,
    ping_payload,
    runtime_install_payload,
    server_info_payload,
)


def create_server(config: McpRuntimeConfig) -> Any:
    server = FastMCP("Agents Remember")

    @server.tool()
    def ping() -> dict[str, Any]:
        return ping_payload()

    @server.tool()
    def server_info() -> dict[str, Any]:
        return server_info_payload(config)

    @server.tool()
    def context_packet(
        repo_id: str,
        include_providers: bool = True,
        include_drift: bool = False,
    ) -> dict[str, Any]:
        return context_packet_payload(
            config,
            repo_id,
            include_providers=include_providers,
            include_drift=include_drift,
        )

    @server.tool()
    def runtime_install(
        dry_run: bool = True,
        include_benchmarks: bool = False,
        install_provider_deps: bool = True,
    ) -> dict[str, Any]:
        return runtime_install_payload(
            config,
            dry_run=dry_run,
            include_benchmarks=include_benchmarks,
            install_provider_deps=install_provider_deps,
        )

    return server


def run_server(config: McpRuntimeConfig) -> None:
    create_server(config).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agents Remember MCP server.")
    parser.add_argument(
        "--config",
        required=True,
        help="Absolute path to trusted MCP settings JSON.",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as error:
        parser.error(str(error))

    run_server(config)
    return 0
