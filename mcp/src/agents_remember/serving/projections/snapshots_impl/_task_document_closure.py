"""The incremental task projection's document dependency closure."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.projections.snapshots_impl._task_documents import _projected_document
from agents_remember.tasks import TaskDocument
from pydantic import ValidationError


def doc_projection_closure(
    all_docs: list[tuple[Path, dict[str, object]]],
    affected: set[Path],
    tasks_root: Path,
) -> set[Path]:
    """Every document to re-project when ``affected`` documents changed (F09).

    The closure holds the affected documents, the masters in their folders (a
    subtask change re-renders its folder master's series checklist), their explicitly
    listed children (order fields consume the whole listed set), masters linked
    by execution-graph refs transitively (a commanded master change re-renders the
    commanding sprint's graph view), and folder masters of subtasks whose ``file``
    points into the closure (cross-document subtask joins). Everything resolves
    against the given payloads with no filesystem reads; validation is CPU-only.
    Over-approximation is safe (re-projecting an unaffected document is identical),
    under-approximation is not: when in doubt the caller falls back to full.
    """
    by_path = dict(all_docs)
    validated: dict[Path, TaskDocument | None] = {}

    def validated_doc(path: Path) -> TaskDocument | None:
        if path not in validated:
            payload = by_path.get(path)
            validated[path] = _projected_document(payload) if payload is not None else None
        return validated[path]

    def resolve_ref(ref: TaskDocumentRef) -> Path | None:
        candidate = tasks_root / ref.repository / ref.path
        return candidate if candidate in by_path else None

    closure = set(affected)
    folders = {path.parent for path in affected}
    for path, payload in by_path.items():
        if (
            path.parent in folders
            and payload.get("kind") == "master"
            and validated_doc(path) is not None
        ):
            closure.add(path)
    # Forward + reverse edges to a fixpoint: execution-graph refs out of the closure
    # and subtask file links into it. Both resolve lexically against retained paths.
    while _closure_step(by_path, validated_doc, resolve_ref, closure):
        pass
    return closure


def _closure_step(
    by_path: dict[Path, dict[str, object]],
    validated_doc: Callable[[Path], TaskDocument | None],
    resolve_ref: Callable[[TaskDocumentRef], Path | None],
    closure: set[Path],
) -> bool:
    """Grow ``closure`` by one fixpoint step over document links; True when it grew."""
    grown = False
    for path, payload in by_path.items():
        if path not in closure and not (
            _potential_targets(path, payload, resolve_ref, by_path) & closure
        ):
            continue
        doc = validated_doc(path)
        if doc is None:
            continue
        targets = _closure_targets(path, payload, doc, resolve_ref, by_path)
        if path in closure:
            before = len(closure)
            closure |= targets
            grown = grown or len(closure) > before
        elif targets & closure:
            grown = _absorb_linker(by_path, path, closure) or grown
    return grown


def _closure_targets(
    path: Path,
    payload: dict[str, object],
    doc: TaskDocument,
    resolve_ref: Callable[[TaskDocumentRef], Path | None],
    by_path: dict[Path, dict[str, object]],
) -> set[Path]:
    """Documents ``path`` renders data from: execution-graph refs and file links."""
    targets: set[Path] = set()
    if doc.executionGraph is not None:
        for ref in doc.executionGraph.master_refs():
            resolved = resolve_ref(ref)
            if resolved is not None:
                targets.add(resolved)
    return targets | _file_targets(path, payload, by_path)


def _potential_targets(
    path: Path,
    payload: dict[str, object],
    resolve_ref: Callable[[TaskDocumentRef], Path | None],
    by_path: dict[Path, dict[str, object]],
) -> set[Path]:
    """Cheap possible links before validating a whole unrelated task document.

    Ref normalization remains the canonical model's. Extra keys are ignored here as the
    full projection parser may prune them; only its later validation admits a consumer.
    """
    targets = _file_targets(path, payload, by_path)
    graph = payload.get("executionGraph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        return targets
    for node in nodes:
        if not isinstance(node, dict):
            continue
        raw_ref = node.get("ref", node)
        if not isinstance(raw_ref, dict):
            continue
        try:
            ref = TaskDocumentRef.model_validate(
                {"repository": raw_ref.get("repository"), "path": raw_ref.get("path")}
            )
        except ValidationError:
            continue
        resolved = resolve_ref(ref)
        if resolved is not None:
            targets.add(resolved)
    return targets


def _file_targets(
    path: Path,
    payload: dict[str, object],
    by_path: dict[Path, dict[str, object]],
) -> set[Path]:
    """The retained payload's cross-document file links, without filesystem access."""
    targets: set[Path] = set()
    subtasks = payload.get("subTasks")
    if not isinstance(subtasks, list):
        return targets
    for sub in subtasks:
        if not isinstance(sub, dict):
            continue
        file = sub.get("file")
        if not isinstance(file, str) or not file:
            continue
        linked = Path(
            os.path.normpath(os.path.join(path.parent.as_posix(), Path(file).with_suffix(".json")))
        )
        if linked in by_path:
            targets.add(linked)
    return targets


def _absorb_linker(by_path: dict[Path, dict[str, object]], path: Path, closure: set[Path]) -> bool:
    """Absorb a document outside the closure that points into it, plus its folder masters."""
    grown = False
    for other, other_payload in by_path.items():
        if (
            other.parent == path.parent
            and other_payload.get("kind") == "master"
            and other not in closure
        ):
            closure.add(other)
            grown = True
    if path not in closure:
        closure.add(path)
        grown = True
    return grown
