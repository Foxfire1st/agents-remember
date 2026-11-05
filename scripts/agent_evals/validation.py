"""Compare real writer validation without hiding unchanged inherited faults."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Any

from agents_remember.application.knowledge_writer import writer
from agents_remember.memory_quality.knowledge_validator import validator
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.trees import CodePathSet, KnowledgeTree
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import (
    dependent_link_index,
    parse_document_text,
)
from agents_remember.models.knowledge_files.records import DependentRecord
from agents_remember.models.knowledge_files.sidecars import LayoutMarker

from .artifacts import json_digest


def _hash(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _target(files: Any, path: str, field: str) -> tuple[Any, str]:
    try:
        value = json.loads(files[path])
        for part in field.split("."):
            if isinstance(value, list):
                value = (
                    value[int(part)]
                    if part.isdecimal()
                    else next(
                        item for item in value if isinstance(item, dict) and item.get("id") == part
                    )
                )
            else:
                value = value[part]
        return value, "resolved from candidate field"
    except (KeyError, IndexError, TypeError, ValueError, StopIteration):
        return None, "no resolvable candidate field; see field and message"


def _finding(violation: Any, tree: KnowledgeTree) -> dict[str, Any]:
    target, resolution = _target(tree.files, violation.path, violation.field)
    return {**asdict(violation), "target": target, "targetResolution": resolution}


def validation_snapshot(
    tree: KnowledgeTree, code: CodePathSet, *, base: KnowledgeTree | None = None
) -> dict[str, Any]:
    """Run the whole native validator and writer split against exact input bytes.

    Only the dependent lookup is rebuilt as the native writer does. A missing or invalid
    layout marker stays invalid; provisioning, not validation, owns creating a marker.
    Labels are constant so diagnostic identity never depends on caller display text.
    """
    files = dict(tree.files)
    raw_marker = files.get("knowledge/layout.json")
    if raw_marker is not None:
        try:
            marker = parse_document_text(raw_marker.decode("utf-8"))
        except (ValueError, UnicodeError):
            marker = None
        if isinstance(marker, LayoutMarker):
            value = json.loads(raw_marker)
            links = dependent_link_index(
                (item.path, item.record)
                for item in parse_tree(tree).records
                if isinstance(item.record, DependentRecord)
            )
            if links is None:
                value.pop("dependent_links", None)
            else:
                value["dependent_links"] = links
            files["knowledge/layout.json"] = canonical_text(value).encode("utf-8")
    candidate = KnowledgeTree(label="eval validation", files=files)
    report = validator.validate_tree(
        candidate, bases=() if base is None else (base,), code=replace(code, label="eval code")
    )
    blocking, reported = writer._writer_split(report)
    memory_hashes = {path: _hash(data) for path, data in sorted(tree.files.items())}
    code_hashes = {path: _hash(code.read(path)) for path in sorted(code.paths)}
    return {
        "blockingViolations": [_finding(item, candidate) for item in blocking],
        "reportedViolations": [_finding(item, candidate) for item in reported],
        "memoryInputSha256": json_digest(memory_hashes),
        "codeInputSha256": json_digest(code_hashes),
        "memoryFileHashes": memory_hashes,
        "codeFileHashes": code_hashes,
        "validatedMemorySha256": json_digest(
            {path: _hash(data) for path, data in sorted(files.items())}
        ),
    }


def _identity(finding: dict[str, Any]) -> str:
    return json.dumps(
        {key: finding[key] for key in ("rule", "path", "field", "message", "target")},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compare_validation(
    before: KnowledgeTree,
    after: KnowledgeTree,
    before_code: CodePathSet,
    after_code: CodePathSet,
    *,
    written_paths: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Block new after-findings and any remaining fault on a written memory file."""
    old = validation_snapshot(before, before_code, base=before)
    new = validation_snapshot(after, after_code, base=before)
    changed = {
        path
        for path in before.files.keys() | after.files.keys()
        if before.files.get(path) != after.files.get(path)
    }
    written = changed | written_paths
    old_ids = {_identity(item) for item in old["blockingViolations"]}
    blocking, inherited = [], []
    for item in new["blockingViolations"]:
        if _identity(item) not in old_ids or item["path"] in written:
            blocking.append(item)
        else:
            inherited.append(
                {
                    **item,
                    "beforeSha256": _hash(before.files.get(item["path"])),
                    "afterSha256": _hash(after.files.get(item["path"])),
                }
            )
    return {
        "status": "fail" if blocking else "pass",
        "beforeBlockingViolations": old["blockingViolations"],
        "afterBlockingViolations": new["blockingViolations"],
        "blockingViolations": blocking,
        "preExistingViolations": inherited,
        "reportedViolations": new["reportedViolations"],
        "beforeReportedViolations": old["reportedViolations"],
        "writtenPaths": sorted(written),
        "changedPaths": sorted(changed),
        "beforeMemorySha256": old["memoryInputSha256"],
        "afterMemorySha256": new["memoryInputSha256"],
        "beforeCodeSha256": old["codeInputSha256"],
        "afterCodeSha256": new["codeInputSha256"],
        "beforeFileHashes": old["memoryFileHashes"],
        "afterFileHashes": new["memoryFileHashes"],
    }
