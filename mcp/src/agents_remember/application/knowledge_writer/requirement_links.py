"""The requirement endpoints of the records one writer run authored, resolved by their owner (MIK-R13).

A decision (or any linked record) may name a requirement packet as a link target, in the MIK-R21
rule 6 shape ``{ task, packet, id, version }``. For every such link on a record this run created,
updated or left unchanged, the writer asks the requirement owner through
:func:`agents_remember.memory.knowledge.requirement_endpoint.resolve_requirement_endpoint` and
reports the answer. An unresolved endpoint is **reported, never refused**: the link is written
exactly as the curator authored it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from agents_remember.application.knowledge_writer.memory_state import MemoryState
from agents_remember.application.knowledge_writer.report import EndpointOutcome, RecordOutcome
from agents_remember.memory.knowledge.requirement_endpoint import resolve_requirement_endpoint
from agents_remember.models.knowledge_files.shapes import RequirementReference


def requirement_endpoints(
    state: MemoryState, records: Sequence[RecordOutcome], coordination_root: Path | None
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
                )
            )
    return tuple(outcomes)
