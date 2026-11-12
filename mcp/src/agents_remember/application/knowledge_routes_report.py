"""The family-route report: each family's routes beside the mechanical suggestion (MIK-R04, MIK-R73 rule 4).

Routes are authored on a family record; the mechanical suggestion is derived from where the
family's realizations live in the code checkout. The curator's ``knowledge-routes`` command reads
this report, and ``knowledge_integrity_check`` answers with it beside the validator's findings, so
one function owns both and neither surface can answer a different route set.

The report reads only: no route is written, suggested or repaired here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

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

__all__ = ["family_route_documents", "routes_report", "working_tree_files"]


def working_tree_files(code_root: Path) -> frozenset[str]:
    """The tracked and untracked, not ignored files of ``code_root``."""

    result = run_git(code_root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    if result.returncode != 0:
        raise KnowledgeTreeReadError(f"cannot list the files of {code_root}: {result.stderr}")
    return frozenset(path for path in result.stdout.split("\0") if path)


# A row states its uncovered locations without becoming unbounded: an answer carries the first
# few and the total, and the whole report a caller can open keeps every one.
UNCOVERED_SHOWN: Final = 10


def _state_document(state: FamilyRouteState) -> dict[str, Any]:
    return {
        "unrealizedFamily": state.unrealized,
        "routeUnassigned": state.unassigned,
        "routeless": state.routeless,
        "uncovered": [location.path for location in state.uncovered[:UNCOVERED_SHOWN]],
        "uncoveredTotal": len(state.uncovered),
        "emptied": list(state.emptied),
    }


def family_route_documents(
    parsed: ParsedTree, wanted: set[str], code_files: frozenset[str] | None
) -> list[dict[str, Any]]:
    """One row per family of ``parsed``: its routes, its route state and the suggestion.

    ``code_files`` is ``None`` when the code root holds no working tree to read (it is not a Git
    checkout). The routes and their state are still reported -- they are the family's own -- and
    the row says the suggestion was not computed instead of guessing one from an empty file list.
    """

    locations = realization_locations(parsed.sidecars)
    documents: list[dict[str, Any]] = []
    for record in parsed.records:
        family = record.record
        if not isinstance(family, FamilyRecord) or (wanted and family.id not in wanted):
            continue
        state = family_route_state(family, locations)
        row: dict[str, Any] = {
            "family": family.id,
            "path": record.path,
            "status": family.status,
            "routes": list(family.routes),
            "state": _state_document(state),
        }
        if code_files is None:
            row["suggestion"] = None
            row["suggestionState"] = {
                "state": "unavailable",
                "detail": "the code root is not a Git checkout, so no working tree was listed",
            }
            row["differs"] = False
        else:
            suggestion = suggest_family_routes(family, locations, code_files)
            row["suggestion"] = suggestion.to_document()
            row["differs"] = set(family.routes) != set(suggestion.routes)
        documents.append(row)
    return documents


def routes_report(
    memory_root: Path,
    code_root: Path,
    *,
    families: set[str] | None = None,
    code_commit: str | None = None,
) -> list[dict[str, Any]]:
    """The route rows of one memory tree, read against ``code_root`` or a commit of it."""

    parsed = parse_tree(knowledge_tree_from_directory(memory_root.resolve()))
    code_files = (
        code_tree_from_git(code_root.resolve(), code_commit).paths
        if code_commit
        else working_tree_files(code_root.resolve())
    )
    return family_route_documents(parsed, families or set(), code_files)
