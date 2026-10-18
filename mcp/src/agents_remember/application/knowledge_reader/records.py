"""How the reader spells records, links, decisions and numbered references (MIK-R29 rules 2 and 3).

* **A record summary** is what a link shows before it is followed: ID, kind, status and a title
  read off the record's own fields (a family's title, an invariant's first sentence, a decision's
  chosen option, a facet's leading field). A target the tree does not hold is shown as missing,
  never dropped.
* **A decision** is always shown whole wherever it appears -- in its truth view and in every view
  that links to it (MIK-R13 rule 6, carried to this leaf): its chosen and rejected alternatives
  with their reasons and ``reconsider_when``, its stored status and the status a reader shows
  (``superseded`` is derived, :func:`~agents_remember.models.knowledge_files.decisions.derived_status`),
  the decisions it supersedes and is superseded by, what it governs, and its ``reconsider_on``
  links per alternative. Every one of these is read through
  :mod:`agents_remember.models.knowledge_files.decisions`; nothing here re-derives them.
* **A link** is one recorded relationship with both ends named; a record end carries its summary.
* **A numbered reference** lists every target it has, each resolved: code and test anchors open at
  their locator, record targets open their truth view.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

if TYPE_CHECKING:
    from agents_remember.application.knowledge_reader.selection import ReaderSelection

from agents_remember.application.knowledge_currentness.observe import observe_entry, open_code_tree
from agents_remember.application.knowledge_leaf.selection import derived_title
from agents_remember.memory.knowledge.requirement_endpoint import (
    latest_approved_requirement_version,
    resolve_requirement_endpoint,
)
from agents_remember.memory.knowledge_index import Entry, KnowledgeIndex, Link, Record
from agents_remember.models.knowledge_files.decisions import (
    derived_status,
    governs_links,
    reconsider_links,
    superseded_by,
)
from agents_remember.models.knowledge_files.records import DecisionRecord
from agents_remember.models.knowledge_files.shapes import (
    ROUTE_TARGET_PREFIX,
    Anchor,
    RequirementReference,
)

__all__ = [
    "Decisions",
    "decision_document",
    "link_document",
    "record_link_target",
    "record_summary",
    "records_linking",
    "reference_items",
    "summary_of",
]

# The field a title is read from, per kind (the first present one wins).
_TITLE_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "family": ("title",),
    "invariant": ("statement",),
    "decision": ("context",),
    "incident": ("occurrence",),
    "assumption": ("proposition",),
    "limitation": ("limited",),
    "failure_mode": ("failure",),
    "scenario": ("situation",),
    "diagnostic": ("condition",),
    "term": ("term",),
}
_RECORD_TARGET_KINDS: Final = frozenset({"invariant", "family", "decision", "incident", "record"})


def record_summary(index: KnowledgeIndex, record_id: str) -> dict[str, Any]:
    """The summary a link to ``record_id`` shows; ``missing`` when the tree holds no such record."""

    record = index.record(record_id).value
    if record is None:
        return {"id": record_id, "missing": True}
    return summary_of(record)


def summary_of(record: Record) -> dict[str, Any]:
    document = record.document
    title = next(
        (str(document[name]) for name in _TITLE_FIELDS.get(record.kind, ()) if document.get(name)),
        record.id,
    )
    return {
        "id": record.id,
        "kind": record.kind,
        "status": record.status,
        "revision": record.revision,
        "title": derived_title(title) if record.kind != "family" else title,
    }


@dataclass
class Decisions:
    """Every decision of one tree, read once per view: the source of derived superseded status."""

    index: KnowledgeIndex
    _records: dict[str, DecisionRecord] | None = None
    _superseding: dict[str, tuple[str, ...]] = field(default_factory=dict)
    problems: dict[str, str] = field(default_factory=dict)

    def records(self) -> dict[str, DecisionRecord]:
        if self._records is None:
            parsed: dict[str, DecisionRecord] = {}
            for record in self.index.records_of_kind("decision").value:
                try:
                    parsed[record.id] = DecisionRecord.model_validate(record.document)
                except ValidationError as error:
                    self.problems[record.id] = str(error).splitlines()[0]
            self._records = parsed
            self._superseding = superseded_by(parsed.values())
        return self._records

    def superseding(self) -> dict[str, tuple[str, ...]]:
        self.records()
        return self._superseding


def decision_document(decisions: Decisions, decision_id: str) -> dict[str, Any] | None:
    """A decision in full, as every view shows it (MIK-R13 rule 6); ``None`` if not a decision."""

    record = decisions.records().get(decision_id)
    if record is None:
        problem = decisions.problems.get(decision_id)
        return None if problem is None else {"id": decision_id, "unreadable": problem}
    superseding = decisions.superseding()
    reopen: dict[int, list[dict[str, Any]]] = {}
    for one in reconsider_links(record):
        reopen.setdefault(one.index, []).append(record_link_target(one.link.target))
    return {
        "id": record.id,
        "revision": record.revision,
        "storedStatus": record.status,
        "derivedStatus": derived_status(record, superseding),
        "context": record.context,
        "alternatives": [
            {
                "index": position,
                "option": one.option,
                "status": one.status,
                "reason": one.reason,
                "reconsiderWhen": one.reconsider_when,
                "reconsiderOn": reopen.get(position, []),
            }
            for position, one in enumerate(record.alternatives)
        ],
        "consequences": list(record.consequences),
        "decider": record.decider,
        "supersedes": [record_summary(decisions.index, one) for one in record.supersedes],
        "supersededBy": [
            record_summary(decisions.index, one) for one in superseding.get(record.id, ())
        ],
        "governs": [
            {"relation": link.relation, "target": record_link_target(link.target)}
            for link in governs_links(record)
        ],
    }


def record_link_target(target: str | Anchor | RequirementReference) -> dict[str, Any]:
    """One end of a record's ``links``: a record ID, a route, a code anchor or a requirement."""

    if isinstance(target, str):
        if target.startswith(ROUTE_TARGET_PREFIX):
            return {"kind": "route", "path": target[len(ROUTE_TARGET_PREFIX) :]}
        return {"kind": "record", "id": target}
    document = target.to_document()
    if isinstance(target, Anchor):
        return {"kind": "code", "anchor": document, "path": target.path}
    return {"kind": "requirement", "requirement": document}


