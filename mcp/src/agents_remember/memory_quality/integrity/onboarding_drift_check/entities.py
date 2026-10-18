"""Repo entity-catalog fingerprint classification for drift detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.coordination_context_resolver import (
    clean_scalar,
    normalize_rel_path,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    EntityFingerprint,
)


def split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def split_evidence_paths(value: str) -> list[str]:
    normalized = re.sub(r"<br\s*/?>", ";", value, flags=re.IGNORECASE)
    paths: list[str] = []
    for raw_path in normalized.split(";"):
        source_path = clean_scalar(raw_path).strip().strip("`")
        if not source_path or source_path.lower() in {"n/a", "none"}:
            continue
        paths.append(normalize_rel_path(source_path))
    return paths


def _is_table_separator_row(cells: list[str]) -> bool:
    return all(set(cell) <= {"-", ":"} for cell in cells)


def _normalized_header_cells(cells: list[str]) -> list[str]:
    return [re.sub(r"\s+", "", cell).lower() for cell in cells]


def _entity_fingerprint_from_row(headers: list[str], cells: list[str]) -> EntityFingerprint | None:
    row = {
        header: cells[index] if index < len(cells) else "" for index, header in enumerate(headers)
    }
    entity = clean_scalar(row.get("entity", "")).strip()
    if not entity:
        return None
    return EntityFingerprint(
        entity=entity,
        algorithm=clean_scalar(row.get("algorithm", "")).strip("`"),
        fingerprint=clean_scalar(row.get("fingerprint", "")).strip("`"),
        evidence_paths=split_evidence_paths(row.get("evidencepaths", "")),
    )


def parse_entity_fingerprint_rows(path: Path) -> list[EntityFingerprint]:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_section = False
    headers: list[str] = []
    rows: list[EntityFingerprint] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            in_section = stripped == "## Entity Fingerprints"
            headers = []
            continue
        if not in_section:
            continue
        if not stripped.startswith("|"):
            if headers:
                break
            continue
        cells = split_table_row(stripped)
        if _is_table_separator_row(cells):
            continue
        normalized_cells = _normalized_header_cells(cells)
        if {"entity", "algorithm", "fingerprint", "evidencepaths"}.issubset(set(normalized_cells)):
            headers = normalized_cells
            continue
        if not headers:
            continue
        fingerprint = _entity_fingerprint_from_row(headers, cells)
        if fingerprint is not None:
            rows.append(fingerprint)
    return rows


def parse_entity_inventory_names(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_section = False
    names: list[str] = []
    seen: set[str] = set()
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            in_section = stripped == "## Entity Inventory"
            continue
        if not in_section or not stripped.startswith("### "):
            continue
        name = clean_scalar(stripped.removeprefix("###").strip()).strip("`")
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


@dataclass(frozen=True)
class CensusEntityRow:
    """Exact inventory key, curated evidence, and row bytes for structural comparison."""

    key: str
    evidence_paths: tuple[str, ...]
    inventory_text: str
    fingerprint_text: str


def _census_entity_key(cell: str) -> str:
    key = cell.strip()
    if len(key) >= 2 and key.startswith("`") and key.endswith("`"):
        key = key[1:-1]
    if not key or "\x00" in key:
        raise ValueError("entity census requires a nonempty decoded Entity key")
    key.encode("utf-8", errors="strict")
    return key


def _census_evidence_paths(fields: dict[str, str]) -> tuple[str, ...]:
    if "evidencepaths" not in fields:
        raise ValueError("entity fingerprint table lacks evidencePaths")
    cells = re.sub(r"<br\s*/?>", ";", fields["evidencepaths"], flags=re.IGNORECASE).split(";")
    evidence = []
    for cell in cells:
        value = cell.strip()
        if not value or value.lower().strip("`") in {"n/a", "none"}:
            continue
        evidence.append(_census_entity_key(value))
    return tuple(evidence)


@dataclass
class _EntityCensusParser:
    inventory: dict[str, list[str]]
    fingerprints: dict[str, tuple[str, tuple[str, ...]]]
    section: str = ""
    headers: tuple[str, ...] = ()
    current: str | None = None

    def inventory_entry(self, key: str, raw: str) -> None:
        if key in self.inventory:
            raise ValueError(f"duplicate entity inventory key: {key}")
        self.inventory[key] = [raw]

    def consume(self, raw: str) -> None:
        line = raw.strip()
        if line.startswith("## "):
            self.section = line
            self.headers = ()
            self.current = None
            return
        if self.section == "## Entity Inventory":
            if line.startswith("### "):
                self.current = _census_entity_key(line.removeprefix("### "))
                self.inventory_entry(self.current, raw)
                return
            if self.current is not None:
                self.inventory[self.current].append(raw)
                return
        if self.section in {"## Entity Inventory", "## Entity Fingerprints"} and line.startswith(
            "|"
        ):
            self.table_row(line, raw)

    def table_row(self, line: str, raw: str) -> None:
        cells = split_table_row(line)
        if _is_table_separator_row(cells):
            return
        normalized = _normalized_header_cells(cells)
        if "entity" in normalized:
            if len(normalized) != len(set(normalized)):
                raise ValueError("duplicate entity catalog table header")
            self.headers = tuple(normalized)
            return
        if not self.headers:
            raise ValueError("entity catalog row precedes its Entity header")
        if len(cells) != len(self.headers):
            raise ValueError("entity catalog row does not match its header width")
        fields = dict(zip(self.headers, cells, strict=True))
        key = _census_entity_key(fields["entity"])
        if self.section == "## Entity Inventory":
            self.inventory_entry(key, raw)
            return
        if key in self.fingerprints:
            raise ValueError(f"duplicate entity fingerprint key: {key}")
        self.fingerprints[key] = (raw, _census_evidence_paths(fields))

    def rows(self) -> tuple[CensusEntityRow, ...]:
        if set(self.inventory) != set(self.fingerprints):
            raise ValueError("entity inventory and fingerprint keys must match exactly once")
        return tuple(
            CensusEntityRow(
                key,
                self.fingerprints[key][1],
                "".join(self.inventory[key]),
                self.fingerprints[key][0],
            )
            for key in sorted(self.inventory, key=lambda item: item.encode("utf-8"))
        )


def parse_entity_census_rows(text: str) -> tuple[CensusEntityRow, ...]:
    """Parse exact keys without the drift reader's historical deduplication.

    Both canonical inventory forms (named subsections and Entity tables) retain
    duplicate keys so ambiguity refuses instead of silently selecting a row.
    """
    parser = _EntityCensusParser({}, {})
    for raw in text.splitlines(keepends=True):
        parser.consume(raw)
    return parser.rows()
