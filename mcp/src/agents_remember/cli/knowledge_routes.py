"""CLI adapter: the curator's view of family routes and their mechanical suggestion (MIK-R04).

    agents-remember knowledge-routes MEMORY_ROOT --code CODE_ROOT [--code-commit REV]
                                     [--family FAM-ID ...] [--json]

For each family record of the memory working tree it prints the family's routes, its route state
(``unrealized_family``, ``route_unassigned``, uncovered realizations and routes with none) and the
mechanical route suggestion of MIK-R04 rule 3, labelled ``mechanical``. The command only reads: it
never writes a route. The curator places routes as deep as makes sense and writes them through the
writer. The code files a suggestion reads are the tracked and untracked, not ignored, files of
``--code`` (or the files of ``--code-commit`` in it).

Exit status: 0 when the families are read, 2 when an input cannot be read.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_routes_report import routes_report
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTreeReadError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("memory_root", type=Path, help="The memory working tree to read.")
    parser.add_argument(
        "--code", required=True, type=Path, help="The paired code checkout (its files)."
    )
    parser.add_argument(
        "--code-commit", help="Read the files of this commit of --code, not its working tree."
    )
    parser.add_argument(
        "--family", action="append", default=[], help="Only this family (repeat for several)."
    )
    parser.add_argument("--json", action="store_true", help="Print the families as JSON.")


def _render(document: dict[str, Any]) -> str:
    state = document["state"]
    lines = [f"{document['family']} ({document['status']}): routes {document['routes']}"]
    for flag, name in (
        ("unrealizedFamily", "unrealized_family"),
        ("routeUnassigned", "route_unassigned"),
    ):
        if state[flag]:
            lines.append(f"  state: {name}")
    if state["routeless"]:
        lines.append("  Coverage: no route, and not legacy-unassessed")
    lines.extend(f"  uncovered: {path}" for path in state["uncovered"])
    lines.extend(f"  emptied route: {route}" for route in state["emptied"])
    suggestion = document["suggestion"]
    lines.append(f"  suggestion ({suggestion['label']}): {suggestion['routes']}")
    if document["differs"]:
        lines.append("  differs: authored routes differ from the mechanical suggestion")
    if suggestion["atRepositoryRoot"]:
        lines.append(
            f"  realizations at the repository root (route .): {suggestion['atRepositoryRoot']}"
        )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    memory_root: Path = args.memory_root.resolve()
    code_root: Path = args.code.resolve()
    wanted = set(args.family)
    try:
        documents = routes_report(
            memory_root, code_root, families=wanted, code_commit=args.code_commit
        )
    except (OSError, KnowledgeTreeReadError, ValueError) as error:
        print(f"cannot read the inputs: {error}")
        return 2
    unknown = sorted(wanted - {document["family"] for document in documents})
    if args.json:
        print(json.dumps(documents, indent=2, sort_keys=True))
    else:
        rendered = [_render(document) for document in documents]
        rendered.extend(f"unknown family: {family}" for family in unknown)
        print("\n".join(rendered) or "no family records")
    return 0
