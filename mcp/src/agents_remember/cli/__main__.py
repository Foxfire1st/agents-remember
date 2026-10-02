"""Umbrella command-line entrypoint: ``agents-remember <subcommand>``.

The single front door for the package's CLI tools. It carries ``dashboard``, the memory
maintenance and migration commands, the knowledge write plane's ingest, the Paseo runtime
commands, and the existing ``context_packet`` adapter as subparsers. The MCP server keeps its
own ``agents-remember-mcp`` console script -- harness configs launch the server by that exact
name, so it is never folded in here.
"""

from __future__ import annotations

import argparse

from agents_remember.cli import (
    dashboard,
    knowledge_ingest,
    leaf_enclosure_start,
    memory_backfill,
    memory_citations,
    paseo_runtime,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents-remember", description="Agents Remember CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    dash = sub.add_parser("dashboard", help="Run the local mission-control dashboard.")
    dashboard.add_arguments(dash)
    dash.set_defaults(func=dashboard.run)
    citations = sub.add_parser(
        "memory-citations",
        help="Check a leaf's memory citations; --fix regenerates their ranges from the anchors.",
    )
    memory_citations.add_arguments(citations)
    citations.set_defaults(func=memory_citations.run)
    backfill = sub.add_parser(
        "memory-backfill",
        help="Plan or apply the Code-Commit trailer backfill for a leaf's memory history.",
    )
    memory_backfill.add_arguments(backfill)
    backfill.set_defaults(func=memory_backfill.run)
    ingest = sub.add_parser(
        "knowledge-ingest",
        help=(
            "Ingest an orchestrator's curator hand-off list into a leaf's candidate; the "
            "knowledge write plane's production entry point."
        ),
    )
    knowledge_ingest.add_arguments(ingest)
    ingest.set_defaults(func=knowledge_ingest.run)
    paseo = sub.add_parser(
        "paseo",
        help="Provision, inspect or stop the pinned Paseo runtime the settings describe.",
    )
    paseo_runtime.add_arguments(paseo)
    paseo.set_defaults(func=paseo_runtime.run)
    # Internal: the dashboard backend runs it as a child process; it has no help entry.
    enclosure = sub.add_parser(leaf_enclosure_start.COMMAND)
    leaf_enclosure_start.add_arguments(enclosure)
    enclosure.set_defaults(func=leaf_enclosure_start.run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
