"""The explorer and the path view of the knowledge reader (MIK-R29 rules 1 and 2).

**The explorer** lists one directory level of the repository at the selection: the code tree's
children, joined with the onboarding mirror's (a path with onboarding but no code -- a deleted file
whose card lingers -- is listed and marked so), each with the number of knowledge entries recorded
at or under it. When the code tree cannot be listed the onboarding mirror still is, and the code
side says why.

**The path view** of a file or directory is read from the selection's derived index and Git
objects, reusing the landed reads:

* its onboarding Markdown with the sidecar's numbered references resolved (every target listed);
* its realization and proof entries (a directory: the entries of the files directly in it) grouped
  by invariant, each invariant and entry with its MIK-R03 state from
  :func:`~agents_remember.application.knowledge_currentness.invariant_currentness` at the
  selection's code tree;
* the families of those invariants and the families whose routes cover the path -- a file's route
  chain is MIK-R05's :func:`~agents_remember.application.knowledge_leaf.chain.select_chain`, a
  directory's is :meth:`~agents_remember.memory.knowledge_index.KnowledgeIndex.families_governing`
  of the directory itself -- each with every other location of its members;
* every record linking to the path (its anchors or its route) or to those invariants, a decision
  shown whole (:func:`.records.decision_document`);
* for a test file, its proofs by invariant with their facets (the same entries, ``testFile``);
* for a directory, its immediate children that hold knowledge with their live entry counts, and the
  size of its subtree. A directory's view is bounded (review F2): the recursive entry list is the
  ``subtree`` view (:mod:`.subtree`), paged through the shared continuation.

The **without-proof list** is MIK-R28 rule 5's
:meth:`~agents_remember.memory.knowledge_index.KnowledgeIndex.invariants_without_proof`, filtered to
the invariants realized at or under a path when one is given.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from agents_remember.application.knowledge_currentness import (
    Currentness,
    invariant_currentness,
)
from agents_remember.application.knowledge_currentness.surface import (
    CURRENTNESS_FAILURES,
    failure_document,
)
from agents_remember.application.knowledge_leaf.chain import select_chain
from agents_remember.application.knowledge_reader.files import (
    code_kind,
    normal_path,
    read_memory_file,
)
from agents_remember.application.knowledge_reader.records import (
    Decisions,
    decision_document,
    record_summary,
    records_linking,
    reference_items,
)
from agents_remember.application.knowledge_reader.selection import ReaderSelection
from agents_remember.application.knowledge_reader.tree_coverage import read_tree_listing
from agents_remember.memory.knowledge_index import Entry, KnowledgeIndex, Link
from agents_remember.models.knowledge_files.documents import (
    ONBOARDING_ROOT,
    file_sidecar_path,
    route_sidecar_path,
)
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH

__all__ = ["entry_document", "path_view", "states_at", "tree_listing", "without_proof"]

_RETIRED: Final = "retired"


# --------------------------------------------------------------------------------------------------
# The explorer
# --------------------------------------------------------------------------------------------------


def tree_listing(selection: ReaderSelection, directory: str) -> dict[str, Any]:
    """One directory level: code and onboarding children, with their entry counts."""

    directory = normal_path(directory)
    return read_tree_listing(selection, directory, _entry_counts(selection.index, directory))


def _entry_counts(index: KnowledgeIndex, directory: str) -> dict[str, int]:
    """Per immediate child of ``directory``, how many entries lie at or under it (paths only)."""

    prefix = "" if directory == ROOT_ROUTE_PATH else f"{directory}/"
    counts: dict[str, int] = defaultdict(int)
    for path in index.live_entry_paths_under(directory).value:
        rest = path[len(prefix) :] if path.startswith(prefix) else None
        if rest:
            counts[rest.split("/", 1)[0]] += 1
    return dict(counts)


# --------------------------------------------------------------------------------------------------
# The path view
# --------------------------------------------------------------------------------------------------


def path_view(selection: ReaderSelection, raw_path: str) -> dict[str, Any]:
    """Everything linked to one file or directory at the selection (rule 2).

    A directory's view is bounded (review F2): its own-level entries (files directly in it), each
    immediate child with its entry count, and the size of its subtree. The full recursive entry
    list is the ``subtree`` view, paged through the shared continuation (:mod:`.subtree`).
    """

    path = normal_path(raw_path)
    index = selection.index
    kind = _path_kind(selection, path)
    prose_path, sidecar_path = _onboarding_paths(path, kind)
    at = (index.entries_at_path(path) if kind == "file" else index.entries_in_directory(path)).value
    live = _live_entries(index, (*at.realizations, *at.proofs))
    invariant_ids = sorted({entry.invariant for entry in live})
    families = _families(index, path, kind, invariant_ids)
    currentness, problem = states_at(selection, invariant_ids, [one["id"] for one in families])
    links = _links_to(index, path, invariant_ids)
    document: dict[str, Any] = {
        "kind": kind,
        "path": path,
        "prose": read_memory_file(selection, prose_path).to_document(),
        "references": _references(selection, sidecar_path, path if kind == "file" else None),
        "testFile": kind == "file" and bool(at.proofs),
        "invariants": _invariant_groups(index, live, currentness),
        "families": [
            _with_locations(index, family, path, kind, currentness) for family in families
        ],
        "records": _linked_records(index, Decisions(index), links),
        "currentness": _currentness_block(currentness, problem, selection),
    }
    if kind == "directory":
        document.update(_directory_summary(index, path))
    return document


def _live_entries(index: KnowledgeIndex, entries: Iterable[Entry]) -> list[Entry]:
    """The entries whose invariant is live: a retired invariant is never shown as current."""

    return [entry for entry in entries if _live(index, entry.invariant, "invariant")]


def _links_to(index: KnowledgeIndex, path: str, invariants: Sequence[str]) -> list[Link]:
    """Every relationship naming the path (its anchors or route) or one of its invariants."""

    links: list[Link] = list(index.links_to_path(path).value)
    for invariant in invariants:
        links.extend(index.invariant(invariant).value.linked_from)
    return links


def _directory_summary(index: KnowledgeIndex, path: str) -> dict[str, Any]:
    """A directory's immediate children that hold knowledge, and its subtree's size."""

    counts = _entry_counts(index, path)
    under = index.live_entry_paths_under(path).value
    return {
        "children": [
            {
                "name": name,
                "path": name if path == ROOT_ROUTE_PATH else f"{path}/{name}",
                "entries": n,
            }
            for name, n in sorted(counts.items())
        ],
        "subtree": {"entries": len(under), "view": "subtree"},
    }


def _onboarding_paths(path: str, kind: str) -> tuple[str, str]:
    """The onboarding prose and sidecar of a file, or of a directory's route (``overview.*``)."""

    if kind == "file":
        return f"{ONBOARDING_ROOT}/{path}.md", file_sidecar_path(path)
    if path == ROOT_ROUTE_PATH:
        return f"{ONBOARDING_ROOT}/overview.md", route_sidecar_path(path)
    return f"{ONBOARDING_ROOT}/{path}/overview.md", route_sidecar_path(path)


