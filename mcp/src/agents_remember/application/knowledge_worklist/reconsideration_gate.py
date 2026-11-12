"""MIK-R55 reconsideration currentness, shared by the registered gate predicate."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents_remember.application.knowledge_currentness.observe import OpenedCodeTree, observe_entry
from agents_remember.application.knowledge_worklist.code import CodeReadError
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.memory.knowledge_index import Entry
from agents_remember.models.knowledge_files.decisions import superseded_by
from agents_remember.models.knowledge_files.reconsideration import (
    REFRESH_NOT_CURRENT,
    reconsideration_item_open,
)
from agents_remember.models.knowledge_files.records import DecisionRecord
from agents_remember.models.knowledge_files.shapes import Anchor


def reconsideration_gate_reason(
    item: Mapping[str, Any],
    candidate: KnowledgeSide,
    code: OpenedCodeTree,
    rows_by_subject: Mapping[str, str],
) -> str | None:
    facts = item.get("facts") or {}
    records = candidate.parsed.records.values() if facts.get("decision") else ()
    decisions = [
        indexed.record for indexed in records if isinstance(indexed.record, DecisionRecord)
    ]
    superseded = set(superseded_by(decisions))
    if facts.get("decision") in superseded:
        return None
    remaining = []
    for changed in facts.get("changed", ()):
        if changed.get("trigger") != REFRESH_NOT_CURRENT:
            remaining.append(changed)
            continue
        indexed = candidate.parsed.records.get(str(facts.get("decision")))
        links = () if indexed is None else getattr(indexed.record, "links", ())
        position = changed.get("link")
        if not isinstance(position, int) or position >= len(links):
            return "the refreshed reconsider_on link is not current at C"
        target = links[position].target
        if isinstance(target, Anchor) and target.to_document() == changed.get("baseTarget"):
            continue
        if not isinstance(target, Anchor):
            return "the refreshed reconsider_on link is not current at C"
        observed = observe_entry(
            Entry(
                "link", "realization", "", target.path or "", "", {"anchor": target.to_document()}
            ),
            code,
        )
        if observed.read_failed:
            raise CodeReadError(observed.reason)
        if observed.state != "current":
            return f"the refreshed reconsider_on link is not current at C: {observed.reason}"
    if not remaining and facts.get("changed"):
        return None
    return (
        "no satisfying row or change answers it"
        if reconsideration_item_open(
            {**item, "facts": {**facts, "changed": remaining}}, rows_by_subject, superseded
        )
        else None
    )
