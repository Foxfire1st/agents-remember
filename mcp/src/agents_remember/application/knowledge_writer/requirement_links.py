"""The requirement endpoints of the records one writer run authored, resolved by their owner (MIK-R13).

A decision (or any linked record) may name a requirement packet as a link target, in the MIK-R21
rule 6 shape ``{ task, packet, id, version }``. For every such link on a record this run created,
updated or left unchanged, the writer asks the requirement owner through
:func:`agents_remember.memory.knowledge.requirement_endpoint.resolve_requirement_endpoint` and
reports the answer. With a coordination root, a new or changed unresolved link refuses;
an unchanged unresolved link remains a report (MIK-R54 rule 10).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.application.knowledge_writer.handoff import Problem
from agents_remember.application.knowledge_writer.memory_state import MemoryState, deep_copy
from agents_remember.application.knowledge_writer.report import EndpointOutcome, RecordOutcome
from agents_remember.memory.knowledge.requirement_endpoint import resolve_requirement_endpoint
from agents_remember.models.knowledge_files.shapes import RequirementReference


def requirement_endpoints(
    state: MemoryState,
    records: Sequence[RecordOutcome],
    coordination_root: Path | None,
    previous: Mapping[str, Any] | None = None,
) -> tuple[EndpointOutcome, ...]:
    """Each requirement link target of ``records``, with the owner's resolution."""

    outcomes: list[EndpointOutcome] = []
    for record_id in dict.fromkeys(record.id for record in records):
        found = state.record(record_id)
        links = found[2].get("links") if found is not None else None
        for index, link in enumerate(links if isinstance(links, list) else ()):
            target = link.get("target") if isinstance(link, dict) else None
            if not isinstance(target, dict) or "task" not in target:
                continue
            try:
                reference = RequirementReference.model_validate(target)
            except ValidationError:
                continue  # the model check of the rendered record names the bad target
            endpoint = resolve_requirement_endpoint(coordination_root, reference)
            outcomes.append(
                EndpointOutcome(
                    record=record_id,
                    field=f"links.{index}",
                    relation=str(link.get("relation", "")),
                    endpoint=endpoint.key,
                    state=endpoint.state,
                    code=endpoint.code,
                    detail=endpoint.detail,
                    changed=link not in ((previous or {}).get(record_id) or []),
                    archived=endpoint.archived,
                )
            )
    return tuple(outcomes)


def stored_links(state: MemoryState) -> dict[str, Any]:
    return {
        record_id: deep_copy(found[2]).get("links", [])
        for record_id in state.records
        if (found := state.record(record_id)) is not None
    }


def requirement_link_problems(
    outcomes: Sequence[EndpointOutcome], coordination_root: Path | None
) -> list[Problem]:
    return [
        Problem(
            f"{one.record}.{one.field}",
            f"requirement {one.endpoint} does not resolve: [{one.code}] {one.detail}; correct or remove the link",
        )
        for one in outcomes
        if coordination_root is not None and one.changed and one.state == "unresolved"
    ]