def _path_kind(selection: ReaderSelection, path: str) -> str:
    if path == ROOT_ROUTE_PATH:
        return "directory"
    found = code_kind(selection, path)
    if found is not None:
        return "file" if found == "file" else "directory"
    if read_memory_file(selection, file_sidecar_path(path)).state == "present":
        return "file"
    if read_memory_file(selection, f"{ONBOARDING_ROOT}/{path}.md").state == "present":
        return "file"
    at = selection.index.entries_at_path(path).value
    return "file" if at.realizations or at.proofs else "directory"


def _references(selection: ReaderSelection, sidecar_path: str, own: str | None) -> dict[str, Any]:
    read = read_memory_file(selection, sidecar_path)
    document: dict[str, Any] = {"path": sidecar_path, "state": read.state, "items": []}
    if read.detail is not None:
        document["detail"] = read.detail
    if read.text is None:
        return document
    try:
        sidecar = json.loads(read.text)
        references = sidecar.get("references") or {}
        document["items"] = reference_items(selection.index, references, own)
    except (ValueError, AttributeError) as error:
        document["state"] = "unavailable"
        document["detail"] = f"the sidecar could not be read: {error}"
    return document


def states_at(
    selection: ReaderSelection, invariants: Sequence[str], families: Sequence[str]
) -> tuple[Currentness | None, str | None]:
    """MIK-R03's states of ``invariants`` (and every member of ``families``) at the selection's code
    tree, or ``None`` and the reason the step failed. The one currentness call of the reader."""

    try:
        return invariant_currentness(
            selection.code_tree, selection.index, invariants, families
        ), None
    except CURRENTNESS_FAILURES as error:
        return None, f"currentness could not be computed ({type(error).__name__}: {error})"


def _currentness_block(
    currentness: Currentness | None, problem: str | None, selection: ReaderSelection
) -> dict[str, Any]:
    if currentness is None:
        document = failure_document(selection.code_tree, RuntimeError(problem or ""))
        document["unverifiableReason"] = problem
        return document
    document = currentness.to_document()
    document.pop("invariants", None)
    return document


def entry_document(entry: Entry, currentness: Currentness | None) -> dict[str, Any]:
    """One realization or proof entry with its MIK-R03 state at the selection's code tree."""

    document = entry.document
    observed = None
    if currentness is not None:
        known = currentness.of(entry.invariant)
        if known is not None:
            observed = next((one for one in known.entries if one.entry_id == entry.id), None)
    return {
        "id": entry.id,
        "kind": entry.kind,
        "invariant": entry.invariant,
        "path": entry.path,
        "sidecar": entry.sidecar,
        "anchor": document.get("anchor"),
        "role": document.get("role"),
        "rationale": document.get("rationale"),
        "facet": document.get("facet"),
        "origin": document.get("origin"),
        "state": None if observed is None else observed.state,
        "reason": None if observed is None else observed.reason,
    }


