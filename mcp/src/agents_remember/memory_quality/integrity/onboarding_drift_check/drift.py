#!/usr/bin/env python3
"""Check Agents Remember file-level onboarding drift.

Requires Python 3.9+ and git. Uses only the Python standard library.

This module is a thin facade. Implementation lives in focused sibling modules
(models, git_ops, discovery, entities, inline, sidecar, report). Public names
are re-exported here so existing imports keep working unchanged
(`import drift; drift.X` and `from ...drift import (...)`).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agents_remember.kernel.coordination_context_resolver import (
    StorageSettings,
    clean_scalar,
    is_sidecar_storage,
    normalize_rel_path,
    resolve_coordination_context,
    resolve_storage_for_source,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    discover_onboarding_files,
    is_supported_sidecar_onboarding,
    mirror_onboarding_path,
    normalize_overview_route,
    parse_table_metadata,
    rel,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.entities import (
    classify_entity_catalog,
    classify_entity_fingerprint,
    missing_entity_fingerprint_row,
    orphaned_entity_fingerprint_row,
    parse_entity_fingerprint_rows,
    parse_entity_inventory_names,
    split_evidence_paths,
    split_table_row,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    compute_git_blob_set_fingerprint,
    current_branch_name,
    entity_local_change_notes,
    git_blob_hash,
    git_stdout,
    list_repo_sources,
    local_change_note,
    local_route_change_note,
    run_git,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.inline import (
    classify_inline_source,
    compute_inline_source_digest,
    discover_inline_onboarding_sources,
    expand_inline_bounds,
    extract_inline_onboarding_block,
    line_bounds,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    ACTIONABLE_CLASSIFICATIONS,
    CLASSIFICATIONS,
    COMMON_BLOCK_DELIMITERS,
    GIT_BLOB_SET_ALGORITHM,
    INLINE_END_MARKER,
    INLINE_START_MARKER,
    SIDECAR_DOC_TYPES,
    DriftRow,
    EntityFingerprint,
    InlineBlock,
    repo_root_placeholder,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.report import (
    counts,
    default_report_dir,
    default_report_filename,
    default_report_path,
    print_csv,
    print_json,
    print_text,
    resolve_report_path,
    sanitize_report_token,
    write_markdown_report,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.sidecar import (
    classify_external_onboarding,
    classify_external_source,
    classify_overview_onboarding,
    classify_sidecar_onboarding_units,
)

__all__ = [
    "ACTIONABLE_CLASSIFICATIONS",
    "CLASSIFICATIONS",
    "COMMON_BLOCK_DELIMITERS",
    "GIT_BLOB_SET_ALGORITHM",
    "INLINE_END_MARKER",
    "INLINE_START_MARKER",
    "SIDECAR_DOC_TYPES",
    "DriftRow",
    "EntityFingerprint",
    "InlineBlock",
    "StorageSettings",
    "classify_entity_catalog",
    "classify_entity_fingerprint",
    "classify_external_onboarding",
    "classify_external_source",
    "classify_inline_source",
    "classify_overview_onboarding",
    "classify_sidecar_onboarding_units",
    "classify_source",
    "clean_scalar",
    "compute_git_blob_set_fingerprint",
    "compute_inline_source_digest",
    "counts",
    "current_branch_name",
    "default_report_dir",
    "default_report_filename",
    "default_report_path",
    "discover_inline_onboarding_sources",
    "discover_onboarding_files",
    "entity_local_change_notes",
    "expand_inline_bounds",
    "extract_inline_onboarding_block",
    "git_blob_hash",
    "git_stdout",
    "is_sidecar_storage",
    "is_supported_sidecar_onboarding",
    "line_bounds",
    "list_repo_sources",
    "local_change_note",
    "local_route_change_note",
    "main",
    "mirror_onboarding_path",
    "missing_entity_fingerprint_row",
    "normalize_overview_route",
    "normalize_rel_path",
    "orphaned_entity_fingerprint_row",
    "parse_entity_fingerprint_rows",
    "parse_entity_inventory_names",
    "parse_table_metadata",
    "print_csv",
    "print_json",
    "print_text",
    "rel",
    "repo_root_placeholder",
    "resolve_coordination_context",
    "resolve_report_path",
    "resolve_storage_for_source",
    "run_git",
    "sanitize_report_token",
    "split_evidence_paths",
    "split_table_row",
    "write_markdown_report",
]


def classify_source(
    source_file: str, repo_root: Path, onboarding_root: Path, settings: StorageSettings
) -> DriftRow:
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
    if is_sidecar_storage(storage_mode):
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


def _collect_drift_rows(
    onboarding_root: Path, code_repository_root: Path, settings: StorageSettings
) -> list[DriftRow]:
    rows = [
        row
        for path in discover_onboarding_files(onboarding_root)
        for row in classify_sidecar_onboarding_units(
            path, code_repository_root, onboarding_root, settings
        )
    ]
    rows.extend(
        classify_inline_source(path, code_repository_root)
        for path in discover_inline_onboarding_sources(code_repository_root, settings)
    )
    rows.sort(key=lambda row: (row.source_file, row.onboarding_file))
    return rows


def _print_drift_rows(rows: list[DriftRow], output_format: str, onboarding_root: Path) -> None:
    if output_format == "json":
        print_json(rows, onboarding_root)
    elif output_format == "csv":
        print_csv(rows, onboarding_root)
    else:
        print_text(rows, onboarding_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--code-repository-root",
        required=True,
        type=Path,
        help="Root directory of the code repository to check.",
    )
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
    parser.add_argument(
        "--format", choices=("text", "json", "csv"), default="text", help="Stdout format."
    )
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
        parser.error(
            f"code repository root is not a git repository: {code_repository_root}\n{git_check.stderr.strip()}"
        )
    settings = context.storage
    rows = _collect_drift_rows(context.onboarding_root, code_repository_root, settings)

    write_markdown_report(
        rows,
        resolve_report_path(
            args.report,
            context.coordination_root,
            context.temp_root,
            code_repository_root,
            context.memory_root,
        ),
        code_repository_root,
        context.onboarding_root,
    )

    _print_drift_rows(rows, args.format, context.onboarding_root)

    if args.fail_on_actionable and any(
        row.classification in ACTIONABLE_CLASSIFICATIONS for row in rows
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
