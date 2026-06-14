"""CLI adapter: run the local mission-control dashboard server.

Mirrors the MCP server's ``--config`` contract (``load_config`` over the same trusted MCP
settings JSON), so the dashboard resolves the identical coordination context. ``--sim``
replays a recorded observer fixture through the byte-identical serving path instead of live
state (slice 4b); the sim's throwaway root is held alive for the server's lifetime.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from agents_remember.mcp.config import ConfigError, load_config
from agents_remember.serving.app import create_app
from agents_remember.serving.sim import SimError, build_sim, parse_sim_speed


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", required=True, help="Absolute path to trusted MCP settings JSON."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (localhost-only by default; do not expose)."
    )
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default 8765).")
    parser.add_argument(
        "--interval", type=float, default=1.0, help="Projection refresh interval, seconds."
    )
    parser.add_argument(
        "--sim",
        default=None,
        help="Replay a recorded observer fixture dir (with logs/observer/...) instead of live.",
    )
    parser.add_argument(
        "--sim-speed",
        default="1",
        help="Sim replay speed multiplier (e.g. 1, 10) or 'paused' (default 1).",
    )


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"error: {error}")
        return 1
    if args.sim:
        try:
            sim = build_sim(config, Path(args.sim), speed=parse_sim_speed(args.sim_speed))
        except SimError as error:
            print(f"error: {error}")
            return 1
        # ``sim`` (and its temp coordination root) stays referenced until this call returns,
        # i.e. for the whole server lifetime, so the throwaway sim root is not reclaimed early.
        app = create_app(
            sim.config, interval=args.interval, now=sim.clock.now, before_tick=sim.feeder.feed
        )
    else:
        app = create_app(config, interval=args.interval)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
