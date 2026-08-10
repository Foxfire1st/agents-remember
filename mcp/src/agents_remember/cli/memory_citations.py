"""CLI adapter: check a leaf's memory citations, regenerate their ranges, or migrate them.

    agents-remember memory-citations --repo <id> --contract <contract>
        [--build-index | --fix | --migrate]

``--contract`` is REQUIRED and is the write guard: this command resolves the leaf enclosure
exactly as the worktree verbs do, and there is no argument list that names the official
memory repo. A whole-tree reflow after a package move is this one command.

THE FOUR MODES DO DIFFERENT WORK AND ARE NOT INTERCHANGEABLE.

    (none)          report. Nothing is written.
    --build-index   build or validate one complete dirty-snapshot source index before a
                    curator wave. Independent document commands reuse it.
    --fix           regenerate the range of every citation ALREADY in the anchored format that
                fails, from its anchor. With --document, also normalise passing ranges in
                that one document so no curator-authored line number survives.
    --migrate       convert a tree still in the superseded format: add the Anchor column, widen
                the delimiter, turn markdown links into `path:start-end`, and rewrite the old
                prose spelling into `cit:`. Run ONCE per repository adopting the format, and
                again after a curator wave supplies anchors it had to decline.

Exit status is the gate's: 0 when nothing is left to do, 1 when anything remains. What
remains is a work order per DOCUMENT, so a parallel dispatch of curator agents takes one
each, with every location in the tree that holds the anchor printed beside it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents_remember.application.memory_tools import (
    CitationOperationScope,
    citation_check_tool,
    citation_fix_tool,
    citation_migrate_tool,
    citation_source_index_build_tool,
)
from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    load_config,
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, help="Repository id from the trusted settings.")
    parser.add_argument(
        "--contract",
        required=True,
        help="Path to the leaf enclosure contract. Required: citations are rewritten inside "
        "a leaf's memory worktree or not at all.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to trusted MCP settings JSON. Omit to discover it from the working "
        "directory, as the other subcommands do.",
    )
    parser.add_argument(
        "--build-index",
        action="store_true",
        help="Build or validate the reusable immutable code-source index before a curator "
        "wave. Cannot be combined with a document or write mode.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Rewrite the ranges that can be regenerated from their anchors. With "
        "--document, normalise passing ranges in that document too. Without --fix this "
        "only reports.",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Convert a tree still in the superseded citation format. Cannot be combined "
        "with --fix: one converts the format, the other maintains it.",
    )
    parser.add_argument(
        "--document",
        default=None,
        help="Scope every mode to ONE document, given as the work order writes it -- "
        "relative to the onboarding root. A curator wave shares one memory worktree, so a "
        "tree-wide --fix can rewrite another curator's document mid-edit. A name matching "
        "no document is refused rather than checking nothing.",
    )
    parser.add_argument(
        "--expected-snapshot",
        default=None,
        help="Open exactly this snapshot from the explicit prebuild without walking or "
        "statting the source tree. This asserts the source wave stayed frozen; it never "
        "uses HEAD as a proxy for dirty or untracked content, and a missing or mismatched "
        "generation is refused rather than rebuilt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --fix or --migrate, report every rewrite it would make and write nothing.",
    )


def run(args: argparse.Namespace) -> int:
    build_index = getattr(args, "build_index", False)
    expected_snapshot = getattr(args, "expected_snapshot", None)
    document = getattr(args, "document", None)
    selected_modes = sum((build_index, args.fix, args.migrate))
    if selected_modes > 1:
        print("--build-index, --fix, and --migrate are different operations; pass one.")
        return 1
    if build_index and (document is not None or args.dry_run or expected_snapshot is not None):
        print(
            "--build-index is repository-wide and cannot use --document, --dry-run, or "
            "--expected-snapshot. Build once, then pass its snapshot to document commands."
        )
        return 1
    try:
        operation_scope = CitationOperationScope(
            document=document,
            expected_snapshot=expected_snapshot,
        )
    except ValueError as error:
        print(str(error))
        return 1
    try:
        config = load_config(args.config if args.config else discover_config(Path.cwd()))
    except (ConfigDiscoveryError, ConfigError) as error:
        print(str(error))
        return 1
    try:
        if build_index:
            payload = citation_source_index_build_tool(
                config,
                repo_id=args.repo,
                contract_path=args.contract,
            )
        elif args.migrate:
            payload = citation_migrate_tool(
                config,
                repo_id=args.repo,
                contract_path=args.contract,
                dry_run=args.dry_run,
                operation_scope=operation_scope,
            )
        elif args.fix:
            payload = citation_fix_tool(
                config,
                repo_id=args.repo,
                contract_path=args.contract,
                dry_run=args.dry_run,
                operation_scope=operation_scope,
            )
        else:
            payload = citation_check_tool(
                config,
                repo_id=args.repo,
                contract_path=args.contract,
                operation_scope=operation_scope,
            )
    except ValueError as error:
        print(str(error))
        return 1
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1
