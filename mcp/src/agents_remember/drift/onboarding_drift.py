#!/usr/bin/env python3
"""Check Agents Remember file-level onboarding drift.

Requires Python 3.9+ and git. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


from agents_remember.kernel.coordination_context_resolver import (  # noqa: E402
    StorageSettings,
    clean_scalar,
    normalize_rel_path,
    resolve_coordination_context,
    resolve_storage_for_source,
    sidecar_storage_label,
)


CLASSIFICATIONS = (
    "up to date",
    "drifted",
    "missing verification",
    "missing",
    "orphaned",
    "disabled",
    "unsupported",
)
ACTIONABLE_CLASSIFICATIONS = {"drifted", "missing verification", "missing", "orphaned", "unsupported"}
INLINE_START_MARKER = "@ar-onboarding"
INLINE_END_MARKER = "@ar-onboarding-end"
GIT_BLOB_SET_ALGORITHM = "git-blob-set-v1"
SIDECAR_DOC_TYPES = {
    "file-level-onboarding",
    "repo-overview",
    "route-local-overview",
    "repo-entity-catalog",
}
COMMON_BLOCK_DELIMITERS = {
    "/*": "*/",
    "<!--": "-->",
    '"""': '"""',
    "'''": "'''",
    "=begin": "=end",
    "{-": "-}",
    "(*": "*)",
}


@dataclass
class DriftRow:
    onboarding_file: str
    source_file: str
    repository: str
    storage_mode: str
    last_verified_hash: str
    last_verified_date: str
    classification: str
    trust: str
    affected_sections: str
    note: str


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root.as_posix()}", *args],
        cwd=repo_root,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def sanitize_report_token(token: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", token.strip())
    normalized = normalized.strip(".-_")
    return normalized or "unknown"


def current_branch_name(repo_root: Path) -> str:
    branch = run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch.returncode != 0:
        return "unknown-branch"
    return branch.stdout.strip() or "unknown-branch"


def default_report_filename(repo_root: Path) -> str:
    repo_name = sanitize_report_token(repo_root.name)
    branch_name = sanitize_report_token(current_branch_name(repo_root))
    return f"{repo_name}_{branch_name}_drift-report.md"


def default_report_dir(temp_root: Path, repo_root: Path) -> Path:
    return temp_root / "drift-reports" / sanitize_report_token(repo_root.name)


def default_report_path(temp_root: Path, repo_root: Path) -> Path:
    return default_report_dir(temp_root, repo_root) / default_report_filename(repo_root)


def local_change_note(repo_root: Path, source_file: str) -> str:
    states: list[str] = []
    unstaged = run_git(repo_root, ["diff", "--quiet", "--", source_file])
    if unstaged.returncode == 1:
        states.append("unstaged")
    elif unstaged.returncode != 0:
        return f"Unable to inspect local unstaged changes: {unstaged.stderr.strip() or 'unknown git error'}."

    staged = run_git(repo_root, ["diff", "--cached", "--quiet", "--", source_file])
    if staged.returncode == 1:
        states.append("staged")
    elif staged.returncode != 0:
        return f"Unable to inspect local staged changes: {staged.stderr.strip() or 'unknown git error'}."

    if not states:
        return ""
    return f"Source has local {' and '.join(states)} changes not represented in HEAD."


def list_repo_sources(repo_root: Path) -> list[str]:
    result = run_git(repo_root, ["ls-files", "-z"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return [normalize_rel_path(value) for value in result.stdout.split("\0") if value]


def mirror_onboarding_path(onboarding_root: Path, source_file: str) -> Path:
    return onboarding_root / f"{normalize_rel_path(source_file)}.md"


def parse_table_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        key, value = cells[0], cells[1]
        if key in {"Field", "---", "----------------------"}:
            continue
        if value.startswith("`") and value.endswith("`"):
            value = value[1:-1]
        metadata[key] = value
    return metadata


def is_file_level_onboarding(path: Path) -> bool:
    try:
        metadata = parse_table_metadata(path)
    except UnicodeDecodeError:
        return False
    return metadata.get("doc_type") == "file-level-onboarding"


def is_supported_sidecar_onboarding(path: Path) -> bool:
    try:
        metadata = parse_table_metadata(path)
    except UnicodeDecodeError:
        return False
    return metadata.get("doc_type") in SIDECAR_DOC_TYPES


def discover_onboarding_files(onboarding_root: Path) -> list[Path]:
    return sorted(
        path
        for path in onboarding_root.rglob("*.md")
        if path.is_file() and is_supported_sidecar_onboarding(path)
    )


def discover_inline_onboarding_sources(repo_root: Path, settings: StorageSettings) -> list[str]:
    inline_sources: list[str] = []
    for source_file in list_repo_sources(repo_root):
        if resolve_storage_for_source(source_file, settings, repo_root.name) != "inline":
            continue

        source_path = repo_root / source_file
        if not source_path.exists():
            continue

        try:
            source_text = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if extract_inline_onboarding_block(source_text) is not None:
            inline_sources.append(source_file)

    return sorted(inline_sources)


def classify_external_onboarding(onboarding_file: Path, repo_root: Path) -> DriftRow:
    metadata = parse_table_metadata(onboarding_file)
    repository = metadata.get("repository", "")
    source_file = normalize_rel_path(metadata.get("path", ""))
    last_hash = metadata.get("lastVerifiedCommitHash", "")
    last_date = metadata.get("lastVerifiedCommitDate", "")

    if not source_file or not last_hash:
        return DriftRow(
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="missing verification",
            trust="medium",
            affected_sections="metadata; verification",
            note="Missing source path or lastVerifiedCommitHash.",
        )

    source_path = repo_root / source_file
    if not source_path.exists():
        return DriftRow(
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="orphaned",
            trust="low",
            affected_sections="all; source missing",
            note="Source file no longer exists.",
        )

    rev = f"{last_hash}^{{commit}}"
    exists = run_git(repo_root, ["cat-file", "-e", rev])
    if exists.returncode != 0:
        return DriftRow(
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="drifted",
            trust="medium",
            affected_sections="logic; invariants; metadata",
            note="Recorded verification commit is not available in git history.",
        )

    diff = run_git(repo_root, ["diff", "--quiet", last_hash, "HEAD", "--", source_file])
    if diff.returncode == 0:
        local_note = local_change_note(repo_root, source_file)
        if local_note:
            return DriftRow(
                onboarding_file=onboarding_file.as_posix(),
                source_file=source_file,
                repository=repository,
                storage_mode="external",
                last_verified_hash=last_hash,
                last_verified_date=last_date,
                classification="drifted",
                trust="medium",
                affected_sections="logic; invariants; conventions; docs references",
                note=local_note,
            )
        return DriftRow(
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="up to date",
            trust="high",
            affected_sections="none",
            note="No source diff since recorded verification commit.",
        )
    if diff.returncode == 1:
        return DriftRow(
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=repository,
            storage_mode="external",
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="drifted",
            trust="medium",
            affected_sections="logic; invariants; conventions; docs references",
            note="Source changed since recorded verification commit.",
        )

    return DriftRow(
        onboarding_file=onboarding_file.as_posix(),
        source_file=source_file,
        repository=repository,
        storage_mode="external",
        last_verified_hash=last_hash,
        last_verified_date=last_date,
        classification="drifted",
        trust="medium",
        affected_sections="logic; invariants; metadata",
        note=f"git diff failed: {diff.stderr.strip() or 'unknown git error'}",
    )


def normalize_overview_route(source_route: str) -> str:
    source_route = clean_scalar(source_route).strip()
    if source_route in {"", ".", "`<repo-root>`", "<repo-root>", repo_root_placeholder()}:
        return "."
    return normalize_rel_path(source_route.strip("`"))


def repo_root_placeholder() -> str:
    return "<repo-root>"


def local_route_change_note(repo_root: Path, source_route: str) -> str:
    return local_change_note(repo_root, "." if source_route in {"", repo_root_placeholder()} else source_route)


def classify_overview_onboarding(onboarding_file: Path, repo_root: Path, onboarding_root: Path, settings: StorageSettings) -> DriftRow:
    metadata = parse_table_metadata(onboarding_file)
    doc_type = metadata.get("doc_type", "")
    repository = metadata.get("repository", repo_root.name)
    source_route = normalize_overview_route(metadata.get("sourceRoute", "."))
    last_hash = metadata.get("lastVerifiedCommitHash", "")
    last_date = metadata.get("lastVerifiedCommitDate", "")
    onboarding_ref = rel(onboarding_file, onboarding_root)

    if not last_hash or not last_date:
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_route,
            repository=repository,
            storage_mode=settings.mode,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="missing verification",
            trust="medium",
            affected_sections="metadata; verification",
            note=f"{doc_type or 'overview'} is missing lastVerifiedCommitHash or lastVerifiedCommitDate.",
        )

    if source_route != "." and not (repo_root / source_route).exists():
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_route,
            repository=repository,
            storage_mode=settings.mode,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="orphaned",
            trust="low",
            affected_sections="all; source route missing",
            note="Overview sourceRoute no longer exists.",
        )

    exists = run_git(repo_root, ["cat-file", "-e", f"{last_hash}^{{commit}}"])
    if exists.returncode != 0:
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_route,
            repository=repository,
            storage_mode=settings.mode,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="drifted",
            trust="medium",
            affected_sections="overview; metadata",
            note="Recorded overview verification commit is not available in git history.",
        )

    diff = run_git(repo_root, ["diff", "--quiet", last_hash, "HEAD", "--", source_route])
    if diff.returncode == 0:
        local_note = local_route_change_note(repo_root, source_route)
        if local_note:
            return DriftRow(
                onboarding_file=onboarding_ref,
                source_file=source_route,
                repository=repository,
                storage_mode=settings.mode,
                last_verified_hash=last_hash,
                last_verified_date=last_date,
                classification="drifted",
                trust="medium",
                affected_sections="overview; route summary; invariants",
                note=local_note,
            )
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_route,
            repository=repository,
            storage_mode=settings.mode,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="up to date",
            trust="high",
            affected_sections="none",
            note="No source-route diff since recorded overview verification commit.",
        )
    if diff.returncode == 1:
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_route,
            repository=repository,
            storage_mode=settings.mode,
            last_verified_hash=last_hash,
            last_verified_date=last_date,
            classification="drifted",
            trust="medium",
            affected_sections="overview; route summary; invariants",
            note="Source route changed since recorded overview verification commit.",
        )
    return DriftRow(
        onboarding_file=onboarding_ref,
        source_file=source_route,
        repository=repository,
        storage_mode=settings.mode,
        last_verified_hash=last_hash,
        last_verified_date=last_date,
        classification="drifted",
        trust="medium",
        affected_sections="overview; metadata",
        note=f"git diff failed: {diff.stderr.strip() or 'unknown git error'}",
    )


@dataclass
class EntityFingerprint:
    entity: str
    algorithm: str
    fingerprint: str
    evidence_paths: list[str]


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
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        normalized_cells = [re.sub(r"\s+", "", cell).lower() for cell in cells]
        if {"entity", "algorithm", "fingerprint", "evidencepaths"}.issubset(set(normalized_cells)):
            headers = normalized_cells
            continue
        if not headers:
            continue
        row = {header: cells[index] if index < len(cells) else "" for index, header in enumerate(headers)}
        entity = clean_scalar(row.get("entity", "")).strip()
        algorithm = clean_scalar(row.get("algorithm", "")).strip("`")
        fingerprint = clean_scalar(row.get("fingerprint", "")).strip("`")
        evidence_paths = split_evidence_paths(row.get("evidencepaths", ""))
        if entity:
            rows.append(
                EntityFingerprint(
                    entity=entity,
                    algorithm=algorithm,
                    fingerprint=fingerprint,
                    evidence_paths=evidence_paths,
                )
            )
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


def git_stdout(repo_root: Path, args: list[str]) -> str:
    result = run_git(repo_root, args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def git_blob_hash(repo_root: Path, source_path: str) -> str:
    return git_stdout(repo_root, ["rev-parse", f"HEAD:{source_path}"])


def compute_git_blob_set_fingerprint(repo_root: Path, evidence_paths: list[str]) -> str:
    lines: list[str] = []
    for source_path in sorted(evidence_paths):
        blob_hash = git_blob_hash(repo_root, source_path)
        lines.append(f"{source_path}\0{blob_hash}\n")
    digest = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def entity_local_change_notes(repo_root: Path, evidence_paths: list[str]) -> list[str]:
    notes: list[str] = []
    for source_path in evidence_paths:
        note = local_change_note(repo_root, source_path)
        if note:
            notes.append(f"{source_path}: {note}")
    return notes


def classify_entity_fingerprint(
    onboarding_file: Path,
    onboarding_root: Path,
    repo_root: Path,
    settings: StorageSettings,
    repository: str,
    last_updated: str,
    row: EntityFingerprint,
) -> DriftRow:
    onboarding_ref = rel(onboarding_file, onboarding_root)
    source_ref = f"entity:{row.entity}"
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
    missing_paths = [source_path for source_path in row.evidence_paths if not (repo_root / source_path).exists()]
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


def missing_entity_fingerprint_row(
    onboarding_file: Path,
    onboarding_root: Path,
    repository: str,
    settings: StorageSettings,
    last_updated: str,
    entity: str,
    note: str,
) -> DriftRow:
    return DriftRow(
        onboarding_file=rel(onboarding_file, onboarding_root),
        source_file=f"entity:{entity}",
        repository=repository,
        storage_mode=settings.mode,
        last_verified_hash="",
        last_verified_date=last_updated,
        classification="missing verification",
        trust="medium",
        affected_sections=f"entity catalog; {entity}; verification",
        note=note,
    )


def orphaned_entity_fingerprint_row(
    onboarding_file: Path,
    onboarding_root: Path,
    repository: str,
    settings: StorageSettings,
    last_updated: str,
    row: EntityFingerprint,
) -> DriftRow:
    return DriftRow(
        onboarding_file=rel(onboarding_file, onboarding_root),
        source_file=f"entity:{row.entity}",
        repository=repository,
        storage_mode=settings.mode,
        last_verified_hash=row.fingerprint,
        last_verified_date=last_updated,
        classification="orphaned",
        trust="low",
        affected_sections=f"entity catalog; {row.entity}; verification",
        note="Entity fingerprint row has no matching inventory entry. Check whether the entity was removed, renamed, or moved before deleting the row.",
    )


def classify_entity_catalog(onboarding_file: Path, repo_root: Path, onboarding_root: Path, settings: StorageSettings) -> list[DriftRow]:
    metadata = parse_table_metadata(onboarding_file)
    repository = metadata.get("repository", repo_root.name)
    last_updated = metadata.get("lastUpdated", "")
    inventory_entities = parse_entity_inventory_names(onboarding_file)
    rows = parse_entity_fingerprint_rows(onboarding_file)
    if not rows:
        if inventory_entities:
            return [
                missing_entity_fingerprint_row(
                    onboarding_file,
                    onboarding_root,
                    repository,
                    settings,
                    last_updated,
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
        classify_entity_fingerprint(onboarding_file, onboarding_root, repo_root, settings, repository, last_updated, row)
        if row.entity in inventory_entities
        else orphaned_entity_fingerprint_row(onboarding_file, onboarding_root, repository, settings, last_updated, row)
        for row in rows
    ]
    missing_inventory_rows = [
        missing_entity_fingerprint_row(
            onboarding_file,
            onboarding_root,
            repository,
            settings,
            last_updated,
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


def classify_external_source(source_file: str, repo_root: Path, onboarding_root: Path) -> DriftRow:
    onboarding_file = mirror_onboarding_path(onboarding_root, source_file)
    onboarding_ref = rel(onboarding_file, onboarding_root)
    if not onboarding_file.exists():
        return DriftRow(
            onboarding_file=onboarding_ref,
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="external",
            last_verified_hash="",
            last_verified_date="",
            classification="missing",
            trust="low",
            affected_sections="all; onboarding missing",
            note="Mirrored onboarding file is missing for this sidecar-managed source.",
        )
    row = classify_external_onboarding(onboarding_file, repo_root)
    row.onboarding_file = onboarding_ref
    return row


def classify_sidecar_onboarding(
    onboarding_file: Path,
    repo_root: Path,
    onboarding_root: Path,
    settings: StorageSettings,
) -> DriftRow:
    rows = classify_sidecar_onboarding_units(onboarding_file, repo_root, onboarding_root, settings)
    if len(rows) == 1:
        return rows[0]
    actionable = [row for row in rows if row.classification in ACTIONABLE_CLASSIFICATIONS]
    metadata = parse_table_metadata(onboarding_file)
    return DriftRow(
        onboarding_file=rel(onboarding_file, onboarding_root),
        source_file="entity-catalog",
        repository=metadata.get("repository", repo_root.name),
        storage_mode=settings.mode,
        last_verified_hash="",
        last_verified_date=metadata.get("lastUpdated", ""),
        classification="drifted" if actionable else "up to date",
        trust="medium" if actionable else "high",
        affected_sections="entity catalog",
        note=f"{len(actionable)} actionable entity fingerprint rows." if actionable else "All entity fingerprint rows are up to date.",
    )


def classify_sidecar_onboarding_units(
    onboarding_file: Path,
    repo_root: Path,
    onboarding_root: Path,
    settings: StorageSettings,
) -> list[DriftRow]:
    metadata = parse_table_metadata(onboarding_file)
    doc_type = metadata.get("doc_type", "")
    if doc_type in {"repo-overview", "route-local-overview"}:
        return [classify_overview_onboarding(onboarding_file, repo_root, onboarding_root, settings)]
    if doc_type == "repo-entity-catalog":
        return classify_entity_catalog(onboarding_file, repo_root, onboarding_root, settings)

    source_file = normalize_rel_path(metadata.get("path", ""))
    storage_mode = resolve_storage_for_source(source_file, settings, repo_root.name) if source_file else settings.mode
    onboarding_ref = rel(onboarding_file, onboarding_root)
    if storage_mode == "disabled":
        return [DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_file,
            repository=metadata.get("repository", repo_root.name),
            storage_mode="disabled",
            last_verified_hash=metadata.get("lastVerifiedCommitHash", ""),
            last_verified_date=metadata.get("lastVerifiedCommitDate", ""),
            classification="disabled",
            trust="high",
            affected_sections="none",
            note="Source path is excluded by pathRules.",
        )]
    if not sidecar_storage_label(storage_mode):
        return [DriftRow(
            onboarding_file=onboarding_ref,
            source_file=source_file,
            repository=metadata.get("repository", repo_root.name),
            storage_mode=storage_mode,
            last_verified_hash=metadata.get("lastVerifiedCommitHash", ""),
            last_verified_date=metadata.get("lastVerifiedCommitDate", ""),
            classification="unsupported",
            trust="low",
            affected_sections="resolver; storage configuration",
            note=f"Sidecar onboarding exists but the source path resolves to '{storage_mode}'.",
        )]
    row = classify_external_onboarding(onboarding_file, repo_root)
    row.onboarding_file = onboarding_ref
    row.storage_mode = storage_mode
    return [row]


@dataclass
class InlineBlock:
    raw_text: str
    metadata: dict[str, str]


def line_bounds(text: str, index: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return start, end


def expand_inline_bounds(source_text: str, start_index: int, end_index: int) -> tuple[int, int]:
    start_line_start, _ = line_bounds(source_text, start_index)
    previous_line_end = start_line_start - 1
    previous_line_start = source_text.rfind("\n", 0, max(previous_line_end, 0)) + 1
    previous_line = source_text[previous_line_start:previous_line_end].strip() if start_line_start > 0 else ""
    block_start = start_line_start
    expected_end = COMMON_BLOCK_DELIMITERS.get(previous_line)
    if expected_end:
        block_start = previous_line_start

    _, end_line_end = line_bounds(source_text, end_index)
    block_end = end_line_end
    if expected_end and end_line_end < len(source_text):
        next_line_start = end_line_end + 1
        next_line_end = source_text.find("\n", next_line_start)
        if next_line_end == -1:
            next_line_end = len(source_text)
        next_line = source_text[next_line_start:next_line_end].strip()
        if next_line == expected_end:
            block_end = next_line_end
    if block_end < len(source_text) and source_text[block_end:block_end + 1] == "\n":
        block_end += 1
    return block_start, block_end


def extract_inline_onboarding_block(source_text: str) -> InlineBlock | None:
    start_index = source_text.find(INLINE_START_MARKER)
    end_index = source_text.find(INLINE_END_MARKER)
    if start_index == -1 or end_index == -1 or end_index < start_index:
        return None

    block_start, block_end = expand_inline_bounds(source_text, start_index, end_index)
    raw_text = source_text[block_start:block_end]
    metadata: dict[str, str] = {}
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {INLINE_START_MARKER, INLINE_END_MARKER}:
            continue
        if stripped.startswith(("/*", "*/", "<!--", "-->", '"""', "'''", "=begin", "=end", "{-", "-}", "(*", "*)")):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = clean_scalar(value)
    return InlineBlock(raw_text=raw_text, metadata=metadata)


