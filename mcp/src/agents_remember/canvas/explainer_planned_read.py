"""The planned-mode page's own reads: one pass over the sources the page states (MIK-R189 rule 5).

The page itself is pure (:func:`agents_remember.canvas.explainer_planned.planned_page`); this module
is the read beside it, and it reads exactly what the rule counts: the leaf's task document, one
memory-tip record per declared existing subject, one file per packet the document names, the names in
`notes/reference/<leaf>/`, and the code tip's path set, resolved once for every Scope path.

Nothing here starts a model turn, captures a comparison or writes: a planned page is what the task is
meant to produce, and reading it must not change the leaf.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_planned import planned_page
from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge_index.cache import (
    KnowledgeIndexCache,
    default_cache_directory,
)
from agents_remember.memory.knowledge_index.query import KnowledgeIndex
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = ["planned_payload", "subject_statements"]

#: The task-relative folder a leaf's reference files live in (MIK-R189 rule 9).
REFERENCE_FOLDER = Path("notes") / "reference"


def planned_payload(
    *,
    document: Mapping[str, Any],
    comparison: ReviewTreeComparisonRecord,
    task_root: Path,
    coordination_root: Path,
    code_repository: Path,
) -> dict[str, Any]:
    """The planned-mode page for one planning leaf, from its own documents and the master's tips."""

    leaf_id = str(document.get("id") or comparison.leaf_id)
    statements = subject_statements(
        document=document,
        memory_repository=Path(comparison.memory_candidate.repository),
        memory_tree=comparison.memory_candidate.tree,
        coordination_root=coordination_root,
    )
    return planned_page(
        document=document,
        comparison=comparison,
        statements=statements,
        packet_texts=_packet_texts(document, task_root),
        reference_files=_reference_names(task_root, leaf_id),
        code_tip_paths=_code_tip_paths(code_repository, comparison.code_candidate.tree),
    )


def subject_statements(
    *,
    document: Mapping[str, Any],
    memory_repository: Path,
    memory_tree: str,
    coordination_root: Path,
) -> dict[str, str]:
    """Each declared existing subject's current statement, read at the memory tip.

    A subject the tree does not hold, or a record whose statement cannot be read, is left out: the
    page then shows the declaration alone rather than a statement it did not read.
    """

    statements: dict[str, str] = {}
    declared = [
        str(entry.get("subject") or "")
        for entry in document.get("expectedKnowledgeEffects") or ()
        if isinstance(entry, Mapping)
        and str(entry.get("subject") or "").startswith(("invariant:", "family:"))
    ]
    if not declared:
        return statements
    index = _index(memory_repository, memory_tree, coordination_root)
    for subject in declared:
        name = subject.partition(":")[2]
        found = index.record(name).value
        if found is None:
            continue
        statement = _statement_at(memory_repository, memory_tree, found.path)
        if statement:
            statements[subject] = statement
    return statements


def _index(repository: Path, tree: str, coordination_root: Path) -> KnowledgeIndex:
    """The memory tip's index, through the same cache the explainer's own reads use."""

    if not repository.is_dir():
        raise ExplainerRefused(
            "knowledge-tree-unreadable", f"the memory repository {repository} is not on this host"
        )
    cache = KnowledgeIndexCache(default_cache_directory(coordination_root))
    try:
        return cache.for_git_tree(repository, tree)
    except Exception as error:  # the tree reader's own fault is a named refusal
        raise ExplainerRefused("knowledge-tree-unreadable", str(error)) from error


def _statement_at(repository: Path, tree: str, path: str) -> str | None:
    """One record's ``statement``, read from the blob the memory tip holds at ``path``."""

    result = run_git(repository, ["show", f"{tree}:{path}"])
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return None
    statement = payload.get("statement") if isinstance(payload, dict) else None
    return statement.strip() if isinstance(statement, str) and statement.strip() else None


def _packet_texts(document: Mapping[str, Any], task_root: Path) -> dict[str, str]:
    """Every packet the document's requirements name, by its task-relative path."""

    texts: dict[str, str] = {}
    for entry in _requirement_paths(document):
        path = task_root / entry
        try:
            texts[entry] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return texts


def _requirement_paths(document: Mapping[str, Any]) -> tuple[str, ...]:
    """The task-relative paths of the packets one document names, in document order."""

    paths: list[str] = []
    entries = document.get("requirements")
    for entry in entries if isinstance(entries, (list, tuple)) else ():
        if isinstance(entry, str):
            target = entry.split("](", 1)[1].split(")", 1)[0] if "](" in entry else ""
        elif isinstance(entry, Mapping):
            target = str(entry.get("path") or "")
        else:
            continue
        if target and target not in paths:
            paths.append(target)
    return tuple(paths)


def _reference_names(task_root: Path, leaf_id: str) -> tuple[str, ...]:
    """The names in the leaf's reference folder, including nested files: what exists, and nothing invented."""

    folder = task_root / REFERENCE_FOLDER / leaf_id
    if not folder.is_dir() or folder.is_symlink():
        return ()
    resolved_folder = folder.resolve()
    names: list[str] = []
    for entry in sorted(folder.rglob("*")):
        if entry.is_symlink() or not entry.is_file():
            continue
        try:
            if not entry.resolve().is_relative_to(resolved_folder):
                continue
            names.append(entry.relative_to(folder).as_posix())
        except (OSError, ValueError):
            continue
    return tuple(sorted(names))


def _code_tip_paths(repository: Path, tree: str) -> tuple[str, ...]:
    """Every path at the code tip, for one resolution per Scope path (rule 10's count)."""

    result = run_git(repository, ["ls-tree", "-r", "--name-only", tree])
    if result.returncode != 0:
        raise ExplainerRefused(
            "planned-comparison-unavailable",
            f"the code tip's paths cannot be read in {repository}: {result.stderr.strip()}",
        )
    return tuple(line for line in result.stdout.splitlines() if line)
