"""CLI adapter: build or reuse the derived knowledge index of one memory tree, and report it.

    agents-remember knowledge-index (--memory-root DIR | --repository REPO --revision REV)
                                    (--cache-dir DIR | --coordination-root DIR)

``--memory-root`` indexes a working tree's current captured state; ``--repository``/``--revision``
indexes a Git tree read through Git objects. The index lands in ``--cache-dir``, or in
``<coordination-root>/runtime/knowledge-index``. The command prints one JSON object: the tree key,
whether the file was reused, its path, its state (``complete`` or ``partial``) with every problem,
and the build's counts and time. The index is a derived SQLite file, never a source of truth: the
text files in Git are, and the read tools build or reuse the index of the tree they are asked about.

A tree without ``knowledge/layout.json`` is not a converted memory tree: the command prints
``state: "legacy-format"`` with the cutover lock's sentence and exits 2 (MIK-R73 rule 6), never
``complete``. A duplicate record ID is by design a ``partial`` index rather than a refusal: the
index must answer for a tree the validator would refuse, so it keeps the first file in path order
and names the second file as the problem.

Exit status: 0 for a complete index, 1 for a partial one, 2 when the tree cannot be read or is
unconverted memory.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agents_remember.memory.knowledge_index import (
    KnowledgeIndexCache,
    MemoryTreeError,
    default_cache_directory,
)
from agents_remember.worktrees.cutover_lock import LEGACY_FORMAT_CODE, legacy_format_refusal


def add_arguments(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--memory-root",
        type=Path,
        help="A memory working tree to index. A duplicate record ID makes the index partial, "
        "naming the second file as the problem; a tree without knowledge/layout.json is "
        "refused as legacy-format.",
    )
    source.add_argument("--repository", type=Path, help="A memory repository to read a tree from.")
    parser.add_argument("--revision", help="The commit, tree or ref to index (with --repository).")
    cache = parser.add_mutually_exclusive_group(required=True)
    cache.add_argument("--cache-dir", type=Path, help="The index cache directory.")
    cache.add_argument(
        "--coordination-root",
        type=Path,
        help="Use <coordination-root>/runtime/knowledge-index as the cache directory.",
    )


def run(args: argparse.Namespace) -> int:
    directory = args.cache_dir or default_cache_directory(args.coordination_root)
    repository: Path | None = args.repository
    if repository is not None and not args.revision:
        print(json.dumps({"error": "--repository needs --revision"}))
        return 2
    started = time.perf_counter()
    try:
        cache = KnowledgeIndexCache(directory)
        if args.memory_root is not None:
            index = cache.for_directory(args.memory_root)
        else:
            assert repository is not None
            index = cache.for_git_tree(repository, args.revision)
    except (MemoryTreeError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    elapsed = time.perf_counter() - started
    with index:
        outcome = cache.last_outcome
        report = None if outcome is None else outcome.report
        if not index.state.converted:
            # MIK-R73 rule 6: an unconverted tree is never answered "complete".
            print(
                json.dumps(
                    {
                        "key": index.state.key,
                        "path": str(index.database_path),
                        "state": LEGACY_FORMAT_CODE,
                        "converted": False,
                        "refusal": str(
                            legacy_format_refusal(
                                args.memory_root if args.memory_root is not None else repository,
                                operation="knowledge-index",
                                subject=(
                                    f"the memory tree {args.memory_root}"
                                    if args.memory_root is not None
                                    else f"the memory tree {repository} at {args.revision}"
                                ),
                            )
                        ),
                        "problems": [{"path": p, "detail": d} for p, d in index.state.problems],
                    },
                    indent=2,
                )
            )
            return 2
        print(
            json.dumps(
                {
                    "key": index.state.key,
                    "path": str(index.database_path),
                    "reused": bool(outcome and outcome.reused),
                    "state": index.state.state,
                    "converted": index.state.converted,
                    "problems": [{"path": p, "detail": d} for p, d in index.state.problems],
                    "records": None if report is None else report.record_count,
                    "entries": None if report is None else report.entry_count,
                    "historyRows": None if report is None else report.history_row_count,
                    "seconds": round(elapsed, 4),
                },
                indent=2,
            )
        )
        return 0 if index.state.complete else 1