def compute_inline_source_digest(source_text: str, block: InlineBlock) -> str:
    source_without_block = source_text.replace(block.raw_text, "", 1)
    normalized_source = source_without_block.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()


def classify_inline_source(source_file: str, repo_root: Path) -> DriftRow:
    source_path = repo_root / normalize_rel_path(source_file)
    if not source_path.exists():
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date="",
            classification="orphaned",
            trust="low",
            affected_sections="all; source missing",
            note="Inline onboarding source file no longer exists.",
        )

    try:
        source_text = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date="",
            classification="unsupported",
            trust="low",
            affected_sections="verification; encoding",
            note="Source file is not UTF-8 text, so inline onboarding cannot be parsed safely.",
        )

    block = extract_inline_onboarding_block(source_text)
    if block is None:
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date="",
            classification="missing",
            trust="low",
            affected_sections="all; onboarding missing",
            note="Inline onboarding block is missing.",
        )

    source_digest = block.metadata.get("sourceDigest", "")
    verified_at = block.metadata.get("verifiedAt", "")
    if source_digest.startswith("sha256:"):
        source_digest = source_digest.split(":", 1)[1]
    if not source_digest:
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date=verified_at,
            classification="missing verification",
            trust="medium",
            affected_sections="metadata; verification",
            note="Inline onboarding block is missing sourceDigest metadata.",
        )

    computed_digest = compute_inline_source_digest(source_text, block)
    classification = "up to date" if computed_digest == source_digest else "drifted"
    return DriftRow(
        onboarding_file=f"inline:{normalize_rel_path(source_file)}",
        source_file=normalize_rel_path(source_file),
        repository=repo_root.name,
        storage_mode="inline",
        last_verified_hash=source_digest,
        last_verified_date=verified_at,
        classification=classification,
        trust="high" if classification == "up to date" else "medium",
        affected_sections="none" if classification == "up to date" else "logic; invariants; metadata",
        note=(
            "Inline source digest matches the current source body."
            if classification == "up to date"
            else "Source body changed since the recorded inline sourceDigest was computed."
        ),
    )


