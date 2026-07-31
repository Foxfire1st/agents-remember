"""Sidecar and overview onboarding classification for drift detection."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.coordination_context_resolver import (
    StorageSettings,
    is_sidecar_storage,
    normalize_rel_path,
    resolve_storage_for_source,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    mirror_onboarding_path,
    normalize_overview_route,
    parse_table_metadata,
    rel,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.entities import (
    classify_entity_catalog,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    local_change_note,
    local_route_change_note,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    DriftRow,
)


def classify_external_onboarding(onboarding_file: Path, repo_root: Path) -> DriftRow:
    metadata = parse_table_metadata(onboarding_file)
    source_file = normalize_rel_path(metadata.get("path", ""))
    last_hash = metadata.get("lastVerifiedCommitHash", "")

    def row(*, classification: str, trust: str, affected_sections: str, note: str) -> DriftRow:
        """One verdict for this sidecar; identity and verification stamp are fixed."""
        return DriftRow(
            onboarding_file=onboarding_file.as_posix(),
            source_file=source_file,
            repository=metadata.get("repository", ""),
            storage_mode="external",
            last_verified_hash=last_hash,
            last_verified_date=metadata.get("lastVerifiedCommitDate", ""),
            classification=classification,
            trust=trust,
            affected_sections=affected_sections,
            note=note,
        )

    def _early_classification() -> DriftRow | None:
        """Classify missing/orphaned/unavailable-commit sidecars before the diff."""
        if not source_file or not last_hash:
            return row(
                classification="missing verification",
                trust="medium",
                affected_sections="metadata; verification",
                note="Missing source path or lastVerifiedCommitHash.",
            )
        if not (repo_root / source_file).exists():
            return row(
                classification="orphaned",
                trust="low",
                affected_sections="all; source missing",
                note="Source file no longer exists.",
            )
        exists = run_git(repo_root, ["cat-file", "-e", f"{last_hash}^{{commit}}"])
        if exists.returncode != 0:
            return row(
                classification="drifted",
                trust="medium",
                affected_sections="logic; invariants; metadata",
                note="Recorded verification commit is not available in git history.",
            )
        return None

    early = _early_classification()
    if early is not None:
        return early

    diff = run_git(repo_root, ["diff", "--quiet", last_hash, "HEAD", "--", source_file])
    if diff.returncode == 0:
        local_note = local_change_note(repo_root, source_file)
        if local_note:
            return row(
                classification="drifted",
                trust="medium",
                affected_sections="logic; invariants; conventions; docs references",
                note=local_note,
            )
        return row(
            classification="up to date",
            trust="high",
            affected_sections="none",
            note="No source diff since recorded verification commit.",
        )
    if diff.returncode == 1:
        return row(
            classification="drifted",
            trust="medium",
            affected_sections="logic; invariants; conventions; docs references",
            note="Source changed since recorded verification commit.",
        )

    return row(
        classification="drifted",
        trust="medium",
        affected_sections="logic; invariants; metadata",
        note=f"git diff failed: {diff.stderr.strip() or 'unknown git error'}",
    )


def classify_overview_onboarding(
    onboarding_file: Path, repo_root: Path, onboarding_root: Path, settings: StorageSettings
) -> DriftRow:
    metadata = parse_table_metadata(onboarding_file)
    doc_type = metadata.get("doc_type", "")
    repository = metadata.get("repository", repo_root.name)
    source_route = normalize_overview_route(metadata.get("sourceRoute", "."))
    last_hash = metadata.get("lastVerifiedCommitHash", "")
    last_date = metadata.get("lastVerifiedCommitDate", "")
    onboarding_ref = rel(onboarding_file, onboarding_root)

    def _early_classification() -> DriftRow | None:
        """Classify missing/orphaned/unavailable-commit overviews before the diff."""
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
        return None

    early = _early_classification()
    if early is not None:
        return early

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
    storage_mode = (
        resolve_storage_for_source(source_file, settings, repo_root.name)
        if source_file
        else settings.mode
    )
    onboarding_ref = rel(onboarding_file, onboarding_root)
    if storage_mode == "disabled":
        return [
            DriftRow(
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
            )
        ]
    if not is_sidecar_storage(storage_mode):
        return [
            DriftRow(
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
            )
        ]
    row = classify_external_onboarding(onboarding_file, repo_root)
    row.onboarding_file = onboarding_ref
    row.storage_mode = storage_mode
    return [row]