def _invariant_groups(
    index: KnowledgeIndex, entries: Iterable[Entry], currentness: Currentness | None
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.invariant].append(entry)
    groups = []
    for invariant in sorted(grouped):
        record = index.record(invariant).value
        state = None if currentness is None else currentness.of(invariant)
        groups.append(
            {
                **record_summary(index, invariant),
                "statement": None if record is None else record.document.get("statement"),
                "state": None if state is None else state.state,
                "families": list(index.invariant(invariant).value.families),
                "entries": [entry_document(entry, currentness) for entry in grouped[invariant]],
            }
        )
    return groups


def _families(
    index: KnowledgeIndex, path: str, kind: str, invariants: Sequence[str]
) -> list[dict[str, Any]]:
    """The families of ``invariants`` and those routed over ``path``, each once, member first."""

    found: dict[str, dict[str, Any]] = {}
    for invariant in invariants:
        for family in index.invariant(invariant).value.families:
            if _live(index, family, "family"):
                found.setdefault(family, {"id": family, "member": True, "via": []})
    if kind == "file":
        via = {one.id: list(one.via) for one in select_chain(index, path, invariants)}
    else:
        via = defaultdict(list)
        for family, route in index.families_governing(path).value:
            if _live(index, family, "family"):
                via[family].append(route)
    for family, routes in via.items():
        found.setdefault(family, {"id": family, "member": False, "via": []})["via"] = routes
    return [found[key] for key in sorted(found, key=lambda one: (not found[one]["member"], one))]


def _with_locations(
    index: KnowledgeIndex,
    family: Mapping[str, Any],
    path: str,
    kind: str,
    currentness: Currentness | None,
) -> dict[str, Any]:
    knowledge = index.family(str(family["id"])).value
    record = knowledge.record
    elsewhere = _elsewhere(index, knowledge.members, path, kind)
    header = None
    if currentness is not None:
        header = next((one for one in currentness.families if one.id == family["id"]), None)
    return {
        **record_summary(index, str(family["id"])),
        "guarantee": None if record is None else record.document.get("guarantee"),
        "member": family["member"],
        "via": family["via"],
        "routes": list(knowledge.routes),
        "members": [record_summary(index, one) for one in knowledge.members],
        "staleMembers": [] if header is None else list(header.stale_members),
        "otherLocations": [
            {"path": where, "entries": elsewhere[where]} for where in sorted(elsewhere)
        ],
    }


def _elsewhere(
    index: KnowledgeIndex, members: Sequence[str], path: str, kind: str
) -> dict[str, list[dict[str, Any]]]:
    """Every entry of ``members`` that is not at (a file) or under (a directory) ``path``."""

    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for member in members:
        knowledge = index.invariant(member).value
        for entry in (*knowledge.realizations, *knowledge.proofs):
            if not _at(entry.path, path, kind):
                found[entry.path].append({"id": entry.id, "kind": entry.kind, "invariant": member})
    return found


def _at(entry_path: str, path: str, kind: str) -> bool:
    if kind == "file":
        return entry_path == path
    return path in (ROOT_ROUTE_PATH, entry_path) or entry_path.startswith(f"{path}/")


def _linked_records(
    index: KnowledgeIndex, decisions: Decisions, links: Iterable[Link]
) -> list[dict[str, Any]]:
    rows = records_linking(index, links)
    for row in rows:
        if row["record"].get("kind") == "decision":
            row["decision"] = decision_document(decisions, row["record"]["id"])
    return rows


def _live(index: KnowledgeIndex, record_id: str, kind: str) -> bool:
    record = index.record(record_id).value
    return record is not None and record.kind == kind and record.status != _RETIRED


# --------------------------------------------------------------------------------------------------
# Without proof (MIK-R28 rule 5)
# --------------------------------------------------------------------------------------------------


def without_proof(selection: ReaderSelection, raw_path: str | None) -> dict[str, Any]:
    """The live invariants no proof entry names, optionally only those realized under a path."""

    index = selection.index
    records = index.invariants_without_proof().value
    path = None if not raw_path else normal_path(raw_path)
    rows = []
    for record in records:
        realizations = index.invariant(record.id).value.realizations
        paths = sorted({entry.path for entry in realizations})
        if path is not None and not any(_at(one, path, "directory") for one in paths):
            continue
        rows.append(
            {
                **record_summary(index, record.id),
                "statement": record.document.get("statement"),
                "realizationPaths": paths,
            }
        )
    return {"path": path, "invariants": rows, "total": len(records)}