def classify_source(source_file: str, repo_root: Path, onboarding_root: Path, settings: StorageSettings) -> DriftRow:
    storage_mode = resolve_storage_for_source(source_file, settings, repo_root.name)
    if storage_mode == "disabled":
        return DriftRow(
            onboarding_file=f"disabled:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="disabled",
            last_verified_hash="",
            last_verified_date="",
            classification="disabled",
            trust="high",
            affected_sections="none",
            note="Source path is excluded by pathRules.",
        )
    if sidecar_storage_label(storage_mode):
        row = classify_external_source(source_file, repo_root, onboarding_root)
        row.storage_mode = storage_mode
        return row
    if storage_mode == "inline":
        return classify_inline_source(source_file, repo_root)
    return DriftRow(
        onboarding_file=f"unsupported:{normalize_rel_path(source_file)}",
        source_file=normalize_rel_path(source_file),
        repository=repo_root.name,
        storage_mode=storage_mode,
        last_verified_hash="",
        last_verified_date="",
        classification="unsupported",
        trust="low",
        affected_sections="resolver; storage configuration",
        note=f"Unsupported storage mode '{storage_mode}'.",
    )


def counts(rows: list[DriftRow]) -> dict[str, int]:
    return {name: sum(1 for row in rows if row.classification == name) for name in CLASSIFICATIONS}


