"""Umbrella command-line entrypoint: ``agents-remember <subcommand>``.

The single front door for the package's CLI tools. Today it carries ``dashboard``; future
commands (and the existing ``context_packet`` adapter) slot in as subparsers. The MCP server
keeps its own ``agents-remember-mcp`` console script -- harness configs launch the server by
that exact name, so it is never folded in here.
"""

from __future__ import annotations

import argparse

from agents_remember.cli import dashboard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents-remember", description="Agents Remember CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    dash = sub.add_parser("dashboard", help="Run the local mission-control dashboard.")
    dashboard.add_arguments(dash)
    dash.set_defaults(func=dashboard.run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
