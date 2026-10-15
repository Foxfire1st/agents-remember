"""Requirement endpoint observations, outside validator findings (MIK-R54 rule 10)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agents_remember.memory.knowledge.requirement_endpoint import resolve_requirement_endpoint
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.models.knowledge_files.shapes import RequirementReference, RequirementTarget


def _references(tree: KnowledgeTree) -> Iterator[tuple[str, str, RequirementReference]]:
    parsed = parse_tree(tree)
    for record in parsed.records:
        for index, link in enumerate(getattr(record.record, "links", ())):
            if isinstance(link.target, RequirementReference):
                yield record.path, f"links.{index}.target", link.target
    for sidecar in parsed.sidecars:
        for number, reference in sidecar.sidecar.references.items():
            for index, target in enumerate(reference.targets):
                if isinstance(target, RequirementTarget):
                    yield sidecar.path, f"references.{number}.targets.{index}", target.requirement


def requirement_endpoint_documents(
    tree: KnowledgeTree, coordination_root: Path
) -> list[dict[str, Any]]:
    """Every authored occurrence with its owner's state; none changes the validator verdict."""
    documents: list[dict[str, Any]] = []
    for path, field, reference in _references(tree):
        endpoint = resolve_requirement_endpoint(coordination_root, reference)
        documents.append(
            {
                "path": path,
                "field": field,
                "key": endpoint.key,
                "reference": reference.to_document(),
                "state": endpoint.state,
                "archived": endpoint.archived,
                "taskRoot": None if endpoint.task_root is None else endpoint.task_root.as_posix(),
                "code": endpoint.code,
                "detail": endpoint.detail,
            }
        )
    return sorted(documents, key=lambda one: (one["path"], one["field"]))
