"""Truth views, the census view, the record list and the code view (MIK-R29 rules 3 and 4).

A **truth view** shows one record of the selected tree with every field it records, its explanation
prose (the record's ``.md``) when it has one, its outgoing links (the record's own ``links``, and a
family's members and routes) and its incoming links (every relationship any file records towards
it), and its timeline (:mod:`.timeline`). Per kind:

* **invariant** -- statement, applicability, conditions, exclusions, status, admission; its
  realizations and proofs, each with its MIK-R03 state at the selection's code tree, and the
  invariant's own state; its families; the records linking to it, a decision shown whole;
* **family** -- guarantee, members (each with its statement and state), routes and every member
  location, grouped by path; the stale-member count of MIK-R03's family header;
* **decision** -- :func:`.records.decision_document`: alternatives with reasons and
  ``reconsider_when``, stored and derived status, supersedes and superseded-by, what it governs;
* **incident and every other facet record** -- every field as recorded (an incident's cause,
  ``cause_uncertainty``, recovery and corrective actions included), with its links both ways.

The **census view** is MIK-R20's report of each census under ``knowledge/census/``, computed from the
selected tree's census files (the index does not read them): measures with counts, and each route's
status history.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from agents_remember.application.knowledge_currentness import Currentness
from agents_remember.application.knowledge_reader.files import (
    FileRead,
    ReaderRequestError,
    census_files,
    code_text,
    normal_path,
    read_memory_file,
)
from agents_remember.application.knowledge_reader.paths import entry_document, states_at
from agents_remember.application.knowledge_reader.records import (
    Decisions,
    decision_document,
    link_document,
    record_link_target,
    record_summary,
    records_linking,
    summary_of,
)
from agents_remember.application.knowledge_reader.selection import READ_FAILURES, ReaderSelection
from agents_remember.application.knowledge_reader.timeline import record_timeline
from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.memory.knowledge_index import Entry, KnowledgeIndex, Record
from agents_remember.memory_quality.knowledge_census import census_reports, read_censuses
from agents_remember.models.knowledge_files.documents import RECORD_DIRECTORIES
from agents_remember.models.knowledge_files.records import RECORD_MODELS
from agents_remember.serving.scope import language_for

__all__ = ["census_view", "code_view", "record_list", "record_view"]

_FACET_FRAME: Final = frozenset({"schema", "id", "links"})


def record_view(selection: ReaderSelection, record_id: str) -> dict[str, Any] | None:
    """The truth view of ``record_id`` at the selection, or ``None`` when the tree holds none."""

    index = selection.index
    record = index.record(record_id).value
    if record is None:
        return None
    decisions = Decisions(index)
    outgoing, unreadable = _outgoing(record)
    document: dict[str, Any] = {
        "record": {**summary_of(record), "path": record.path, "document": record.document},
        "prose": read_memory_file(selection, _prose_path(record.path)).to_document(),
        "outgoing": outgoing,
        "incoming": [link_document(index, link) for link in index.incoming_links(record.id).value],
    }
    if unreadable is not None:
        # The record's own links could not be read: named, never shown as "no outgoing link".
        document["outgoingState"] = {"state": "unavailable", "detail": unreadable}
    part, entries = _kind_part(selection, record, decisions)
    document.update(part)
    history = index.history_rows_about(record.id).value
    document["timeline"] = record_timeline(selection, record, entries, history)
    return document


def _kind_part(
    selection: ReaderSelection, record: Record, decisions: Decisions
) -> tuple[dict[str, Any], tuple[Entry, ...]]:
    """The kind's own section of the truth view, and the entries whose log feeds its timeline."""

    index = selection.index
    if record.kind == "invariant":
        knowledge = index.invariant(record.id).value
        entries = (*knowledge.realizations, *knowledge.proofs)
        return {"invariant": _invariant(selection, record, decisions)}, entries
    if record.kind == "family":
        return {"family": _family(selection, record)}, _member_entries(index, record.id)
    if record.kind == "decision":
        return {"decision": decision_document(decisions, record.id)}, ()
    facet = {key: value for key, value in record.document.items() if key not in _FACET_FRAME}
    return {"facet": facet}, ()


def _member_entries(index: KnowledgeIndex, family_id: str) -> tuple[Entry, ...]:
    return tuple(
        entry
        for member in index.family(family_id).value.members
        for entry in (
            *index.invariant(member).value.realizations,
            *index.invariant(member).value.proofs,
        )
    )


def _prose_path(record_path: str) -> str:
    return f"{record_path[: -len('.json')]}.md" if record_path.endswith(".json") else record_path


def _outgoing(record: Record) -> tuple[list[dict[str, Any]], str | None]:
    """The record's own relationships -- its ``links``, then a family's members and routes, then
    what it supersedes -- and why its ``links`` could not be read, if they could not."""

    document = record.document
    links, unreadable = _links_of(record)
    return [
        *links,
        *(
            {"relation": "member", "target": {"kind": "record", "id": one}}
            for one in document.get("members") or ()
        ),
        *(
            {"relation": "routes", "target": {"kind": "route", "path": one}}
            for one in document.get("routes") or ()
        ),
        *(
            {"relation": "supersedes", "target": {"kind": "record", "id": one}}
            for one in document.get("supersedes") or ()
        ),
    ], unreadable


def _links_of(record: Record) -> tuple[list[dict[str, Any]], str | None]:
    model = RECORD_MODELS.get(record.kind)  # type: ignore[call-overload]
    if model is None:
        return [], f"no record model reads kind {record.kind!r}"
    try:
        parsed = model.model_validate(record.document)
    except ValidationError as error:
        return [], f"the record does not validate: {str(error).splitlines()[0]}"
    rows = []
    for link in getattr(parsed, "links", ()):
        row: dict[str, Any] = {"relation": link.relation, "target": record_link_target(link.target)}
        if link.alternative is not None:
            row["alternative"] = link.alternative
        rows.append(row)
    return rows, None