def rel(path: Path | str, base: Path) -> str:
    if isinstance(path, str):
        return path
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def write_markdown_report(rows: list[DriftRow], report_path: Path, repo_root: Path, onboarding_root: Path) -> None:
    generated = dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    head = run_git(repo_root, ["rev-parse", "--short", "HEAD"])
    head_text = head.stdout.strip() if head.returncode == 0 else "unknown"
    summary = counts(rows)
    actionable = [row for row in rows if row.classification != "up to date"]

    lines: list[str] = [
        "# Onboarding Drift Report",
        "",
        f"**Scope checked:** `{onboarding_root.as_posix()}`",
        f"**Generated:** {generated}",
        f"**Repository HEAD:** `{head_text}`",
        "",
        "## Summary",
        "",
        "| Classification | Count |",
        "| --- | ---: |",
    ]
    for name in CLASSIFICATIONS:
        lines.append(f"| {name} | {summary[name]} |")

    lines.extend(
        [
            "",
            "## Actionable Findings",
            "",
            "| Onboarding unit | Source file | Storage | Classification | Trust | Likely affected sections | Note |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if actionable:
        for row in actionable:
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{row.onboarding_file}`",
                        f"`{row.source_file}`" if row.source_file else "",
                        row.storage_mode,
                        row.classification,
                        row.trust,
                        row.affected_sections,
                        row.note.replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| _None_ |  |  |  |  |  |")

    lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def resolve_report_path(
    report_path: Path | None,
    coordination_root: Path,
    temp_root: Path,
    repo_root: Path,
    memory_root: Path | None = None,
) -> Path:
    if report_path is None:
        return default_report_path(temp_root, repo_root)
    if memory_root is not None and report_path.is_absolute() and report_path.resolve().is_relative_to(memory_root.resolve()):
        return default_report_dir(temp_root, repo_root) / report_path.name
    if report_path.is_absolute():
        if report_path.resolve().is_relative_to(coordination_root.resolve()):
            return report_path
        return default_report_dir(temp_root, repo_root) / report_path.name
    candidate = temp_root / report_path
    if candidate.resolve().is_relative_to(temp_root.resolve()):
        return candidate
    return default_report_dir(temp_root, repo_root) / report_path.name


def print_text(rows: list[DriftRow], onboarding_root: Path) -> None:
    for row in rows:
        print(
            f"{row.onboarding_file}\t"
            f"{row.source_file}\t"
            f"{row.storage_mode}\t"
            f"{row.classification}\t"
            f"{row.trust}\t"
            f"{row.note}"
        )


def print_json(rows: list[DriftRow], onboarding_root: Path) -> None:
    payload = [
        {
            "onboarding_file": rel(row.onboarding_file, onboarding_root),
            "storage_mode": row.storage_mode,
            "source_file": row.source_file,
            "repository": row.repository,
            "last_verified_hash": row.last_verified_hash,
            "last_verified_date": row.last_verified_date,
            "classification": row.classification,
            "trust": row.trust,
            "affected_sections": row.affected_sections,
            "note": row.note,
        }
        for row in rows
    ]
    print(json.dumps(payload, indent=2))


def print_csv(rows: list[DriftRow], onboarding_root: Path) -> None:
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=[
            "onboarding_file",
            "storage_mode",
            "source_file",
            "repository",
            "last_verified_hash",
            "last_verified_date",
            "classification",
            "trust",
            "affected_sections",
            "note",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "onboarding_file": rel(row.onboarding_file, onboarding_root),
                "storage_mode": row.storage_mode,
                "source_file": row.source_file,
                "repository": row.repository,
                "last_verified_hash": row.last_verified_hash,
                "last_verified_date": row.last_verified_date,
                "classification": row.classification,
                "trust": row.trust,
                "affected_sections": row.affected_sections,
                "note": row.note,
            }
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-repository-root", required=True, type=Path, help="Root directory of the code repository to check.")
    parser.add_argument(
        "--onboarding-root",
        type=Path,
        help="Override for the resolved code repository onboarding root.",
    )
    parser.add_argument(
        "--topology",
        choices=("internal", "external"),
        help="Topology for this code repository. Defaults to internal when no onboarding root is supplied.",
    )
    parser.add_argument(
        "--coordination-root",
        type=Path,
        help="Coordination root. Required for --topology external unless --onboarding-root is supplied.",
    )
    parser.add_argument(
        "--settings-path",
        type=Path,
        help="Override the active settings.md path for this run.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional Markdown report output path. Relative paths resolve from the C-08 temp root; absolute paths are constrained to the coordination root.",
    )
    parser.add_argument("--format", choices=("text", "json", "csv"), default="text", help="Stdout format.")
    parser.add_argument(
        "--fail-on-actionable",
        action="store_true",
        help="Exit with code 1 when drifted, missing-verification, or orphaned files are found.",
    )
    args = parser.parse_args(argv)

    code_repository_root = args.code_repository_root.resolve()
    if not code_repository_root.exists():
        parser.error(f"code repository root does not exist: {code_repository_root}")
    try:
        context = resolve_coordination_context(
            code_repository_name=code_repository_root.name,
            workspace_root=code_repository_root.parent,
            requested_topology=args.topology,
            coordination_root=args.coordination_root,
            settings_path=args.settings_path,
            onboarding_root=args.onboarding_root,
            code_repository_root=code_repository_root,
        )
    except ValueError as error:
        parser.error(str(error))
    if not context.onboarding_root.exists():
        parser.error(f"onboarding root does not exist: {context.onboarding_root}")

    git_check = run_git(code_repository_root, ["rev-parse", "--show-toplevel"])
    if git_check.returncode != 0:
        parser.error(f"code repository root is not a git repository: {code_repository_root}\n{git_check.stderr.strip()}")
    settings = context.storage
    rows = [
        row
        for path in discover_onboarding_files(context.onboarding_root)
        for row in classify_sidecar_onboarding_units(path, code_repository_root, context.onboarding_root, settings)
    ]
    rows.extend(classify_inline_source(path, code_repository_root) for path in discover_inline_onboarding_sources(code_repository_root, settings))
    rows.sort(key=lambda row: (row.source_file, row.onboarding_file))

    write_markdown_report(
        rows,
        resolve_report_path(args.report, context.coordination_root, context.temp_root, code_repository_root, context.memory_root),
        code_repository_root,
        context.onboarding_root,
    )

    if args.format == "json":
        print_json(rows, context.onboarding_root)
    elif args.format == "csv":
        print_csv(rows, context.onboarding_root)
    else:
        print_text(rows, context.onboarding_root)

    if args.fail_on_actionable and any(row.classification in ACTIONABLE_CLASSIFICATIONS for row in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