def link_document(
    index: KnowledgeIndex, link: Link, selection: ReaderSelection | None = None
) -> dict[str, Any]:
    """One indexed relationship, with the record at either end summarised and its state assessed."""

    document: dict[str, Any] = {
        "source": link.source,
        "sourceKind": link.source_kind,
        "relation": link.relation,
        "targetKind": link.target_kind,
        "target": link.target,
        "detail": link.detail,
        "originPath": link.origin_path,
    }
    if link.source_kind == "history_row":
        # A row has no page of its own: the reader names, and navigates to, the record it is about.
        # The lookup may return one row per file that holds the ID; the link's own origin path
        # picks the one this relationship was filed with, never the first of several.
        rows = index.history_row(link.source).value
        row = next((one for one in rows if one.path == link.origin_path), None)
        if row is not None:
            document["sourceSubject"] = record_summary(index, row.subject)
    elif link.source_kind not in ("file", "route"):
        document["sourceRecord"] = record_summary(index, link.source)
    if link.target_kind == "record":
        document["targetRecord"] = record_summary(index, link.target)
    document["targetState"] = _target_state(
        index, link.target_kind, link.target, selection, detail=link.detail
    )
    return document


def _target_state(
    index: KnowledgeIndex,
    kind: str,
    target: str,
    selection: ReaderSelection | None,
    *,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What a link's target is at this read: present or retired, current or stale, resolved or not.

    MIK-R73 rule 2. A record target is the record the index holds, or ``retired`` when its status
    says it left the line. Anchors use the full MIK-R03 observation at the captured tree; routes
    are current when that tree holds their path and stale when absent. Requirements carry their
    owner's answer, including its refusal code, archival flag and latest approved version.
    """

    if kind == "record":
        return _record_target_state(index, target)
    if kind in ("anchor", "code"):
        return _code_target_state(target, detail or {}, selection)
    if kind in ("route", "file"):
        return _route_target_state(target, selection)
    if kind == "requirement":
        reference = RequirementReference.model_validate(
            {
                key: value
                for key, value in (detail or {}).items()
                if key in RequirementReference.model_fields
            }
        )
        endpoint = resolve_requirement_endpoint(
            None if selection is None else selection.coordination_root, reference
        )
        return {
            "state": endpoint.state,
            "code": endpoint.code,
            "detail": endpoint.detail,
            "archived": endpoint.archived,
            "latestApproved": None
            if endpoint.task_root is None
            else latest_approved_requirement_version(endpoint.task_root, reference.id),
        }
    return {"state": "unverifiable", "detail": f"this reader does not assess a {kind} target"}


def _record_target_state(index: KnowledgeIndex, target: str) -> dict[str, Any]:
    found = index.record(target).value
    if found is None:
        return {"state": "absent", "detail": f"the tree holds no record {target}"}
    if found.status in _RETIRED_STATUSES:
        return {"state": "retired", "status": found.status}
    return {"state": "present", "status": found.status}


def _code_target_state(
    target: str, anchor: Mapping[str, Any], selection: ReaderSelection | None
) -> dict[str, Any]:
    tree = None if selection is None else selection.code_tree
    # The same link-to-entry adapter as reconsideration_gate_reason; nothing is indexed or authored.
    observed = observe_entry(
        Entry("link", "realization", "", target, "", {"anchor": dict(anchor)}), open_code_tree(tree)
    ).to_document()
    observed.pop("id")
    observed.pop("kind")
    return {**observed, "codeTree": None if tree is None else tree.to_document()}


def _route_target_state(target: str, selection: ReaderSelection | None) -> dict[str, Any]:
    tree = None if selection is None else selection.code_tree
    code = open_code_tree(tree)
    document: dict[str, Any] = {
        "path": target,
        "codeTree": None if tree is None else tree.to_document(),
    }
    if code.problem is not None:
        return {**document, "state": "unverifiable", "reason": code.problem}
    present = (
        target == "."
        or target in code.files
        or any(path.startswith(f"{target}/") for path in code.files)
    )
    return {
        **document,
        "state": "current" if present else "stale",
    }


_RETIRED_STATUSES = frozenset({"retired", "superseded", "withdrawn"})


def reference_items(
    index: KnowledgeIndex, references: Mapping[str, Any], own_path: str | None
) -> list[dict[str, Any]]:
    """A sidecar's numbered references, in number order, every target resolved (rule 2)."""

    def number(key: str) -> tuple[int, str]:
        return (int(key), key) if key.isdigit() else (1 << 30, key)

    return [
        {
            "number": key,
            "note": references[key].get("note"),
            "targets": [
                _reference_target(index, target, own_path)
                for target in references[key].get("targets", ())
            ],
        }
        for key in sorted(references, key=number)
    ]


def _reference_target(
    index: KnowledgeIndex, target: Mapping[str, Any], own_path: str | None
) -> dict[str, Any]:
    kind = str(target.get("kind"))
    if kind in ("code", "test"):
        anchor = dict(target.get("anchor") or {})
        path = anchor.get("path") or own_path
        return {"kind": kind, "path": path, "anchor": {**anchor, "path": path}}
    if kind in _RECORD_TARGET_KINDS:
        return {
            "kind": kind,
            "id": target.get("id"),
            "record": record_summary(index, str(target.get("id"))),
        }
    return {key: value for key, value in target.items()}


def records_linking(index: KnowledgeIndex, links: Iterable[Link]) -> list[dict[str, Any]]:
    """The records among ``links``' sources (never sidecars or history rows), each once."""

    seen: dict[str, dict[str, Any]] = {}
    for link in links:
        if link.source_kind in ("file", "route", "history_row"):
            continue
        entry = seen.setdefault(
            link.source, {"record": record_summary(index, link.source), "links": []}
        )
        entry["links"].append(
            {
                "relation": link.relation,
                "targetKind": link.target_kind,
                "target": link.target,
                "detail": link.detail,
            }
        )
    return [seen[key] for key in sorted(seen)]