def _currentness_header(
    selection: ReaderSelection, currentness: Currentness | None, problem: str | None
) -> dict[str, Any]:
    return {
        "codeTree": None if selection.code_tree is None else selection.code_tree.to_document(),
        "unverifiableReason": problem if currentness is None else currentness.problem,
    }


def _invariant(selection: ReaderSelection, record: Record, decisions: Decisions) -> dict[str, Any]:
    index = selection.index
    knowledge = index.invariant(record.id).value
    currentness, problem = states_at(selection, [record.id], [])
    own = None if currentness is None else currentness.of(record.id)
    linked = records_linking(index, knowledge.linked_from)
    for row in linked:
        if row["record"].get("kind") == "decision":
            row["decision"] = decision_document(decisions, row["record"]["id"])
    return {
        "state": None if own is None else own.state,
        "currentness": _currentness_header(selection, currentness, problem),
        "realizations": [entry_document(one, currentness) for one in knowledge.realizations],
        "proofs": [entry_document(one, currentness) for one in knowledge.proofs],
        "families": [record_summary(index, one) for one in knowledge.families],
        "linked": linked,
    }


def _family(selection: ReaderSelection, record: Record) -> dict[str, Any]:
    index = selection.index
    knowledge = index.family(record.id).value
    currentness, problem = states_at(selection, list(knowledge.members), [record.id])
    locations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    members = []
    for member in knowledge.members:
        member_knowledge = index.invariant(member).value
        state = None if currentness is None else currentness.of(member)
        members.append(
            {
                **record_summary(index, member),
                "statement": None
                if member_knowledge.record is None
                else member_knowledge.record.document.get("statement"),
                "state": None if state is None else state.state,
            }
        )
        for entry in (*member_knowledge.realizations, *member_knowledge.proofs):
            locations[entry.path].append(entry_document(entry, currentness))
    header = None if currentness is None else next(iter(currentness.families), None)
    return {
        "currentness": _currentness_header(selection, currentness, problem),
        "members": members,
        "routes": list(knowledge.routes),
        "staleMembers": [] if header is None else list(header.stale_members),
        "locations": [{"path": path, "entries": locations[path]} for path in sorted(locations)],
    }


# --------------------------------------------------------------------------------------------------
# The record list, the census view and the code view
# --------------------------------------------------------------------------------------------------


def record_list(selection: ReaderSelection) -> dict[str, Any]:
    """Every record of the selected tree, by kind: the reader's index of truths."""

    index: KnowledgeIndex = selection.index
    return {
        "kinds": {
            kind: [summary_of(record) for record in index.records_of_kind(kind).value]
            for kind in RECORD_DIRECTORIES
        }
    }


def census_view(selection: ReaderSelection, census_id: str | None) -> dict[str, Any]:
    """MIK-R20's report of every census (or of one) in the selected tree."""

    try:
        tree = read_censuses(census_files(selection))
    except READ_FAILURES as error:
        return {
            "state": "unavailable",
            "detail": f"{type(error).__name__}: {error}",
            "censuses": [],
        }
    if census_id is not None and census_id not in tree.censuses:
        return {
            "state": "not-found",
            "detail": f"no census {census_id!r}; known: {sorted(tree.censuses)}",
            "censuses": [],
            "known": sorted(tree.censuses),
        }
    return {
        "state": "census",
        "censuses": [report.to_document() for report in census_reports(tree, census_id)],
        "known": sorted(tree.censuses),
        "problems": [
            {"path": one.path, "field": one.field, "message": one.message} for one in tree.problems
        ],
    }


def code_view(
    selection: ReaderSelection, raw_path: str, locator: str | None, recorded_blob: str | None
) -> dict[str, Any]:
    """One code file at the selection's code tree, opened at ``locator`` when one is given."""

    path = normal_path(raw_path)
    parsed = None if not locator else _parse_locator(locator)
    read = code_text(selection, path)
    if isinstance(read, FileRead):
        return {"state": read.state, "path": path, "detail": read.detail}
    blob, text = read
    document: dict[str, Any] = {
        "state": "present",
        "path": path,
        "blob": blob,
        "language": language_for(Path(path)),
        "text": text,
        "codeTree": None if selection.code_tree is None else selection.code_tree.to_document(),
    }
    if parsed is None:
        return document
    try:
        tree = selection.code_tree.tree if selection.code_tree is not None else blob
        trees = CodeTrees.open(selection.code_repository, tree, tree)
        resolved = trees.resolve(path, parsed, recorded_blob or blob, blob)
    except (CodeReadError, ValueError, TypeError, KeyError, *READ_FAILURES) as error:
        # A well-formed locator whose fields do not resolve (a non-integer line, an unknown kind)
        # is the caller's data: it is named unresolved, not an error.
        document["locator"] = {"state": "unresolved", "detail": f"{type(error).__name__}: {error}"}
        return document
    if resolved is None:
        document["locator"] = {
            "state": "unresolved",
            "detail": "the locator does not resolve uniquely in this blob",
        }
    else:
        document["locator"] = {"state": "resolved", "lines": list(resolved.span)}
    return document


def _parse_locator(locator: str) -> dict[str, Any]:
    """A locator is a JSON object with a string ``kind``; anything else is a bad request (F3)."""

    try:
        parsed = json.loads(locator)
    except ValueError as error:
        raise ReaderRequestError(f"the locator is not JSON: {error}") from error
    if not isinstance(parsed, dict) or not isinstance(parsed.get("kind"), str):
        raise ReaderRequestError('a locator is a JSON object with a string "kind"')
    return parsed
