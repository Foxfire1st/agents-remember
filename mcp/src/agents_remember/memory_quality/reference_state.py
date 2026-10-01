"""Reference currentness over sidecars, and the mechanical reference fixer (MIK-R24 rule 5).

On a converted memory tree the citation checker works over sidecar ``references`` instead of
citation tables. Each code or test anchor target is observed in the code working tree:

* ``current`` -- the file's blob equals the anchor's ``blob``, or the located bytes still hash to
  the anchor's ``content`` (the MIK-R03 entry rule);
* ``stale`` -- the content differs, a symbol no longer resolves uniquely, or the path is absent.

A stale reference is **reported, never a gate finding**: the onboarding gate (MIK-R30) is the route by
which a curator refreshes it. The fixer only rewrites *mechanical* fields: an anchor whose content is
unchanged but whose ``blob`` (or, for a line range, whose line numbers) moved is re-recorded at the
working tree -- a line range is re-found only when its exact bytes occur exactly once. It never
touches a stale anchor, a note or a target list.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agents_remember.memory_quality.style.citations import extents, grammars
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    blob_lines,
    content_identity,
    range_bytes,
)
from agents_remember.models.knowledge_files.canonical import canonical_text, parse_json
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH

STALE_REFERENCE_CHECK: Final = "knowledge.references.stale"
_ANCHOR_KINDS: Final = frozenset({"code", "test"})


def is_converted_memory(memory_root: Path) -> bool:
    """A memory tree is converted exactly when it holds ``knowledge/layout.json``."""

    return (memory_root / LAYOUT_MARKER_PATH).is_file()


def git_blob_id(data: bytes) -> str:
    """The Git blob ID of ``data`` (SHA-1 object format), computed without Git."""

    return hashlib.sha1(b"blob %d\x00" % len(data) + data, usedforsecurity=False).hexdigest()


@dataclass
class _WorkingFiles:
    root: Path
    _cache: dict[str, bytes | None] = field(default_factory=dict)

    def read(self, path: str) -> bytes | None:
        if path not in self._cache:
            target = self.root / path
            self._cache[path] = target.read_bytes() if target.is_file() else None
        return self._cache[path]


def _span(path: str, data: bytes, name: str) -> tuple[int, int] | None:
    if not grammars.parsed(path):
        return None
    lines = data.decode("utf-8", errors="surrogateescape").split("\n")
    spans = extents.qualified_spans(name, extents.definitions(path, lines))
    return spans[0] if len(spans) == 1 else None


def located(path: str, data: bytes, locator: dict[str, Any]) -> bytes | None:
    """The bytes ``locator`` names in ``data``, or ``None`` when it does not resolve."""

    kind = locator.get("kind")
    if kind == "file":
        return data
    if kind == "symbol":
        span = _span(path, data, str(locator.get("name", "")))
        if span is None:
            return None
        start, end = span
    else:
        start, end = int(locator["start"]), int(locator["end"])
    try:
        return range_bytes(data, start, end)
    except RangeOutsideBlobError:
        return None


def anchor_state(path: str, anchor: dict[str, Any], files: _WorkingFiles) -> str:
    data = files.read(path)
    if data is None:
        return "stale:path-absent"
    if git_blob_id(data) == anchor.get("blob"):
        return "current"
    found = located(path, data, anchor["locator"])
    if found is None:
        return "stale:unresolved"
    return "current" if content_identity(found) == anchor.get("content") else "stale"


_MALFORMED: Final = (AttributeError, KeyError, TypeError, ValueError)
"""What reading a sidecar that is not valid JSON, or not shaped as a sidecar, raises."""


def _sidecars(
    memory_root: Path, unreadable: list[str], only: str | None = None
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every sidecar that holds references (or the one ``only`` names), parsed.

    A sidecar that is not valid JSON is named in ``unreadable`` and skipped: the knowledge validator
    refuses it by its own rule, and the reference check and the fixer never raise on it.
    """

    onboarding = memory_root / "onboarding"
    for sidecar in sorted(onboarding.rglob("*.json")):
        relative = sidecar.relative_to(memory_root).as_posix()
        if relative.endswith(".index.json") or not sidecar.is_file():
            continue
        if only is not None and relative != only:
            continue
        try:
            document = parse_json(sidecar.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            unreadable.append(relative)
            continue
        if isinstance(document, dict) and "references" in document:
            yield relative, document


def _anchor_targets(document: dict[str, Any]) -> Iterator[tuple[str, int, dict[str, Any]]]:
    own = document.get("path") if document.get("schema") == "ar-onboarding-file/v1" else None
    for number, reference in document.get("references", {}).items():
        for index, target in enumerate(reference.get("targets", [])):
            if target.get("kind") in _ANCHOR_KINDS:
                anchor = target["anchor"]
                path = anchor.get("path") or own
                if path:
                    yield f"references.{number}.targets.{index}", 0, {"path": path, **anchor}


def check_references(memory_root: Path, code_root: Path) -> dict[str, Any]:
    """Report every reference anchor's state in the code working tree (report-only)."""

    files = _WorkingFiles(code_root)
    states: Counter[str] = Counter()
    stale: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for relative, document in _sidecars(memory_root, unreadable):
        try:
            observed = [
                (field_name, anchor, anchor_state(anchor["path"], anchor, files))
                for field_name, _, anchor in _anchor_targets(document)
            ]
        except _MALFORMED:
            unreadable.append(relative)
            continue
        for field_name, anchor, state in observed:
            states[state.split(":")[0]] += 1
            if state != "current":
                stale.append(_stale_finding(relative, field_name, anchor["path"], state))
    return {
        "ok": True,
        "check": STALE_REFERENCE_CHECK,
        "status": "converted",
        "findingCount": 0,
        "findings": [],
        "reportOnlyFindings": stale,
        "states": dict(sorted(states.items())),
        "unreadableSidecars": unreadable,
    }


def _stale_finding(relative: str, field_name: str, target: str, state: str) -> dict[str, Any]:
    return {
        "check": STALE_REFERENCE_CHECK,
        "path": relative,
        "field": field_name,
        "target": target,
        "state": state,
        "message": (
            f"{relative}: {field_name}: reference target {target} is "
            f"{state}; refresh it through the onboarding gate (MIK-R30)"
        ),
    }


def _refreshed(path: str, anchor: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    """The anchor re-recorded at ``data`` when only mechanical fields moved, else ``None``."""

    blob = git_blob_id(data)
    if blob == anchor.get("blob"):
        return None
    locator = dict(anchor["locator"])
    found = located(path, data, locator)
    if found is not None and content_identity(found) == anchor.get("content"):
        return {**anchor, "blob": blob}
    if locator.get("kind") != "line_range":
        return None
    old_length = int(locator["end"]) - int(locator["start"]) + 1
    lines = blob_lines(data)
    matches = [
        start
        for start in range(1, len(lines) - old_length + 2)
        if content_identity(b"".join(lines[start - 1 : start - 1 + old_length]))
        == anchor.get("content")
    ]
    if len(matches) != 1:
        return None
    start = matches[0]
    moved = {"kind": "line_range", "start": start, "end": start + old_length - 1}
    return {**anchor, "locator": moved, "blob": blob}


def _refresh_document(document: dict[str, Any], files: _WorkingFiles) -> int:
    """Re-record the mechanically moved anchors of one sidecar in place; return how many."""

    own = document.get("path") if document.get("schema") == "ar-onboarding-file/v1" else None
    refreshed = 0
    for reference in document.get("references", {}).values():
        for target in reference.get("targets", []):
            if target.get("kind") not in _ANCHOR_KINDS:
                continue
            path = target["anchor"].get("path") or own
            data = files.read(path) if path else None
            updated = (
                _refreshed(path, target["anchor"], data) if path and data is not None else None
            )
            if updated is not None:
                target["anchor"] = updated
                refreshed += 1
    return refreshed


def fix_references(
    memory_root: Path, code_root: Path, *, dry_run: bool = False, only: str | None = None
) -> dict[str, Any]:
    """Re-record mechanically moved reference anchors; leave every stale one to the curator.

    ``only`` names one sidecar (memory-root relative): no other sidecar is read or rewritten. A
    sidecar that cannot be read as one is named in ``unreadableSidecars``, never rewritten, and
    makes the run not ``ok``; the others are still fixed.
    """

    files = _WorkingFiles(code_root)
    rewritten: list[str] = []
    unreadable: list[str] = []
    refreshed = 0
    for relative, document in _sidecars(memory_root, unreadable, only):
        try:
            count = _refresh_document(document, files)
        except _MALFORMED:
            unreadable.append(relative)
            continue
        if count:
            refreshed += count
            rewritten.append(relative)
            if not dry_run:
                (memory_root / relative).write_text(canonical_text(document), encoding="utf-8")
    return {
        "ok": not unreadable,
        "status": "converted",
        "dryRun": dry_run,
        "refreshedAnchors": refreshed,
        "rewrittenSidecars": rewritten,
        "unreadableSidecars": unreadable,
        "stale": check_references(memory_root, code_root)["reportOnlyFindings"],
    }
