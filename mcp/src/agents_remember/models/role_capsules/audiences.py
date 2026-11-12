"""Select explicitly marked stage passages without rewriting their sentences."""

from __future__ import annotations

import re
from dataclasses import replace

from agents_remember.errors import CapsuleCompilationError
from agents_remember.models.role_capsules.types import CapsuleInstructionBlock

_MARKER = re.compile(r"<!-- ar:roles ([a-z,\-]+) -->\s*$")
_MARKER_SHAPED = re.compile(r"^\s*<!--\s*ar:roles\b", re.IGNORECASE)


def select_operation_audience(
    block: CapsuleInstructionBlock,
    audience: str,
    roles: tuple[str, ...],
    role: str | None,
    *,
    source_text: str,
) -> CapsuleInstructionBlock:
    """Validate the whole source, then carry only common and addressed passages."""
    if audience == "all":
        return block
    if audience != "sections":
        _refuse(block.source_path, 1, f"unknown stage audience mode {audience!r}")
    source_line_offset = source_text.count("\n") - block.content.count("\n")
    selected: list[str] = []
    recipients: tuple[str, ...] = ()
    for number, line in enumerate(block.content.splitlines(keepends=True), source_line_offset + 1):
        marker = _MARKER.fullmatch(line)
        if marker is not None:
            recipients = tuple(marker[1].split(","))
            _require_audience(recipients, roles, block.source_path, number)
        elif _MARKER_SHAPED.match(line):
            _refuse(block.source_path, number, "malformed stage audience marking")
        elif not recipients and line.strip():
            _refuse(block.source_path, number, "stage text has no audience marking")
        elif "all" in recipients or role in recipients:
            selected.append(line)
    if not recipients:
        _refuse(block.source_path, source_line_offset + 1, "stage text has no audience marking")
    return replace(block, content="".join(selected))


def _require_audience(
    recipients: tuple[str, ...], roles: tuple[str, ...], path: str, line: int
) -> None:
    if recipients == ("all",):
        return
    if len(set(recipients)) != len(recipients) or any(role not in roles for role in recipients):
        _refuse(path, line, f"stage audience {','.join(recipients)!r} is not among {roles!r}")


def _refuse(path: str, line: int, detail: str) -> None:
    raise CapsuleCompilationError(
        status="operation-audience-invalid",
        detail=f"{path}:{line}: {detail}",
        next_action=(
            "Mark every stage passage with '<!-- ar:roles all -->' or the comma-separated "
            "roles the operation admits; declare audience='sections' in the composition manifest."
        ),
    )
