"""Bounded drift summary helpers for context packets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.memory_quality.integrity.onboarding_drift_check import drift

ACTIONABLE_CLASSIFICATIONS = {
    "drifted",
    "missing verification",
    "missing",
    "orphaned",
    "unsupported",
}


def not_checked() -> dict[str, Any]:
    return {"status": "notChecked"}


def run_drift_summary(
    *,
    code_repository_root: Path,
    context: Any,
    detail_limit: int = 10,
) -> dict[str, Any]:
    if not context.onboarding_root.exists():
        return {
            "status": "error",
            "error": f"onboarding root does not exist: {context.onboarding_root}",
        }

    rows = [
        row
        for path in drift.discover_onboarding_files(context.onboarding_root)
        for row in drift.classify_sidecar_onboarding_units(
            path,
            code_repository_root,
            context.onboarding_root,
            context.storage,
        )
    ]
    rows.extend(
        drift.classify_inline_source(path, code_repository_root)
        for path in drift.discover_inline_onboarding_sources(
            code_repository_root,
            context.storage,
        )
    )
    rows.sort(key=lambda row: (row.source_file, row.onboarding_file))
    report_path = drift.resolve_report_path(
        None,
        context.coordination_root,
        context.temp_root,
        code_repository_root,
        context.memory_root,
    )
    drift.write_markdown_report(
        rows,
        report_path,
        code_repository_root,
        context.onboarding_root,
    )
    return summarize_rows(
        [_row_to_dict(row, context.onboarding_root) for row in rows],
        report_path=report_path.as_posix(),
        detail_limit=detail_limit,
    )


def summarize_rows(
    rows: list[dict[str, Any]],
    *,
    report_path: str = "",
    detail_limit: int = 10,
) -> dict[str, Any]:
    actionable = [
        row for row in rows if str(row.get("classification", "")) in ACTIONABLE_CLASSIFICATIONS
    ]
    return {
        "status": "checked",
        "count": len(rows),
        "actionableCount": len(actionable),
        "reportPath": report_path,
        "actionableSample": actionable[:detail_limit],
    }


def _row_to_dict(row: Any, onboarding_root: Path) -> dict[str, Any]:
    return {
        "onboarding_file": drift.rel(row.onboarding_file, onboarding_root),
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
