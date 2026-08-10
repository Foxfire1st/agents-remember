"""Enforce structural alignment inside the repository entity catalog."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from agents_remember.kernel.coordination_context_resolver import clean_scalar
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import rel
from agents_remember.memory_quality.integrity.onboarding_drift_check.entities import (
    parse_entity_fingerprint_rows,
    parse_entity_inventory_names,
    split_table_row,
)
from agents_remember.memory_quality.style.finding import QualityFinding, check_result

CHECK_NAME = "style.document_shape.entity_catalog_alignment"
CATALOG_NAME = "entities.md"
INVENTORY_HEADING = "## Entity Inventory"
FINGERPRINT_HEADING = "## Entity Fingerprints"


def _heading_line(lines: list[str], heading: str) -> int:
    return next(
        (line_number for line_number, line in enumerate(lines, start=1) if line.strip() == heading),
        1,
    )


def _entity_line(lines: list[str], heading: str, entity: str) -> int:
    in_section = False
    for line_number, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            in_section = stripped == heading
            continue
        if not in_section:
            continue
        if heading == INVENTORY_HEADING and stripped.startswith("### "):
            candidate = clean_scalar(stripped.removeprefix("###").strip()).strip("`")
        elif heading == FINGERPRINT_HEADING and stripped.startswith("|"):
            cells = split_table_row(stripped)
            candidate = clean_scalar(cells[0]).strip("`") if cells else ""
        else:
            continue
        if candidate == entity:
            return line_number
    return _heading_line(lines, heading)


def _finding(
    path: Path,
    onboarding_root: Path,
    *,
    line: int,
    code: str,
    message: str,
) -> QualityFinding:
    return QualityFinding(
        check=CHECK_NAME,
        path=rel(path, onboarding_root),
        line=line,
        severity="warning",
        code=code,
        message=message,
    )


def check_onboarding_root(onboarding_root: Path) -> dict[str, Any]:
    catalog = onboarding_root / CATALOG_NAME
    if not catalog.is_file():
        return check_result(check=CHECK_NAME, files_checked=0, findings=[])

    lines = catalog.read_text(encoding="utf-8").splitlines()
    headings = {line.strip() for line in lines if line.strip().startswith("## ")}
    findings: list[QualityFinding] = []
    for heading, code, label in (
        (INVENTORY_HEADING, "entity_inventory_section_missing", "inventory"),
        (FINGERPRINT_HEADING, "entity_fingerprint_section_missing", "fingerprint"),
    ):
        if heading not in headings:
            findings.append(
                _finding(
                    catalog,
                    onboarding_root,
                    line=1,
                    code=code,
                    message=f"Entity catalog is missing its {label} section ({heading}).",
                )
            )

    inventory = set(parse_entity_inventory_names(catalog))
    fingerprints = parse_entity_fingerprint_rows(catalog)
    fingerprint_counts = Counter(row.entity for row in fingerprints)
    fingerprint_names = set(fingerprint_counts)

    for entity in sorted(fingerprint_names - inventory):
        findings.append(
            _finding(
                catalog,
                onboarding_root,
                line=_entity_line(lines, FINGERPRINT_HEADING, entity),
                code="entity_fingerprint_without_inventory",
                message=f"Entity fingerprint '{entity}' has no matching inventory entry.",
            )
        )
    for entity in sorted(inventory - fingerprint_names):
        findings.append(
            _finding(
                catalog,
                onboarding_root,
                line=_entity_line(lines, INVENTORY_HEADING, entity),
                code="entity_inventory_without_fingerprint",
                message=f"Entity inventory entry '{entity}' has no matching fingerprint row.",
            )
        )
    for entity, count in sorted(fingerprint_counts.items()):
        if count > 1:
            findings.append(
                _finding(
                    catalog,
                    onboarding_root,
                    line=_entity_line(lines, FINGERPRINT_HEADING, entity),
                    code="entity_fingerprint_duplicate",
                    message=f"Entity fingerprint '{entity}' appears {count} times; expected one row.",
                )
            )

    return check_result(check=CHECK_NAME, files_checked=1, findings=findings)
