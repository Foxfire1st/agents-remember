"""CLI adapter: compute the change-to-knowledge worklist (MIK-R08) for a leaf or for named sides.

    agents-remember knowledge-worklist --contract CONTRACT
    agents-remember knowledge-worklist --code REPO --base B (--candidate C | --code-worktree DIR)
                                       --memory REPO --memory-base K_B
                                       (--memory-candidate REV | --memory-worktree DIR)
                                       [--maintenance-scope] [--owner LEAF] [--output FILE]
                                       [--cache-dir DIR]

``--contract`` computes a leaf's worklist from its series contract exactly as the curator's
memory-quality run does, and persists it beside the contract. The explicit form names the four sides
directly (K_B is taken as given, not searched by trailer), for evidence runs on scratch copies; it
writes only ``--output`` when named. The command prints the worklist as JSON.

Exit status: 0 for a complete worklist, 1 for an incomplete one, 2 when no worklist applies (both
memory sides are unconverted, or the contract is not a leaf's).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents_remember.application.knowledge_worklist import (
    ExplicitSides,
    leaf_worklist,
    persist_worklist,
    worklist_for_sides,
)
from agents_remember.worktrees.worktree_contract import load_contract


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contract", type=Path, help="A leaf's series contract.")
    parser.add_argument("--code", type=Path, help="The code repository.")
    parser.add_argument("--base", help="B: the code base commit.")
    parser.add_argument("--candidate", help="C: a code commit or tree.")
    parser.add_argument("--code-worktree", type=Path, help="C: a code working tree to capture.")
    parser.add_argument("--memory", type=Path, help="The memory repository.")
    parser.add_argument("--memory-base", help="K_B: the memory base commit.")
    parser.add_argument("--memory-candidate", help="K_C: a memory commit or tree.")
    parser.add_argument("--memory-worktree", type=Path, help="K_C: a memory working tree.")
    parser.add_argument(
        "--maintenance-scope",
        action="store_true",
        help="Classify every K_B entry (knowledgeMaintenanceScope: true).",
    )
    parser.add_argument("--owner", help="The leaf the worklist is for.")
    parser.add_argument("--output", type=Path, help="Also write the worklist to this file.")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="Cache converted bases here (never inside a Git working tree).",
    )


def _explicit(args: argparse.Namespace) -> ExplicitSides | str:
    missing = [
        flag
        for flag, value in (
            ("--code", args.code),
            ("--base", args.base),
            ("--memory", args.memory),
            ("--memory-base", args.memory_base),
        )
        if value is None
    ]
    if missing:
        return f"missing {', '.join(missing)}"
    if (args.candidate is None) == (args.code_worktree is None):
        return "name exactly one of --candidate and --code-worktree"
    if (args.memory_candidate is None) == (args.memory_worktree is None):
        return "name exactly one of --memory-candidate and --memory-worktree"
    return ExplicitSides(
        code_repository=args.code.resolve(),
        base=args.base,
        memory_repository=args.memory.resolve(),
        memory_base=args.memory_base,
        memory_candidate=(
            args.memory_worktree.resolve()
            if args.memory_worktree is not None
            else args.memory_candidate
        ),
        code_candidate=args.candidate,
        code_worktree=None if args.code_worktree is None else args.code_worktree.resolve(),
        maintenance_scope=args.maintenance_scope,
        owner=args.owner,
        cache_directory=args.cache_dir,
    )


def run(args: argparse.Namespace) -> int:
    if args.contract is not None:
        document = leaf_worklist(load_contract(args.contract))
    else:
        sides = _explicit(args)
        if isinstance(sides, str):
            print(json.dumps({"error": sides}))
            return 2
        document = worklist_for_sides(sides)
    if document is None:
        print(json.dumps({"state": "not-applicable", "detail": "no memory side is converted"}))
        return 2
    if args.output is not None:
        persist_worklist(args.output, document)
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if document["state"] == "complete" else 1
