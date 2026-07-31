"""Repo entity-catalog fingerprint classification for drift detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.coordination_context_resolver import (
    StorageSettings,
    clean_scalar,
    normalize_rel_path,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    parse_table_metadata,
    rel,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    compute_git_blob_set_fingerprint,
    entity_local_change_notes,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    GIT_BLOB_SET_ALGORITHM,
    DriftRow,
    EntityFingerprint,
)


@dataclass(frozen=True)
class EntityCatalog:
    """The repo entity catalog being classified.

    Its onboarding file, the onboarding root every emitted row is relative to, the repository
    it documents, the storage settings that stamp each row, and the ``lastUpdated`` date the
    rows inherit. All five are read out of one document before any row is emitted, and every
    row builder needs all five -- so the catalog travels as the document it is.
    """

    onboarding_file: Path
    onboarding_root: Path
    repository: str
    settings: StorageSettings
    last_updated: str


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


def classify_entity_fingerprint(
    catalog: EntityCatalog,
    repo_root: Path,
    row: EntityFingerprint,
) -> DriftRow:
    onboarding_file = catalog.onboarding_file
    settings = catalog.settings
    repository = catalog.repository
    last_updated = catalog.last_updated
    onboarding_ref = rel(onboarding_file, catalog.onboarding_root)
    source_ref = f"entity:{row.entity}"

    def _early_classification() -> DriftRow | None:
        """Classify structurally-invalid rows before the HEAD comparison."""
        if row.algorithm != GIT_BLOB_SET_ALGORITHM:
            return DriftRow(
                onboarding_file=onboarding_ref,
                source_file=source_ref,
                repository=repository,
                storage_mode=settings.mode,
                last_verified_hash=row.fingerprint,
                last_verified_date=last_updated,
                classification="unsupported",
                trust="low",
                affected_sections=f"entity catalog; {row.entity}",
                note=f"Unsupported entity fingerprint algorithm '{row.algorithm}'.",
            )
        if not row.fingerprint or not row.evidence_paths:
            return DriftRow(
                onboarding_file=onboarding_ref,
                source_file=source_ref,
                repository=repository,
                storage_mode=settings.mode,
                last_verified_hash=row.fingerprint,
                last_verified_date=last_updated,
                classification="missing verification",
                trust="medium",
                affected_sections=f"entity catalog; {row.entity}",
                note="Entity fingerprint row is missing a fingerprint value or evidence paths.",
            )
        missing_paths = [
            source_path
            for source_path in row.evidence_paths
            if not (repo_root / source_path).exists()
        ]
        if missing_paths:
            return DriftRow(
                onboarding_file=onboarding_ref,
                source_file=source_ref,
                repository=repository,
                storage_mode=settings.mode,
                last_verified_hash=row.fingerprint,
                last_verified_date=last_updated,
                classification="drifted",
                trust="low",
                affected_sections=f"entity catalog; {row.entity}; source evidence",
                note=(
                    f"Entity evidence path missing: {', '.join(missing_paths)}. "
                    "Check whether the entity was removed, renamed, or moved before deleting or replacing the fingerprint evidence."
                ),
            )
        return None

    early = _early_classification()
    if early is not None:
        return early
    try:
        current = compute_git_blob_set_fingerprint(repo_root, row.evidence_paths)
    except RuntimeError as error:
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_ref,
            repository=repository,
            storage_mode=settings.mode,
            last_verified_hash=row.fingerprint,
            last_verified_date=last_updated,
            classification="drifted",
            trust="low",
            affected_sections=f"entity catalog; {row.entity}; source evidence",
            note=f"Unable to compute entity fingerprint: {error}",
        )

    local_notes = entity_local_change_notes(repo_root, row.evidence_paths)
    if current == row.fingerprint and not local_notes:
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_ref,
            repository=repository,
            storage_mode=settings.mode,
            last_verified_hash=row.fingerprint,
            last_verified_date=last_updated,
            classification="up to date",
            trust="high",
            affected_sections="none",
            note="Entity evidence fingerprint matches current HEAD.",
        )
    if current == row.fingerprint:
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_ref,
            repository=repository,
            storage_mode=settings.mode,
            last_verified_hash=row.fingerprint,
            last_verified_date=last_updated,
            classification="drifted",
            trust="medium",
            affected_sections=f"entity catalog; {row.entity}; source evidence",
            note="; ".join(local_notes),
        )
    note = "Entity evidence fingerprint changed since the catalog was refreshed."
    if local_notes:
        note = f"{note} Local changes also exist: {'; '.join(local_notes)}"
    return DriftRow(
        onboarding_file=onboarding_ref,
        source_file=source_ref,
        repository=repository,
        storage_mode=settings.mode,
        last_verified_hash=row.fingerprint,
        last_verified_date=last_updated,
        classification="drifted",
        trust="medium",
        affected_sections=f"entity catalog; {row.entity}; source evidence",
        note=note,
    )


def missing_entity_fingerprint_row(catalog: EntityCatalog, entity: str, note: str) -> DriftRow:
    return DriftRow(
        onboarding_file=rel(catalog.onboarding_file, catalog.onboarding_root),
        source_file=f"entity:{entity}",
        repository=catalog.repository,
        storage_mode=catalog.settings.mode,
        last_verified_hash="",
        last_verified_date=catalog.last_updated,
        classification="missing verification",
        trust="medium",
        affected_sections=f"entity catalog; {entity}; verification",
        note=note,
    )


def orphaned_entity_fingerprint_row(catalog: EntityCatalog, row: EntityFingerprint) -> DriftRow:
    return DriftRow(
        onboarding_file=rel(catalog.onboarding_file, catalog.onboarding_root),
        source_file=f"entity:{row.entity}",
        repository=catalog.repository,
        storage_mode=catalog.settings.mode,
        last_verified_hash=row.fingerprint,
        last_verified_date=catalog.last_updated,
        classification="orphaned",
        trust="low",
        affected_sections=f"entity catalog; {row.entity}; verification",
        note="Entity fingerprint row has no matching inventory entry. Check whether the entity was removed, renamed, or moved before deleting the row.",
    )


def classify_entity_catalog(
    onboarding_file: Path, repo_root: Path, onboarding_root: Path, settings: StorageSettings
) -> list[DriftRow]:
    metadata = parse_table_metadata(onboarding_file)
    repository = metadata.get("repository", repo_root.name)
    last_updated = metadata.get("lastUpdated", "")
    catalog = EntityCatalog(
        onboarding_file=onboarding_file,
        onboarding_root=onboarding_root,
        repository=repository,
        settings=settings,
        last_updated=last_updated,
    )
    inventory_entities = parse_entity_inventory_names(onboarding_file)
    rows = parse_entity_fingerprint_rows(onboarding_file)
    if not rows:
        if inventory_entities:
            return [
                missing_entity_fingerprint_row(
                    catalog,
                    entity,
                    "Repo entity catalog has no parseable Entity Fingerprints table for this inventory entry.",
                )
                for entity in inventory_entities
            ]
        return [
            DriftRow(
                onboarding_file=rel(onboarding_file, onboarding_root),
                source_file="entity-catalog",
                repository=repository,
                storage_mode=settings.mode,
                last_verified_hash="",
                last_verified_date=last_updated,
                classification="missing verification",
                trust="medium",
                affected_sections="entity catalog; verification",
                note="Repo entity catalog has no parseable Entity Fingerprints table.",
            )
        ]
    if not inventory_entities:
        return [
            DriftRow(
                onboarding_file=rel(onboarding_file, onboarding_root),
                source_file="entity-catalog",
                repository=repository,
                storage_mode=settings.mode,
                last_verified_hash="",
                last_verified_date=last_updated,
                classification="missing verification",
                trust="medium",
                affected_sections="entity catalog; inventory; verification",
                note="Repo entity catalog has fingerprint rows but no parseable Entity Inventory section.",
            )
        ]
    fingerprint_entities = {row.entity for row in rows}
    rows_by_inventory = [
        classify_entity_fingerprint(catalog, repo_root, row)
        if row.entity in inventory_entities
        else orphaned_entity_fingerprint_row(catalog, row)
        for row in rows
    ]
    missing_inventory_rows = [
        missing_entity_fingerprint_row(
            catalog,
            entity,
            "Entity inventory entry has no matching fingerprint row. Add a git-blob-set-v1 row with curated evidence paths before treating it as verified.",
        )
        for entity in inventory_entities
        if entity not in fingerprint_entities
    ]
    return [
        *rows_by_inventory,
        *missing_inventory_rows,
    ]
