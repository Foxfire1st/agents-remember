"""CLI adapter: the migration census's inventory and report (MIK-R20 rules 1 and 5).

    agents-remember knowledge-census inventory MEMORY_ROOT --census ID --code CODE_REPO
                                     [--code-commit REV] [--memory-commit REV] [--scope DIR ...]
    agents-remember knowledge-census report MEMORY_ROOT [--revision REV] [--census ID] [--json]

``inventory`` pins the baseline -- ``--code-commit`` of ``CODE_REPO`` and ``--memory-commit`` of
``MEMORY_ROOT``'s repository, both ``HEAD`` by default -- and writes the census's ``baseline.json``
and ``inventory.json`` into the converted memory working tree ``MEMORY_ROOT``. An unreadable baseline
is refused and nothing is written. Claims, assessments and route statuses are agent work, written
through the census writer (:class:`agents_remember.memory.knowledge_census.CensusWriter`).

``report`` reads the censuses of ``MEMORY_ROOT`` (its working tree, or ``--revision`` of it) and
prints each census's measures with their counts, its dispositions, its routes' governing statuses,
and the slices by route and by claim kind. It only reads.

Exit status: 0 on success; 1 when a census file is invalid (the report still prints what could be
read) or a write is refused; 2 when an input cannot be read or the baseline is unreadable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents_remember.memory.knowledge_census.inventory import (
    BaselineSide,
    CensusBaselineError,
    take_inventory,
)
from agents_remember.memory.knowledge_census.writer import CensusWriteError, CensusWriter
from agents_remember.memory_quality.knowledge_census.files import read_censuses
from agents_remember.memory_quality.knowledge_census.report import CensusReport, census_reports
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTreeReadError,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="census_command", required=True)
    inventory = sub.add_parser("inventory", help="Pin a census baseline and inventory it.")
    inventory.add_argument("memory_root", type=Path, help="The converted memory working tree.")
    inventory.add_argument("--census", required=True, help="The new census's ID.")
    inventory.add_argument("--code", required=True, type=Path, help="The code repository.")
    inventory.add_argument("--code-commit", default="HEAD", help="The code commit (HEAD).")
    inventory.add_argument(
        "--memory-commit", default="HEAD", help="The memory commit of MEMORY_ROOT (HEAD)."
    )
    inventory.add_argument(
        "--scope",
        action="append",
        default=[],
        help="A code directory in scope (repeat); none means the whole code tree.",
    )
    inventory.set_defaults(census_func=_run_inventory)
    report = sub.add_parser("report", help="Report each census's measures and route status.")
    report.add_argument("memory_root", type=Path, help="The memory repository or working tree.")
    report.add_argument("--revision", help="Read this memory commit instead of the working tree.")
    report.add_argument("--census", help="Report this census only.")
    report.add_argument("--json", action="store_true", help="Print the report as JSON.")
    report.set_defaults(census_func=_run_report)


def run(args: argparse.Namespace) -> int:
    return int(args.census_func(args))


def _run_inventory(args: argparse.Namespace) -> int:
    memory_root: Path = args.memory_root.resolve()
    try:
        baseline, inventory = take_inventory(
            args.census,
            code=BaselineSide(args.code.resolve(), args.code_commit),
            memory=BaselineSide(memory_root, args.memory_commit),
            scope=tuple(args.scope),
        )
    except (CensusBaselineError, OSError, ValueError) as error:
        print(f"inventory refused: {error}")
        return 2
    try:
        CensusWriter(memory_root).create(baseline, inventory)
    except CensusWriteError as error:
        print(error)
        return 1
    print(
        f"census {args.census}: code {baseline.code.commit}, memory {baseline.memory.commit}; "
        f"{len(inventory.sources)} source files, {len(inventory.artifacts)} onboarding artifacts, "
        f"{len(inventory.routes)} routes"
    )
    return 0


def _print_reports(
    reports: tuple[CensusReport, ...], problems: list[str], *, label: str, as_json: bool
) -> None:
    if as_json:
        document = {"censuses": [report.to_document() for report in reports], "problems": problems}
        print(json.dumps(document, indent=2, sort_keys=True))
        return
    if reports:
        print("\n\n".join(report.render() for report in reports))
    else:
        print(f"{label}: no census under knowledge/census/")
    for problem in problems:
        print(f"invalid census file: {problem}")


def _run_report(args: argparse.Namespace) -> int:
    memory_root: Path = args.memory_root.resolve()
    try:
        tree = (
            knowledge_tree_from_git(memory_root, args.revision)
            if args.revision
            else knowledge_tree_from_directory(memory_root)
        )
    except (OSError, KnowledgeTreeReadError, ValueError) as error:
        print(f"cannot read the memory tree: {error}")
        return 2
    censuses = read_censuses(tree.files)
    try:
        reports = census_reports(censuses, args.census)
    except KeyError as error:
        print(error.args[0])
        return 2
    problems = [
        f"{problem.path}: {problem.field or '-'}: {problem.message}"
        for problem in censuses.problems
    ]
    _print_reports(reports, problems, label=tree.label, as_json=args.json)
    return 1 if problems else 0
