"""Shared onboarding-document parsing and body/history change helpers.

Owns the markdown-table metadata parsing, route normalization, and the
section-aware body/history change classification used by closeout gates and
carryover planning. The "meaningful body" of an onboarding document is its
content minus the verification metadata rows and the Update History section,
so metadata stamps and history appendices never count as content updates.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from agents_remember.kernel import filesystem

ROUTE_OVERVIEW_DOC_TYPES = {"repo-overview", "route-local-overview"}
METADATA_FIELDS = {"lastUpdated", "lastVerifiedCommitHash", "lastVerifiedCommitDate"}
UPDATE_HISTORY_HEADING = "## update history"
NO_IMPACT_MARKER_PATTERN = re.compile(r"\bno (?:content|route) impact:", re.IGNORECASE)


def onboarding_metadata_row(
    text: str, field: str, value: str, *, code: bool = False
) -> tuple[str, bool]:
    rendered = f"`{value}`" if code else value
    pattern = re.compile(rf"(\|\s*{re.escape(field)}\s*\|\s*)`?[^|`]*`?(\s*\|)")
    updated, count = pattern.subn(rf"\g<1>{rendered}\g<2>", text, count=1)
    return updated, count > 0


def markdown_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def table_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in filesystem.read_text(path, encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = markdown_table_cells(line)
        if len(cells) < 2:
            continue
        field = cells[0].strip()
        value = cells[1].strip().strip("`")
        if not field or set(field) <= {"-"} or field.lower() == "field":
            continue
        metadata[field] = value
    return metadata


def normalize_route(route: str) -> str:
    normalized = route.strip().strip("`").replace("\\", "/").strip("/")
    if normalized in {"", ".", "<repo-root>"}:
        return "."
    return normalized


def route_contains_changed_path(route: str, changed_paths: list[str]) -> bool:
    if not changed_paths:
        return False
    normalized_route = normalize_route(route)
    if normalized_route == ".":
        return True
    prefix = f"{normalized_route}/"
    return any(path == normalized_route or path.startswith(prefix) for path in changed_paths)


def _is_update_history_heading(line: str) -> bool:
    return line.strip().lower() == UPDATE_HISTORY_HEADING


def meaningful_body(text: str) -> str:
    """Document content minus verification metadata rows and Update History."""
    kept: list[str] = []
    in_history = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_history = _is_update_history_heading(stripped)
        if in_history:
            continue
        cells = markdown_table_cells(line) if stripped.startswith("|") else []
        if cells and cells[0] in METADATA_FIELDS:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def meaningful_body_changed(old_text: str | None, new_text: str) -> bool:
    return old_text is None or meaningful_body(old_text) != meaningful_body(new_text)


def update_history_section(text: str) -> list[str]:
    """Stripped, non-empty lines of the Update History section, heading excluded."""
    lines: list[str] = []
    in_history = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_history = _is_update_history_heading(stripped)
            continue
        if in_history and stripped:
            lines.append(stripped)
    return lines


def new_history_lines(old_text: str | None, new_text: str) -> list[str]:
    """Update History lines present in new_text but absent from old_text."""
    old_lines = set(update_history_section(old_text)) if old_text is not None else set()
    return [line for line in update_history_section(new_text) if line not in old_lines]


def has_no_impact_marker(lines: Iterable[str]) -> bool:
    """True when any line carries the explicit reviewed-no-impact marker.

    The marker is the in-band attestation that a document was reviewed against
    the changed sources and intentionally left unchanged: an Update History
    entry containing ``No content impact:`` (file sidecars) or
    ``No route impact:`` (route overviews).
    """
    return any(NO_IMPACT_MARKER_PATTERN.search(line) for line in lines)
