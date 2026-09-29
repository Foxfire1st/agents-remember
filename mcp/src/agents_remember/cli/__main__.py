"""Umbrella command-line entrypoint: ``agents-remember <subcommand>``.

The single front door for the package's CLI tools. It carries ``dashboard``, the memory
maintenance and migration commands, the knowledge write plane's ingest and its **taskless**
``knowledge-bootstrap`` entry, the text knowledge format's ``knowledge-format`` formatter, ``knowledge-convert`` conversion,
``knowledge-validate`` validator, ``knowledge-index`` derived index and read-only
``knowledge-routes`` family-route report, the migration census's ``knowledge-census`` inventory
and report, the review plane's ``review-record-comparison`` entry, and the existing
``context_packet`` adapter as subparsers. The MCP server keeps its own ``agents-remember-mcp``
console script -- harness configs launch the server by that exact name, so it is never folded in
here.
"""

from __future__ import annotations

import argparse

from agents_remember.cli import (
    dashboard,
    knowledge_bootstrap,
    knowledge_census,
    knowledge_convert,
    knowledge_format,
    knowledge_index,
    knowledge_ingest,
    knowledge_routes,
    knowledge_validate,
    memory_backfill,
    memory_citations,
    review_comparison_record,
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
    bootstrap = sub.add_parser(
        "knowledge-bootstrap",
        help=(
            "Initialize or resume a repository's knowledge foundation without a leaf enclosure; "
            "the taskless bootstrap's production entry point."
        ),
    )
    knowledge_bootstrap.add_arguments(bootstrap)
    bootstrap.set_defaults(func=knowledge_bootstrap.run)
    fmt = sub.add_parser(
        "knowledge-format",
        help="Rewrite knowledge JSON files in the canonical formatting; --check only reports.",
    )
    knowledge_format.add_arguments(fmt)
    fmt.set_defaults(func=knowledge_format.run)
    convert = sub.add_parser(
        "knowledge-convert",
        help="Convert a memory tree and its knowledge database into the text knowledge format.",
    )
    knowledge_convert.add_arguments(convert)
    convert.set_defaults(func=knowledge_convert.run)
    validate = sub.add_parser(
        "knowledge-validate",
        help="Validate a converted memory tree against the knowledge formats and integrity rules.",
    )
    knowledge_validate.add_arguments(validate)
    validate.set_defaults(func=knowledge_validate.run)
    index = sub.add_parser(
        "knowledge-index",
        help="Build or reuse the derived knowledge index of one memory tree and report it.",
    )
    knowledge_index.add_arguments(index)
    index.set_defaults(func=knowledge_index.run)
    routes = sub.add_parser(
        "knowledge-routes",
        help="Show each family's routes, route state and mechanical route suggestion (read-only).",
    )
    knowledge_routes.add_arguments(routes)
    routes.set_defaults(func=knowledge_routes.run)
    census = sub.add_parser(
        "knowledge-census",
        help="Pin and inventory a migration census, or report its measures and route status.",
    )
    knowledge_census.add_arguments(census)
    census.set_defaults(func=knowledge_census.run)
    record = sub.add_parser(
        "review-record-comparison",
        help=(
            "Record one leaf's review comparison as a durable generation; the Intent Reviewer's "
            "comparison producer."
        ),
    )
    review_comparison_record.add_arguments(record)
    record.set_defaults(func=review_comparison_record.run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
