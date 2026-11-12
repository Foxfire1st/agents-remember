"""Private carryover dependency closure (MIK-R56 rule 9); never writes history or records.

Indexes must describe the supplied current roots. Missing target records are needed; missing
source records and differing target bytes are conflicts. Apply must reopen indexes/replan, then
use ``record_payloads`` inside its own transaction. Markdown companions are optional in the
existing record schema; when present they participate in byte comparisons and travel with JSON.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from agents_remember.memory.carryover_documents import safe_memory_path
from agents_remember.memory.knowledge_index import KnowledgeIndex, Record
from agents_remember.models.knowledge_files.documents import (
    RECORD_DIRECTORIES,
    parse_document,
    parse_document_text,
    split_record_filename,
)
from agents_remember.models.knowledge_files.records import RECORD_MODELS
from agents_remember.models.knowledge_files.shapes import IdTarget
from agents_remember.models.knowledge_files.sidecars import FileSidecar, RouteSidecar


@dataclass(frozen=True)
class RecordContext:
    source_root: Path
    target_root: Path
    source_index: KnowledgeIndex
    target_index: KnowledgeIndex


@dataclass(frozen=True)
class RecordDependency:
    id: str
    source_paths: tuple[str, ...]
    target_paths: tuple[str, ...]
    state: str
    reason: str


@dataclass(frozen=True)
class RecordPlan:
    subjects: tuple[str, ...]
    needed: tuple[RecordDependency, ...]
    conflicts: tuple[RecordDependency, ...]
    history: tuple[dict[str, Any], ...]

    def to_document(self) -> dict[str, Any]:
        return asdict(self)


def named_record_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Exact schema edges: realizes/proves invariants, ID references, members/supersedes/links."""
    model = parse_document(document)
    ids: set[str] = set()
    if isinstance(model, FileSidecar | RouteSidecar):
        ids.update(
            target.id
            for reference in model.references.values()
            for target in reference.targets
            if isinstance(target, IdTarget)
        )
    if isinstance(model, FileSidecar):
        ids.update(entry.invariant for entry in (*model.realizes, *(model.proves or ())))
    if isinstance(model, tuple(RECORD_MODELS.values())):
        for field in ("members", "supersedes"):
            ids.update(getattr(model, field, ()))
        ids.update(
            link.target
            for link in getattr(model, "links", ())
            if isinstance(link.target, str) and not link.target.startswith("route:")
        )
    return tuple(sorted(ids))


def _record_pair(root: Path, record: Record) -> dict[str, bytes]:
    relative = PurePosixPath(record.path)
    identity, _slug, extension = split_record_filename(relative.name)
    allowed = {f"knowledge/{directory}" for directory in RECORD_DIRECTORIES.values()}
    if str(relative.parent) not in allowed or identity != record.id or extension != "json":
        raise ValueError(f"record path/ID mismatch: {record.id}: {record.path}")
    facts = safe_memory_path(root, record.path).read_bytes()
    model = parse_document_text(facts.decode("utf-8"))
    if not isinstance(model, tuple(RECORD_MODELS.values())):
        raise ValueError(f"not a record: {record.path}")
    if model.model_dump(mode="json", by_alias=True, exclude_none=True) != record.document:
        raise ValueError(f"record changed since index snapshot: {record.path}")
    expected = f"knowledge/{RECORD_DIRECTORIES[model.record_kind]}"
    if model.id != record.id or str(relative.parent) != expected:
        raise ValueError(f"record path/ID mismatch: {record.id}: {record.path}")
    pair = {record.path: facts}
    markdown = str(relative.with_suffix(".md"))
    prose = safe_memory_path(root, markdown)
    if prose.exists():
        pair[markdown] = prose.read_bytes()
    return pair


