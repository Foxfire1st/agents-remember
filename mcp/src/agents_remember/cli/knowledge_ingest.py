"""CLI adapter: write a curator hand-off list into a leaf's memory worktree as knowledge files.

    agents-remember knowledge-ingest --contract <leaf enclosure contract> --list <hand-off list>
        --authorization-ref <ref> [--commit] [--json] [--config <MCP settings>]
    agents-remember knowledge-ingest --contract <master series contract>
        --crossing <task-id>-crossing-<n> --list <hand-off list> --authorization-ref <ref>
        [--commit] [--json]

``--contract`` is REQUIRED and is the write guard: the run reads the code worktree and writes the
memory worktree that contract names, so no argument list can aim a knowledge write at another leaf's
line.

``--authorization-ref`` is REQUIRED: an admitted write records the authorization it ran under, and
a blank reference is refused.

PLANNING IS THE DEFAULT, AND PLANNING IS ALSO THE DRY RUN. Without ``--commit`` the run reads the
list, resolves every target, validates the tree it would produce, reports, and writes nothing.
``--commit`` is the commit word and the only mode that writes files. The writer commits nothing to
Git: the leaf's closeout commits its memory worktree.

KNOWLEDGE IS WRITTEN AS FILES (MIK-R12 rule 7). The memory worktree must be converted (it holds
``knowledge/layout.json``). The curator file writer (:mod:`agents_remember.cli.knowledge_write_route`)
writes records, sidecar entries and the leaf's history file into it, validated. An unconverted
memory worktree is refused by name: the canonical knowledge database and its candidate, baseline and
publication route are retired (MIK-R26), and the refusal says how the tree converts -- the crossing
sync (MIK-R24 rule 8) or the conversion command.

``--crossing`` is the route of a master line's crossing sync (MIK-R24 rule 8 step 4): with the
master's series contract, a record both sides changed is resolved (its revision becomes one more
than the higher side's) and the rows about it go into the crossing history file the sync opened.

Exit status: 0 when the operation wrote or planned; 1 when the writer refused it (every problem and
violation is in the report, and nothing was written); 2 when the invocation itself is refused (a
contract that cannot be loaded, a blank authorization reference, a list that cannot be read, an
unconverted memory worktree).
"""

from __future__ import annotations

import argparse

from agents_remember.cli.knowledge_write_route import (
    EXIT_REFUSED,
    is_converted,
    load_leaf_contract,
    run_crossing_write,
    run_leaf_write,
    unconverted_write_refusal,
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--contract",
        required=True,
        help="Path to the leaf enclosure contract. Required: the run reads the code worktree and "
        "writes the memory worktree that contract names and refuses any other target.",
    )
    parser.add_argument(
        "--list",
        required=True,
        dest="hand_off_list",
        help="Path to the curator hand-off list (JSON).",
    )
    parser.add_argument(
        "--authorization-ref",
        required=True,
        help="The authorization this run is admitted under; the report records it.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write the files. Without it this plans, validates and reports, and writes nothing.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the whole report as JSON instead of the human-readable summary.",
    )
    parser.add_argument(
        "--crossing",
        default=None,
        help="MIK-R24 rule 8 step 4: resolve a record a master line's crossing sync left conflicted "
        "(by its id; its revision becomes one more than the higher side's) and record the rows into "
        "the open <task-id>-crossing-<n> history file. --contract then names the master's series "
        "contract; the sync's memory worktree is written, and no entry or new record is authored.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="MIK-R14: the MCP authority settings through which a raise row's question is appended "
        "to the leaf's task document (task_doc). Omit to discover them from the working directory; "
        "without them a raise is refused.",
    )


def run(args: argparse.Namespace) -> int:
    """Run one write and print its report; the report IS the result."""

    loaded = load_leaf_contract(args.contract)
    if getattr(args, "crossing", None):
        return run_crossing_write(args, loaded)
    if isinstance(loaded, str):
        print(loaded)
        return EXIT_REFUSED
    if is_converted(loaded.memory_worktree):
        return run_leaf_write(args, loaded)
    print(unconverted_write_refusal(loaded))
    return EXIT_REFUSED
