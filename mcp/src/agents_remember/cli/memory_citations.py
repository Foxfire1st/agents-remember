"""Check converted references or regenerate their ranges inside the admitted memory root.

    agents-remember memory-citations --repo <id> --contract <contract> [--fix]

``--fix`` accepts repeated ``--document`` selections and ``--dry-run``. Without a
contract, only the configured root's pre-baseline fixer admission applies.
Old-format memory is refused and must convert through knowledge-convert or the
crossing sync. The command prints the whole result; exit status is 0 on success
and 1 when work remains or the operation is refused.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents_remember.application.memory_tools import (
    CitationOperationScope,
    citation_check_tool,
    citation_fix_tool,
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
        required=False,
        default=None,
        help="Path to the leaf enclosure contract. Required except for --fix on the "
        "configured memory root while that root has no attributed baseline (the "
        "pre-baseline bootstrap admission): citations are rewritten inside a leaf's "
        "memory worktree or not at all.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to trusted MCP settings JSON. Omit to discover it from the working "
        "directory, as the other subcommands do.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Rewrite the ranges that can be regenerated from their anchors. With "
        "--document, normalise passing ranges in that document too. Without --fix this "
        "only reports.",
    )
    parser.add_argument(
        "--document",
        action="append",
        default=None,
        metavar="PATH",
        help="Scope every mode to documents, given as the work order writes them -- "
        "relative to the onboarding root. Repeat for more than one document: one call "
        "checks or repairs them all. A curator wave shares one memory worktree, so a "
        "tree-wide --fix can rewrite another curator's document mid-edit. A name matching "
        "no document is refused rather than checking nothing. --fix takes --document alone "
        "(those cards and their sidecars). Several --document flags need --fix.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --fix, report every rewrite it would make and write nothing.",
    )


def run(args: argparse.Namespace) -> int:
    named = getattr(args, "document", None) or []
    if len(named) > 1 and not args.fix:
        print("several --document flags name cards for one call and need --fix.")
        return 1
    document = named[0] if len(named) == 1 else None
    documents = tuple(named) if len(named) > 1 else None
    try:
        if args.contract is None and not args.fix:
            raise ValueError(
                "--contract is required without --fix: only --fix is admitted on the "
                "configured memory root before the first baseline."
            )
        operation_scope = CitationOperationScope(
            document=document,
            documents=documents,
        )
        config = load_config(args.config if args.config else discover_config(Path.cwd()))
    except (ValueError, ConfigDiscoveryError, ConfigError) as error:
        print(str(error))
        return 1
    try:
        if args.fix:
            payload = citation_fix_tool(
                config,
                repo_id=args.repo,
                contract_path=args.contract,
                dry_run=args.dry_run,
                operation_scope=operation_scope,
            )
        else:
            assert args.contract is not None  # guarded above: only --fix runs unscoped
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