def _dependency(context: RecordContext, source: Record) -> RecordDependency:
    pair = _record_pair(context.source_root, source)
    target = context.target_index.record(source.id).value
    if target is not None:
        existing = _record_pair(context.target_root, target)
        source_bytes = {PurePosixPath(path).suffix: value for path, value in pair.items()}
        target_bytes = {PurePosixPath(path).suffix: value for path, value in existing.items()}
        identical = source_bytes == target_bytes
        resumable = (
            target.path == source.path
            and existing.keys() < pair.keys()
            and all(pair[path] == data for path, data in existing.items())
        )
        return RecordDependency(
            source.id,
            tuple(pair),
            tuple(existing),
            "present" if identical else "needed" if resumable else "record-differs",
            "target record pair is byte-identical"
            if identical
            else "target has exact source members; complete the missing companions"
            if resumable
            else "record-differs: curator must change differing target through the writer",
        )
    occupied = tuple(
        path
        for path in (source.path, str(PurePosixPath(source.path).with_suffix(".md")))
        if safe_memory_path(context.target_root, path).exists()
    )
    # A prose-only interrupted write has no indexed record yet. Admit only its
    # exact canonical source bytes, never an occupied JSON lacking the indexed ID.
    resumable = source.path not in occupied and all(
        path in pair and safe_memory_path(context.target_root, path).read_bytes() == pair[path]
        for path in occupied
    )
    return RecordDependency(
        source.id,
        tuple(pair),
        occupied,
        "needed" if resumable else "record-differs",
        "record-differs: target path is occupied without the expected indexed ID"
        if not resumable
        else "target lacks record members; carry its source pair",
    )


def _history(context: RecordContext, subjects: set[str]) -> tuple[dict[str, Any], ...]:
    rows = []
    for subject in sorted(subjects):
        for row in context.source_index.history_rows_about(subject).value:
            rows.append({**asdict(row), "reading_aid_only": True})
    return tuple(rows)


def plan_record_dependencies(context: RecordContext, document: Mapping[str, Any]) -> RecordPlan:
    """Transitive outgoing closure, with ID/path conflicts and source history as reading aids.

    A partial index never proves a record absent. ``subjects`` also includes entry IDs for
    subject-history lookups; only record IDs are traversed. No incoming records are fabricated.
    """
    pending = list(named_record_ids(document))
    subjects = set(pending)
    model = parse_document(document)
    if isinstance(model, FileSidecar):
        subjects.add(f"onboarding:{model.path}")
    elif isinstance(model, RouteSidecar):
        suffix = "overview" if model.path == "." else f"{model.path}/overview"
        subjects.add(f"onboarding:{suffix}")
    subjects.update(
        entry["id"] for field in ("realizes", "proves") for entry in document.get(field) or ()
    )
    seen: set[str] = set()
    needed: list[RecordDependency] = []
    conflicts: list[RecordDependency] = []
    partial = [
        f"{name} index is partial: {index.state.problems}"
        for name, index in (("source", context.source_index), ("target", context.target_index))
        if not index.state.complete or not index.state.converted
    ]
    if partial:
        return RecordPlan(
            tuple(sorted(subjects)),
            (),
            (RecordDependency("", (), (), "index-incomplete", "; ".join(partial)),),
            (),
        )
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        seen.add(identity)
        subjects.add(identity)
        source = context.source_index.record(identity).value
        if source is None:
            conflicts.append(
                RecordDependency(
                    identity,
                    (),
                    (),
                    "source-record-missing",
                    f"source record is missing: {identity}",
                )
            )
            continue
        try:
            dependency = _dependency(context, source)
            linked = named_record_ids(source.document)
        except (OSError, ValueError) as error:
            conflicts.append(
                RecordDependency(
                    identity,
                    (source.path,),
                    (),
                    "record-unreadable",
                    str(error),
                )
            )
            continue
        pending.extend(linked)
        if dependency.state == "needed":
            needed.append(dependency)
        elif dependency.state != "present":
            conflicts.append(dependency)
    return RecordPlan(
        tuple(sorted(subjects)),
        tuple(sorted(needed, key=lambda item: item.id)),
        tuple(sorted(conflicts, key=lambda item: item.id)),
        _history(context, subjects),
    )


def record_payloads(context: RecordContext, plan: RecordPlan) -> dict[str, bytes]:
    """Fresh source units for missing/resumable records; no history or writes.

    Any conflict refuses the candidate. Include exact present members so the transaction
    checks the complete unit, skips equal writes and reports them on validation refusal.
    Caller must replan on fresh indexes to detect relocated ID conflicts.
    """
    if plan.conflicts:
        raise ValueError("; ".join(item.reason for item in plan.conflicts))
    result = {}
    for dependency in plan.needed:
        record = context.source_index.record(dependency.id).value
        if record is None:
            raise ValueError(f"source record is missing: {dependency.id}")
        pair = _record_pair(context.source_root, record)
        if tuple(pair) != dependency.source_paths:
            raise ValueError(f"source record pair changed since plan: {dependency.id}")
        missing = False
        for path, value in pair.items():
            target = safe_memory_path(context.target_root, path)
            if target.exists():
                if target.read_bytes() != value:
                    raise ValueError(f"record-differs: never overwrite {path}")
            else:
                missing = True
        if missing:
            result.update(pair)
    return result
