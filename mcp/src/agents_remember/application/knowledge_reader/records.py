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
from typing import Any, Final

from pydantic import ValidationError

from agents_remember.application.knowledge_leaf.selection import derived_title
from agents_remember.memory.knowledge_index import KnowledgeIndex, Link, Record
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


def link_document(index: KnowledgeIndex, link: Link) -> dict[str, Any]:
    """One indexed relationship, with the record at either end summarised."""

    document: dict[str, Any] = {
        "source": link.source,
        "sourceKind": link.source_kind,
        "relation": link.relation,
        "targetKind": link.target_kind,
        "target": link.target,
        "detail": link.detail,
        "originPath": link.origin_path,
    }
    if link.source_kind not in ("file", "route", "history_row"):
        document["sourceRecord"] = record_summary(index, link.source)
    if link.target_kind == "record":
        document["targetRecord"] = record_summary(index, link.target)
    return document


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
