"""The receipts every explainer packet carries (MIK-R185 v2 items 4 and 5).

The explainer explains a change set that rests on documents as much as on code: the leaf's own
packets, the task document that holds its Prepared context, the reference folder his picks name,
and -- once the leaf has an agreed revision -- the revision the intent review accepted, as the
yardstick for everything after it (MIK-R189 rule 9).

None of those is part of the comparison, so none may be cited as though it were: each is admitted
through the task that owns it and bound by the bytes actually read
(:mod:`agents_remember.application.review_supplemental_evidence`). A source that cannot be read is
an ``unavailable`` receipt with its limitation, which is a measured loss rather than a refusal
(MIK-R102 rule 12): the build goes on and the page knows what it could not see.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.application.review_supplemental_evidence import capture_supplemental
from agents_remember.kernel.recorded_reads import observed_entries
from agents_remember.models.knowledge.review_evidence import SupplementalReceipt
from agents_remember.tasks.document import resolve_agreed_revision

__all__ = [
    "TASK_DOCUMENT_JSON_ID",
    "TASK_DOCUMENT_MD_ID",
    "compose_receipts",
    "requirement_paths",
]

#: One packet's byte-read budget: every source beyond it is receipted as an explicit omission.
CAPTURE_IDENTITY = "the explainer's build and Update"
ESTABLISHES = "a document the explained change set rests on, bound by the bytes read"
REFERENCE_DIRECTORY = "notes/reference"
AGREED_MANIFEST = "notes/reference"
TASK_DOCUMENT_JSON_ID = "task-document"
TASK_DOCUMENT_MD_ID = "task-document-md"
_LINK = re.compile(r"\]\((?P<path>requirements/[^)\s]+\.md)\)")


def requirement_paths(document: Mapping[str, Any]) -> tuple[str, ...]:
    """The requirement files the leaf's own document names, in the order it names them."""

    seen: list[str] = []
    for entry in document.get("requirements") or ():
        for path in _entry_paths(entry):
            if path not in seen:
                seen.append(path)
    return tuple(seen)


def _entry_paths(entry: object) -> tuple[str, ...]:
    if isinstance(entry, str):
        found = tuple(match.group("path") for match in _LINK.finditer(entry))
        if found:
            return found
        cleaned = entry.strip()
        is_direct = (
            cleaned.startswith("requirements/") and cleaned.endswith(".md") and " " not in cleaned
        )
        return (cleaned,) if is_direct else ()
    raw = entry.get("path") if isinstance(entry, Mapping) else getattr(entry, "path", None)
    return (raw.strip(),) if isinstance(raw, str) and raw.strip() else ()


def _document_sources(document: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    slug = document.get("slug")
    if not isinstance(slug, str) or not slug:
        return ()
    return (
        (TASK_DOCUMENT_JSON_ID, f"{slug}.json"),
        (TASK_DOCUMENT_MD_ID, f"{slug}.md"),
    )


def _reference_files(task_root: Path, leaf_id: str) -> tuple[str, ...]:
    """Every regular file under the leaf's reference folder; each listing walked is an input."""

    files: list[Path] = []
    pending = [task_root / REFERENCE_DIRECTORY / leaf_id]
    while pending:
        folder = pending.pop()
        try:
            entries = observed_entries(folder)
        except OSError:
            continue
        for name, kind in entries:
            if kind == "directory":
                pending.append(folder / name)
            elif kind == "file":
                files.append(folder / name)
    return tuple(entry.relative_to(task_root).as_posix() for entry in sorted(files))


def _agreed_manifest_entry(
    document: Mapping[str, Any], task_root: Path, leaf_id: str
) -> tuple[str, str | None] | None:
    """The agreed revision's manifest path and any verification failure (MIK-R189 rule 9)."""

    agreed = document.get("intentReview")
    dump = getattr(agreed, "model_dump", None)
    if callable(dump):
        agreed = dump(by_alias=True)
    if not isinstance(agreed, Mapping) or agreed.get("agreedRevision") is None:
        return None
    resolved = resolve_agreed_revision(task_root, document, leaf_id=leaf_id)
    if resolved is None:
        return (
            f"{REFERENCE_DIRECTORY}/{leaf_id}/manifest.json",
            "the source was not read: intentReview.agreedRevision did not resolve to a retained revision",
        )
    source = _task_relative(task_root, resolved.manifest_path)
    if not resolved.digest_verified:
        return (
            source,
            f"the source was not read: retained revision {resolved.revision} does not match the "
            f"agreed sha256 {resolved.sha256}",
        )
    return (source, None)


def _task_relative(task_root: Path, path: Path) -> str:
    """One receipt source, stated relative to the task that owns it (rule 11)."""

    try:
        return path.relative_to(task_root).as_posix()
    except ValueError:
        return path.as_posix()


def compose_receipts(
    task_root: Path, document: dict[str, Any], leaf_id: str
) -> tuple[SupplementalReceipt, ...]:
    """Every receipt one build or Update carries, each admitted through the task that owns it."""

    slug = document.get("slug") if isinstance(document.get("slug"), str) else None
    # The leaf's own document is admitted under its name only when this is that leaf's document:
    # a document that names another leaf admits nothing of its own (MIK-R102 v2 item 2).
    own = document.get("id") == leaf_id
    packets = [
        (f"packet-{index:02d}", path)
        for index, path in enumerate(requirement_paths(document), start=1)
    ]
    docs = list(_document_sources(document))
    agreed = _agreed_manifest_entry(document, task_root, leaf_id)
    reserved_paths = (
        {path for _, path in packets}
        | {path for _, path in docs}
        | ({agreed[0]} if agreed is not None else set())
    )
    references: list[tuple[str, str]] = []
    for path in _reference_files(task_root, leaf_id):
        if path not in reserved_paths:
            reserved_paths.add(path)
            references.append((f"reference-{len(references) + 1:02d}", path))
    sources: list[tuple[str, str, str | None]] = [
        *((receipt_id, path, None) for receipt_id, path in packets),
        *((receipt_id, path, None) for receipt_id, path in docs),
        *((receipt_id, path, None) for receipt_id, path in references),
    ]
    if agreed is not None:
        sources.append(("agreed-manifest", agreed[0], agreed[1]))
    context = (task_root, leaf_id, slug if own else None)
    return tuple(_build_receipt(source, context) for source in sources)


def _build_receipt(
    source: tuple[str, str, str | None],
    context: tuple[Path, str, str | None],
) -> SupplementalReceipt:
    receipt_id, relative, failure = source
    task_root, leaf_id, leaf_document = context
    producer = f"leaf {leaf_id}"
    authority = "the task document this leaf is registered by"
    if failure is not None:
        return SupplementalReceipt(
            receipt_id=receipt_id,
            canonical_source=relative,
            producer=producer,
            authority=authority,
            read_state="unavailable",
            capture_identity=CAPTURE_IDENTITY,
            establishes=ESTABLISHES,
            limitation=failure,
        )
    return capture_supplemental(
        task_root,
        relative,
        receipt_id=receipt_id,
        capture_identity=CAPTURE_IDENTITY,
        establishes=ESTABLISHES,
        producer=producer,
        authority=authority,
        leaf_document=leaf_document,
    )
