"""JSON-only CLI adapter for the context packet controller."""

from __future__ import annotations

import argparse
import json
from typing import Any

from agents_remember.controllers.context_packet import (
    ContextPacketError,
    ContextPacketRequest,
    build_context_packet,
)
from agents_remember.mcp.config import ConfigError, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an Agents Remember context packet.")
    parser.add_argument(
        "--config",
        required=True,
        help="Absolute path to trusted MCP settings JSON.",
    )
    parser.add_argument("--repo", required=True, help="Repository id from MCP settings.")
    parser.add_argument("--skip-providers", action="store_true", help="Omit provider detail items.")
    parser.add_argument(
        "--include-drift",
        action="store_true",
        help="Request the drift summary section.",
    )
    parser.add_argument("--provider-detail-limit", type=int, default=20)
    parser.add_argument("--drift-detail-limit", type=int, default=10)
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        packet: dict[str, Any] = build_context_packet(
            config,
            ContextPacketRequest(
                repo_id=args.repo,
                include_providers=not args.skip_providers,
                include_drift=args.include_drift,
                provider_detail_limit=args.provider_detail_limit,
                drift_detail_limit=args.drift_detail_limit,
            ),
        )
    except (ConfigError, ContextPacketError, ValueError) as error:
        print(json.dumps({"ok": False, "operation": "context_packet", "error": str(error)}))
        return 1

    print(json.dumps(packet, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
