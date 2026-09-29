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

from agents_remember.kernel.git_command import run_git
from agents_remember.memory_quality.knowledge_validator.family_routes import (
    FamilyRouteState,
    family_route_state,
    realization_locations,
    suggest_family_routes,
)
from agents_remember.memory_quality.knowledge_validator.parsed import ParsedTree, parse_tree
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTreeReadError,
    code_tree_from_git,
    knowledge_tree_from_directory,
)
from agents_remember.models.knowledge_files.records import FamilyRecord


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


def _working_tree_files(code_root: Path) -> frozenset[str]:
    result = run_git(code_root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    if result.returncode != 0:
        raise KnowledgeTreeReadError(f"cannot list the files of {code_root}: {result.stderr}")
    return frozenset(path for path in result.stdout.split("\0") if path)


def _state_document(state: FamilyRouteState) -> dict[str, Any]:
    return {
        "unrealizedFamily": state.unrealized,
        "routeUnassigned": state.unassigned,
        "routeless": state.routeless,
        "uncovered": [location.path for location in state.uncovered],
        "emptied": list(state.emptied),
    }


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
    if suggestion["atRepositoryRoot"]:
        lines.append(
            f"  realizations at the repository root (route .): {suggestion['atRepositoryRoot']}"
        )
    return "\n".join(lines)


def _family_documents(
    parsed: ParsedTree, wanted: set[str], code_files: frozenset[str]
) -> list[dict[str, Any]]:
    locations = realization_locations(parsed.sidecars)
    documents: list[dict[str, Any]] = []
    for record in parsed.records:
        family = record.record
        if not isinstance(family, FamilyRecord) or (wanted and family.id not in wanted):
            continue
        state = family_route_state(family, locations)
        documents.append(
            {
                "family": family.id,
                "path": record.path,
                "status": family.status,
                "routes": list(family.routes),
                "state": _state_document(state),
                "suggestion": suggest_family_routes(family, locations, code_files).to_document(),
            }
        )
    return documents


def run(args: argparse.Namespace) -> int:
    memory_root: Path = args.memory_root.resolve()
    code_root: Path = args.code.resolve()
    try:
        parsed = parse_tree(knowledge_tree_from_directory(memory_root))
        code_files = (
            code_tree_from_git(code_root, args.code_commit).paths
            if args.code_commit
            else _working_tree_files(code_root)
        )
    except (OSError, KnowledgeTreeReadError, ValueError) as error:
        print(f"cannot read the inputs: {error}")
        return 2
    wanted = set(args.family)
    documents = _family_documents(parsed, wanted, code_files)
    unknown = sorted(wanted - {document["family"] for document in documents})
    if args.json:
        print(json.dumps(documents, indent=2, sort_keys=True))
    else:
        rendered = [_render(document) for document in documents]
        rendered.extend(f"unknown family: {family}" for family in unknown)
        print("\n".join(rendered) or "no family records")
    return 0
